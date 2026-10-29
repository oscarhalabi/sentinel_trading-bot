"""Registro de motores: fuente unica de identidad (bot_id, magic, simbolos).

Los motores se declaran en engines.json (junto a este archivo, no depende del
directorio desde donde se lance el bot). Aqui se cargan y se validan.
"""

import glob
import importlib
import json
import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engines.json")

# Magics de los bots de produccion: ningun motor nuevo puede usarlos. Si el .env
# no los trae, valen los mismos por defecto que usa produccion (bot/config.py).
PRODUCTION_MAGIC_DEFAULTS = {"MAGIC_M15": 100100, "MAGIC_M5": 100200, "MAGIC_E2": 100300}
PRODUCTION_MAGIC_ENVS = tuple(PRODUCTION_MAGIC_DEFAULTS)

# Archivos de instances\ que NO son instancias (plantillas y borradores).
# Regla unica: la usan el vigilante, start_all.ps1 (via "list") y el provisionador.
PREFIJOS_NO_INSTANCIA = ("example", "template", "_")


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
    for name, defecto in PRODUCTION_MAGIC_DEFAULTS.items():
        value = int(env.get(name) or defecto)
        if value in magics:
            raise SystemExit(
                f"[ABORTADO] El magic {value} del bot {magics[value]} "
                f"choca con {name} de produccion."
            )


def assert_usuario_propio(client, user_id: str, registry: dict[str, EngineSpec]) -> None:
    """Aborta si el usuario ya tiene instancias de motores que no son de este bot.

    Un usuario con filas bot_instances de m15/m5/e2 (o cualquier bot_id fuera de
    engines.json) es de produccion: el bot nuevo no debe operar ni dar de alta
    sobre el.
    """
    filas = (client.table("bot_instances").select("bot_id")
             .eq("user_id", user_id).execute().data) or []
    ajenos = sorted({str(f.get("bot_id")) for f in filas if f.get("bot_id") not in registry})
    if ajenos:
        raise SystemExit(
            f"[ABORTADO] Este usuario pertenece a produccion (motores: {', '.join(ajenos)}); "
            f"usa un usuario nuevo."
        )


def es_env_de_instancia(nombre: str) -> bool:
    """True si el archivo es el .env de una instancia (no plantilla ni borrador)."""
    n = os.path.basename(nombre).lower()
    return n.endswith(".env") and not n.startswith(PREFIJOS_NO_INSTANCIA)


def instance_env_files(instances_dir: str) -> list[str]:
    """Rutas de los .env de instancia de la carpeta, en orden alfabetico."""
    rutas = glob.glob(os.path.join(instances_dir, "*.env"))
    return sorted((r for r in rutas if es_env_de_instancia(r)), key=lambda r: r.lower())


def symbols_for(spec: EngineSpec, default_symbol: str) -> tuple[str, ...]:
    """Simbolos del motor; si no declara, el SYMBOL del .env."""
    return spec.symbols if spec.symbols else (default_symbol,)


def load_engine_class(spec: EngineSpec) -> type:
    """Resuelve 'modulo:Clase' con importlib (solo al usarla, no al importar)."""
    modulo, _, clase = spec.engine_class.partition(":")
    return getattr(importlib.import_module(modulo), clase)


def read_env_file(path: str) -> dict[str, str]:
    """Lee un .env (KEY=VALUE por linea) igual que lo hace run_motor.ps1."""
    env: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            env[clave.strip()] = valor.strip().strip('"').strip("'")
    return env


def main(argv: list[str] | None = None) -> int:
    """Mini-CLI para los scripts .ps1.

    magic <bot_id>    imprime el magic del motor (lee os.environ).
    check <envfile>   valida BOTS y los magics del .env (sale con error si algo no cuadra).
    list <carpeta>    imprime los .env de instancia de la carpeta (uno por linea).
    """
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in ("magic", "check", "list"):
        raise SystemExit("Uso: python -m motor.registry magic <bot_id> | check <envfile> "
                         "| list <carpeta>")
    cmd, valor = args
    if cmd == "list":
        for ruta in instance_env_files(valor):
            print(ruta)
        return 0
    reg = load_registry()
    if cmd == "magic":
        bot_id = valor.strip().lower()
        if bot_id not in reg:
            raise SystemExit(
                f"[ABORTADO] Bot desconocido: {bot_id}. Disponibles: {', '.join(sorted(reg))}."
            )
        print(magic_for(reg[bot_id], os.environ))
        return 0
    if not os.path.isfile(valor):
        raise SystemExit(f"[ABORTADO] No existe el archivo de entorno: {valor}")
    env = read_env_file(valor)
    bots = parse_bots(env.get("BOTS"))
    validate_instance(reg, bots, env)
    print(f"[OK] {os.path.basename(valor)}: motores {', '.join(bots)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
