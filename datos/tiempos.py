"""Reglas de "cuándo se conoció" cada dato de contexto (sin mirar al futuro).

Todas las funciones son puras: reciben fechas y la configuración de `contexto.json` y
devuelven un instante en UTC (con zona). Ante la duda, una regla da una hora más tarde,
nunca más temprano: llegar tarde a un dato no inventa ventaja; llegar antes sí.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from datos.horarios import es_habil_eeuu

ZONA_NY = "America/New_York"
RETRASO_POR_DEFECTO = "habil+1@16:30"   # serie de mercado sin retraso configurado
HORA_COT = "15:30"
MIN_PERIODOS_BLOQUE = 24
MIN_DIAS_BLOQUE = 365


def local_a_utc(dia: date, hora: str, zona: str) -> datetime:
    """`dia` a la `hora` ('HH:MM') local de `zona`, pasado a UTC."""
    hh, mm = hora.split(":")
    local = datetime.combine(dia, time(int(hh), int(mm)), tzinfo=ZoneInfo(zona))
    return local.astimezone(timezone.utc)


def _habil_desde(dia: date) -> date:
    """`dia` si es hábil en EE. UU.; si no, el siguiente hábil."""
    while not es_habil_eeuu(dia):
        dia += timedelta(days=1)
    return dia


def publicacion_serie_mercado(dia: date, retraso: str | None) -> datetime:
    """Cuándo se conoce el dato de `dia` de una serie de mercado de FRED.

    - 'habil+1@HH:MM': siguiente día hábil de EE. UU. (sin fines de semana ni festivos).
    - 'mismo@HH:MM': ese mismo día.
    - 'lunes@HH:MM': lunes siguiente (si es festivo, el siguiente día hábil).
    - None: se usa RETRASO_POR_DEFECTO (día hábil siguiente a las 16:30, prudente).
    """
    regla, sep, hora = (retraso or RETRASO_POR_DEFECTO).partition("@")
    if not sep:
        raise ValueError(f"Retraso sin hora: {retraso!r}")
    if regla == "habil+1":
        dia_pub = _habil_desde(dia + timedelta(days=1))
    elif regla == "mismo":
        dia_pub = dia
    elif regla == "lunes":
        dia_pub = _habil_desde(dia + timedelta(days=7 - dia.weekday()))
    else:
        raise ValueError(f"Retraso desconocido: {retraso!r}")
    return local_a_utc(dia_pub, hora, ZONA_NY)


def publicacion_alfred(fecha_vintage: date, serie_conf: dict) -> datetime:
    """Una versión de ALFRED se conoce su fecha (`realtime_start`) a la hora de la serie."""
    return local_a_utc(fecha_vintage, serie_conf["hora"], serie_conf["zona"])


def _fin_periodo(periodo: date, frecuencia: str) -> date:
    if frecuencia == "w":
        return periodo  # FRED fecha la semana por el sábado en que termina
    if frecuencia == "q":
        mes_fin = (periodo.month - 1) // 3 * 3 + 3
        return date(periodo.year, mes_fin, calendar.monthrange(periodo.year, mes_fin)[1])
    if frecuencia == "m":
        return date(periodo.year, periodo.month,
                    calendar.monthrange(periodo.year, periodo.month)[1])
    raise ValueError(f"Frecuencia desconocida: {frecuencia!r}")


def estimar_publicacion(periodo: date, serie_conf: dict) -> datetime:
    """Publicación estimada de un periodo cargado en bloque: fin del periodo (mes, trimestre
    o semana (la fecha ya es el fin) según `frecuencia`, 'm' por defecto) + `retraso_tipico_dias`, a la hora de la serie."""
    fin = _fin_periodo(periodo, serie_conf.get("frecuencia", "m"))
    dia = fin + timedelta(days=serie_conf["retraso_tipico_dias"])
    return local_a_utc(dia, serie_conf["hora"], serie_conf["zona"])


def detectar_carga_en_bloque(vintages: pd.DataFrame, periodo: str = "periodo",
                             publicacion: str = "realtime_start") -> pd.Series:
    """True en las primeras versiones cuya fecha de publicación la comparten 24 periodos o más
    que abarcan más de 365 días (ALFRED cargó de golpe la historia vieja ese día).

    Solo cuenta la primera versión de cada periodo: una revisión general (p. ej. GDP en 2013
    hasta 1929) también comparte fecha, pero es un dato publicado ese día, no estimable antes.
    """
    if vintages.empty:
        return pd.Series(False, index=vintages.index, dtype=bool)
    per = pd.to_datetime(vintages[periodo])
    pub = pd.to_datetime(vintages[publicacion])
    primera = pub == pub.groupby(per).transform("min")
    grupos = per[primera].groupby(pub[primera])
    cuantos = grupos.transform("nunique")
    abarca = grupos.transform("max") - grupos.transform("min")
    en_bloque = (cuantos >= MIN_PERIODOS_BLOQUE) & (abarca > pd.Timedelta(days=MIN_DIAS_BLOQUE))
    return en_bloque.reindex(vintages.index, fill_value=False).astype(bool)


def publicacion_cot(fecha_informe: date, informe: str, cot_conf: dict) -> tuple[datetime, bool]:
    """(cuándo se conoce, publicación tardía) de un informe COT con datos al `fecha_informe`.

    Normal: viernes de esa semana a las 15:30 NY, o la excepción de
    `cot_conf['excepciones_publicacion']` (clave: el viernes normal; valor: 'AAAA-MM-DDTHH:MM'
    en hora de NY). Si el informe es anterior a su primera publicación (Disaggregated y TFF
    empezaron el 2009-09-04), se conoce ese día a las 15:30 y es tardía.
    """
    if fecha_informe.weekday() >= 5:
        raise ValueError(f"Fecha de informe COT en fin de semana: {fecha_informe}")
    viernes = fecha_informe + timedelta(days=4 - fecha_informe.weekday())
    excepcion = cot_conf.get("excepciones_publicacion", {}).get(viernes.isoformat())
    if excepcion:
        local = datetime.strptime(excepcion, "%Y-%m-%dT%H:%M")
        normal = local_a_utc(local.date(), local.strftime("%H:%M"), ZONA_NY)
    else:
        normal = local_a_utc(viernes, HORA_COT, ZONA_NY)
    primera = cot_conf.get("primera_publicacion", {}).get(informe)
    if primera and fecha_informe < date.fromisoformat(primera):
        tardia = local_a_utc(date.fromisoformat(primera), HORA_COT, ZONA_NY)
        return max(normal, tardia), True
    return normal, False


def publicacion_etf(fecha: date, etf_conf: dict) -> datetime:
    """Las tenencias de un ETF del día `fecha` se conocen ese día a su hora (NY salvo `zona`)."""
    return local_a_utc(fecha, etf_conf["hora"], etf_conf.get("zona", ZONA_NY))
