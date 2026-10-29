r"""Vigilante: relanza los motores nuevos que se hayan caido.

Compara los motores que DEBERIAN estar corriendo (cada instances\*.env con
BOTS=...) con los que estan corriendo de verdad (lanzadores run_motor.ps1 de
este repo) y abre en una ventana nueva los que faltan.

Solo mira procesos de este repo (scripts.procs.select_ours): nunca toca
produccion. Pensado para la tarea programada SentinelN1Vigilante.

Un motor parado a mano se relanza mientras su .env lo liste en BOTS. Para
pararlo de forma permanente, quitalo de BOTS o apagalo desde el dashboard
(is_active=false lo deja vivo en modo solo-cerrar).

Limite anti-bucle: si un motor se ha relanzado 5 veces en 30 minutos (suele ser
que se cae al arrancar), el vigilante deja de relanzarlo y lo registra como
[ERROR] hasta que pase la ventana. El estado va en logs\vigilante_estado.json.

Uso:  python -m scripts.vigilante [--dry-run] [--instances DIR]
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

from motor.registry import load_registry, parse_bots, read_env_file
from scripts.procs import (CLONES_POR_DEFECTO, Proc, _raiz_repo, list_processes,
                           select_ours)

# Mismo criterio que Test-YaCorriendo de start_all.ps1.
_ENVFILE = re.compile(r'-envfile\s+"?([^"\s][^"]*?)"?\s+-botid\s+"?([\w.-]+)"?(?![\w.-])',
                      re.IGNORECASE)


def _norm(ruta: str) -> str:
    return ruta.replace("/", "\\").lower().strip().rstrip("\\")


def _log(msg: str, log_path: str | None) -> None:
    linea = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(linea)
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(linea + "\n")


def expected_instances(instances_dir: str, log_path: str | None = None) -> set[tuple[str, str]]:
    """Pares (env_path, bot_id) de los .env con BOTS. Salta example.env y los que empiezan por '_'.

    Un .env roto (sin BOTS, vacio o con un motor que no existe) se avisa en el
    log y se salta: no tumba al vigilante.
    """
    registro = load_registry()
    pares: set[tuple[str, str]] = set()
    for ruta in sorted(glob.glob(os.path.join(instances_dir, "*.env"))):
        nombre = os.path.basename(ruta).lower()
        if nombre == "example.env" or nombre.startswith("_"):
            continue
        try:
            bots = parse_bots(read_env_file(ruta).get("BOTS"))
        except (SystemExit, OSError) as e:
            _log(f"[AVISO] {os.path.basename(ruta)} se salta: {e}", log_path)
            continue
        for bot in bots:
            if bot not in registro:
                _log(f"[AVISO] {os.path.basename(ruta)}: motor '{bot}' no esta en el registro, se salta", log_path)
                continue
            pares.add((ruta, bot))
    return pares


def running_instances(procs: list[Proc]) -> set[tuple[str, str]]:
    """Pares (env_path, bot_id) sacados de la linea de comandos de run_motor.ps1."""
    pares: set[tuple[str, str]] = set()
    for p in procs:
        if not p.name.lower().startswith(("powershell", "pwsh")):
            continue
        if "run_motor.ps1" not in p.cmdline.lower():
            continue
        m = _ENVFILE.search(p.cmdline)
        if m:
            pares.add((m.group(1), m.group(2).lower()))
    return pares


def missing(expected: set[tuple[str, str]], running: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Esperados que no estan corriendo (rutas sin mayusculas ni barras)."""
    vivos = {(_norm(e), b.lower()) for e, b in running}
    return {(e, b) for e, b in expected if (_norm(e), b.lower()) not in vivos}


MAX_REINTENTOS = 5
VENTANA_S = 1800


def should_relaunch(history: list[float], now: float, max_n: int = MAX_REINTENTOS,
                    window_s: int = VENTANA_S) -> bool:
    """False si ya hubo max_n relanzamientos o mas dentro de la ventana."""
    return sum(1 for t in history if now - t < window_s) < max_n


def _cargar_estado(path: str) -> dict[str, list[float]]:
    try:
        with open(path, encoding="utf-8") as f:
            datos = json.load(f)
        return {k: [float(t) for t in v] for k, v in datos.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _guardar_estado(path: str, estado: dict[str, list[float]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(estado, f)


def _lanzar(repo: str, env_path: str, bot: str) -> None:
    """Abre run_motor.ps1 en una ventana nueva (ruta absoluta, como start_all.ps1)."""
    runner = os.path.join(repo, "scripts", "run_motor.ps1")
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", runner,
         "-EnvFile", env_path, "-BotId", bot, "-Mode", "lite"],
        cwd=repo, creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.vigilante")
    parser.add_argument("--dry-run", action="store_true", help="solo loguea, no lanza nada")
    parser.add_argument("--instances", default=None, help="carpeta con los .env (por defecto instances)")
    args = parser.parse_args(argv)

    repo = _raiz_repo()
    log_path = os.path.join(repo, "logs", "vigilante.log")
    instancias = args.instances or os.path.join(repo, "instances")
    clones = os.environ.get("MT5_CLONES_DIR") or CLONES_POR_DEFECTO

    esperadas = expected_instances(instancias, log_path)
    vivas = running_instances(select_ours(list_processes(), repo, clones))
    faltan = sorted(missing(esperadas, vivas))
    if not faltan:
        _log(f"todo en orden: {len(esperadas)} motor(es) esperados, ninguno falta", log_path)
        return 0
    estado_path = os.path.join(repo, "logs", "vigilante_estado.json")
    ahora = time.time()
    estado = _cargar_estado(estado_path)
    # Se descartan las marcas fuera de la ventana.
    estado = {k: [t for t in v if ahora - t < VENTANA_S] for k, v in estado.items()}
    estado = {k: v for k, v in estado.items() if v}
    for env_path, bot in faltan:
        clave = f"{_norm(env_path)}|{bot}"
        historial = estado.get(clave, [])
        if not should_relaunch(historial, ahora):
            _log(f"[ERROR] {bot} ({os.path.basename(env_path)}) desactivado por "
                 f"{len(historial)} reintentos en {VENTANA_S // 60} min; revisar log del motor", log_path)
            continue
        if args.dry_run:
            _log(f"[dry-run] faltaria relanzar {bot} ({os.path.basename(env_path)})", log_path)
            continue
        _log(f"relanzando {bot} ({os.path.basename(env_path)})", log_path)
        try:
            _lanzar(repo, env_path, bot)
            estado[clave] = historial + [ahora]
        except OSError as e:
            _log(f"[ERROR] no se pudo lanzar {bot}: {e}", log_path)
    if not args.dry_run:
        _guardar_estado(estado_path, estado)
    return 0


if __name__ == "__main__":
    sys.exit(main())
