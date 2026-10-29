"""Motor nulo: esqueleto de la Fase 1. No abre ni gestiona operaciones.

Sirve para probar de punta a punta la conexion, el heartbeat y los comandos
del dashboard (cierre forzado, activar/desactivar) sin ninguna estrategia.
Es la interfaz minima que main.setup / main.trading_loop esperan de un motor.
"""

import time

from motor import config

LATIDO_SEGUNDOS = 60


class NullEngine:
    def __init__(self, broker, logger):
        self.b = broker
        self.logger = logger
        self.cfg = {}
        self.is_active = False
        self.close_only = True
        self.user_email = None
        self.initial_balance = None
        self.last_flow_ticket = None
        self.baseline_applied_at = None
        self.symbols = (config.SYMBOL,)          # main.setup lo reasigna segun el registro
        self.hud = {"status": "Motor nulo: sin estrategia"}
        self._ultimo_latido = 0.0

    def init_history_cursor(self):
        """Sin historial propio que recorrer."""

    def on_tick(self):
        """No envia ordenes; solo avisa cada 60 s que sigue vivo."""
        ahora = time.time()
        if ahora - self._ultimo_latido >= LATIDO_SEGUNDOS:
            self._ultimo_latido = ahora
            self.logger.write("SYSTEM", "Motor nulo vivo")

    def close_all(self, reason):
        """Cierra las posiciones de su magic. True cuando no queda ninguna."""
        for p in self.b.positions():
            self.b.close_position(p, reason)
        return not self.b.positions()
