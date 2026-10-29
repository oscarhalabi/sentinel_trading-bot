"""Registro de motores: fuente unica de identidad (bot_id, magic, simbolos).

Los motores se declaran en engines.json (junto a este archivo, no depende del
directorio desde donde se lance el bot). Aqui se cargan y se validan.
"""

import importlib
import json
import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engines.json")

# Magics de los bots de produccion: ningun motor nuevo puede usarlos.
PRODUCTION_MAGIC_ENVS = ("MAGIC_M15", "MAGIC_M5", "MAGIC_E2")


@dataclass(frozen=True)
class EngineSpec:
    bot_id: str
    engine_class: str          # "modulo:Clase"
    magic_env: str             # variable de entorno que puede cambiar el magic
    magic_default: int
    symbols: tuple[str, ...] | None   # None = usar el SYMBOL del .env
    description: str


def load_registry(path: str | None = None) -> dict[str, EngineSpec]:
    """Lee engines.json y devuelve {bot_id: EngineSpec}."""
    with open(path or DEFAULT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    reg: dict[str, EngineSpec] = {}
    for e in data["engines"]:
        symbols = e.get("symbols")
        spec = EngineSpec(
            bot_id=e["bot_id"],
            engine_class=e["engine_class"],
            magic_env=e["magic_env"],
            magic_default=int(e["magic_default"]),
            symbols=tuple(symbols) if symbols else None,
            description=e.get("description", ""),
        )
        reg[spec.bot_id] = spec
    return reg


def magic_for(spec: EngineSpec, env: Mapping[str, str]) -> int:
    """Magic del motor: el del entorno si existe, si no el por defecto."""
    value = env.get(spec.magic_env)
    return int(value) if value else spec.magic_default


def parse_bots(value: str | None) -> list[str]:
    """Separa BOTS por comas, pasa a minusculas y quita vacios."""
    bots = [b.strip().lower() for b in (value or "").split(",") if b.strip()]
    if not bots:
        raise SystemExit("[ABORTADO] BOTS esta vacio: indica al menos un motor (ej. BOTS=n1).")
    return bots


def validate_instance(registry: dict[str, EngineSpec], bots: list[str],
                      env: Mapping[str, str]) -> None:
    """Aborta si hay un bot desconocido o si los magics se pisan."""
    desconocidos = [b for b in bots if b not in registry]
    if desconocidos:
        raise SystemExit(
            f"[ABORTADO] Bot(s) desconocido(s) en BOTS: {', '.join(desconocidos)}. "
            f"Disponibles: {', '.join(sorted(registry))}."
        )
    magics: dict[int, str] = {}
    for b in bots:
        m = magic_for(registry[b], env)
        if m in magics:
            raise SystemExit(
                f"[ABORTADO] Los bots {magics[m]} y {b} comparten el magic {m}."
            )
        magics[m] = b
    for name in PRODUCTION_MAGIC_ENVS:
        value = env.get(name)
        if value and int(value) in magics:
            raise SystemExit(
                f"[ABORTADO] El magic {value} del bot {magics[int(value)]} "
                f"choca con {name} de produccion."
            )


def symbols_for(spec: EngineSpec, default_symbol: str) -> tuple[str, ...]:
    """Simbolos del motor; si no declara, el SYMBOL del .env."""
    return spec.symbols if spec.symbols else (default_symbol,)


def load_engine_class(spec: EngineSpec) -> type:
    """Resuelve 'modulo:Clase' con importlib (solo al usarla, no al importar)."""
    modulo, _, clase = spec.engine_class.partition(":")
    return getattr(importlib.import_module(modulo), clase)
