"""Carga y validación de la configuración de contexto (contexto.json y eventos.json)."""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from datos.config import ConfigInvalida

RUTA_CONTEXTO = Path(__file__).resolve().parent / "contexto.json"
RUTA_EVENTOS = Path(__file__).resolve().parent / "eventos.json"

_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_RETRASO = re.compile(r"^(habil\+1|mismo|lunes)@([01]\d|2[0-3]):[0-5]\d$")
_LATENCIA = re.compile(r"^\d+(\.\d+)?s$")
_DIVISA = re.compile(r"^[A-Z]{3}$")
_IMPACTOS = ("bajo", "medio", "alto")


def _leer(ruta: Path) -> dict:
    try:
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigInvalida(f"No existe el archivo de configuración: {ruta}") from None
    except json.JSONDecodeError as e:
        raise ConfigInvalida(f"{ruta} no es JSON válido: {e}") from None
    if not isinstance(datos, dict):
        raise ConfigInvalida(f"{ruta} debe ser un objeto JSON.")
    return datos


def _sin_notas(d: dict) -> dict:
    """Quita las claves que empiezan por '_' (comentarios)."""
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _exigir(cond: bool, ruta: str, mensaje: str) -> None:
    if not cond:
        raise ConfigInvalida(f"contexto.json: {ruta} {mensaje}")


def _hora(valor, ruta: str) -> None:
    _exigir(isinstance(valor, str) and bool(_HORA.match(valor)), ruta,
            f"debe ser HH:MM, no {valor!r}.")


def _zona(valor, ruta: str) -> None:
    try:
        ZoneInfo(valor)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        raise ConfigInvalida(f"contexto.json: {ruta} no es una zona horaria válida: {valor!r}.") from None


def _fecha(valor, ruta: str) -> None:
    try:
        date.fromisoformat(valor)
    except (ValueError, TypeError):
        raise ConfigInvalida(f"contexto.json: {ruta} debe ser AAAA-MM-DD, no {valor!r}.") from None


def _lista_series(ctx: dict, clave: str, validar_item) -> None:
    items = ctx.get(clave)
    _exigir(isinstance(items, list) and items, clave, "debe ser una lista con elementos.")
    for i, it in enumerate(items):
        ruta = f"{clave}[{i}]"
        _exigir(isinstance(it, dict), ruta, "debe ser un objeto.")
        _exigir(isinstance(it.get("id"), str) and it["id"], f"{ruta}.id", "falta o está vacío.")
        validar_item(it, ruta)


def _item_alfred(it: dict, ruta: str) -> None:
    _hora(it.get("hora"), f"{ruta}.hora")
    _zona(it.get("zona"), f"{ruta}.zona")
    r = it.get("retraso_tipico_dias")
    _exigir(isinstance(r, int) and not isinstance(r, bool) and r >= 0,
            f"{ruta}.retraso_tipico_dias", f"debe ser un entero de 0 o más, no {r!r}.")


def _item_fred(it: dict, ruta: str) -> None:
    r = it.get("retraso")
    _exigir(isinstance(r, str) and bool(_RETRASO.match(r)), f"{ruta}.retraso",
            f"debe ser 'habil+1@HH:MM', 'mismo@HH:MM' o 'lunes@HH:MM', no {r!r}.")


def cargar_contexto(ruta: Path | str = RUTA_CONTEXTO) -> dict:
    """Lee contexto.json, valida forma y valores, y devuelve el diccionario."""
    ctx = _leer(Path(ruta))

    divisas = ctx.get("divisas_calendario")
    _exigir(isinstance(divisas, list) and bool(divisas)
            and all(isinstance(d, str) and _DIVISA.match(d) for d in divisas),
            "divisas_calendario", "debe ser una lista de códigos de 3 letras mayúsculas.")
    _exigir(ctx.get("impacto_min") in _IMPACTOS, "impacto_min",
            f"debe ser uno de {', '.join(_IMPACTOS)}.")
    h = ctx.get("horizonte_anuncio_dias")
    _exigir(isinstance(h, int) and not isinstance(h, bool) and h >= 0,
            "horizonte_anuncio_dias", "debe ser un entero de 0 o más.")
    lat = ctx.get("latencia_por_defecto")
    _exigir(isinstance(lat, str) and bool(_LATENCIA.match(lat)), "latencia_por_defecto",
            f"debe tener formato como '5s', no {lat!r}.")

    _lista_series(ctx, "series_alfred", _item_alfred)
    _lista_series(ctx, "series_fred", _item_fred)

    cot = ctx.get("cot")
    _exigir(isinstance(cot, dict), "cot", "debe ser un objeto.")
    mercados = cot.get("mercados")
    _exigir(isinstance(mercados, dict) and bool(_sin_notas(mercados)), "cot.mercados",
            "debe ser un objeto nombre → código CFTC.")
    for nombre, codigo in _sin_notas(mercados).items():
        _exigir(isinstance(codigo, str) and bool(codigo), f"cot.mercados.{nombre}",
                "debe ser un código CFTC (texto).")
    campos = cot.get("campos")
    _exigir(isinstance(campos, list) and bool(campos) and all(isinstance(c, str) for c in campos),
            "cot.campos", "debe ser una lista de textos.")
    exc = cot.get("excepciones_publicacion")
    _exigir(isinstance(exc, dict), "cot.excepciones_publicacion", "debe ser un objeto.")
    for dia, pub in _sin_notas(exc).items():
        _fecha(dia, f"cot.excepciones_publicacion[{dia!r}] (clave)")
        try:
            datetime.strptime(pub, "%Y-%m-%dT%H:%M")
        except (ValueError, TypeError):
            raise ConfigInvalida(f"contexto.json: cot.excepciones_publicacion.{dia} "
                                 f"debe ser AAAA-MM-DDTHH:MM, no {pub!r}.") from None
    prim = cot.get("primera_publicacion")
    _exigir(isinstance(prim, dict), "cot.primera_publicacion", "debe ser un objeto.")
    for informe in ("disaggregated", "tff"):
        _fecha(prim.get(informe), f"cot.primera_publicacion.{informe}")

    etf = ctx.get("etf")
    _exigir(isinstance(etf, dict) and bool(etf), "etf", "debe ser un objeto con fondos.")
    for fondo, conf in etf.items():
        _exigir(isinstance(conf, dict), f"etf.{fondo}", "debe ser un objeto.")
        _hora(conf.get("hora"), f"etf.{fondo}.hora")
    return ctx


def cargar_eventos(ruta: Path | str = RUTA_EVENTOS) -> dict:
    """Lee eventos.json (ignora claves que empiezan por '_') y valida cada entrada."""
    mapa = _sin_notas(_leer(Path(ruta)))
    for clave, v in mapa.items():
        fuente, sep, nombre = clave.partition(":")
        if not (sep and fuente and nombre):
            raise ConfigInvalida(f"eventos.json: la clave {clave!r} debe ser '<fuente>:<nombre original>'.")
        if not (isinstance(v, dict) and isinstance(v.get("evento"), str) and v["evento"]
                and isinstance(v.get("categoria"), str) and v["categoria"]):
            raise ConfigInvalida(f"eventos.json: {clave!r} debe tener 'evento' y 'categoria' (texto).")
    return mapa


def normalizar_evento(fuente: str, nombre: str, mapa: dict) -> tuple[str, str]:
    """Devuelve (evento, categoria). Si no está en el mapa: (nombre, 'otros')."""
    e = mapa.get(f"{fuente}:{nombre}")
    if e is None:
        return nombre, "otros"
    return e["evento"], e["categoria"]
