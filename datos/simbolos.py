"""Registro de símbolos: qué se descarga, de dónde y con qué reglas."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from datos.horarios import HORARIOS

CLASES = frozenset({"metal", "fx_mayor", "fx_cruce", "fx_exotico", "indice", "energia",
                    "materia_prima", "accion", "etf", "cripto"})
RUTA_REGISTRO = Path(__file__).resolve().parent / "simbolos.json"


class RegistroInvalido(SystemExit):
    """Registro mal formado. Al ser SystemExit, la CLI muestra el mensaje sin traza."""


@dataclass(frozen=True)
class Simbolo:
    simbolo: str
    clase: str
    prioridad: int
    fuentes: dict[str, str]
    decimales: int
    horario: str
    divisa: str
    activo: bool = True
    ajustado_a_mano: tuple[str, ...] = ()


def _error(nombre, campo, detalle) -> RegistroInvalido:
    return RegistroInvalido(f"Registro de símbolos: {nombre}, campo '{campo}': {detalle}")


def _es_entero(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validar(crudo, vistos: set[str]) -> Simbolo:
    if not isinstance(crudo, dict):
        raise RegistroInvalido(f"Registro de símbolos: entrada {crudo!r} no es un objeto.")
    nombre = crudo.get("simbolo")
    if not isinstance(nombre, str) or not nombre.strip():
        raise RegistroInvalido(f"Registro de símbolos: entrada sin campo 'simbolo' válido: {crudo!r}")
    if nombre in vistos:
        raise _error(nombre, "duplicado", "el nombre aparece más de una vez")
    vistos.add(nombre)

    clase = crudo.get("clase")
    if clase not in CLASES:
        raise _error(nombre, "clase", f"{clase!r} no es una clase conocida")
    prioridad = crudo.get("prioridad")
    if not _es_entero(prioridad) or prioridad not in (1, 2):
        raise _error(nombre, "prioridad", f"debe ser 1 o 2, no {prioridad!r}")
    fuentes = crudo.get("fuentes")
    if (not isinstance(fuentes, dict) or not fuentes
            or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in fuentes.items())):
        raise _error(nombre, "fuentes", "debe ser un objeto no vacío {fuente: código}")
    decimales = crudo.get("decimales")
    if not _es_entero(decimales) or not 0 <= decimales <= 8:
        raise _error(nombre, "decimales", f"debe ser un entero entre 0 y 8, no {decimales!r}")
    horario = crudo.get("horario")
    if horario not in HORARIOS:
        raise _error(nombre, "horario", f"{horario!r} no es un horario conocido")
    divisa = crudo.get("divisa")
    if not isinstance(divisa, str) or not divisa:
        raise _error(nombre, "divisa", f"debe ser un texto no vacío, no {divisa!r}")
    activo = crudo.get("activo", True)
    if not isinstance(activo, bool):
        raise _error(nombre, "activo", f"debe ser true o false, no {activo!r}")
    ajustado = crudo.get("ajustado_a_mano", [])
    if not isinstance(ajustado, list) or not all(isinstance(x, str) for x in ajustado):
        raise _error(nombre, "ajustado_a_mano", "debe ser una lista de textos")
    return Simbolo(nombre, clase, prioridad, dict(fuentes), decimales, horario, divisa,
                   activo, tuple(ajustado))


def cargar(ruta: Path = RUTA_REGISTRO) -> dict[str, Simbolo]:
    """Lee y valida el registro completo. Aborta con un mensaje que nombra símbolo y campo."""
    try:
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RegistroInvalido(f"No se pudo leer el registro de símbolos {ruta}: {e}") from None
    if not isinstance(datos, dict) or not isinstance(datos.get("simbolos"), list):
        raise RegistroInvalido(f"El registro {ruta} debe ser un objeto con la lista 'simbolos'.")
    vistos: set[str] = set()
    resultado: dict[str, Simbolo] = {}
    for crudo in datos["simbolos"]:
        s = _validar(crudo, vistos)
        resultado[s.simbolo] = s
    return resultado


def guardar(simbolos: dict[str, Simbolo], ruta: Path = RUTA_REGISTRO) -> None:
    """Escribe el registro ordenado por prioridad y nombre, de forma segura (.tmp + rename)."""
    ruta = Path(ruta)
    ordenados = sorted(simbolos.values(), key=lambda s: (s.prioridad, s.simbolo))
    lista = [{"simbolo": s.simbolo, "clase": s.clase, "prioridad": s.prioridad,
              "fuentes": s.fuentes, "decimales": s.decimales, "horario": s.horario,
              "divisa": s.divisa, "activo": s.activo,
              "ajustado_a_mano": list(s.ajustado_a_mano)} for s in ordenados]
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text(json.dumps({"version": 1, "simbolos": lista}, indent=2, ensure_ascii=False)
                   + "\n", encoding="utf-8")
    os.replace(tmp, ruta)


def ordenar_para_descarga(simbolos: dict[str, Simbolo]) -> list[Simbolo]:
    """Solo activos; prioridad 1 antes que 2; después por nombre."""
    return sorted((s for s in simbolos.values() if s.activo),
                  key=lambda s: (s.prioridad, s.simbolo))


_INDICES_CANONICOS = {
    "USA500IDXUSD": "US500", "USATECHIDXUSD": "US100", "USA30IDXUSD": "US30",
    "DEUIDXEUR": "DE40", "GBRIDXGBP": "UK100", "JPNIDXJPY": "JP225",
}
_ENERGIA_CANONICA = {"LIGHTCMDUSD": "WTI", "BRENTCMDUSD": "BRENT"}


def _horario_de(inst) -> str:
    """Horario según la clase y la divisa del instrumento."""
    c = inst.clase
    if c in ("fx_mayor", "fx_cruce", "fx_exotico"):
        return "fx"
    if c == "indice":
        return ("indice_us" if inst.divisa == "USD"
                else "indice_asia" if inst.divisa in ("JPY", "HKD", "AUD") else "indice_eu")
    if c in ("accion", "etf"):
        return "accion_us" if inst.divisa == "USD" else "accion_eu"
    return "cripto_24x7" if c == "cripto" else c  # metal, energia y materia_prima se llaman igual


def nombre_canonico(inst) -> str:
    """Nombre propio del símbolo: divisas y metales tal cual, acciones <TICKER>.<PAÍS>."""
    if inst.clase in ("accion", "etf"):
        ticker = inst.nombre.split("/")[0].strip()
        if "." in ticker:
            return ticker
    return _INDICES_CANONICOS.get(inst.codigo) or _ENERGIA_CANONICA.get(inst.codigo) or inst.codigo


def fusionar_descubiertos(actuales: dict[str, Simbolo], descubiertos: list,
                          fuente: str) -> tuple[dict[str, Simbolo], list[str], list[str]]:
    """Mezcla lo descubierto con el registro. Devuelve (registro nuevo, nuevos, desaparecidos).

    Los campos de `ajustado_a_mano` no se pisan; los nuevos entran con prioridad 2;
    los que ya no aparecen quedan inactivos (nunca se borran).
    """
    nuevo = dict(actuales)
    nuevos: list[str] = []
    vistos: set[str] = set()
    for inst in descubiertos:
        nombre = nombre_canonico(inst)
        vistos.add(nombre)
        horario = _horario_de(inst)
        actual = actuales.get(nombre)
        if actual is None:
            nuevo[nombre] = Simbolo(nombre, inst.clase, 2, {fuente: inst.codigo},
                                    inst.decimales, horario, inst.divisa)
            nuevos.append(nombre)
            continue
        campos = {"clase": inst.clase, "decimales": inst.decimales, "horario": horario,
                  "divisa": inst.divisa}
        campos = {k: v for k, v in campos.items() if k not in actual.ajustado_a_mano}
        nuevo[nombre] = replace(actual, fuentes={**actual.fuentes, fuente: inst.codigo}, **campos)
    desaparecidos = sorted(n for n, s in actuales.items()
                           if fuente in s.fuentes and n not in vistos and s.activo)
    for n in desaparecidos:
        nuevo[n] = replace(nuevo[n], activo=False)
    return nuevo, sorted(nuevos), desaparecidos
