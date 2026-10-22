"""Fonctions pures : pas d'acces disque, reseau, base, horloge ou mutation d'entree.

Les entrees sont des Transaction validees, aux montants positifs de type Decimal.
Les cles incluent toujours la devise pour ne pas melanger les monnaies.
"""

from collections.abc import Iterable
from decimal import Decimal, localcontext
from typing import Callable, TypeVar

from .models import Transaction


Key = TypeVar("Key")


def _sum_by(
    transactions: Iterable[Transaction], key: Callable[[Transaction], Key]
) -> dict[Key, Decimal]:
    totals: dict[Key, Decimal] = {}
    # Contexte local stable, independant d'un eventuel reglage decimal appelant.
    with localcontext() as context:
        context.prec = 40
        for transaction in transactions:
            group = key(transaction)
            total = totals.get(group, Decimal("0.00")) + transaction.montant
            if total > Decimal("999999999999999999.99"):
                raise ValueError("total trop grand pour NUMERIC(20,2)")
            totals[group] = total
    return totals


def sum_sent_by_iban(transactions: Iterable[Transaction]) -> dict[tuple[str, str], Decimal]:
    """Sommes envoyees par (IBAN origine, devise)."""
    return _sum_by(transactions, lambda tx: (tx.iban_origine, tx.devise))


def sum_sent_by_bank(transactions: Iterable[Transaction]) -> dict[tuple[str, str], Decimal]:
    """Sommes envoyees par (banque source, devise)."""
    return _sum_by(transactions, lambda tx: (tx.banque_source, tx.devise))


def sum_received_by_account(
    transactions: Iterable[Transaction],
) -> dict[tuple[str, str, str], Decimal]:
    """Sommes recues par (pays destinataire, compte ou IBAN, devise)."""
    return _sum_by(
        transactions, lambda tx: (tx.pays_destinataire, tx.compte_destinataire, tx.devise)
    )


def flag_transaction(transaction: Transaction) -> bool:
    """Vrai uniquement au-dessus de 5000, dans la devise de la transaction."""
    return transaction.montant > Decimal("5000.00")
