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
    _watch(monkeypatch, "OK")
    monkeypatch.setattr(botmain, "_mt5_vivo", lambda: True)


def _watch(monkeypatch, *estados):
    """Vigilante de MT5 falso: devuelve los estados dados (el ultimo se repite)."""
    seq = list(estados)

    class FakeWatch:
        def __init__(self, *a, **k):
            pass

        def check(self):
            return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr(botmain, "Mt5Watch", FakeWatch)


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


def test_loop_sale_con_give_up(monkeypatch):
    _entorno(monkeypatch)
    _watch(monkeypatch, "OK", "GIVE_UP")
    db, engine = _doble_db({"params": {}}), _doble_engine()
    with pytest.raises(SystemExit) as e:
        botmain.trading_loop(db, engine, MagicMock(), StopDespues(10))
    assert e.value.code == 3


def test_loop_no_llama_on_tick_reconectando(monkeypatch):
    _entorno(monkeypatch)
    _watch(monkeypatch, "RECONNECTING")
    db, engine = _doble_db({"params": {}}), _doble_engine()
    botmain.trading_loop(db, engine, MagicMock(), StopDespues(3))
    assert not engine.on_tick.called
    assert db.report.called  # el heartbeat sigue saliendo


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


def test_force_close_espera_si_mt5_caido(monkeypatch):
    _entorno(monkeypatch)
    _watch(monkeypatch, "RECONNECTING", "RECONNECTING", "OK")
    db = _doble_db({}, instancias=[{"force_close": True}] * 3)
    engine = _doble_engine(close_all=[True])
    logger = MagicMock()
    botmain.trading_loop(db, engine, logger, StopDespues(1))
    botmain.trading_loop(db, engine, logger, StopDespues(1))
    assert not engine.close_all.called
    assert not db.clear_force_close.called
    botmain.trading_loop(db, engine, logger, StopDespues(1))
    assert engine.close_all.call_count == 1
    assert db.clear_force_close.call_count == 1


def test_mt5_caido_reporta_mt5_down_y_no_toca_posiciones(monkeypatch):
    _entorno(monkeypatch)
    _watch(monkeypatch, "RECONNECTING")
    db, engine = _doble_db({}), _doble_engine()
    botmain.trading_loop(db, engine, MagicMock(), StopDespues(2))
    db.report.assert_called_with("MT5_DOWN")
    assert not db.upsert_positions.called
    assert not db.report_state.called


def test_tickets_conocidos_sobreviven_a_la_caida(monkeypatch):
    _entorno(monkeypatch)
    estados = iter(["OK", "RECONNECTING", "OK"])
    actual = {}

    class W:
        def __init__(self, *a, **k):
            pass

        def check(self):
            actual["e"] = next(estados)
            return actual["e"]

    monkeypatch.setattr(botmain, "Mt5Watch", W)
    db, engine = _doble_db({}), _doble_engine()
    # Con MT5 caido positions() devuelve [] (como el Broker real).
    engine.b.positions.side_effect = lambda: (
        [SimpleNamespace(ticket=7)] if actual["e"] == "OK" else [])
    monkeypatch.setattr(botmain, "_open_position_row", lambda p: {"ticket": p.ticket})
    cerradas = []
    monkeypatch.setattr(botmain, "_closed_position_row",
                        lambda b, t: cerradas.append(t) or {"ticket": t})
    botmain.trading_loop(db, engine, MagicMock(), StopDespues(3))
    assert cerradas == []


def test_motivo_salida_distingue_give_up():
    try:
        raise SystemExit(3)
    except SystemExit:
        assert "MT5" in botmain._motivo_salida()
    assert "manual" in botmain._motivo_salida()


def test_force_close_no_se_consume_si_mt5_muere_entre_latidos(monkeypatch):
    """El vigilante dijo OK en el ultimo latido, pero el terminal murio despues."""
    _entorno(monkeypatch)
    monkeypatch.setattr(config, "HEARTBEAT_INTERVAL", 10 ** 9)   # un solo latido
    _watch(monkeypatch, "OK")
    vivo = {"v": True}
    monkeypatch.setattr(botmain, "_mt5_vivo", lambda: vivo["v"])
    # Primer ciclo (latido) sin orden; luego muere MT5 y llega force_close.
    db = _doble_db({}, instancias=[{}, {"force_close": True}, {"force_close": True}])
    engine = _doble_engine(close_all=[True])

    class MuereTras:
        n = 0

        def is_set(self):
            MuereTras.n += 1
            if MuereTras.n == 2:
                vivo["v"] = False
            if MuereTras.n == 4:
                vivo["v"] = True
            return MuereTras.n > 4

    botmain.trading_loop(db, engine, MagicMock(), MuereTras())
    # Ciclos 2 y 3 con MT5 muerto: no se consume. Ciclo 4 ya vivo, pero el estado
    # quedo en RECONNECTING hasta el proximo latido, asi que sigue en espera.
    assert not db.clear_force_close.called
    assert not engine.close_all.called


def test_nulo_close_all_false_si_mt5_no_disponible():
    from motor.broker import MT5NoDisponible
    broker = MagicMock()
    broker.positions.side_effect = MT5NoDisponible("x")
    assert NullEngine(broker, MagicMock()).close_all("x") is False


def test_bot_status_mt5_down_si_positions_no_disponible():
    from motor.broker import MT5NoDisponible
    engine = MagicMock()
    engine.close_only = True
    engine.b.positions.side_effect = MT5NoDisponible("x")
    assert botmain._bot_status(engine) == "MT5_DOWN"


def test_positions_all_none_es_error_y_vacio_es_plano(monkeypatch):
    from motor import broker as brk
    b = brk.Broker(MagicMock(), shadow=True, symbols=("XAUUSD",))
    monkeypatch.setattr(brk.mt5, "last_error", lambda: (1, "x"))
    monkeypatch.setattr(brk.mt5, "positions_get", lambda symbol=None: None)
    with pytest.raises(brk.MT5NoDisponible):
        b.positions_all()
    monkeypatch.setattr(brk.mt5, "positions_get", lambda symbol=None: ())
    assert b.positions_all() == []
