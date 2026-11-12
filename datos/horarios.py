"""Horarios de mercado conocidos y la función abierto()."""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

HORARIOS = frozenset({
    "fx", "metal", "indice_us", "indice_eu", "indice_asia", "accion_us",
    "accion_eu", "energia", "materia_prima", "cripto_24x7",
})

# (mes, día): cerrado para todos menos cripto_24x7
FERIADOS_FIJOS = {(1, 1), (12, 25)}

_NY = ZoneInfo("America/New_York")
_BERLIN = ZoneInfo("Europe/Berlin")
_TOKIO = ZoneInfo("Asia/Tokyo")

# Sesión diaria lunes a viernes: (zona, apertura, cierre)
_SESIONES = {
    "indice_us": (_NY, time(9, 30), time(16, 0)),
    "accion_us": (_NY, time(9, 30), time(16, 0)),
    "indice_eu": (_BERLIN, time(9, 0), time(17, 30)),
    "accion_eu": (_BERLIN, time(9, 0), time(17, 30)),
    "indice_asia": (_TOKIO, time(9, 0), time(15, 0)),
}
_CON_PAUSA_DIARIA = {"metal", "energia", "materia_prima"}


def _es_feriado(momento_utc: datetime, local: datetime) -> bool:
    # Se mira la fecha UTC y la local para que el día de feriado quede cerrado entero.
    return ((momento_utc.month, momento_utc.day) in FERIADOS_FIJOS
            or (local.month, local.day) in FERIADOS_FIJOS)


def abierto(horario: str, momento: datetime) -> bool:
    """True si el mercado `horario` está abierto en `momento` (UTC, con zona)."""
    if horario not in HORARIOS:
        raise ValueError(f"Horario desconocido: {horario!r}")
    if momento.tzinfo is None:
        raise ValueError("momento debe llevar zona horaria (UTC)")
    if horario == "cripto_24x7":
        return True
    utc = momento.astimezone(timezone.utc)
    if horario in _SESIONES:
        zona, apertura, cierre = _SESIONES[horario]
        local = utc.astimezone(zona)
        if _es_feriado(utc, local) or local.weekday() >= 5:
            return False
        return apertura <= local.time() < cierre
    # fx, metal, energia, materia_prima: domingo 17:00 a viernes 17:00 (Nueva York)
    local = utc.astimezone(_NY)
    if _es_feriado(utc, local):
        return False
    dia, hora = local.weekday(), local.time()
    if dia == 5:
        return False
    if dia == 6 and hora < time(17, 0):
        return False
    if dia == 4 and hora >= time(17, 0):
        return False
    if horario in _CON_PAUSA_DIARIA and time(17, 0) <= hora < time(18, 0):
        return False
    return True
