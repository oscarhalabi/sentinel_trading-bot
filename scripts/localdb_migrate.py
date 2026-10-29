"""Aplica las migraciones del Postgres local (localdb/migrations/NNN_*.sql).

Uso:  python -m scripts.localdb_migrate
Lee LOCAL_PG_DSN del entorno / .env de la instancia.
"""
import os
import re

_PATRON = re.compile(r"^(\d+)_.+\.sql$")
MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "localdb", "migrations")


def pending_versions(applied, files):
    """Archivos NNN_*.sql que faltan por aplicar, en orden numerico."""
    nuevos = [f for f in files if _PATRON.match(f) and f not in applied]
    return sorted(nuevos, key=lambda f: (int(_PATRON.match(f).group(1)), f))


def apply_pending(conn, migrations_dir):
    """Aplica en orden las migraciones que faltan, cada una en su transaccion.

    Devuelve las versiones aplicadas (nombre de archivo).
    """
    with conn, conn.cursor() as cur:  # la tabla de control puede no existir aun
        cur.execute("create table if not exists schema_migrations ("
                    "version text primary key, applied_at timestamptz not null default now())")
        cur.execute("select version from schema_migrations")
        applied = {r[0] for r in cur.fetchall()}

    aplicadas = []
    for nombre in pending_versions(applied, os.listdir(migrations_dir)):
        with open(os.path.join(migrations_dir, nombre), encoding="utf-8") as fh:
            sql = fh.read()
        with conn, conn.cursor() as cur:  # `with conn` = commit o rollback por migracion
            cur.execute(sql)
            cur.execute("insert into schema_migrations (version) values (%s)", (nombre,))
        aplicadas.append(nombre)
    return aplicadas


def main():
    import psycopg2
    from motor import config
    if not config.LOCAL_PG_DSN:
        raise SystemExit("Falta LOCAL_PG_DSN: no hay base local que migrar.")
    conn = psycopg2.connect(config.LOCAL_PG_DSN)
    try:
        hechas = apply_pending(conn, MIGRATIONS_DIR)
    finally:
        conn.close()
    print("Migraciones aplicadas:", ", ".join(hechas) if hechas else "ninguna (al dia)")


if __name__ == "__main__":
    main()
