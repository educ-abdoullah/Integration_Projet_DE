"""Scenarios reels de panne sur des schemas PostgreSQL temporaires."""

import argparse
from datetime import datetime, timezone
import io
import json
import logging
from pathlib import Path
from unittest.mock import patch

import psycopg

from pipeline_data.database import process_blob
from pipeline_data.loading import DataValidationError
from pipeline_data.settings import database_url
from scripts.generate_csv import generate_csv_bytes
from tests.support import isolated_database


def counts(dsn: str) -> dict:
    with psycopg.connect(dsn) as connection:
        return {
            "fichiers": connection.execute("SELECT COUNT(*) FROM fichiers_traites").fetchone()[0],
            "transactions": connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
            "total_banque": str(connection.execute(
                "SELECT COALESCE(SUM(montant_total), 0.00) FROM totaux_envoyes_par_banque"
            ).fetchone()[0]),
        }


def execute_scenario(scenario: str, dsn: str) -> dict:
    name, content = next(iter(generate_csv_bytes().items()))
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    result = {"scenario": scenario, "avant": counts(dsn)}
    try:
        if scenario == "generation":
            damaged = content.replace(b"100.00", b"MONTANT_INVALIDE", 1)
            try:
                process_blob(dsn, name, damaged)
            except DataValidationError as exc:
                result.update(statut="ECHEC_ATTENDU", erreur=type(exc).__name__, detail=str(exc))
                logging.error("ECHEC generation : montant invalide ; aucun retry metier")
            else:
                raise AssertionError("la generation invalide aurait du echouer")
        elif scenario == "traitement":
            # Bug volontaire : tous les virements sont marques suspects.
            with patch("pipeline_data.database.flag_transaction", return_value=True):
                try:
                    process_blob(dsn, name, content)
                except psycopg.errors.CheckViolation:
                    result.update(statut="ECHEC_ATTENDU", erreur="CheckViolation",
                                  detail="Flag vrai sur 100.00 EUR : contrainte seuil_transaction_suspecte violee.")
                    logging.error("ECHEC traitement : contrainte SQL ; rollback de toutes les ecritures")
                else:
                    raise AssertionError("le mauvais flag aurait du echouer")
        elif scenario == "retry":
            calls = []
            def fail_once():
                calls.append(1)
                if len(calls) == 1:
                    logging.error("PANNE SIMULEE apres les ecritures et avant COMMIT")
                    raise psycopg.OperationalError("panne temporaire simulee")
            process_blob(dsn, name, content, after_insert=fail_once, base_delay=0.2)
            if len(calls) != 2:
                raise AssertionError("deux tentatives attendues")
            result.update(statut="REPRISE_REUSSIE", tentatives=len(calls),
                          detail="Premiere tentative annulee ; seconde tentative validee.")
            # Meme contenu sous un autre nom : il doit etre ignore AVANT le parse.
            with patch("pipeline_data.database.load_csv_bytes", side_effect=AssertionError("retraitement interdit")):
                if process_blob(dsn, "copie_renommee.csv", content):
                    raise AssertionError("le doublon ne doit pas etre insere")
            result["doublon_renomme"] = "IGNORE_SANS_RETRAITEMENT"
        else:
            raise ValueError("scenario inconnu")

        result["apres"] = counts(dsn)
        if scenario in {"generation", "traitement"}:
            if result["apres"]["transactions"] or result["apres"]["fichiers"]:
                raise AssertionError("des donnees partielles ont ete conservees")
        elif result["apres"] != {"fichiers": 1, "transactions": 7, "total_banque": "19092.51"}:
            raise AssertionError("la reprise a produit des valeurs incorrectes")
        result["logs"] = log_stream.getvalue()
        return result
    finally:
        root.removeHandler(handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--fail", choices=("generation", "traitement"),
                           help="Reproduire un echec et retourner volontairement le code 1.")
    selection.add_argument("--scenario", choices=("generation", "traitement", "retry"),
                           help="Executer uniquement ce scenario et verifier son resultat.")
    parser.add_argument("--output", type=Path, default=Path("artifacts/demonstrations.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    results = []
    selected = args.fail or args.scenario
    for scenario in ([selected] if selected else ["generation", "traitement", "retry"]):
        with isolated_database(database_url()) as dsn:
            result = execute_scenario(scenario, dsn)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    report = {"date_execution": datetime.now(timezone.utc).isoformat(), "scenarios": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.fail:
        parser.exit(1, "ECHEC VOLONTAIRE : code de sortie 1, rollback verifie.\n")


if __name__ == "__main__":
    main()
