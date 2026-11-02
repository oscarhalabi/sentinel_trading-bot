r"""Vigilante: relanza los motores nuevos que se hayan caido.

Compara los motores que DEBERIAN estar corriendo (cada instances\*.env con
BOTS=...) con los que estan corriendo de verdad (lanzadores run_motor.ps1 de
este repo) y abre en una ventana nueva los que faltan.

Solo mira procesos de este repo (scripts.procs.select_ours): nunca toca
produccion. Pensado para la tarea programada SentinelN1Vigilante.

Que cuenta como "corriendo": el proceso de PowerShell de run_motor.ps1 con ese
-EnvFile y -BotId, o el propio python de motor.run, que run_motor.ps1 lanza con
--env-file <ruta absoluta> --bot-id <motor> (asi tambien se reconoce un motor
arrancado a mano desde una consola abierta o con una ruta relativa). Por eso:
  - Un motor que se cae con error (python sale con codigo distinto de 0) se
    relanza: run_motor.ps1 cierra tambien su PowerShell, aunque se abriera con
    -NoExit (start_all.ps1).
  - Un motor detenido limpiamente (Ctrl+C, codigo 0) en una ventana -NoExit NO
    se relanza hasta que se cierre esa ventana: el PowerShell sigue vivo.
  - Un motor lanzado con el PowerShell cerrado (o lanzado por el propio
    vigilante, sin -NoExit) se relanza mientras su .env lo liste en BOTS.
Para pararlo de forma permanente, quitalo de BOTS o apagalo desde el dashboard
(is_active=false lo deja vivo en modo solo-cerrar). Si por error se lanzan dos
motores iguales (mismo USER_ID y BOT_ID), el segundo ve el candado de motor.run
y sale sin operar.

Cada .env se valida igual que en start_all.ps1 (motor.registry.validate_env:
motores conocidos, magics sin choques y terminal/cuenta MT5 que no sean de
produccion). Un .env que no pasa se registra como [ERROR] y no se lanza. Si dos
.env tienen el mismo USER_ID y el mismo motor, solo se lanza el primero (por
orden alfabetico): el segundo chocaria siempre con el candado.

Limite anti-bucle: si un motor se ha relanzado 5 veces en 30 minutos (suele ser
que se cae al arrancar), el vigilante deja de relanzarlo y lo registra como
[ERROR] hasta que pase la ventana. El estado va en logs\vigilante_estado.json.

Uso:  python -m scripts.vigilante [--dry-run] [--instances DIR] [--root <ruta del repo>]

--root es solo una marca en la linea de comandos (como en motor.run): con ella
restart_motor.ps1 reconoce al vigilante como proceso de este repo. La tarea
SentinelN1Vigilante siempre la pasa.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

from motor.registry import instance_env_files, load_registry, read_env_file, validate_env
from scripts.procs import (CLONES_POR_DEFECTO, Proc, _norm, _raiz_repo, list_processes,
                           select_ours)

# Mismo criterio que Test-YaCorriendo de start_all.ps1.
_ENVFILE = re.compile(r'-envfile\s+"?([^"\s][^"]*?)"?\s+-botid\s+"?([\w.-]+)"?(?![\w.-])',
                      re.IGNORECASE)
# Marca que run_motor.ps1 pone en la linea de comandos del python de motor.run.
_ENVFILE_PY = re.compile(r'--env-file\s+"?([^"\s][^"]*?)"?\s+--bot-id\s+"?([\w.-]+)"?(?![\w.-])',
                         re.IGNORECASE)


def _log(msg: str, log_path: str | None) -> None:
    linea = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(linea)
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(linea + "\n")


def expected_instances(instances_dir: str, log_path: str | None = None) -> set[tuple[str, str]]:
    """Pares (env_path, bot_id) de los .env validos.

    Salta plantillas y borradores (example*, template*, _*) con la misma regla
    que start_all.ps1: motor.registry.instance_env_files.

    Un .env que no pasa motor.registry.validate_env (sin BOTS, motor
    desconocido, magics que chocan, terminal o cuenta de produccion...) se
    registra como [ERROR] y se salta entero: no se lanza nada de el y no tumba
    al vigilante. Un (USER_ID, motor) repetido en otro .env tambien se salta.
    """
    registro = load_registry()
    pares: set[tuple[str, str]] = set()
    vistos: dict[tuple[str, str], str] = {}
    for ruta in instance_env_files(instances_dir):
        nombre = os.path.basename(ruta)
        try:
            env = read_env_file(ruta)
            bots = validate_env(registro, env)
        except (SystemExit, OSError, ValueError) as e:
            _log(f"[ERROR] {nombre} no se lanza: {e}", log_path)
            continue
        usuario = (env.get("USER_ID") or "").strip().lower()
        for bot in bots:
            previo = vistos.get((usuario, bot)) if usuario else None
            if previo is not None:
                _log(f"[ERROR] {nombre}: el motor {bot} del usuario {usuario} "
                     f"ya lo lanza {previo}; no se duplica", log_path)
                continue
            if usuario:
                vistos[(usuario, bot)] = nombre
            pares.add((ruta, bot))
    return pares


def running_instances(procs: list[Proc]) -> set[tuple[str, str]]:
    """Pares (env_path, bot_id) de los motores vivos.

    Sale de la linea de comandos del PowerShell de run_motor.ps1 (-EnvFile /
    -BotId) o del python de motor.run (--env-file / --bot-id).
    """
    pares: set[tuple[str, str]] = set()
    for p in procs:
        nombre = p.name.lower()
        if nombre.startswith(("powershell", "pwsh")) and "run_motor.ps1" in p.cmdline.lower():
            m = _ENVFILE.search(p.cmdline)
        elif nombre.startswith("python") and "motor.run" in p.cmdline.lower():
            m = _ENVFILE_PY.search(p.cmdline)
        else:
            continue
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
    parser.add_argument("--root", default=None,
                        help="ruta absoluta de la raiz del repo (marca para identificar el proceso)")
    args = parser.parse_args(argv)

    repo = _raiz_repo()
    if args.root is not None and (os.path.normcase(os.path.realpath(args.root))
                                  != os.path.normcase(os.path.realpath(repo))):
        raise SystemExit(f"[ABORTADO] --root '{args.root}' no es la raiz de este repo ({repo}).")
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
