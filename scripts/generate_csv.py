"""Genere trois CSV de sept transactions fictives, sans dependance externe."""

import argparse
import csv
import io
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


TRANSACTION_DATE = "2026-10-22"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"

FIELDNAMES = (
    "transaction_id",
    "date_transaction",
    "iban_origine",
    "pays_source",
    "banque_source",
    "iban_destinataire",
    "compte_destinataire",
    "pays_destinataire",
    "montant",
    "devise",
)

# Identifiants synthetiques reserves aux donnees de demonstration du projet.
SOURCE_IBANS = (
    "FR7630004000010000000000136",
    "FR7630004000010000000000233",
    "FR7630004000010000000000330",
)
DESTINATION_ACCOUNTS = ("000000000001", "000000000002", "000000000003")

# Chaque ligne : indice du compte source, indice du destinataire, montant EUR.
# Les montants sont des chaines pour ecrire exactement deux decimales.
# Aucun tirage aleatoire ni recours a l'horloge de la machine.
BATCHES = (
    (
        (0, 0, "100.00"),
        (0, 1, "250.00"),
        (1, 0, "5000.00"),
        (1, 2, "5000.01"),
        (2, 1, "7500.00"),
        (2, 2, "42.50"),
        (0, 0, "1200.00"),
    ),
    (
        (0, 1, "300.00"),
        (1, 2, "1500.00"),
        (2, 0, "6500.00"),
        (0, 2, "5000.00"),
        (1, 0, "5000.01"),
        (2, 1, "999.99"),
        (0, 0, "2000.00"),
    ),
    (
        (1, 1, "80.00"),
        (2, 2, "125.25"),
        (0, 0, "10000.00"),
        (1, 2, "4999.99"),
        (2, 1, "5000.01"),
        (0, 1, "600.00"),
        (1, 0, "3500.00"),
    ),
)


def generate_csv_bytes() -> dict[str, bytes]:
    """Construit les CSV en memoire, aussi reutilisables par les tests."""
    files = {}
    for batch_number, transactions in enumerate(BATCHES, start=1):
        name = f"transactions_{TRANSACTION_DATE}_{batch_number:02d}.csv"
        with io.StringIO(newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES, lineterminator="\n")
            writer.writeheader()
            for row_number, (source, destination, amount) in enumerate(transactions, start=1):
                transaction_id = uuid5(
                    NAMESPACE_URL,
                    f"projetde:{TRANSACTION_DATE}:{batch_number}:{row_number}",
                )
                writer.writerow(
                    {
                        "transaction_id": str(transaction_id),
                        "date_transaction": (
                            f"{TRANSACTION_DATE}T{8 + batch_number:02d}:{row_number:02d}:00+02:00"
                        ),
                        "iban_origine": SOURCE_IBANS[source],
                        "pays_source": "FR",
                        "banque_source": "BNP Paribas",
                        "iban_destinataire": "",
                        "compte_destinataire": DESTINATION_ACCOUNTS[destination],
                        "pays_destinataire": "IN",
                        "montant": amount,
                        "devise": "EUR",
                    }
                )
            files[name] = csv_file.getvalue().encode("utf-8")
    return files


def generate_csv(output_dir: Path = DEFAULT_OUTPUT_DIR) -> list[Path]:
    """Ecrit les fichiers fixes ; remplace seulement les trois noms prevus."""
    output_dir.mkdir(parents=True, exist_ok=True)
    created_files = []
    for name, content in generate_csv_bytes().items():
        path = output_dir / name
        path.write_bytes(content)
        created_files.append(path)

    return created_files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Dossier des CSV (par defaut : data/raw dans le projet).",
    )
    args = parser.parse_args()
    for path in generate_csv(args.output_dir):
        print(f"{path} : 7 transactions")


if __name__ == "__main__":
    main()
