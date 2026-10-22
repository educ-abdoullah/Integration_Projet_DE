"""Tests metier et contrat entre le generateur, le lecteur et les calculs."""

import csv
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, localcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pipeline_data.loading import CSV_COLUMNS, DataValidationError, load_csv, load_directory
from pipeline_data.processing import (
    flag_transaction,
    sum_received_by_account,
    sum_sent_by_bank,
    sum_sent_by_iban,
)
from scripts.generate_csv import generate_csv


class PipelineProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.paths = generate_csv(self.directory)
        self.transactions = load_directory(self.directory)

    def test_known_totals_for_the_21_transactions(self):
        self.assertEqual(len(self.transactions), 21)
        self.assertEqual(sum_sent_by_iban(self.transactions), {
            ("FR7630004000010000000000136", "EUR"): Decimal("19450.00"),
            ("FR7630004000010000000000233", "EUR"): Decimal("25080.01"),
            ("FR7630004000010000000000330", "EUR"): Decimal("20167.75"),
        })
        self.assertEqual(sum_sent_by_bank(self.transactions), {
            ("BNP Paribas", "EUR"): Decimal("64697.76"),
        })
        self.assertEqual(sum_received_by_account(self.transactions), {
            ("IN", "000000000001", "EUR"): Decimal("33300.01"),
            ("IN", "000000000002", "EUR"): Decimal("14730.00"),
            ("IN", "000000000003", "EUR"): Decimal("16667.75"),
        })
        self.assertEqual(sum(map(flag_transaction, self.transactions)), 6)

    def test_threshold_is_strict(self):
        for amount, expected in (("4999.99", False), ("5000.00", False), ("5000.01", True)):
            with self.subTest(amount=amount):
                tx = replace(self.transactions[0], montant=Decimal(amount))
                self.assertEqual(flag_transaction(tx), expected)

    def test_decimal_addition_is_exact(self):
        tx = self.transactions[0]
        sample = (replace(tx, montant=Decimal("0.10")), replace(tx, montant=Decimal("0.20")))
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(sum_sent_by_bank(sample), {("BNP Paribas", "EUR"): Decimal("0.30")})

    def test_currencies_countries_and_banks_are_separated(self):
        tx = self.transactions[0]
        sample = (tx, replace(tx, devise="USD"), replace(tx, pays_destinataire="FR"),
                  replace(tx, banque_source="Banque test"))
        self.assertEqual(sum_sent_by_bank(sample), {
            ("BNP Paribas", "EUR"): Decimal("200.00"),
            ("BNP Paribas", "USD"): Decimal("100.00"),
            ("Banque test", "EUR"): Decimal("100.00"),
        })
        self.assertEqual(sum_sent_by_iban(sample)[(tx.iban_origine, "USD")], Decimal("100.00"))
        self.assertEqual(sum_received_by_account(sample), {
            ("IN", "000000000001", "EUR"): Decimal("200.00"),
            ("IN", "000000000001", "USD"): Decimal("100.00"),
            ("FR", "000000000001", "EUR"): Decimal("100.00"),
        })

    def test_functions_preserve_inputs_and_accept_empty_collections(self):
        before = self.transactions
        for function in (sum_sent_by_iban, sum_sent_by_bank, sum_received_by_account):
            self.assertEqual(function(()), {})
            self.assertEqual(function(before), function(tuple(reversed(before))))
            first = function(before)
            first.clear()
            self.assertTrue(function(before))
        self.assertEqual(self.transactions, before)
        with self.assertRaises(FrozenInstanceError):
            self.transactions[0].montant = Decimal("1.00")

    def test_total_overflow_is_rejected_before_insertion(self):
        tx = replace(self.transactions[0], montant=Decimal("999999999999999999.99"))
        with self.assertRaisesRegex(ValueError, "NUMERIC"):
            sum_sent_by_bank((tx, self.transactions[0]))

    def _write_csv(self, rows, columns=CSV_COLUMNS):
        path = self.directory / "invalid.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def _sample_row(self):
        with self.paths[0].open(encoding="utf-8", newline="") as stream:
            return next(csv.DictReader(stream))

    def test_invalid_amounts_fail_with_path_and_line(self):
        for amount in ("NaN", "Infinity", "1e3", "-1.00", "0.00", "12.345", "1000000000000.00", ""):
            with self.subTest(amount=amount):
                row = self._sample_row()
                row["montant"] = amount
                with self.assertRaisesRegex(DataValidationError, r"invalid.csv, ligne 2"):
                    load_csv(self._write_csv([row]))

    def test_missing_columns_extra_fields_and_empty_files_are_rejected(self):
        with self.assertRaises(DataValidationError):
            load_csv(self._write_csv([], columns=("montant",)))
        with self.assertRaisesRegex(DataValidationError, "aucune transaction"):
            load_csv(self._write_csv([]))
        malformed = self._write_csv([self._sample_row()])
        content = malformed.read_text(encoding="utf-8").splitlines()
        content[1] += ",champ_en_trop"
        malformed.write_text("\n".join(content) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(DataValidationError, "nombre de champs"):
            load_csv(malformed)

    def test_bad_date_uuid_and_required_fields_are_rejected(self):
        cases = (("date_transaction", "2026-10-22T10:00:00"),
                 ("transaction_id", "incorrect"), ("banque_source", " "),
                 ("pays_source", "France"), ("devise", "euros"))
        for column, value in cases:
            with self.subTest(column=column):
                row = self._sample_row()
                row[column] = value
                with self.assertRaises(DataValidationError):
                    load_csv(self._write_csv([row]))

    def test_duplicate_ids_are_rejected_within_a_file(self):
        row = self._sample_row()
        with self.assertRaisesRegex(DataValidationError, "duplique"):
            load_csv(self._write_csv([row, row]))

    def test_duplicate_ids_are_rejected_across_files(self):
        self._write_csv([self._sample_row()])
        with self.assertRaisesRegex(DataValidationError, "autre fichier"):
            load_directory(self.directory)

    def test_empty_directory_and_missing_directory_are_rejected(self):
        empty = self.directory / "empty"
        empty.mkdir()
        with self.assertRaises(DataValidationError):
            load_directory(empty)
        with self.assertRaises(FileNotFoundError):
            load_directory(self.directory / "missing")


if __name__ == "__main__":
    unittest.main()
