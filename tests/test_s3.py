"""Niveau 2 : SeaweedFS et PostgreSQL reels, avec pannes de recuperation injectees."""

import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from botocore.exceptions import ClientError, EndpointConnectionError, IncompleteReadError

from pipeline_data.runner import run_pipeline
from pipeline_data.settings import database_url
from pipeline_data.storage import (
    download_csv, ensure_bucket, list_csv_keys, s3_client, upload_csv,
)
from scripts.generate_csv import generate_csv_bytes
from tests.support import isolated_database


@unittest.skipUnless(os.getenv("RUN_S3_TESTS") == "1", "SeaweedFS requis")
class S3IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = s3_client()
        self.bucket = "test-" + uuid4().hex
        ensure_bucket(self.client, self.bucket)
        self.addCleanup(self.cleanup_bucket)
        self.name, self.content = next(iter(generate_csv_bytes().items()))
        self.key = "incoming/" + self.name
        upload_csv(self.client, self.bucket, self.key, self.content)

    def cleanup_bucket(self):
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket):
            for obj in page.get("Contents", []):
                self.client.delete_object(Bucket=self.bucket, Key=obj["Key"])
        self.client.delete_bucket(Bucket=self.bucket)

    def test_full_pipeline_uses_s3_without_local_csv_and_skips_renamed_copy(self):
        with isolated_database(database_url()) as dsn:
            with patch.dict(os.environ, {"DATABASE_URL": dsn, "S3_BUCKET": self.bucket}):
                # Ni ecriture des CSV sur disque, ni repli sur une lecture locale.
                with patch("pipeline_data.runner.generate_csv", side_effect=AssertionError("pas de CSV local")), \
                     patch.object(Path, "read_bytes", side_effect=AssertionError("pas de lecture locale")):
                    self.assertEqual(run_pipeline(generate=True),
                                     {"fichiers_inseres": 3, "fichiers_ignores": 0})
                expected_names = ["incoming/" + name for name in generate_csv_bytes()]
                self.assertEqual(list_csv_keys(self.client, self.bucket), sorted(expected_names))
                self.assertEqual(download_csv(self.client, self.bucket, self.key), self.content)
                upload_csv(self.client, self.bucket, "incoming/renamed.csv", self.content)
                with patch("pipeline_data.database.load_csv_bytes", side_effect=AssertionError("pas de recalcul")):
                    self.assertEqual(run_pipeline(), {"fichiers_inseres": 0, "fichiers_ignores": 4})
            with psycopg.connect(dsn) as connection:
                nb, total, flagged = connection.execute(
                    "SELECT COUNT(*), SUM(montant), COUNT(*) FILTER (WHERE est_suspecte) FROM transactions"
                ).fetchone()
                self.assertEqual((nb, str(total), flagged), (21, "64697.76", 6))
                self.assertEqual(connection.execute(
                    "SELECT nb_transactions FROM fichiers_traites ORDER BY nom_fichier"
                ).fetchall(), [(7,), (7,), (7,)])

    def test_connection_error_is_retried_then_downloads_real_object(self):
        original = self.client.get_object
        calls = []
        def fail_once(**kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise EndpointConnectionError(endpoint_url="http://panne-simulee")
            return original(**kwargs)
        with patch.object(self.client, "get_object", side_effect=fail_once):
            self.assertEqual(download_csv(self.client, self.bucket, self.key, base_delay=0), self.content)
        self.assertEqual(len(calls), 2)

    def test_incomplete_response_is_closed_and_entire_object_is_retried(self):
        original = self.client.get_object
        class BrokenBody:
            closed = False
            def read(self):
                raise IncompleteReadError(actual_bytes=12, expected_bytes=100)
            def close(self):
                self.closed = True
        body = BrokenBody()
        calls = []
        def fail_once(**kwargs):
            calls.append(1)
            return {"Body": body} if len(calls) == 1 else original(**kwargs)
        with patch.object(self.client, "get_object", side_effect=fail_once):
            self.assertEqual(download_csv(self.client, self.bucket, self.key, base_delay=0), self.content)
        self.assertTrue(body.closed)
        self.assertEqual(len(calls), 2)

    def test_persistent_failure_stops_after_three_attempts(self):
        with patch.object(self.client, "get_object", side_effect=EndpointConnectionError(
            endpoint_url="http://panne-simulee"
        )) as get:
            with self.assertRaises(EndpointConnectionError):
                download_csv(self.client, self.bucket, self.key, base_delay=0)
            self.assertEqual(get.call_count, 3)

    def test_missing_object_is_not_retried(self):
        with patch.object(self.client, "get_object", wraps=self.client.get_object) as get:
            with self.assertRaises(ClientError):
                download_csv(self.client, self.bucket, "missing.csv", base_delay=0)
            self.assertEqual(get.call_count, 1)

    def test_access_denied_is_not_retried(self):
        error = ClientError({"Error": {"Code": "AccessDenied"},
                             "ResponseMetadata": {"HTTPStatusCode": 403}}, "GetObject")
        with patch.object(self.client, "get_object", side_effect=error) as get:
            with self.assertRaises(ClientError):
                download_csv(self.client, self.bucket, self.key, base_delay=0)
            self.assertEqual(get.call_count, 1)

    def test_listing_handles_pagination_and_filters_non_csv(self):
        upload_csv(self.client, self.bucket, "incoming/second.csv", self.content)
        upload_csv(self.client, self.bucket, "incoming/readme.txt", b"ignore")
        upload_csv(self.client, self.bucket, "other/third.csv", self.content)
        original = self.client.get_paginator("list_objects_v2")
        class SmallPages:
            def paginate(self, **kwargs):
                return original.paginate(**kwargs, PaginationConfig={"PageSize": 1})
        with patch.object(self.client, "get_paginator", return_value=SmallPages()):
            self.assertEqual(list_csv_keys(self.client, self.bucket), sorted([self.key, "incoming/second.csv"]))

    def test_failed_download_leaves_no_database_trace(self):
        with isolated_database(database_url()) as dsn:
            with patch.dict(os.environ, {"DATABASE_URL": dsn, "S3_BUCKET": self.bucket}):
                with patch("pipeline_data.runner.download_csv", side_effect=EndpointConnectionError(
                    endpoint_url="http://panne-simulee"
                )):
                    with self.assertRaises(EndpointConnectionError):
                        run_pipeline()
            with psycopg.connect(dsn) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM fichiers_traites").fetchone(), (0,))
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM transactions").fetchone(), (0,))
