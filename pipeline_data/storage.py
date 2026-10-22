"""Lecture et ecriture S3, en memoire, avec retries des erreurs temporaires."""

import logging
import os

import boto3
from botocore.config import Config
from botocore.exceptions import (
    ClientError, ConnectionClosedError, ConnectTimeoutError, EndpointConnectionError,
    IncompleteReadError, ReadTimeoutError, ResponseStreamingError,
)

from .retry import retry


logger = logging.getLogger(__name__)


def is_transient_s3_error(exc: Exception) -> bool:
    if isinstance(exc, (EndpointConnectionError, ConnectionClosedError, ConnectTimeoutError,
                        ReadTimeoutError, IncompleteReadError, ResponseStreamingError)):
        return True
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return status in {429, 500, 502, 503, 504} or code in {
            "SlowDown", "RequestTimeout", "InternalError", "ServiceUnavailable",
        }
    return False


def s3_client():
    return boto3.client(
        "s3", endpoint_url=os.environ["S3_ENDPOINT_URL"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        region_name="us-east-1",
        config=Config(
            signature_version="s3v4", s3={"addressing_style": "path"},
            connect_timeout=3, read_timeout=10,
            # Le retry applicatif englobe aussi la lecture du corps HTTP : pas de
            # multiplication des tentatives avec une seconde politique SDK.
            retries={"mode": "standard", "total_max_attempts": 1},
        ),
    )


def ensure_bucket(client, bucket: str) -> None:
    def check():
        try:
            client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                raise
            try:
                client.create_bucket(Bucket=bucket)
            except ClientError as creation:
                if creation.response.get("Error", {}).get("Code") != "BucketAlreadyOwnedByYou":
                    raise
    # Attente initiale du service nouvellement demarre, bornee a 7 tentatives.
    retry(check, is_transient_s3_error, attempts=7, base_delay=0.25)


def upload_csv(client, bucket: str, key: str, content: bytes) -> None:
    retry(lambda: client.put_object(Bucket=bucket, Key=key, Body=content, ContentType="text/csv"),
          is_transient_s3_error)
    logger.info("DEPOT S3 %s/%s", bucket, key)


def list_csv_keys(client, bucket: str, prefix: str = "incoming/") -> list[str]:
    def fetch():
        keys = []
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []) if obj["Key"].endswith(".csv"))
        return sorted(keys)
    return retry(fetch, is_transient_s3_error)


def download_csv(client, bucket: str, key: str, *, attempts: int = 3,
                 base_delay: float = 0.5) -> bytes:
    def fetch():
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()
    content = retry(fetch, is_transient_s3_error, attempts=attempts, base_delay=base_delay)
    logger.info("LECTURE S3 %s/%s : %d octets", bucket, key, len(content))
    return content
