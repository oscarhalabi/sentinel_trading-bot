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
