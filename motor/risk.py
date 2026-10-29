"""Guardián de riesgo por cuenta.

Punto de enchufe: el Broker le pregunta antes de abrir cualquier operación.
La versión por defecto siempre permite; más adelante se sustituye por una
que mire el riesgo de toda la cuenta (todos los bots juntos).
"""


class AccountRiskGuard:
    def allow_open(self, *, bot_id: str, symbol: str, side: str, lots: float) -> tuple[bool, str]:
        """Devuelve (permitido, motivo). Por defecto siempre permite."""
        return True, ""
