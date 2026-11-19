"""Fuente CFTC: posicionamiento semanal (COT) en zips anuales, en formato largo y con versiones."""
from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from datos.almacen import ESQUEMA_COT, escribir, escribir_crudo, ruta_relativa
from datos.config import Config
from datos.contexto_conf import cargar_contexto
from datos.fuentes.base import ClienteHttp, ClienteRequests
from datos.tiempos import publicacion_cot
from datos.versiones import versionar

log = logging.getLogger(__name__)

# PROVISIONALES hasta verificarlas con una descarga real (Task 12).
URLS_COT = {
    "legacy": "https://www.cftc.gov/files/dea/history/deacot{anio}.zip",
    "disaggregated": "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{anio}.zip",
    "tff": "https://www.cftc.gov/files/dea/history/fut_fin_txt_{anio}.zip",
}
PRIMER_ANIO = {"legacy": 1986, "disaggregated": 2006, "tff": 2006}
TIMEOUT = 120.0
CLAVE = ["mercado", "informe", "fecha_informe", "campo"]

# Columna del código de contrato y de la fecha, por informe (PROVISIONAL).
COLUMNA_CODIGO = {"legacy": "CFTC Contract Market Code",
                  "disaggregated": "CFTC_Contract_Market_Code", "tff": "CFTC_Contract_Market_Code"}
COLUMNA_FECHA = {"legacy": "As of Date in Form YYYY-MM-DD",
                 "disaggregated": "Report_Date_as_YYYY-MM-DD", "tff": "Report_Date_as_YYYY-MM-DD"}

# Nuestro campo -> columna de la CFTC, por informe. Un campo ausente se salta en ese informe.
COLUMNAS = {
    "legacy": {
        "interes_abierto": "Open Interest (All)",
        "no_comerciales_largos": "Noncommercial Positions-Long (All)",
        "no_comerciales_cortos": "Noncommercial Positions-Short (All)",
        "comerciales_largos": "Commercial Positions-Long (All)",
        "comerciales_cortos": "Commercial Positions-Short (All)",
    },
    "disaggregated": {
        "interes_abierto": "Open_Interest_All",
        "gestores_largos": "M_Money_Positions_Long_All",
        "gestores_cortos": "M_Money_Positions_Short_All",
        "comerciales_largos": "Prod_Merc_Positions_Long_All",
        "comerciales_cortos": "Prod_Merc_Positions_Short_All",
    },
    "tff": {
        "interes_abierto": "Open_Interest_All",
        "fondos_largos": "Lev_Money_Positions_Long_All",
        "fondos_cortos": "Lev_Money_Positions_Short_All",
    },
}


def _n(nombre: str) -> str:
    """Nombre de columna sin mayúsculas, espacios ni signos (para tolerar pequeñas diferencias)."""
    return re.sub(r"[^a-z0-9]", "", nombre.lower())


def _texto_del_zip(contenido: bytes) -> bytes:
    """El primer archivo del zip (si ya viene como CSV/TXT suelto, se devuelve tal cual)."""
    if not zipfile.is_zipfile(io.BytesIO(contenido)):
        return contenido
    with zipfile.ZipFile(io.BytesIO(contenido)) as z:
        nombres = [n for n in z.namelist() if not n.endswith("/")]
        if not nombres:
            raise RuntimeError("El zip de la CFTC está vacío")
        return z.read(nombres[0])


def _normalizar_filas(csv_bytes: bytes, informe: str, cot_conf: dict,
                      ts_descarga: datetime) -> tuple[list[dict], int]:
    mercados = {v: k for k, v in cot_conf["mercados"].items() if not k.startswith("_")}
    campos = [c for c in cot_conf["campos"] if c in COLUMNAS[informe]]
    lector = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig", errors="replace")))
    cabecera = {_n(c): c for c in (lector.fieldnames or [])}
    col_codigo = cabecera.get(_n(COLUMNA_CODIGO[informe]))
    col_fecha = cabecera.get(_n(COLUMNA_FECHA[informe]))
    if not col_codigo or not col_fecha:
        raise ValueError(f"Al informe {informe} le faltan las columnas de código o fecha")
    col_campo = {c: cabecera.get(_n(COLUMNAS[informe][c])) for c in campos}
    ts_descarga = ts_descarga.astimezone(timezone.utc)
    filas, desconocidos, vistos = [], 0, set()
    for r in lector:
        codigo = (r[col_codigo] or "").strip()
        if codigo not in mercados:
            desconocidos += 1
            continue
        fecha = date.fromisoformat((r[col_fecha] or "").strip())
        ts_conocido, tardia = publicacion_cot(fecha, informe, cot_conf)
        for campo in campos:
            col = col_campo[campo]
            texto = (r.get(col) or "").strip().replace(",", "") if col else ""
            try:
                valor = float(texto)
            except ValueError:
                continue   # vacío, "." u otro marcador de "sin dato"
            filas.append({"mercado": mercados[codigo], "informe": informe, "campo": campo,
                          "fecha_informe": fecha, "valor": valor, "ts_conocido": ts_conocido,
                          "ts_version": ts_descarga, "publicacion_tardia": tardia})
    return filas, desconocidos


def normalizar_cot(csv: bytes, informe: str, cot_conf: dict, ts_descarga: datetime) -> pa.Table:
    """CSV de la CFTC → formato largo (una fila por mercado, fecha y campo configurado).

    Los códigos de contrato que no están en `cot.mercados` se ignoran y se cuentan en el log.
    """
    filas, desconocidos = _normalizar_filas(csv, informe, cot_conf, ts_descarga)
    if desconocidos:
        log.info("COT %s: %d filas con códigos desconocidos ignoradas", informe, desconocidos)
    return pa.Table.from_pylist(filas, schema=ESQUEMA_COT)


def _descargar(http: ClienteHttp, url: str) -> bytes:
    ruta = urlparse(url).path
    try:
        estado, cuerpo = http.get(url, TIMEOUT)
    except Exception as e:  # noqa: BLE001 - se re-lanza sin detalles de la URL
        raise RuntimeError(f"Falló la descarga de {ruta} ({type(e).__name__})") from None
    if estado != 200:
        raise RuntimeError(f"Falló la descarga de {ruta} (HTTP {estado})")
    return cuerpo


def _mezclar(raiz: Path, nuevas: pd.DataFrame, mercado: str, anio: int, ts_version: datetime,
             primera_vez: bool) -> dict[str, int]:
    """Mezcla las filas de un mercado y año con lo guardado. Devuelve filas nuevas por informe."""
    periodo = date(anio, 1, 1)
    ruta = raiz / ruta_relativa("cot", "cftc", mercado, periodo)
    previas = (pq.read_table(ruta).to_pandas() if ruta.exists()
               else ESQUEMA_COT.empty_table().to_pandas())
    nuevas = nuevas.drop_duplicates(subset=CLAVE, keep="last")
    res = versionar(previas, nuevas, CLAVE, ["valor"], ts_version)
    agregadas = res.iloc[len(previas):].copy()
    if agregadas.empty:
        return {}
    if primera_vez:   # carga histórica: la versión no puede ser anterior a la publicación
        res.loc[agregadas.index, "ts_version"] = agregadas["ts_conocido"]
    tabla = pa.Table.from_pandas(res[ESQUEMA_COT.names], schema=ESQUEMA_COT, preserve_index=False)
    escribir(raiz, tabla, "cot", "cftc", mercado, periodo)
    return agregadas.groupby("informe").size().to_dict()


def bajar_cot(cfg: Config, anios: list[int] | None = None, http: ClienteHttp | None = None,
              ahora: datetime | None = None, primera_vez: bool = False) -> dict[str, int]:
    """Baja los zips de la CFTC, guarda el crudo y mezcla. Devuelve {informe: filas_nuevas}.

    Por defecto el año actual y el anterior; con `primera_vez` toda la historia (legacy desde
    1986, disaggregated y tff desde 2006) y `ts_version` = `ts_conocido`.
    """
    ahora = (ahora or datetime.now(timezone.utc)).astimezone(timezone.utc)
    http = http or ClienteRequests()
    raiz = Path(cfg.data_dir)
    cot_conf = cargar_contexto()["cot"]
    actual = ahora.year
    cuenta = {informe: 0 for informe in URLS_COT}
    tablas, desconocidos = [], {}
    for informe, plantilla in URLS_COT.items():
        años = (anios if anios is not None
                else range(PRIMER_ANIO[informe], actual + 1) if primera_vez
                else [actual - 1, actual])
        for anio in años:
            cuerpo = _descargar(http, plantilla.format(anio=anio))
            escribir_crudo(raiz, cuerpo, "cftc", f"{informe}_{anio}", ahora, "zip")
            filas, desc = _normalizar_filas(_texto_del_zip(cuerpo), informe, cot_conf, ahora)
            if desc:
                desconocidos[informe] = desconocidos.get(informe, 0) + desc
            tablas.append(pa.Table.from_pylist(filas, schema=ESQUEMA_COT))
    for informe, n in desconocidos.items():
        log.info("COT %s: %d filas con códigos desconocidos ignoradas", informe, n)
    df = pa.concat_tables(tablas).to_pandas()
    if df.empty:
        return cuenta
    df["_anio"] = [f.year for f in df["fecha_informe"]]
    for (mercado, anio), grupo in df.groupby(["mercado", "_anio"]):
        for informe, n in _mezclar(raiz, grupo.drop(columns="_anio"), mercado, int(anio),
                                   ahora, primera_vez).items():
            cuenta[informe] += n
    return cuenta
