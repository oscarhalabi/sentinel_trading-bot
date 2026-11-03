"""El guardián de riesgo por defecto no veta nada."""
from motor.risk import AccountRiskGuard


def test_guardian_por_defecto_permite():
    ok, motivo = AccountRiskGuard().allow_open(
        bot_id="n1", symbol="XAUUSD", side="BUY", lots=0.01)
    assert ok is True
    assert motivo == ""
