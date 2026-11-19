"""Calendario económico y acciones corporativas desde APIs de pago (FMP y EODHD).

Las rutas y los nombres de campo son PROVISIONALES hasta la Task 12 (se ajustan con una respuesta
real). Por eso cada fuente tiene todo su mapeo de campos en un solo diccionario (`MAPA`).

Límite conocido: en corporativos solo se bajan los símbolos de EE. UU. (`<TICKER>.US`); los demás
se omiten y se cuentan en el log.
"""
from __future__ import annotations

import json
import logging
import re
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from datos.almacen import (ESQUEMA_CALENDARIO, ESQUEMA_CORPORATIVO, Manifiesto, escribir,
                           escribir_crudo, momento_crudo, nombre_crudo, ruta_relativa)
from datos.config import Config, ConfigInvalida
from datos.contexto_conf import cargar_contexto, cargar_eventos, normalizar_evento
from datos.fuentes.base import ClienteHttp, ClienteRequests
from datos.fuentes.forexfactory import NY, TIMEOUT, _numero
from datos.horarios import es_habil_eeuu
from datos.simbolos import Simbolo
from datos.versiones import versionar

log = logging.getLogger(__name__)

_CAMPOS = ["prevision", "real", "anterior", "anterior_revisado", "ts_evento", "impacto",
           "hora_exacta"]
_ORDEN_IMPACTO = {"bajo": 0, "medio": 1, "alto": 2}

# País (código ISO, nombre o "EU"/"Euro Area") → divisa. Cubre el G10 y China.
_EURO = ("EU", "EZ", "EMU", "Euro Area", "Eurozone", "DE", "FR", "IT", "ES", "NL", "BE", "AT",
         "FI", "IE", "PT", "GR", "LU", "SK", "SI", "EE", "LV", "LT", "CY", "MT", "HR")
PAIS_A_DIVISA: dict[str, str] = {
    **{p: "EUR" for p in _EURO},
    "US": "USD", "United States": "USD", "GB": "GBP", "UK": "GBP", "United Kingdom": "GBP",
    "JP": "JPY", "Japan": "JPY", "CH": "CHF", "Switzerland": "CHF", "AU": "AUD",
    "Australia": "AUD", "CA": "CAD", "Canada": "CAD", "NZ": "NZD", "New Zealand": "NZD",
    "NO": "NOK", "Norway": "NOK", "SE": "SEK", "Sweden": "SEK", "CN": "CNY", "China": "CNY",
}

# Mapeo de campos por fuente (provisional). `impacto` traduce el valor de la API a bajo/medio/alto.
MAPA = {
    "fmp": {
        "id": "id", "fecha": "date", "pais": "country", "divisa": "currency", "titulo": "event",
        "prevision": "estimate", "real": "actual", "anterior": "previous",
        "anterior_revisado": "previousRevised", "unidad": "unit", "impacto": "impact",
        "impactos": {"low": "bajo", "medium": "medio", "high": "alto"},
    },
    "eodhd": {
        "id": "id", "fecha": "date", "pais": "country", "divisa": None, "titulo": "type",
        "prevision": "estimate", "real": "actual", "anterior": "previous",
        "anterior_revisado": "previousRevised", "unidad": "unit", "impacto": "importance",
        "impactos": {"0": "bajo", "1": "bajo", "2": "medio", "3": "alto",
                     "low": "bajo", "medium": "medio", "high": "alto"},
    },
}


def _momento(texto: str) -> tuple[datetime, bool]:
    """(ts_evento UTC, hora_exacta). La API da la hora en UTC; sin hora o 00:00:00 → día
    sin hora exacta, anclado a la medianoche de Nueva York (como Forex Factory)."""
    t = str(texto).strip().replace("T", " ").replace("Z", "")
    dia = date.fromisoformat(t[:10])
    hms = t[11:19] if len(t) > 10 else ""
    if hms in ("", "00:00:00", "00:00"):
        return datetime(dia.year, dia.month, dia.day, tzinfo=NY).astimezone(timezone.utc), False
    h, m, *s = (int(x) for x in hms.split(":"))
    return datetime(dia.year, dia.month, dia.day, h, m, s[0] if s else 0,
                    tzinfo=timezone.utc), True


def _valor(v) -> tuple[float | None, str]:
    """Número de la API (a veces texto como '3.0%' o '250K'). Devuelve (valor, unidad)."""
    if v is None or isinstance(v, bool):
        return None, ""
    if isinstance(v, (int, float)):
        return float(v), ""
    return _numero(v)


def _id(v) -> str | None:
    return None if v in (None, "") else str(v)


class FuenteCalendario(Protocol):
    nombre: str

    def bajar_eventos(self, desde: date, hasta: date) -> bytes: ...

    def normalizar_eventos(self, cuerpo: bytes, ts_descarga: datetime,
                           historico: bool) -> pa.Table: ...

    def bajar_corporativos(self, ticker: str) -> list[bytes]: ...

    def normalizar_corporativos(self, cuerpos: list[bytes], simbolo: str) -> pa.Table: ...


class _Base:
    """Lo común de FMP y EODHD: clave, GET sin filtrar la clave y normalización de eventos."""
    nombre = ""
    _url_base = ""
    _param_clave = ""

    def __init__(self, cfg: Config | None, http: ClienteHttp | None = None) -> None:
        self._cfg = cfg
        self._http = http

    def _clave(self) -> str:
        clave = getattr(self._cfg, f"{self.nombre}_api_key", "") if self._cfg else ""
        if not clave:
            raise ConfigInvalida(f"Falta {self.nombre.upper()}_API_KEY en el .env para usar "
                                 f"la fuente {self.nombre}.")
        return clave

    def exigir_clave(self) -> None:
        self._clave()

    def _get(self, ruta: str, params: dict) -> bytes:
        clave = self._clave()
        if self._http is None:
            self._http = ClienteRequests()
        url = f"{self._url_base}{ruta}?{urlencode({**params, self._param_clave: clave})}"
        try:
            estado, cuerpo = self._http.get(url, TIMEOUT)
        except Exception as e:  # noqa: BLE001 - el mensaje de requests lleva la URL con la clave
            raise RuntimeError(f"{self.nombre}: error de red en {ruta}: "
                               f"{type(e).__name__}") from None
        if estado != 200:   # sin la URL: lleva la clave
            raise RuntimeError(f"{self.nombre}: HTTP {estado} en {ruta} {params}")
        return cuerpo

    def paginas_eventos(self, desde: date, hasta: date) -> list[bytes]:
        raise NotImplementedError

    def bajar_eventos(self, desde: date, hasta: date) -> bytes:
        """Todas las páginas juntas en un solo arreglo JSON."""
        filas: list = []
        for p in self.paginas_eventos(desde, hasta):
            filas += json.loads(p)
        return json.dumps(filas).encode()

    def normalizar_eventos(self, cuerpo: bytes, ts_descarga: datetime,
                           historico: bool) -> pa.Table:
        """Esquema de calendario. Solo divisas de `divisas_calendario` e impacto ≥ `impacto_min`."""
        ctx = cargar_contexto()
        mapa_ev = cargar_eventos()
        m = MAPA[self.nombre]
        horizonte = timedelta(days=ctx["horizonte_anuncio_dias"])
        ts_descarga = ts_descarga.astimezone(timezone.utc)
        filas = []
        for it in json.loads(cuerpo):
            pais = it.get(m["pais"])
            divisa = (it.get(m["divisa"]) if m["divisa"] else None) or PAIS_A_DIVISA.get(pais)
            if divisa not in ctx["divisas_calendario"]:
                continue
            impacto = m["impactos"].get(str(it.get(m["impacto"], "")).lower(), "bajo")
            if _ORDEN_IMPACTO[impacto] < _ORDEN_IMPACTO[ctx["impacto_min"]]:
                continue
            ts_evento, exacta = _momento(it[m["fecha"]])
            prev, u1 = _valor(it.get(m["prevision"]))
            real, u2 = _valor(it.get(m["real"]))
            ant, u3 = _valor(it.get(m["anterior"]))
            rev, _ = _valor(it.get(m["anterior_revisado"]))
            titulo = it[m["titulo"]]
            evento, categoria = normalizar_evento(self.nombre, titulo, mapa_ev)
            anunciado = ts_evento - horizonte if historico else ts_descarga
            filas.append({
                "ts_evento": ts_evento, "ts_anunciado": anunciado, "ts_conocido": ts_evento,
                "ts_version": anunciado, "ts_foto": None, "fuente": self.nombre,
                "id_fuente": _id(it.get(m["id"])), "pais": pais, "divisa": divisa,
                "evento": evento, "evento_fuente": titulo, "categoria": categoria,
                "impacto": impacto, "unidad": it.get(m["unidad"]) or u1 or u2 or u3 or None,
                "programado": True, "hora_exacta": exacta,
                # Solo es previa al dato si se bajó antes del evento (nunca en el histórico).
                "prevision_es_pit": not historico and anunciado < ts_evento,
                "real": real, "prevision": prev, "anterior": ant, "anterior_revisado": rev,
            })
        return pa.Table.from_pylist(filas, schema=ESQUEMA_CALENDARIO)

    # --- corporativos ---
    def bajar_corporativos(self, ticker: str) -> list[bytes]:
        """[cuerpo de splits, cuerpo de dividendos]."""
        raise NotImplementedError

    def normalizar_corporativos(self, cuerpos: list[bytes], simbolo: str) -> pa.Table:
        filas = []
        for it in json.loads(cuerpos[0]):
            fila = self._split(it)
            if fila:
                filas.append(("split", *fila))
        for it in json.loads(cuerpos[1]):
            fila = self._dividendo(it)
            if fila:
                filas.append(("dividendo", *fila))
        salida = []
        for tipo, ex, anuncio, razon, importe, divisa in filas:
            # Con anuncio: apertura del SIGUIENTE día hábil (el anuncio puede salir tras el
            # cierre y no sabemos a qué hora). Sin anuncio: apertura del día ex.
            dia_conocido = _siguiente_habil(anuncio) if anuncio else ex
            conocido = datetime(dia_conocido.year, dia_conocido.month, dia_conocido.day, 9, 30,
                                tzinfo=NY).astimezone(timezone.utc)
            salida.append({"simbolo": simbolo, "tipo": tipo, "fuente": self.nombre,
                           "fecha_ex": ex, "fecha_anuncio": anuncio, "razon": razon,
                           "importe": importe, "divisa": divisa, "ts_conocido": conocido})
        return pa.Table.from_pylist(salida, schema=ESQUEMA_CORPORATIVO)

    def _split(self, it: dict):
        raise NotImplementedError

    def _dividendo(self, it: dict):
        raise NotImplementedError


def _siguiente_habil(dia: date) -> date:
    """El primer día hábil de EE. UU. después de `dia`."""
    dia += timedelta(days=1)
    while not es_habil_eeuu(dia):
        dia += timedelta(days=1)
    return dia


def _fecha(v) -> date | None:
    return date.fromisoformat(str(v)[:10]) if v else None


class FMP(_Base):
    nombre = "fmp"
    _url_base = "https://financialmodelingprep.com/stable/"
    _param_clave = "apikey"

    def paginas_eventos(self, desde: date, hasta: date) -> list[bytes]:
        return [self._get("economic-calendar", {"from": desde.isoformat(), "to": hasta.isoformat()})]

    def bajar_corporativos(self, ticker: str) -> list[bytes]:
        return [self._get("splits", {"symbol": ticker}), self._get("dividends", {"symbol": ticker})]

    def _split(self, it):   # 4-for-1: numerator 4, denominator 1 → razón 4.0
        return (_fecha(it["date"]), None, float(it["numerator"]) / float(it["denominator"]),
                None, None)

    def _dividendo(self, it):
        return (_fecha(it["date"]), _fecha(it.get("declarationDate")), None,
                float(it["dividend"]), it.get("currency") or "USD")


class EODHD(_Base):
    nombre = "eodhd"
    _url_base = "https://eodhd.com/api/"
    _param_clave = "api_token"
    PAGINA = 1000   # filas por página; una página más corta es la última

    def paginas_eventos(self, desde: date, hasta: date) -> list[bytes]:
        paginas, offset = [], 0
        while True:
            cuerpo = self._get("economic-events", {
                "from": desde.isoformat(), "to": hasta.isoformat(), "limit": self.PAGINA,
                "offset": offset, "fmt": "json"})
            paginas.append(cuerpo)
            if len(json.loads(cuerpo)) < self.PAGINA:
                return paginas
            offset += self.PAGINA

    def bajar_corporativos(self, ticker: str) -> list[bytes]:
        return [self._get(f"splits/{ticker}.US", {"fmt": "json"}),
                self._get(f"div/{ticker}.US", {"fmt": "json"})]

    def _split(self, it):   # "10.000000/1.000000" = nuevas/viejas
        nuevas, viejas = (float(x) for x in str(it["split"]).split("/"))
        return _fecha(it["date"]), None, nuevas / viejas, None, None

    def _dividendo(self, it):
        importe = it.get("unadjustedValue", it.get("value"))
        return (_fecha(it["date"]), _fecha(it.get("declarationDate")), None, float(importe),
                it.get("currency") or "USD")


FUENTES_CALENDARIO = {"fmp": FMP, "eodhd": EODHD}


def _fuente(cfg: Config, nombre: str, http: ClienteHttp | None) -> _Base:
    try:
        fuente = FUENTES_CALENDARIO[nombre](cfg, http)
    except KeyError:
        raise ConfigInvalida(f"Fuente desconocida: {nombre!r}. Disponibles: "
                             f"{', '.join(FUENTES_CALENDARIO)}.") from None
    fuente.exigir_clave()
    return fuente


def _meses(desde: date, hasta: date):
    d = date(desde.year, desde.month, 1)
    while d <= hasta:
        fin = date(d.year, d.month, monthrange(d.year, d.month)[1])
        yield max(d, desde), min(fin, hasta)
        d = fin + timedelta(days=1)


def _con_clave(df: pd.DataFrame) -> pd.DataFrame:
    """Añade `_clave` (solo para versionar; no se guarda): la id de la fuente si la hay, si no
    evento + divisa + día de Nueva York (como Forex Factory)."""
    df = df.copy()
    dia = df["ts_evento"].dt.tz_convert(NY).dt.date.astype(str)
    por_nombre = "ev:" + df["evento"] + "|" + df["divisa"] + "|" + dia
    por_id = "id:" + df["id_fuente"].astype("string")
    df["_clave"] = por_id.where(df["id_fuente"].notna(), por_nombre)
    return df


def _mezclar(raiz: Path, nuevas: pd.DataFrame, fuente: str, divisa: str, anio: int,
             ts_descarga: datetime, historico: bool) -> int:
    """Mezcla las filas de una divisa y año con lo guardado. Devuelve filas nuevas."""
    periodo = date(anio, 1, 1)
    ruta = raiz / ruta_relativa("calendario", fuente, divisa, periodo)
    previas = (pq.read_table(ruta).to_pandas() if ruta.exists()
               else ESQUEMA_CALENDARIO.empty_table().to_pandas())
    previas_c = _con_clave(previas) if not previas.empty else previas.assign(_clave=pd.Series(dtype=str))
    nuevas = _con_clave(nuevas).drop_duplicates(subset="_clave", keep="last")
    primera = {}
    if not previas.empty:   # ts_anunciado = el de la primera versión del evento
        primera = (previas_c.sort_values("ts_version", kind="stable")
                   .groupby("_clave")["ts_anunciado"].first().to_dict())
        nuevas["ts_anunciado"] = [primera.get(k, a) for k, a
                                  in zip(nuevas["_clave"], nuevas["ts_anunciado"])]
    res = versionar(previas_c, nuevas, ["_clave"], _CAMPOS, ts_descarga)
    agregadas = len(res) - len(previas)
    if not agregadas:
        return 0
    if historico:   # evento nuevo: su versión es su anuncio estimado; una corrección: la descarga
        for i in range(len(previas), len(res)):
            if res.at[i, "_clave"] not in primera:
                res.at[i, "ts_version"] = res.at[i, "ts_anunciado"]
    res = res.drop(columns="_clave")
    tabla = pa.Table.from_pandas(res[ESQUEMA_CALENDARIO.names], schema=ESQUEMA_CALENDARIO,
                                 preserve_index=False)
    escribir(raiz, tabla, "calendario", fuente, divisa, periodo)
    return agregadas


def nombre_crudo_calendario(fuente: str, mes: str, pagina: int, historico: bool) -> str:
    """`fmp_cal_2024-01`, `eodhd_cal_2024-01_p0`; con `_hist` al final si es carga histórica
    (así `reprocesar` sabe cómo se normalizó)."""
    return (f"{fuente}_cal_{mes}" + (f"_p{pagina}" if fuente == "eodhd" else "")
            + ("_hist" if historico else ""))


_RE_CAL = re.compile(r"^(?P<fuente>[a-z]+)_cal_\d{4}-\d{2}(?:_p\d+)?(?P<hist>_hist)?$")
_RE_CORP = re.compile(r"^(?P<fuente>[a-z]+)_(?P<tipo>split|div)_(?P<ticker>[A-Za-z0-9_-]+)$")


def _procesar_calendario(raiz: Path, src: _Base, cuerpo: bytes, ts: datetime,
                         historico: bool) -> int:
    df = src.normalizar_eventos(cuerpo, ts, historico).to_pandas()
    if df.empty:
        return 0
    df["_anio"] = df["ts_evento"].dt.year
    nuevas = 0
    for (divisa, anio), grupo in df.groupby(["divisa", "_anio"]):
        nuevas += _mezclar(raiz, grupo.drop(columns="_anio"), src.nombre, divisa, int(anio),
                           ts, historico)
    return nuevas


def _procesar_corporativo(raiz: Path, src: _Base, cuerpo: bytes, tipo: str,
                          simbolo: str) -> int:
    """Un crudo de splits o de dividendos. Se mezclan por separado: la clave lleva el tipo."""
    cuerpos = [cuerpo, b"[]"] if tipo == "split" else [b"[]", cuerpo]
    return _mezclar_corporativo(raiz, src.normalizar_corporativos(cuerpos, simbolo),
                                src.nombre, simbolo)


def _ticker_seguro(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", ticker)


def procesar_crudo(cfg: Config, cuerpo: bytes, meta: Manifiesto) -> int:
    """Normaliza un crudo guardado de FMP o EODHD (calendario, splits o dividendos) y lo mezcla.

    Todo sale del crudo: la fuente, si fue histórico (`_hist`), el ticker y la hora de descarga.
    Es el mismo camino que usa la descarga en vivo; `reprocesar` lo usa sin red. No pide clave.
    """
    nombre = nombre_crudo(meta)
    raiz = Path(cfg.data_dir)
    cal, corp = _RE_CAL.match(nombre), _RE_CORP.match(nombre)
    m = cal or corp
    if m is None or m["fuente"] != meta.fuente or meta.fuente not in FUENTES_CALENDARIO:
        raise ValueError(f"Crudo de {meta.fuente} con nombre desconocido: {meta.ruta}")
    src = FUENTES_CALENDARIO[meta.fuente](cfg)
    if cal:
        return _procesar_calendario(raiz, src, cuerpo, momento_crudo(meta), bool(cal["hist"]))
    # El ticker guardado es el de EE. UU. sin `.US` (un "." se guardó como "-").
    simbolo = corp["ticker"].replace("-", ".") + ".US"
    return _procesar_corporativo(raiz, src, cuerpo, corp["tipo"], simbolo)


def bajar_calendario(cfg: Config, fuente: str, desde: date, hasta: date, historico: bool,
                     http: ClienteHttp | None = None) -> int:
    """Baja el calendario por meses, guarda el crudo de cada página y lo mezcla (solo añadir).

    Devuelve las filas nuevas. Sin clave de la fuente falla antes de pedir nada.
    """
    src = _fuente(cfg, fuente, http)
    raiz = Path(cfg.data_dir)
    ts = datetime.now(timezone.utc)
    nuevas = 0
    for ini, fin in _meses(desde, hasta):
        for i, cuerpo in enumerate(src.paginas_eventos(ini, fin)):
            nombre = nombre_crudo_calendario(fuente, f"{ini:%Y-%m}", i, historico)
            m = escribir_crudo(raiz, cuerpo, fuente, nombre, ts, "json")
            nuevas += procesar_crudo(cfg, cuerpo, m)
    return nuevas


def _mezclar_corporativo(raiz: Path, nueva: pa.Table, fuente: str, simbolo: str) -> int:
    """Une con lo guardado por (tipo, fecha_ex): nunca borra filas; añade las nuevas.

    Si una fila guardada cambió en la API se conserva la guardada y se avisa en el log.
    Solo escribe si hay algo nuevo (o si el archivo aún no existe). Devuelve las filas añadidas.
    """
    ruta = raiz / ruta_relativa("corporativo", fuente, simbolo, None)
    if not ruta.exists():
        escribir(raiz, nueva, "corporativo", fuente, simbolo, None)
        return nueva.num_rows
    previa = pq.read_table(ruta)
    guardadas = {(f["tipo"], f["fecha_ex"]): f for f in previa.to_pylist()}
    nuevas = []
    for f in nueva.to_pylist():
        k = (f["tipo"], f["fecha_ex"])
        if k not in guardadas:
            nuevas.append(f)
        elif any(guardadas[k][c] != f[c] for c in ("razon", "importe", "fecha_anuncio")):
            log.warning("%s: %s del %s cambió en la API; se conserva lo guardado",
                        simbolo, k[0], k[1])
    if not nuevas:
        return 0
    union = pa.concat_tables([previa, pa.Table.from_pylist(nuevas, schema=ESQUEMA_CORPORATIVO)])
    escribir(raiz, union, "corporativo", fuente, simbolo, None)
    return len(nuevas)


def bajar_corporativos(cfg: Config, fuente: str, simbolos: list[Simbolo],
                       http: ClienteHttp | None = None) -> int:
    """Splits y dividendos de cada símbolo de EE. UU. Devuelve las filas nuevas guardadas (une con lo existente, no borra).

    Los símbolos que no terminan en `.US` se omiten (limitación) y se cuentan en el log.
    Si un símbolo falla se sigue con los demás y al final se lanza RuntimeError.
    """
    src = _fuente(cfg, fuente, http)
    raiz = Path(cfg.data_dir)
    ts = datetime.now(timezone.utc)
    filas, omitidos, fallos = 0, 0, []
    for s in simbolos:
        if not s.simbolo.endswith(".US"):
            omitidos += 1
            continue
        ticker = s.simbolo[:-3]
        try:
            cuerpos = src.bajar_corporativos(ticker)
            for tipo, cuerpo in zip(("split", "div"), cuerpos):
                m = escribir_crudo(raiz, cuerpo, fuente,
                                   f"{fuente}_{tipo}_{_ticker_seguro(ticker)}", ts, "json")
                filas += procesar_crudo(cfg, cuerpo, m)
        except Exception as e:  # noqa: BLE001 - seguimos con el resto
            fallos.append(f"{s.simbolo} ({e})")
    if omitidos:
        log.warning("%d símbolos no EE. UU. omitidos en corporativos (limitación de la Task 6)",
                    omitidos)
    if fallos:
        raise RuntimeError("Fallaron corporativos de: " + "; ".join(fallos))
    return filas
