"""Etiquetas de operación por comment, guardián de riesgo y posiciones multi-símbolo."""
from types import SimpleNamespace

import MetaTrader5 as mt5

from motor.broker import Broker


class _Logger:
    def __init__(self):
        self.lines = []

    def write(self, log_type, msg, *a, **k):
        self.lines.append((log_type, msg))


def test_tag_desde_comment():
    assert Broker._open_log_type("SCALP|entrada") == "SCALP"
    assert Broker._open_log_type("sin tag") == "OPERACION"
    assert Broker._open_log_type("a b|x") == "AB"
    assert Broker._open_log_type("") == "OPERACION"
    assert Broker._open_log_type(None) == "OPERACION"
    assert Broker._open_log_type("x" * 30 + "|y") == "X" * 20


def test_guardian_veta_apertura(monkeypatch):
    class Veto:
        def allow_open(self, *, bot_id, symbol, side, lots):
            return False, "riesgo"

    llamadas = []
    monkeypatch.setattr(mt5, "order_send", lambda r: llamadas.append(r))
    monkeypatch.setattr(Broker, "trade_allowed", lambda self: (True, None))
    log = _Logger()
    b = Broker(log, risk_guard=Veto())
    assert b.market_order(mt5.ORDER_TYPE_BUY, 0.01, "SCALP|x") is None
    assert llamadas == []
    assert any(t == "BLOQUEO" and "riesgo" in m for t, m in log.lines)


def test_positions_multi_simbolo(monkeypatch):
    mias = lambda s: SimpleNamespace(symbol=s, magic=300100)
    ajena = SimpleNamespace(symbol="EURUSD", magic=999)
    datos = {"XAUUSD": [mias("XAUUSD")], "EURUSD": [mias("EURUSD"), ajena]}
    monkeypatch.setattr(mt5, "positions_get", lambda symbol: datos[symbol])
    b = Broker(_Logger(), symbols=("XAUUSD", "EURUSD"))
    b.magic = 300100
    assert b.symbol == "XAUUSD"
    assert len(b.positions_all()) == 3
    assert sorted(p.symbol for p in b.positions()) == ["EURUSD", "XAUUSD"]


def test_cierre_y_sl_usan_simbolo_de_la_posicion(monkeypatch):
    enviados = []
    monkeypatch.setattr(mt5, "order_send", lambda r: enviados.append(r))
    ticks = {"XAUUSD": SimpleNamespace(bid=1.0, ask=1.1),
             "EURUSD": SimpleNamespace(bid=2.0, ask=2.1)}
    monkeypatch.setattr(mt5, "symbol_info_tick", lambda s: ticks.get(s))
    b = Broker(_Logger(), symbols=("XAUUSD", "EURUSD"))
    compra = SimpleNamespace(symbol="EURUSD", ticket=7, volume=0.1, tp=0.0,
                             type=mt5.POSITION_TYPE_BUY)
    venta = SimpleNamespace(symbol="EURUSD", ticket=8, volume=0.1, tp=0.0,
                            type=mt5.POSITION_TYPE_SELL)
    b.close_position(compra)
    b.close_position(venta)
    b.modify_sl(compra, 1.5)
    assert [r["symbol"] for r in enviados] == ["EURUSD"] * 3
    assert enviados[0]["price"] == 2.0   # cerrar BUY -> bid
    assert enviados[1]["price"] == 2.1   # cerrar SELL -> ask


def test_sin_tick_no_revienta(monkeypatch):
    monkeypatch.setattr(mt5, "symbol_info_tick", lambda s: None)
    monkeypatch.setattr(mt5, "order_send", lambda r: 1 / 0)
    monkeypatch.setattr(Broker, "trade_allowed", lambda self: (True, None))
    log = _Logger()
    b = Broker(log)
    pos = SimpleNamespace(symbol="XAUUSD", ticket=1, volume=0.1, tp=0.0,
                          type=mt5.POSITION_TYPE_BUY)
    assert b.close_position(pos) is None
    assert b.market_order(mt5.ORDER_TYPE_BUY, 0.01, "X|y") is None
    assert sum(1 for t, _ in log.lines if t == "ERROR") == 2
