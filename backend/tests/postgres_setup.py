"""Setup shared by the tests that run on a real PostgreSQL.

Two costs used to be paid in full by every such test: building SQLModel's
whole schema in a scratch schema, and walking the Alembic chain from base in a
scratch database. Each is now paid once per session and then emptied or
copied, so a test still starts from exactly the state it used to build for
itself. ``backend/tests/conftest.py`` drops what this module made when the
session ends.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import psycopg
from sqlalchemy import Connection, Engine, Table, text
from sqlalchemy.engine import URL, make_url
from sqlmodel import SQLModel

POSTGRES_TEST_URL_ENV = "NOJOIN_TEST_POSTGRES_URL"
REPO_ROOT = Path(__file__).resolve().parents[2]
# A connection an earlier test left open can hold a lock on the tables a reset
# empties. Fail the setup that waits on it rather than hang the suite.
LOCK_TIMEOUT = "30s"

# Every property of a schema's objects that can change how a test behaves,
# hashed. While the hash matches the one taken just after create_all, the
# schema holds what create_all made and nothing else, so emptying its tables
# leaves it as a new one would be. A test that adds a trigger or a function,
# or alters a table or a type, changes the hash, and the next test rebuilds.
_SCHEMA_FINGERPRINT = text(
    """
    WITH ns AS (SELECT to_regnamespace(:schema) AS oid)
    SELECT md5(string_agg(item, E'\\n' ORDER BY item)) FROM (
      SELECT concat_ws(' ', 'rel', c.relname, c.relkind, c.relpersistence,
                       c.relrowsecurity, c.relowner, c.relacl::text)
        FROM pg_class c JOIN ns ON c.relnamespace = ns.oid
      UNION ALL
      SELECT concat_ws(' ', 'col', c.relname, a.attnum, a.attname,
                       format_type(a.atttypid, a.atttypmod), a.attnotnull,
                       a.attidentity, a.attgenerated, a.attcollation,
                       pg_get_expr(d.adbin, d.adrelid))
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN ns ON c.relnamespace = ns.oid
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
       WHERE a.attnum > 0 AND NOT a.attisdropped
      UNION ALL
      SELECT concat_ws(' ', 'con', c.relname, k.conname, pg_get_constraintdef(k.oid))
        FROM pg_constraint k
        JOIN ns ON k.connamespace = ns.oid
        LEFT JOIN pg_class c ON c.oid = k.conrelid
      UNION ALL
      SELECT concat_ws(' ', 'idx', pg_get_indexdef(i.indexrelid))
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        JOIN ns ON c.relnamespace = ns.oid
      UNION ALL
      -- A foreign key's own triggers are named by oid, so name them by
      -- their constraint instead and the hash survives a rebuild.
      SELECT concat_ws(' ', 'trg', c.relname, t.tgenabled,
                       CASE WHEN t.tgisinternal
                            THEN concat_ws(' ', k.conname, t.tgfoid::regproc, t.tgtype)
                            ELSE pg_get_triggerdef(t.oid) END)
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN ns ON c.relnamespace = ns.oid
        LEFT JOIN pg_constraint k ON k.oid = t.tgconstraint
      UNION ALL
      SELECT concat_ws(' ', 'fn', p.proname, pg_get_function_identity_arguments(p.oid))
        FROM pg_proc p JOIN ns ON p.pronamespace = ns.oid
      UNION ALL
      SELECT concat_ws(' ', 'type', t.typname, t.typtype,
                       (SELECT string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder)
                          FROM pg_enum e WHERE e.enumtypid = t.oid))
        FROM pg_type t JOIN ns ON t.typnamespace = ns.oid
      UNION ALL
      SELECT concat_ws(' ', 'seq', c.relname, s.seqtypid, s.seqstart, s.seqincrement,
                       s.seqmax, s.seqmin, s.seqcache, s.seqcycle)
        FROM pg_sequence s
        JOIN pg_class c ON c.oid = s.seqrelid
        JOIN ns ON c.relnamespace = ns.oid
      UNION ALL
      SELECT concat_ws(' ', 'rule', c.relname, r.rulename)
        FROM pg_rewrite r
        JOIN pg_class c ON c.oid = r.ev_class
        JOIN ns ON c.relnamespace = ns.oid
      UNION ALL
      SELECT concat_ws(' ', 'policy', c.relname, p.polname)
        FROM pg_policy p
        JOIN pg_class c ON c.oid = p.polrelid
        JOIN ns ON c.relnamespace = ns.oid
    ) AS items(item)
    """
)


@dataclass(frozen=True)
class _BuiltSchema:
    tables: tuple[str, ...]
    fingerprint: str | None


# (database URL, schema) -> what create_all built there this session.
_schemas: dict[tuple[str, str], _BuiltSchema] = {}
# Alembic revision -> the template database migrated to it this session.
_templates: dict[str, str] = {}


def _psycopg_url(url: str | URL, database: str | None = None) -> str:
    parsed = make_url(url).set(drivername="postgresql")
    if database is not None:
        parsed = parsed.set(database=database)
    return parsed.render_as_string(hide_password=False)


def fresh_schema(
    bind: Engine | Connection, schema: str, *, tables: Sequence[Table] | None = None
) -> None:
    """Leave ``schema`` holding SQLModel's tables, every one empty.

    The result is what dropping the schema, creating it again and running
    ``SQLModel.metadata.create_all(bind, tables=tables)`` gives, sequences
    included. The first call for a schema in a session does exactly that.
    Later calls empty its tables with ``TRUNCATE ... RESTART IDENTITY``,
    unless the schema's objects or the tables asked for have changed since,
    when it is built again. create_all names no schema, so ``schema`` must
    come first on the connection's search path.
    """
    if isinstance(bind, Engine):
        with bind.begin() as connection:
            _fresh_schema(connection, schema, tables)
    else:
        _fresh_schema(bind, schema, tables)


def _fresh_schema(
    connection: Connection, schema: str, tables: Sequence[Table] | None
) -> None:
    chosen = SQLModel.metadata.tables.values() if tables is None else tables
    wanted = tuple(sorted(table.name for table in chosen))
    key = (_psycopg_url(connection.engine.url), schema)
    connection.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))

    built = _schemas.get(key)
    if (
        built is not None
        and built.tables == wanted
        and _fingerprint(connection, schema) == built.fingerprint
    ):
        names = connection.execute(
            text(
                "SELECT format('%I.%I', schemaname, tablename) FROM pg_tables"
                " WHERE schemaname = :schema"
            ),
            {"schema": schema},
        ).scalars()
        if listed := ", ".join(names):
            connection.execute(text(f"TRUNCATE {listed} RESTART IDENTITY"))
        return

    quoted = connection.dialect.identifier_preparer.quote(schema)
    connection.execute(text(f"DROP SCHEMA IF EXISTS {quoted} CASCADE"))
    connection.execute(text(f"CREATE SCHEMA {quoted}"))
    current = connection.execute(text("SELECT current_schema()")).scalar_one()
    if current != schema:
        raise ValueError(f"{schema} must come first on the search path, not {current}")
    SQLModel.metadata.create_all(connection, tables=tables)
    _schemas[key] = _BuiltSchema(wanted, _fingerprint(connection, schema))


def _fingerprint(connection: Connection, schema: str) -> str | None:
    return connection.execute(_SCHEMA_FINGERPRINT, {"schema": schema}).scalar_one()


def _admin_url() -> str:
    """The test server's own database, from which scratch databases are made."""
    return _psycopg_url(os.environ[POSTGRES_TEST_URL_ENV])


def migrate_empty_database(database_url: str, revision: str) -> None:
    """Bring an empty database to ``revision``, as ``alembic upgrade`` would.

    The first call for a revision in a session runs the real Alembic CLI on a
    template database. Each call then replaces the empty database with a copy
    of that template, under the same name. A database that holds a table is
    refused, so this never stands in for a migration over data.
    """
    target = make_url(database_url).database
    with psycopg.connect(_psycopg_url(database_url)) as conn:
        row = conn.execute(
            "SELECT count(*) FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE c.relkind IN ('r', 'p')"
            " AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
        ).fetchone()
    if row is None or row[0]:
        raise ValueError(f"{target} is not an empty database")

    template = _template(revision)
    with psycopg.connect(_admin_url(), autocommit=True) as admin:
        admin.execute(f'DROP DATABASE "{target}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{target}" TEMPLATE "{template}"')


def _template(revision: str) -> str:
    if revision in _templates:
        return _templates[revision]
    name = f"nojoin_template_{uuid4().hex[:12]}"
    with psycopg.connect(_admin_url(), autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    _templates[revision] = name
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": _psycopg_url(_admin_url(), name)},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _drop_database(_templates.pop(revision))
        raise AssertionError(result.stdout + result.stderr)
    return name


def _drop_database(name: str) -> None:
    with psycopg.connect(_admin_url(), autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def drop_session_objects() -> None:
    """Drop the schemas and template databases this session made."""
    while _schemas:
        url, schema = next(iter(_schemas))
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        del _schemas[(url, schema)]
    while _templates:
        _drop_database(_templates.popitem()[1])
