"""Postgres local del bot: punto de enchufe para registrar eventos del motor.

Todo es best-effort: si no hay DSN o la base no responde, el bot sigue
funcionando y nada de aqui lanza una excepcion.
"""
import json
import threading

from . import config


class LocalDB:
    def __init__(self, dsn, logger=None):
        self.dsn = dsn or ""
        self.logger = logger
        self.conn = None
        self.ok = False            # True mientras la base responde
        self._avisado = False      # el aviso de fallo se da una sola vez
        self._lock = threading.Lock()

    def _avisar_fallo(self, detalle):
        """Un solo aviso en el log; nunca lanza."""
        if self._avisado or self.logger is None:
            return
        self._avisado = True
        try:
            self.logger.write("SYSTEM", f"AVISO: falla la base local ({detalle}). "
                                        "El bot sigue sin ella.")
        except Exception:  # noqa: BLE001
            pass

    def connect(self):
        """Conecta. Devuelve False (sin lanzar) si no hay DSN o falla."""
        if not self.dsn:
            return False
        try:
            import psycopg2
            self.conn = psycopg2.connect(
                self.dsn, connect_timeout=5,
                # un Postgres colgado no debe frenar el bucle de trading
                options="-c statement_timeout=2000 -c lock_timeout=1000")
            self.ok = True
            return True
        except Exception:  # noqa: BLE001
            self.conn = None
            self.ok = False
            return False

    def record_event(self, kind, payload):
        """Guarda un evento. Nunca lanza: ante cualquier error hace rollback."""
        with self._lock:
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
                self.ok = True
            except Exception as e:  # noqa: BLE001
                self.ok = False
                self._avisar_fallo(type(e).__name__)
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
