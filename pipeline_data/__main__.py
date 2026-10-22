"""Commande de chargement et de traitement, sans insertion PostgreSQL."""

import argparse
import json
from pathlib import Path

from .loading import load_directory
from .processing import (
    flag_transaction,
    sum_received_by_account,
    sum_sent_by_bank,
    sum_sent_by_iban,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir", type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "raw",
        help="Dossier contenant les CSV (par defaut : data/raw).",
    )
    args = parser.parse_args()
    try:
        transactions = load_directory(args.input_dir)
        sent_iban = sum_sent_by_iban(transactions)
        sent_bank = sum_sent_by_bank(transactions)
        received = sum_received_by_account(transactions)
        flags = [
            {"transaction_id": str(tx.transaction_id), "montant": str(tx.montant),
             "devise": tx.devise, "est_suspecte": flag_transaction(tx)}
            for tx in transactions
        ]
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Erreur : {exc}\n")

    result = {
        "nb_transactions": len(transactions),
        "nb_suspectes": sum(row["est_suspecte"] for row in flags),
        "totaux_envoyes_par_iban": [
            {"iban_origine": iban, "devise": currency, "montant_total": str(total)}
            for (iban, currency), total in sorted(sent_iban.items())
        ],
        "totaux_envoyes_par_banque": [
            {"banque_source": bank, "devise": currency, "montant_total": str(total)}
            for (bank, currency), total in sorted(sent_bank.items())
        ],
        "totaux_recus_par_compte": [
            {"pays_destinataire": country, "compte_destinataire": account,
             "devise": currency, "montant_total": str(total)}
            for (country, account, currency), total in sorted(received.items())
        ],
        "transactions": flags,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
