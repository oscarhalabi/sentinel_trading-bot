"""Órdenes `contexto bajar` y `contexto reprocesar`: datos de contexto (calendario, macro, COT...).

`bajar` llama a cada fuente por separado: si una falla, las demás siguen.
`reprocesar` rehace los Parquet de contexto desde los crudos guardados, sin red: aparta los de la
fuente, repite cada crudo por orden de descarga con el mismo `procesar_crudo` que usa la descarga
en vivo y, si algo se rompe a medias, devuelve lo apartado.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from datos.almacen import (leer_crudo, listar_crudos, listar_manifiestos, mover_fuente,
                           nombre_crudo, restaurar_fuente)
from datos.config import Config
from datos.contexto_conf import cargar_contexto
from datos.fuentes import calendario_api, cftc, forexfactory, fred, spdr
from datos.fuentes.base import ClienteHttp
from datos.simbolos import Simbolo

# Orden en que `contexto bajar` sin argumentos recorre las fuentes.
FUENTES_BAJAR = ("ff", "alfred", "fred", "cot", "gld", "calendario", "corporativo")
DIAS_ATRAS_CALENDARIO = 30
DIAS_ADELANTE_CALENDARIO = 14
TIPOS_CONTEXTO = ("calendario", "serie", "cot", "etf", "corporativo")

# Fuente de los crudos -> (cómo procesar un crudo, tipos de Parquet que salen de ella).
_PROCESADORES: dict[str, tuple[Callable, tuple[str, ...]]] = {
    "forexfactory": (forexfactory.procesar_crudo, ("calendario",)),
    "alfred": (fred.procesar_crudo, ("serie",)),
    "fred": (fred.procesar_crudo, ("serie",)),
    "cftc": (cftc.procesar_crudo, ("cot",)),
    "spdr": (spdr.procesar_crudo, ("etf",)),
    "fmp": (calendario_api.procesar_crudo, ("calendario", "corporativo")),
    "eodhd": (calendario_api.procesar_crudo, ("calendario", "corporativo")),
}
FUENTES_CRUDO = tuple(_PROCESADORES)
# Nombres cortos que también se aceptan en `reprocesar --fuente`.
ALIAS_CRUDO = {"ff": "forexfactory", "cot": "cftc", "gld": "spdr"}
PREFIJO_PRUEBA = "prueba_"   # crudos de `probar-calendario`: no son parte del almacén normal


@dataclass
class ResultadoBajar:
    filas: dict[str, int] = field(default_factory=dict)
    fallos: dict[str, str] = field(default_factory=dict)


def _sin_claves(cfg: Config, texto: str) -> str:
    for clave in (cfg.fred_api_key, cfg.fmp_api_key, cfg.eodhd_api_key, cfg.te_api_key):
        if clave:
            texto = texto.replace(clave, "***")
    return texto


def _total(r) -> int:
    return sum(r.values()) if isinstance(r, dict) else int(r)


def bajar_contexto(cfg: Config, fuentes: list[str], simbolos: list[Simbolo],
                   desde: date | None = None, historico: bool = False,
                   http: ClienteHttp | None = None, catalogo=None,
                   hoy: date | None = None) -> ResultadoBajar:
    """Baja cada fuente pedida en su propio intento. Devuelve filas nuevas y fallos por fuente.

    `desde` afecta a calendario (y a cot: años desde ese). `historico` solo a calendario.
    Con `catalogo`, anota en él cada Parquet de contexto escrito en esta corrida.
    """
    hoy = hoy or datetime.now(timezone.utc).date()
    ctx = cargar_contexto()
    tareas = {
        "ff": lambda: forexfactory.guardar_foto(cfg, http),
        "alfred": lambda: fred.bajar_series(cfg, http, ids=[s["id"] for s in ctx["series_alfred"]]),
        "fred": lambda: fred.bajar_series(cfg, http, ids=[s["id"] for s in ctx["series_fred"]]),
        "cot": lambda: cftc.bajar_cot(
            cfg, anios=list(range(desde.year, hoy.year + 1)) if desde else None, http=http),
        "gld": lambda: spdr.bajar_gld(cfg, http),
        "calendario": lambda: calendario_api.bajar_calendario(
            cfg, cfg.fuente_calendario,
            desde or hoy - timedelta(days=DIAS_ATRAS_CALENDARIO),
            hoy if historico else hoy + timedelta(days=DIAS_ADELANTE_CALENDARIO),
            historico, http=http),
        "corporativo": lambda: calendario_api.bajar_corporativos(
            cfg, cfg.fuente_corporativo, simbolos, http=http),
    }
    res = ResultadoBajar()
    inicio = datetime.now(timezone.utc).replace(microsecond=0).isoformat(timespec="seconds")
    for nombre in fuentes:
        try:
            res.filas[nombre] = _total(tareas[nombre]())
        except (Exception, SystemExit) as e:  # noqa: BLE001 - ConfigInvalida es SystemExit
            res.fallos[nombre] = _sin_claves(cfg, str(e) or type(e).__name__)
    if catalogo is not None:
        for tipo in TIPOS_CONTEXTO:
            for m in listar_manifiestos(cfg.data_dir, tipo):
                if m.descargado_en >= inicio:
                    catalogo.registrar(m)
    return res


def imprimir(res: ResultadoBajar, fuentes: list[str]) -> None:
    print("Contexto:")
    for nombre in fuentes:
        if nombre in res.fallos:
            print(f"  {nombre}: FALLÓ ({res.fallos[nombre]})")
        else:
            print(f"  {nombre}: {res.filas.get(nombre, 0)} filas nuevas")
    if res.fallos:
        print(f"Fallaron {len(res.fallos)} de {len(fuentes)} fuentes.")
    else:
        print("Todas las fuentes terminaron bien.")


# --- reprocesar ------------------------------------------------------------

def _orden_crudo(m) -> tuple:
    """Por momento de descarga; a igual momento, por nombre con los números en orden natural
    (así `_p2` va antes que `_p10`)."""
    partes = re.split(r"(\d+)", nombre_crudo(m))
    return (m.ts_min or "", [int(p) if p.isdigit() else p for p in partes])


@dataclass
class ResultadoReprocesar:
    crudos: int = 0
    filas: int = 0
    fallidos: list[str] = field(default_factory=list)
    restaurado: bool = False   # True: algo falló y el almacén quedó como estaba antes


def reprocesar(cfg: Config, fuentes: list[str] | None = None) -> ResultadoReprocesar:
    """Rehace los Parquet de contexto de `fuentes` (todas si es None) desde sus crudos, sin red.

    Solo se tocan los tipos de cada fuente (nunca ticks, M1 ni otras fuentes). Lo anterior se
    aparta en `_respaldo/` y se borra al terminar bien. Si algo se interrumpe o algún crudo no se
    puede procesar, se devuelve todo lo apartado tal cual (`restaurado`) y se listan los fallidos.
    """
    raiz = Path(cfg.data_dir)
    fuentes = list(fuentes or FUENTES_CRUDO)
    objetivos = sorted({(tipo, f) for f in fuentes for tipo in _PROCESADORES[f][1]})
    respaldo = raiz / "_respaldo" / f"reprocesar-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}"
    res = ResultadoReprocesar()
    procesando = False
    try:
        for tipo, f in objetivos:
            mover_fuente(raiz, respaldo, tipo, f)
        procesando = True
        crudos = sorted((m for f in fuentes for m in listar_crudos(raiz, f)), key=_orden_crudo)
        for m in crudos:
            if nombre_crudo(m).startswith(PREFIJO_PRUEBA):
                continue
            procesar = _PROCESADORES[m.fuente][0]
            try:
                res.filas += procesar(cfg, leer_crudo(raiz, m.ruta), m)
                res.crudos += 1
            except Exception as e:  # noqa: BLE001 - seguimos para listar todos los que fallan
                res.fallidos.append(f"{m.ruta} ({_sin_claves(cfg, str(e))})")
    except BaseException:
        # Se devuelve lo apartado tal como estaba (también con Ctrl+C o si apartar falló a medias).
        _restaurar(raiz, respaldo, objetivos, procesando)
        raise
    if res.fallidos:
        # Con un crudo fallido el resultado quedaría incompleto: se deja todo como estaba.
        _restaurar(raiz, respaldo, objetivos, True)
        res.restaurado = True
    _limpiar_respaldo(raiz, respaldo)
    return res


def _restaurar(raiz: Path, respaldo: Path, objetivos, borrar_nuevo: bool) -> None:
    for tipo, f in objetivos:
        restaurar_fuente(respaldo, raiz, tipo, f, borrar_nuevo)
    _limpiar_respaldo(raiz, respaldo)


def _limpiar_respaldo(raiz: Path, respaldo: Path) -> None:
    shutil.rmtree(respaldo, ignore_errors=True)
    carpeta = raiz / "_respaldo"
    if carpeta.is_dir() and not any(carpeta.iterdir()):
        carpeta.rmdir()
