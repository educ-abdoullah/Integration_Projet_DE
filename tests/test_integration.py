"""Tests contre PostgreSQL reel, actives explicitement par Compose."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
import unittest
from unittest.mock import patch

import psycopg

from pipeline_data.database import is_transient_database_error, process_blob
from pipeline_data.loading import DataValidationError, load_csv_bytes
from pipeline_data.retry import retry
from pipeline_data.settings import database_url
from scripts.generate_csv import generate_csv_bytes
from tests.support import isolated_database


@unittest.skipUnless(os.getenv("RUN_INTEGRATION_TESTS") == "1", "Services Docker requis")
class DatabaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.context = isolated_database(database_url())
        self.dsn = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.files = generate_csv_bytes()
        self.name, self.content = next(iter(self.files.items()))

    def query(self, statement):
        with psycopg.connect(self.dsn) as connection:
            return connection.execute(statement).fetchall()

    def assert_empty(self):
        for table in ("transactions", "fichiers_traites", "totaux_envoyes_par_iban",
                      "totaux_envoyes_par_banque", "totaux_recus_par_compte"):
            self.assertEqual(self.query(f"SELECT COUNT(*) FROM {table}"), [(0,)])

    def test_values_inserted_and_totals_reconciled_with_sql(self):
        for name, content in self.files.items():
            self.assertTrue(process_blob(self.dsn, name, content))
        self.assertEqual(self.query("SELECT COUNT(*), SUM(montant), COUNT(*) FILTER "
                                    "(WHERE est_suspecte) FROM transactions"),
                         [(21, Decimal("64697.76"), 6)])
        self.assertEqual(self.query("SELECT nb_transactions FROM fichiers_traites ORDER BY nom_fichier"),
                         [(7,), (7,), (7,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM transactions WHERE est_suspecte <> (montant > 5000)"), [(0,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM fichiers_traites WHERE date_trt IS NULL OR length(empreinte) <> 64"), [(0,)])
        comparisons = (
            ("totaux_envoyes_par_iban", "iban_origine, devise"),
            ("totaux_envoyes_par_banque", "banque_source, devise"),
            ("totaux_recus_par_compte", "pays_destinataire, compte_destinataire, devise"),
        )
        for table, columns in comparisons:
            self.assertEqual(
                self.query(f"SELECT {columns}, montant_total FROM {table} ORDER BY {columns}"),
                self.query(f"SELECT {columns}, SUM(montant) FROM transactions GROUP BY {columns} ORDER BY {columns}"),
            )

    def test_renamed_file_is_skipped_before_parsing_or_calculation(self):
        process_blob(self.dsn, self.name, self.content)
        with patch("pipeline_data.database.load_csv_bytes", side_effect=AssertionError("ne pas recalculer")):
            self.assertFalse(process_blob(self.dsn, "renamed.csv", self.content))
        self.assertEqual(self.query("SELECT COUNT(*) FROM transactions"), [(7,)])

    def test_failure_after_writes_rolls_back_all_five_tables(self):
        def fail():
            raise psycopg.OperationalError("panne simulee apres ecritures")
        with self.assertRaises(psycopg.OperationalError):
            process_blob(self.dsn, self.name, self.content, attempts=1, after_insert=fail)
        self.assert_empty()

    def test_temporary_failure_is_retried_without_double_totals(self):
        calls = []
        def fail_once():
            calls.append(1)
            if len(calls) == 1:
                raise psycopg.OperationalError("panne temporaire simulee")
        self.assertTrue(process_blob(self.dsn, self.name, self.content,
                                     after_insert=fail_once, base_delay=0))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.query("SELECT COUNT(*), SUM(montant) FROM transactions"),
                         [(7, Decimal("19092.51"))])
        self.assertEqual(self.query("SELECT montant_total FROM totaux_envoyes_par_banque"),
                         [(Decimal("19092.51"),)])

    def test_invalid_csv_is_not_marked_processed(self):
        with self.assertRaises(DataValidationError):
            process_blob(self.dsn, self.name, self.content.replace(b"100.00", b"ERREUR"))
        self.assert_empty()
        self.assertTrue(process_blob(self.dsn, self.name, self.content))

    def test_processing_bug_fails_constraint_without_retry(self):
        with patch("pipeline_data.database.flag_transaction", return_value=True) as flag:
            with self.assertRaises(psycopg.errors.CheckViolation):
                process_blob(self.dsn, self.name, self.content)
            self.assertEqual(flag.call_count, 7)
        self.assert_empty()

    def test_duplicate_uuid_in_different_content_rolls_back_second_file(self):
        process_blob(self.dsn, self.name, self.content)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            process_blob(self.dsn, "changed.csv", self.content.replace(b"100.00", b"101.00"))
        self.assertEqual(self.query("SELECT COUNT(*) FROM fichiers_traites"), [(1,)])
        self.assertEqual(self.query("SELECT COUNT(*), SUM(montant) FROM transactions"),
                         [(7, Decimal("19092.51"))])

    def test_two_workers_insert_same_file_only_once(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda name: process_blob(self.dsn, name, self.content), ("worker1.csv", "worker2.csv")
            ))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.query("SELECT COUNT(*) FROM transactions"), [(7,)])

    def test_lost_commit_response_does_not_reprocess_committed_file(self):
        calls = []
        def operation():
            result = process_blob(self.dsn, self.name, self.content)
            calls.append(result)
            if len(calls) == 1:
                raise psycopg.OperationalError("reponse perdue apres COMMIT")
            return result
        with patch("pipeline_data.database.load_csv_bytes", wraps=load_csv_bytes) as loader:
            self.assertFalse(retry(operation, is_transient_database_error, base_delay=0))
            self.assertEqual(loader.call_count, 1)
        self.assertEqual(calls, [True, False])
        self.assertEqual(self.query("SELECT COUNT(*) FROM transactions"), [(7,)])


class RetryTests(unittest.TestCase):
    def test_persistent_failure_stops_at_limit(self):
        calls = []
        def fail():
            calls.append(1)
            raise psycopg.OperationalError("panne persistante")
        with self.assertRaises(psycopg.OperationalError):
            retry(fail, is_transient_database_error, attempts=3, base_delay=0)
        self.assertEqual(len(calls), 3)

    def test_authentication_and_validation_errors_are_not_transient(self):
        self.assertFalse(is_transient_database_error(psycopg.errors.InvalidPassword()))
        self.assertFalse(is_transient_database_error(DataValidationError("invalide")))
