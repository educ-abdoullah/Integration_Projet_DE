"""Schemas temporaires isoles : aucun test ne vide les tables du projet."""

from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


@contextmanager
def isolated_database(dsn: str):
    schema = "test_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    scoped_dsn = make_conninfo(dsn, options=f"-c search_path={schema}")
    try:
        with psycopg.connect(scoped_dsn, autocommit=True) as connection:
            schema_sql = (Path(__file__).resolve().parents[1] / "sql/001_schema.sql").read_text()
            connection.execute(schema_sql)
        yield scoped_dsn
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            # Uniquement le schema aleatoire cree ci-dessus, jamais public.
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
