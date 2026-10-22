"""Affichage et verification du jeu de demonstration, en lecture seule."""

import argparse
from collections import Counter
import csv
from datetime import datetime
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .settings import database_url
from .storage import download_csv, list_csv_keys, s3_client


QUERIES = {
    "transactions": "SELECT * FROM transactions ORDER BY date_transaction, transaction_id",
    "fichiers_traites": "SELECT * FROM fichiers_traites ORDER BY empreinte",
    "totaux_envoyes_par_iban": "SELECT * FROM totaux_envoyes_par_iban ORDER BY iban_origine, devise",
    "totaux_envoyes_par_banque": "SELECT * FROM totaux_envoyes_par_banque ORDER BY banque_source, devise",
    "totaux_recus_par_compte": "SELECT * FROM totaux_recus_par_compte ORDER BY pays_destinataire, compte_destinataire, devise",
}


def table(title: str, columns: list[str], rows: list[list]) -> None:
    print("\n" + title, flush=True)
    rendered = [[str(value) for value in row] for row in rows]
    widths = [max(len(name), *(len(row[index]) for row in rendered))
              if rendered else len(name) for index, name in enumerate(columns)]
    def line(values):
        return " | ".join(value.ljust(width) for value, width in zip(values, widths))
    print(line(columns))
    print("-+-".join("-" * width for width in widths))
    for row in rendered:
        print(line(row))
    print(f"{len(rows)} ligne(s)", flush=True)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError("Controle non satisfait : " + message)
    print("[OK] " + message, flush=True)


def json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def show_proof(save_state: Path | None = None, compare_state: Path | None = None,
               summary_only: bool = False) -> None:
    print("\n=== PREUVES : LECTURES REELLES S3 ET POSTGRESQL ===", flush=True)
    client = s3_client()
    bucket = os.environ["S3_BUCKET"]
    objects = []
    for key in list_csv_keys(client, bucket):
        content = download_csv(client, bucket, key)
        count = len(list(csv.DictReader(io.StringIO(content.decode("utf-8-sig")))))
        objects.append({"cle": key, "nb_transactions": count,
                        "empreinte": hashlib.sha256(content).hexdigest()})

    with psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=5) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        tables = {name: connection.execute(query).fetchall() for name, query in QUERIES.items()}
        # Comparaison independante des tables de resultats avec les GROUP BY SQL.
        groups = (
            ("totaux_envoyes_par_iban", "iban_origine, devise"),
            ("totaux_envoyes_par_banque", "banque_source, devise"),
            ("totaux_recus_par_compte", "pays_destinataire, compte_destinataire, devise"),
        )
        reconciled = all(
            connection.execute(f"SELECT {columns}, montant_total FROM {name} ORDER BY {columns}").fetchall()
            == connection.execute(f"SELECT {columns}, SUM(montant) AS montant_total FROM transactions "
                                  f"GROUP BY {columns} ORDER BY {columns}").fetchall()
            for name, columns in groups
        )

    transactions = tables["transactions"]
    total = sum((row["montant"] for row in transactions), Decimal("0.00"))
    flagged = sum(row["est_suspecte"] for row in transactions)
    if not summary_only:
        table("OBJETS S3 : " + bucket, ["cle", "transactions", "empreinte (debut)"],
              [[obj["cle"], obj["nb_transactions"], obj["empreinte"][:12]] for obj in objects])
        table("TABLE transactions (colonnes principales)",
              ["date_transaction", "iban_origine", "compte_destinataire", "montant", "devise", "flag"],
              [[row["date_transaction"].isoformat(), row["iban_origine"], row["compte_destinataire"],
                f"{row['montant']:.2f}", row["devise"], row["est_suspecte"]] for row in transactions])
        table("TABLE fichiers_traites", ["nom_fichier", "nb_transactions", "date_trt", "empreinte (debut)"],
              [[row["nom_fichier"], row["nb_transactions"], row["date_trt"].isoformat(),
                row["empreinte"][:12]] for row in tables["fichiers_traites"]])
        for name in ("totaux_envoyes_par_iban", "totaux_envoyes_par_banque", "totaux_recus_par_compte"):
            rows = tables[name]
            columns = list(rows[0]) if rows else []
            table("TABLE " + name, columns, [[row[column] for column in columns] for row in rows])

    table("RESUME POSTGRESQL", ["transactions", "fichiers", "total EUR", "flags"],
          [[len(transactions), len(tables["fichiers_traites"]), f"{total:.2f}", flagged]])
    require(len(objects) == 3 and all(obj["nb_transactions"] == 7 for obj in objects),
            "S3 contient 3 CSV de 7 transactions")
    require(len(transactions) == 21 and total == Decimal("64697.76") and flagged == 6
            and all(row["devise"] == "EUR" for row in transactions),
            "PostgreSQL contient 21 transactions, 64697.76 EUR et 6 flags")
    require(all(row["est_suspecte"] == (row["montant"] > Decimal("5000")) for row in transactions),
            "Chaque flag respecte le seuil strictement superieur a 5000")
    require(reconciled, "Les 3 tables de sommes correspondent aux GROUP BY des transactions")
    files = tables["fichiers_traites"]
    require(len(files) == 3 and {row["empreinte"] for row in files} == {obj["empreinte"] for obj in objects},
            "Les empreintes des CSV S3 correspondent aux 3 fichiers suivis en base")
    counts = Counter(row["fichier_id"] for row in transactions)
    require(all(row["nb_transactions"] == counts[row["fichier_id"]] == 7 for row in files),
            "Chaque fichier suivi correspond a 7 transactions inserees")
    state = json.loads(json.dumps(tables, default=json_value))
    if compare_state:
        previous = json.loads(compare_state.read_text(encoding="utf-8"))
        require(state == previous,
                "ANTI-DOUBLON : les 5 tables sont identiques avant et apres relance")
    if save_state:
        save_state.parent.mkdir(parents=True, exist_ok=True)
        save_state.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print("PREUVES VALIDEES : donnees S3 et tables PostgreSQL coherentes", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-state", type=Path)
    parser.add_argument("--compare-state", type=Path)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    try:
        show_proof(args.save_state, args.compare_state, args.summary_only)
    except Exception as exc:
        print(f"[ECHEC] Verification ({type(exc).__name__}) : {exc}", flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
