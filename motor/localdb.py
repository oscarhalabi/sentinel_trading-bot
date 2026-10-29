"""Postgres local del bot: punto de enchufe para registrar eventos del motor.

Todo es best-effort: si no hay DSN o la base no responde, el bot sigue
funcionando y nada de aqui lanza una excepcion.
"""
import json

from . import config


class LocalDB:
    def __init__(self, dsn):
        self.dsn = dsn or ""
        self.conn = None

    def connect(self):
        """Conecta. Devuelve False (sin lanzar) si no hay DSN o falla."""
        if not self.dsn:
            return False
        try:
            import psycopg2
            self.conn = psycopg2.connect(self.dsn, connect_timeout=5)
            return True
        except Exception:  # noqa: BLE001
            self.conn = None
            return False

    def record_event(self, kind, payload):
        """Guarda un evento. Nunca lanza: ante cualquier error hace rollback."""
        if self.conn is None:
            return
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    "insert into engine_events (user_id, bot_id, kind, payload) "
                    "values (%s, %s, %s, %s::jsonb)",
                    (config.USER_ID or None, config.BOT_ID, kind,
                     json.dumps(payload, default=str)))
            self.conn.commit()
        except Exception:  # noqa: BLE001
            try:
                self.conn.rollback()
            except Exception:  # noqa: BLE001
                pass

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:  # noqa: BLE001
                pass
            self.conn = None
