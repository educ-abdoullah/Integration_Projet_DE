"""Representation immuable d'une transaction validee."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True)
class Transaction:
    transaction_id: UUID
    date_transaction: datetime
    iban_origine: str
    pays_source: str
    banque_source: str
    iban_destinataire: str | None
    compte_destinataire: str
    pays_destinataire: str
    montant: Decimal
    devise: str
