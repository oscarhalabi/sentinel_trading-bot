"""Tests del Postgres local. Los que usan base real se saltan sin LOCAL_PG_DSN_TEST."""
import os

import pytest

from motor.localdb import LocalDB
from scripts import localdb_migrate

MIGRACIONES = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "localdb", "migrations")
DSN_TEST = os.environ.get("LOCAL_PG_DSN_TEST", "")

necesita_pg = pytest.mark.skipif(not DSN_TEST, reason="falta LOCAL_PG_DSN_TEST")


def test_localdb_sin_dsn_no_rompe():
    db = LocalDB("")
    assert db.connect() is False
    db.record_event("prueba", {"a": 1})  # no lanza
    db.close()


def test_localdb_dsn_invalido_no_rompe():
    db = LocalDB("postgresql://nadie:x@127.0.0.1:1/no_existe?connect_timeout=1")
    assert db.connect() is False
    db.record_event("prueba", {})


def test_pending_versions_orden_y_filtro():
    files = ["010_b.sql", "002_a.sql", "001_base.sql", "notas.txt", "x_003.sql", "README.md"]
    assert localdb_migrate.pending_versions(set(), files) == [
        "001_base.sql", "002_a.sql", "010_b.sql"]
    assert localdb_migrate.pending_versions({"001_base.sql"}, files) == [
        "002_a.sql", "010_b.sql"]
    assert localdb_migrate.pending_versions({"001_base.sql", "002_a.sql", "010_b.sql"}, files) == []


@pytest.fixture
def conn_limpia():
    import psycopg2
    conn = psycopg2.connect(DSN_TEST)
    with conn, conn.cursor() as cur:
        cur.execute("drop table if exists engine_events, schema_migrations cascade")
    yield conn
    conn.close()


@necesita_pg
def test_migraciones_idempotentes(conn_limpia):
    primera = localdb_migrate.apply_pending(conn_limpia, MIGRACIONES)
    assert "001_base.sql" in primera
    assert localdb_migrate.apply_pending(conn_limpia, MIGRACIONES) == []


@necesita_pg
def test_record_event_inserta(conn_limpia):
    localdb_migrate.apply_pending(conn_limpia, MIGRACIONES)
    db = LocalDB(DSN_TEST)
    assert db.connect() is True
    db.record_event("prueba", {"x": 1})
    db.close()
    with conn_limpia, conn_limpia.cursor() as cur:
        cur.execute("select kind, payload->>'x', bot_id from engine_events")
        fila = cur.fetchone()
    assert fila[0] == "prueba" and fila[1] == "1" and fila[2]
