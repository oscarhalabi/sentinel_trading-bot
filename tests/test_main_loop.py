"""Pruebas del bucle principal y de la entrada unica motor.run (sin MT5 real)."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from motor import config, main as botmain
from motor import run as motor_run
from motor.engines.nulo import NullEngine


class StopDespues:
    """stop_event de pruebas: se activa tras N consultas (una por iteracion)."""

    def __init__(self, n):
        self.n = n

    def is_set(self):
        self.n -= 1
        return self.n < 0


def _entorno(monkeypatch, instancia=None):
    """Prepara config rapida, sleep sin espera y mt5 parcheado."""
    monkeypatch.setattr(config, "HEARTBEAT_INTERVAL", 0)
    monkeypatch.setattr(config, "CONTROL_REFRESH", 0)
    monkeypatch.setattr(botmain.time, "sleep", lambda s: None)
    monkeypatch.setattr(botmain.mt5, "account_info", lambda: None)
    monkeypatch.setattr(botmain, "_publish_candles", lambda db, n: None)


def _doble_db(cfg, instancias=None):
    db = MagicMock()
    db.load_config.return_value = (cfg, "INACTIVE")
    if instancias is None:
        db.get_instance.return_value = {}
    else:
        db.get_instance.side_effect = instancias
    db.has_pending_commission.return_value = False
    return db


def _doble_engine(close_all=None):
    engine = MagicMock()
    engine.close_only = True
    engine.b.positions.return_value = []
    engine.b.capital_flows.return_value = []
    engine.b.trade_allowed.return_value = (True, "")
    if close_all is not None:
        engine.close_all.side_effect = close_all
    return engine


def test_sin_config_reporta_heartbeat(monkeypatch):
    _entorno(monkeypatch)
    db, engine = _doble_db({}), _doble_engine()
    botmain.trading_loop(db, engine, MagicMock(), StopDespues(3))
    assert db.report.called
    assert not engine.on_tick.called


def test_con_config_corre_on_tick(monkeypatch):
    _entorno(monkeypatch)
    db, engine = _doble_db({"params": {}}), _doble_engine()
    botmain.trading_loop(db, engine, MagicMock(), StopDespues(2))
    assert engine.on_tick.call_count == 2


def test_loop_sobrevive_mt5_caido(monkeypatch):
    _entorno(monkeypatch)

    def cae():
        raise RuntimeError("MT5 caido")

    monkeypatch.setattr(botmain.mt5, "account_info", cae)
    db, engine = _doble_db({}), _doble_engine()
    botmain.trading_loop(db, engine, MagicMock(), StopDespues(3))
    assert db.report.call_count == 3


def test_force_close_consumido_solo_si_plano(monkeypatch):
    _entorno(monkeypatch)
    db = _doble_db({}, instancias=[{"force_close": True}, {"force_close": True}])
    engine = _doble_engine(close_all=[False, True])
    botmain.trading_loop(db, engine, MagicMock(), StopDespues(2))
    assert engine.close_all.call_count == 2
    assert db.clear_force_close.call_count == 1


def test_run_resuelve_motor(monkeypatch):
    capturado = {}
    monkeypatch.setattr(config, "BOT_ID", "n2")
    monkeypatch.setattr(botmain, "run", lambda engine_cls=None: capturado.update(cls=engine_cls))
    motor_run.main([])
    assert capturado["cls"] is NullEngine


def test_run_bot_desconocido(monkeypatch):
    monkeypatch.setattr(config, "BOT_ID", "zzz")
    with pytest.raises(SystemExit):
        motor_run.main([])


def test_run_root_valido_e_invalido(monkeypatch):
    import os
    monkeypatch.setattr(config, "BOT_ID", "n1")
    monkeypatch.setattr(botmain, "run", lambda engine_cls=None: None)
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(motor_run.__file__)))
    motor_run.main(["--root", raiz.upper()])  # no distingue mayusculas
    with pytest.raises(SystemExit):
        motor_run.main(["--root", os.path.join(raiz, "otro")])


def test_nulo_close_all_devuelve_true_cuando_plano():
    broker = MagicMock()
    pos = SimpleNamespace(ticket=1)
    broker.positions.side_effect = [[pos], []]
    eng = NullEngine(broker, MagicMock())
    assert eng.close_all("x") is True
    broker.close_position.assert_called_once()


def test_nulo_close_all_false_si_quedan():
    broker = MagicMock()
    broker.positions.return_value = [SimpleNamespace(ticket=1)]
    assert NullEngine(broker, MagicMock()).close_all("x") is False


def test_render_params_generico():
    from motor import tui
    panel = tui.render_params({"id": 1, "params": {"a": 1, "b": "x"}})
    assert panel is not None
    vacio = tui.render_params({})
    assert vacio is not None
