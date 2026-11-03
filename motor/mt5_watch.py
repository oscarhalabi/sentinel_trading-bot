"""Vigilante de la conexion a MT5.

Si el terminal muere (p. ej. un reinicio general lo mata) el proceso Python
sigue vivo pero todas las llamadas a MT5 fallan. Este vigilante lo detecta
(una vez por heartbeat), intenta reconectar con espera creciente y, si pasan
demasiados minutos (aprox.: se evalua en cada latido), se rinde para que el bucle salga y el vigilante de
procesos relance el motor.

Cada intento de reconexion llama a mt5.initialize(), que bloquea el bucle
mientras espera. Por eso el intento usa un timeout corto (RECONEXION_TIMEOUT_MS,
no los 120 s del primer arranque) y, justo antes, avisa con on_attempt (el
bucle lo usa para mandar un latido MT5_DOWN y que el dashboard no vea la
instancia como apagada mientras espera).
"""
import time

# Tope del timeout de mt5.initialize() al reconectar (ms). El primer arranque de
# un clon puede necesitar mas (MT5_TIMEOUT_MS); una reconexion no.
RECONEXION_TIMEOUT_MS = 30000


class Mt5Watch:
    def __init__(self, init_kwargs, logger, *, fail_threshold=3, giveup_s=300.0,
                 backoff_s=(5, 15, 30, 60), clock=time.monotonic, mt5_module=None, on_reconnect=None,
                 on_attempt=None, reconnect_timeout_ms=RECONEXION_TIMEOUT_MS):
        if mt5_module is None:
            import MetaTrader5 as mt5_module
        self._mt5 = mt5_module
        # Mismos argumentos que el arranque, con el timeout recortado.
        self._kw = {**init_kwargs,
                    "timeout": min(int(init_kwargs.get("timeout", reconnect_timeout_ms)),
                                   reconnect_timeout_ms)}
        self._log = logger
        self._threshold = fail_threshold
        self._giveup_s = giveup_s
        self._backoff = tuple(backoff_s)
        self._clock = clock
        self._on_reconnect = on_reconnect  # se llama tras reconectar (p. ej. symbol_select)
        self._on_attempt = on_attempt      # se llama justo antes de cada intento (latido MT5_DOWN)
        self._reset()

    def _reset(self):
        self._fallos = 0
        self._primer_fallo = None
        self._proximo_intento = None
        self._intentos = 0
        self._rendido = False

    def _vivo(self):
        try:
            return (self._mt5.terminal_info() is not None
                    and self._mt5.account_info() is not None)
        except Exception:  # noqa: BLE001  (una excepcion cuenta como fallo)
            return False

    def _reconectar(self):
        try:
            self._mt5.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            return bool(self._mt5.initialize(**self._kw))
        except Exception:  # noqa: BLE001
            return False

    def check(self):
        """Devuelve "OK", "RECONNECTING" o "GIVE_UP". Nunca lanza.

        Cualquier fallo devuelve "RECONNECTING" (el llamador no debe operar),
        aunque solo se intenta reconectar al llegar al umbral de fallos.
        """
        try:
            return self._check()
        except Exception:  # noqa: BLE001
            return "RECONNECTING"

    def _check(self):
        if self._vivo():
            if self._fallos >= self._threshold:
                self._log.write("SYSTEM", "MT5 reconectado.")
            self._reset()
            return "OK"

        ahora = self._clock()
        if self._primer_fallo is None:
            self._primer_fallo = ahora
        self._fallos += 1

        if self._rendido:
            return "GIVE_UP"
        if ahora - self._primer_fallo > self._giveup_s:
            self._rendido = True
            self._log.write("ERROR", f"MT5 sin respuesta hace mas de {self._giveup_s:g} s: "
                                     "me rindo y salgo para que el vigilante relance el motor.")
            return "GIVE_UP"

        if self._fallos < self._threshold:
            return "RECONNECTING"
        if self._proximo_intento is not None and ahora < self._proximo_intento:
            return "RECONNECTING"

        espera = self._backoff[min(self._intentos, len(self._backoff) - 1)]
        self._intentos += 1
        self._proximo_intento = ahora + espera
        self._log.write("SYSTEM", f"MT5 sin respuesta: reconectando (intento {self._intentos}).")
        if self._on_attempt is not None:
            try:
                self._on_attempt()
            except Exception:  # noqa: BLE001
                pass
        if self._reconectar():
            if self._on_reconnect is not None:
                try:
                    self._on_reconnect()
                except Exception:  # noqa: BLE001
                    pass
            self._log.write("SYSTEM", "MT5 reconectado.")
            self._reset()
            return "OK"
        return "RECONNECTING"
