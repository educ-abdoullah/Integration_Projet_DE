"""Pipeline S3 du niveau 2 ; --source local conserve le parcours du niveau 1."""

import argparse
import logging
import os
from pathlib import Path

import psycopg
from botocore.exceptions import BotoCoreError, ClientError

from scripts.generate_csv import DEFAULT_OUTPUT_DIR, generate_csv, generate_csv_bytes

from .database import process_blob
from .settings import database_url
from .storage import download_csv, ensure_bucket, list_csv_keys, s3_client, upload_csv


def run_pipeline(generate: bool = False, input_dir: Path = DEFAULT_OUTPUT_DIR,
                 source: str = "s3") -> dict:
    dsn = database_url()
    if source == "local":
        if generate:
            generate_csv(input_dir)
        if not input_dir.is_dir():
            raise FileNotFoundError(f"Dossier introuvable : {input_dir}")
        paths = sorted(input_dir.glob("*.csv"))
        if not paths:
            raise ValueError(f"{input_dir} : aucun CSV")
        blobs = ((path.name, path.read_bytes()) for path in paths)
    elif source == "s3":
        client = s3_client()
        bucket = os.environ["S3_BUCKET"]
        ensure_bucket(client, bucket)
        if generate:
            # Le niveau 2 ne depose aucun CSV sur le disque local.
            for name, content in generate_csv_bytes().items():
                upload_csv(client, bucket, "incoming/" + name, content)
        keys = list_csv_keys(client, bucket)
        if not keys:
            logging.info("AUCUN CSV dans S3 : %s/incoming/", bucket)
        blobs = ((key, download_csv(client, bucket, key)) for key in keys)
    else:
        raise ValueError(f"Source inconnue : {source}")

    inserted = skipped = 0
    for name, content in blobs:
        if process_blob(dsn, name, content):
            inserted += 1
        else:
            skipped += 1
    result = {"fichiers_inseres": inserted, "fichiers_ignores": skipped}
    logging.info("TERMINE %s", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("s3", "local"), default="s3")
    parser.add_argument("--generate", action="store_true", help="Generer les exemples avant traitement.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        run_pipeline(args.generate, args.input_dir, args.source)
    except (OSError, ValueError, KeyError, psycopg.Error, BotoCoreError, ClientError) as exc:
        # Le type et le nom du fichier sont dans les logs ; jamais de DSN avec mot de passe.
        logging.error("ECHEC pipeline (%s)", type(exc).__name__)
        parser.exit(1)


if __name__ == "__main__":
    main()
