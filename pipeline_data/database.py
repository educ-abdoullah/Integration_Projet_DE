"""Insertion atomique, verrouillage par empreinte et reprise sans doublon."""

import hashlib
import logging
from collections.abc import Callable
from dataclasses import astuple

import psycopg

from .loading import load_csv_bytes
from .processing import (
    flag_transaction, sum_received_by_account, sum_sent_by_bank, sum_sent_by_iban,
)
from .retry import retry


logger = logging.getLogger(__name__)


def is_transient_database_error(exc: Exception) -> bool:
    if not isinstance(exc, psycopg.Error):
        return False
    state = exc.sqlstate
    if state:
        return state.startswith("08") or state in {"40001", "40P01", "57P01", "57P02", "57P03"}
    return isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError))


def process_blob(
    dsn: str, name: str, content: bytes, *, attempts: int = 3,
    base_delay: float = 0.5, after_insert: Callable[[], None] | None = None,
) -> bool:
    """True si insere, False si deja traite ; chaque tentative est atomique.

    after_insert est un point d'injection de panne pour les tests et demonstrations.
    Il est appele apres toutes les ecritures mais avant le COMMIT.
    """
    fingerprint = hashlib.sha256(content).hexdigest()
    lock_id = int.from_bytes(bytes.fromhex(fingerprint[:16]), "big", signed=True)

    def insert_once() -> bool:
        with psycopg.connect(dsn, connect_timeout=5) as connection:
            # Deux workers sur le meme contenu attendent ici avant toute lecture
            # ou tout calcul. Les collisions de verrou ne font que serialiser.
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_id,))
            existing = connection.execute(
                "SELECT fichier_id FROM fichiers_traites WHERE empreinte = %s", (fingerprint,)
            ).fetchone()
            if existing:
                logger.info("IGNORE deja traite : %s", name)
                return False

            transactions = load_csv_bytes(content, name)
            sent_iban = sum_sent_by_iban(transactions)
            sent_bank = sum_sent_by_bank(transactions)
            received = sum_received_by_account(transactions)
            file_id = connection.execute(
                "INSERT INTO fichiers_traites (nom_fichier, empreinte, nb_transactions) "
                "VALUES (%s, %s, %s) RETURNING fichier_id",
                (name, fingerprint, len(transactions)),
            ).fetchone()[0]
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO transactions (transaction_id, date_transaction, iban_origine, "
                    "pays_source, banque_source, iban_destinataire, compte_destinataire, "
                    "pays_destinataire, montant, devise, est_suspecte, fichier_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    [(*astuple(tx), flag_transaction(tx), file_id) for tx in transactions],
                )
                # Ordre stable des cles : limite les interblocages entre workers.
                cursor.executemany(
                    "INSERT INTO totaux_envoyes_par_iban VALUES (%s, %s, %s) "
                    "ON CONFLICT (iban_origine, devise) DO UPDATE SET montant_total = "
                    "totaux_envoyes_par_iban.montant_total + EXCLUDED.montant_total",
                    [(*key, total) for key, total in sorted(sent_iban.items())],
                )
                cursor.executemany(
                    "INSERT INTO totaux_envoyes_par_banque VALUES (%s, %s, %s) "
                    "ON CONFLICT (banque_source, devise) DO UPDATE SET montant_total = "
                    "totaux_envoyes_par_banque.montant_total + EXCLUDED.montant_total",
                    [(*key, total) for key, total in sorted(sent_bank.items())],
                )
                cursor.executemany(
                    "INSERT INTO totaux_recus_par_compte VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (pays_destinataire, compte_destinataire, devise) "
                    "DO UPDATE SET montant_total = totaux_recus_par_compte.montant_total "
                    "+ EXCLUDED.montant_total",
                    [(*key, total) for key, total in sorted(received.items())],
                )
            if after_insert is not None:
                after_insert()
        logger.info("INSERE %s : %d transactions", name, len(transactions))
        return True

    return retry(insert_once, is_transient_database_error,
                 attempts=attempts, base_delay=base_delay)
