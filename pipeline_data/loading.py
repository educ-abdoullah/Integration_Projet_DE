"""Lecture et validation des CSV ; aucun calcul de sommes ici."""

import csv
import io
import re
from dataclasses import fields
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from .models import Transaction


CSV_COLUMNS = tuple(field.name for field in fields(Transaction))


class DataValidationError(ValueError):
    """Le fichier ne respecte pas le contrat de donnees."""


def _parse_transaction(row: dict[str, str]) -> Transaction:
    values = {key: value.strip() for key, value in row.items()}
    for name in CSV_COLUMNS:
        if name != "iban_destinataire" and not values[name]:
            raise ValueError(f"champ obligatoire vide : {name}")

    date = datetime.fromisoformat(values["date_transaction"])
    if date.tzinfo is None or date.utcoffset() is None:
        raise ValueError("date_transaction doit inclure un fuseau horaire")

    # Compatible avec NUMERIC(14,2), sans exposant ni arrondi silencieux.
    if not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,2})?", values["montant"]):
        raise ValueError("montant invalide : maximum 12 chiffres entiers et 2 decimales")
    amount = Decimal(values["montant"])
    if amount <= 0:
        raise ValueError("montant doit etre strictement positif")

    for name in ("pays_source", "pays_destinataire"):
        if not re.fullmatch(r"[A-Z]{2}", values[name]):
            raise ValueError(f"{name} doit contenir deux lettres majuscules")
    if not re.fullmatch(r"[A-Z]{3}", values["devise"]):
        raise ValueError("devise doit contenir trois lettres majuscules")
    for name in ("iban_origine", "iban_destinataire"):
        if len(values[name]) > 34:
            raise ValueError(f"{name} depasse 34 caracteres")

    return Transaction(
        transaction_id=UUID(values["transaction_id"]),
        date_transaction=date,
        iban_origine=values["iban_origine"],
        pays_source=values["pays_source"],
        banque_source=values["banque_source"],
        iban_destinataire=values["iban_destinataire"] or None,
        compte_destinataire=values["compte_destinataire"],
        pays_destinataire=values["pays_destinataire"],
        montant=amount,
        devise=values["devise"],
    )


def load_csv_bytes(content: bytes, name: str = "<memoire>") -> tuple[Transaction, ...]:
    """Valide les octets d'un CSV local ou S3 sans creer de fichier temporaire."""
    transactions = []
    seen_ids = set()
    # utf-8-sig accepte aussi les CSV UTF-8 contenant un BOM.
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DataValidationError(f"{name} : encodage UTF-8 invalide") from exc
    with io.StringIO(decoded, newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        try:
            header = reader.fieldnames
            if header is None or len(header) != len(CSV_COLUMNS) or set(header) != set(CSV_COLUMNS):
                raise ValueError(f"colonnes attendues : {', '.join(CSV_COLUMNS)}")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("nombre de champs incorrect")
                transaction = _parse_transaction(row)
                if transaction.transaction_id in seen_ids:
                    raise ValueError(f"transaction_id duplique : {transaction.transaction_id}")
                seen_ids.add(transaction.transaction_id)
                transactions.append(transaction)
        except (ValueError, csv.Error) as exc:
            raise DataValidationError(f"{name}, ligne {reader.line_num} : {exc}") from exc

    if not transactions:
        raise DataValidationError(f"{name} : aucune transaction")
    return tuple(transactions)


def load_csv(path: Path) -> tuple[Transaction, ...]:
    """Charge tout un CSV ou leve une erreur sans retourner de resultat partiel."""
    return load_csv_bytes(path.read_bytes(), str(path))


def load_directory(directory: Path) -> tuple[Transaction, ...]:
    """Charge les *.csv dans l'ordre et refuse les doublons entre fichiers."""
    if not directory.is_dir():
        raise FileNotFoundError(f"Dossier introuvable : {directory}")
    paths = sorted(directory.glob("*.csv"))
    if not paths:
        raise DataValidationError(f"{directory} : aucun fichier CSV")
    transactions = []
    seen_ids = set()
    for path in paths:
        for transaction in load_csv(path):
            if transaction.transaction_id in seen_ids:
                raise DataValidationError(
                    f"{path} : transaction_id deja present dans un autre fichier : "
                    f"{transaction.transaction_id}"
                )
            seen_ids.add(transaction.transaction_id)
            transactions.append(transaction)
    return tuple(transactions)
