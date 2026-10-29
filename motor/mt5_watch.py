"""Vigilante de la conexion a MT5.

Si el terminal muere (p. ej. un reinicio general lo mata) el proceso Python
sigue vivo pero todas las llamadas a MT5 fallan. Este vigilante lo detecta
(una vez por heartbeat), intenta reconectar con espera creciente y, si pasan
demasiados minutos, se rinde para que el bucle salga y el vigilante de
procesos relance el motor.
"""
import time


class Mt5Watch:
    def __init__(self, init_kwargs, logger, *, fail_threshold=3, giveup_s=300.0,
                 backoff_s=(5, 15, 30, 60), clock=time.monotonic, mt5_module=None):
        if mt5_module is None:
            import MetaTrader5 as mt5_module
        self._mt5 = mt5_module
        self._kw = init_kwargs
        self._log = logger
        self._threshold = fail_threshold
        self._giveup_s = giveup_s
        self._backoff = tuple(backoff_s)
        self._clock = clock
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
        if self._reconectar():
            self._log.write("SYSTEM", "MT5 reconectado.")
            self._reset()
            return "OK"
        return "RECONNECTING"
