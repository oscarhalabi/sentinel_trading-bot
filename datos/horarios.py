"""Horarios de mercado conocidos y la función abierto()."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
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


# --- festivos de EE. UU. ------------------------------------------------------
# Reglas generales, sin cierres excepcionales (funerales de Estado, huracanes...): un día
# así sale en `verificar` como hueco y se explica a mano.

_CIERRE_TEMPRANO = time(13, 0)      # hora de Nueva York
_REAPERTURA_CME = time(18, 0)        # la sesión del día siguiente abre a las 18:00 NY
_HORARIOS_CME = {"metal", "energia", "materia_prima"}
_HORARIOS_NYSE = {"indice_us", "accion_us"}


def _pascua(anio: int) -> date:
    """Domingo de Pascua (calendario gregoriano, algoritmo anónimo)."""
    a, b, c = anio % 19, anio // 100, anio % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = (h + l - 7 * m + 114) % 31 + 1
    return date(anio, mes, dia)


def _enesimo(anio: int, mes: int, dia_semana: int, n: int) -> date:
    """n-ésimo `dia_semana` (0 = lunes) del mes; n = -1 es el último."""
    if n > 0:
        primero = date(anio, mes, 1)
        return primero + timedelta(days=(dia_semana - primero.weekday()) % 7 + 7 * (n - 1))
    siguiente = date(anio + (mes == 12), mes % 12 + 1, 1)
    ultimo = siguiente - timedelta(days=1)
    return ultimo - timedelta(days=(ultimo.weekday() - dia_semana) % 7)


def _observado(d: date) -> date:
    """Festivo en sábado se observa el viernes; en domingo, el lunes."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _anio_nuevo(anio: int) -> date | None:
    # Si el 1 de enero cae en sábado no se cierra el viernes anterior (regla de NYSE y CME).
    d = date(anio, 1, 1)
    return None if d.weekday() == 5 else _observado(d)


def _festivos_federales(anio: int) -> list[date]:
    dias = [
        _enesimo(anio, 1, 0, 3),    # Martin Luther King
        _enesimo(anio, 2, 0, 3),    # Presidentes
        _enesimo(anio, 5, 0, -1),   # Memorial Day
        _observado(date(anio, 7, 4)),
        _enesimo(anio, 9, 0, 1),    # Labor Day
        _enesimo(anio, 11, 3, 4),   # Acción de Gracias
    ]
    if anio >= 2022:
        dias.append(_observado(date(anio, 6, 19)))  # Juneteenth
    return dias


@lru_cache(maxsize=None)
def _festivos_eeuu_civiles(anio: int) -> frozenset[date]:
    """Los 11 festivos federales de EE. UU. con su fecha observada (oficinas del Gobierno).

    Distinto de los festivos de bolsa: aquí cuentan también Colón y Veteranos, y no el
    Viernes Santo. Se usa para el "día hábil" de las publicaciones (Fed, Tesoro, BLS).
    Incluye el viernes 31 de diciembre cuando el 1 de enero del año siguiente cae en sábado.
    """
    dias = [
        _observado(date(anio, 1, 1)),
        _observado(date(anio, 7, 4)),
        _enesimo(anio, 9, 0, 1),     # Labor Day
        _enesimo(anio, 11, 3, 4),    # Acción de Gracias
        _observado(date(anio, 12, 25)),
    ]
    siguiente_anio_nuevo = date(anio + 1, 1, 1)
    if siguiente_anio_nuevo.weekday() == 5:
        dias.append(date(anio, 12, 31))
    if anio >= 1986:
        dias.append(_enesimo(anio, 1, 0, 3))   # Martin Luther King
    if anio >= 1971:  # ley de lunes festivos (Uniform Monday Holiday Act)
        dias += [
            _enesimo(anio, 2, 0, 3),    # Presidentes
            _enesimo(anio, 5, 0, -1),   # Memorial Day
            _enesimo(anio, 10, 0, 2),   # Colón
        ]
    else:
        dias += [_observado(date(anio, 2, 22)), _observado(date(anio, 5, 30)),
                 _observado(date(anio, 10, 12))]
    if 1971 <= anio <= 1977:
        dias.append(_enesimo(anio, 10, 0, 4))   # Veteranos (cuarto lunes de octubre)
    else:
        dias.append(_observado(date(anio, 11, 11)))
    if anio >= 2021:
        dias.append(_observado(date(anio, 6, 19)))  # Juneteenth
    # Un 1 de enero en sábado se observa el 31 de diciembre del año anterior: no es de este año.
    return frozenset(d for d in dias if d.year == anio)


def es_habil_eeuu(dia: date) -> bool:
    """True si `dia` es de lunes a viernes y no es festivo federal de EE. UU. (fecha observada)."""
    return dia.weekday() < 5 and dia not in _festivos_eeuu_civiles(dia.year)


@lru_cache(maxsize=None)
def _festivos(mercado: str, anio: int) -> dict[date, str]:
    """{día: 'cerrado' | 'temprano'} para 'cme' o 'nyse'."""
    salida: dict[date, str] = {}
    navidad = _observado(date(anio, 12, 25))
    viernes_santo = _pascua(anio) - timedelta(days=2)
    cerrados = [viernes_santo, navidad, _anio_nuevo(anio)]
    tempranos = [_enesimo(anio, 11, 3, 4) + timedelta(days=1)]  # viernes tras Acción de Gracias
    nochebuena = date(anio, 12, 24)
    if nochebuena.weekday() < 5 and nochebuena != navidad:
        tempranos.append(nochebuena)
    if mercado == "cme":
        tempranos += _festivos_federales(anio)  # la CME abre, pero corta a las 13:00
    else:
        cerrados += _festivos_federales(anio)
        julio3 = date(anio, 7, 3)
        if julio3.weekday() < 5 and _observado(date(anio, 7, 4)) != julio3:
            tempranos.append(julio3)
    for d in tempranos:
        salida[d] = "temprano"
    for d in cerrados:  # un cierre total pisa a un cierre temprano
        if d is not None:
            salida[d] = "cerrado"
    return salida


def festivo(mercado: str, dia: date) -> str | None:
    """'cerrado', 'temprano' o None para `dia` (fecha de Nueva York) en 'cme' o 'nyse'."""
    return _festivos(mercado, dia.year).get(dia)


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
        if horario in _HORARIOS_NYSE:
            f = festivo("nyse", local.date())
            if f == "cerrado" or (f == "temprano" and local.time() >= _CIERRE_TEMPRANO):
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
    if horario in _HORARIOS_CME:
        f = festivo("cme", local.date())
        if f == "cerrado" and hora < _REAPERTURA_CME:
            return False
        if f == "temprano" and _CIERRE_TEMPRANO <= hora < _REAPERTURA_CME:
            return False
        # La tarde antes de un día cerrado no abre la sesión de ese día.
        if hora >= time(17, 0) and festivo("cme", local.date() + timedelta(days=1)) == "cerrado":
            return False
    return True
