"""Pruebas del vigilante de conexion a MT5 (sin MT5 real: modulo y reloj falsos)."""
from unittest.mock import MagicMock

from motor.mt5_watch import Mt5Watch


class FakeMt5:
    def __init__(self):
        self.vivo = True
        self.init_ok = True
        self.explota = False
        self.shutdown_calls = 0
        self.init_calls = []

    def terminal_info(self):
        if self.explota:
            raise RuntimeError("terminal muerto")
        return object() if self.vivo else None

    def account_info(self):
        return object() if self.vivo else None

    def shutdown(self):
        self.shutdown_calls += 1

    def initialize(self, **kw):
        self.init_calls.append(kw)
        if self.init_ok:
            self.vivo = True
        return self.init_ok


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _armar(**kw):
    mt5, reloj, logger = FakeMt5(), Reloj(), MagicMock()
    w = Mt5Watch({"path": "x"}, logger, clock=reloj, mt5_module=mt5, **kw)
    return w, mt5, reloj, logger


def _niveles(logger):
    return [c.args[0] for c in logger.write.call_args_list]


def test_ok_sin_fallos():
    w, mt5, _, logger = _armar()
    assert w.check() == "OK"
    assert mt5.init_calls == []
    assert not logger.write.called


def test_reconecta_tras_umbral():
    w, mt5, _, logger = _armar()
    mt5.vivo = False
    mt5.init_ok = False
    assert w.check() == "RECONNECTING"
    assert w.check() == "RECONNECTING"
    assert mt5.init_calls == []          # 2 fallos: todavia no
    assert w.check() == "RECONNECTING"   # 3er fallo: intenta
    assert mt5.shutdown_calls == 1
    assert mt5.init_calls == [{"path": "x", "timeout": 30000}]
    mt5.init_ok = True
    # Tras el backoff, el intento exitoso devuelve OK y loguea una sola vez.
    w._clock.t += 5
    assert w.check() == "OK"
    assert w.check() == "OK"
    avisos = [c for c in logger.write.call_args_list if "MT5 reconectado" in c.args[1]]
    assert len(avisos) == 1
    assert avisos[0].args[0] == "SYSTEM"


def test_respeta_backoff():
    w, mt5, reloj, _ = _armar()
    mt5.vivo = False
    mt5.init_ok = False
    for _ in range(3):
        w.check()
    assert len(mt5.init_calls) == 1
    reloj.t += 4.9
    w.check()
    assert len(mt5.init_calls) == 1      # antes de 5 s no
    reloj.t += 0.2
    w.check()
    assert len(mt5.init_calls) == 2      # pasados 5 s si
    reloj.t += 14
    w.check()
    assert len(mt5.init_calls) == 2      # el siguiente espera 15 s
    reloj.t += 1.5
    w.check()
    assert len(mt5.init_calls) == 3


def test_give_up_tras_5_min():
    w, mt5, reloj, logger = _armar()
    mt5.vivo = False
    mt5.init_ok = False
    assert w.check() == "RECONNECTING"
    reloj.t += 299
    assert w.check() == "RECONNECTING"
    reloj.t += 2
    assert w.check() == "GIVE_UP"
    assert w.check() == "GIVE_UP"
    assert _niveles(logger).count("ERROR") == 1


def test_excepciones_cuentan_como_fallo():
    w, mt5, _, _ = _armar()
    mt5.explota = True
    mt5.init_ok = False
    for _ in range(3):
        assert w.check() == "RECONNECTING"
    assert len(mt5.init_calls) == 1


def test_fallo_suelto_se_recupera_sin_reconectar():
    w, mt5, _, logger = _armar()
    mt5.vivo = False
    w.check()
    mt5.vivo = True
    assert w.check() == "OK"
    assert mt5.init_calls == []
    assert not logger.write.called


def test_on_reconnect_se_llama_al_reconectar():
    llamadas = []
    mt5, reloj, logger = FakeMt5(), Reloj(), MagicMock()
    w = Mt5Watch({}, logger, clock=reloj, mt5_module=mt5,
                 on_reconnect=lambda: llamadas.append(1))
    mt5.vivo = False
    for _ in range(3):
        w.check()
    assert llamadas == [1]
    assert w.check() == "OK"
    assert llamadas == [1]


def test_reconexion_usa_timeout_corto():
    """mt5.initialize bloquea el bucle: al reconectar nunca se esperan los 120 s del arranque."""
    mt5, reloj, logger = FakeMt5(), Reloj(), MagicMock()
    w = Mt5Watch({"path": "x", "timeout": 120000, "portable": True}, logger,
                 clock=reloj, mt5_module=mt5, fail_threshold=1)
    mt5.vivo = False
    mt5.init_ok = False
    w.check()
    assert mt5.init_calls == [{"path": "x", "timeout": 30000, "portable": True}]


def test_on_attempt_antes_de_cada_intento():
    """El bucle manda un latido MT5_DOWN antes del intento (que bloquea)."""
    orden = []
    mt5, reloj, logger = FakeMt5(), Reloj(), MagicMock()
    mt5.initialize = lambda **kw: orden.append("init") or False
    w = Mt5Watch({"path": "x"}, logger, clock=reloj, mt5_module=mt5, fail_threshold=1,
                 on_attempt=lambda: orden.append("latido"))
    mt5.vivo = False
    w.check()
    assert orden == ["latido", "init"]
