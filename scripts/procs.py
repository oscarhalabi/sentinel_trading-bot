"""Seleccion de los procesos que son de ESTE repo (y solo de este).

Sirve para que restart_motor.ps1 pueda reiniciar los motores nuevos sin tocar
nunca los de produccion (bot.lite_e2, news_exporter, terminales de
C:/MT5_instances). Aqui no se mata nada: solo se lista y se filtra.

Uso:  python -m scripts.procs list --json
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass

CLONES_POR_DEFECTO = r"C:\MT5_instances_n1"

# Un proceso python es nuestro si su linea de comandos lleva alguna de estas marcas
# Y la ruta de este repo.
MARCAS_PYTHON = ("motor.run", "scripts.vigilante")
MARCAS_POWERSHELL = ("run_motor.ps1",)


@dataclass
class Proc:
    pid: int
    name: str
    cmdline: str
    exe: str


def _norm(ruta: str) -> str:
    """Minusculas, barras a '\' y sin comillas ni barra final."""
    return (ruta or "").replace("/", "\\").lower().strip().strip('"').rstrip("\\")


def _contiene_ruta(texto: str, ruta: str) -> bool:
    """True si 'ruta' aparece en 'texto' como carpeta completa (no como prefijo de otra)."""
    r = _norm(ruta)
    if not r:
        return False
    patron = r"(?<![\w\-.])" + re.escape(r) + r"(?![\w\-.])"
    return re.search(patron, _norm_texto(texto)) is not None


def _norm_texto(texto: str) -> str:
    return (texto or "").replace("/", "\\").lower()


def _bajo_carpeta(exe: str, carpeta: str) -> bool:
    e, c = _norm(exe), _norm(carpeta)
    return bool(e) and bool(c) and e.startswith(c + "\\")


def select_ours(procs: list[Proc], repo_root: str, clones_dir: str) -> list[Proc]:
    """Filtra los procesos de este repo: motores, lanzadores y terminales MT5 propios."""
    nuestros: list[Proc] = []
    for p in procs:
        nombre = p.name.lower()
        cmd = _norm_texto(p.cmdline)
        if nombre.startswith("python"):
            marcas = MARCAS_PYTHON
        elif nombre.startswith(("powershell", "pwsh")):
            marcas = MARCAS_POWERSHELL
        elif nombre.startswith("terminal64"):
            if _bajo_carpeta(p.exe, clones_dir):
                nuestros.append(p)
            continue
        else:
            continue
        if any(m in cmd for m in marcas) and _contiene_ruta(p.cmdline, repo_root):
            nuestros.append(p)
    return nuestros


def list_processes() -> list[Proc]:
    """Lee los procesos del sistema con Get-CimInstance (solo lectura)."""
    ps = ("Get-CimInstance Win32_Process | "
          "Select-Object ProcessId,Name,CommandLine,ExecutablePath | ConvertTo-Json -Compress")
    salida = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if not salida:
        return []
    datos = json.loads(salida)
    if isinstance(datos, dict):
        datos = [datos]
    return [Proc(int(d["ProcessId"]), d.get("Name") or "",
                 d.get("CommandLine") or "", d.get("ExecutablePath") or "")
            for d in datos]


def _raiz_repo() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.procs")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="lista los procesos propios de este repo")
    ls.add_argument("--json", action="store_true", help="salida en JSON")
    args = parser.parse_args(argv)

    clones = os.environ.get("MT5_CLONES_DIR") or CLONES_POR_DEFECTO
    propios = select_ours(list_processes(), _raiz_repo(), clones)
    if args.json:
        print(json.dumps([asdict(p) for p in propios], ensure_ascii=False))
    else:
        for p in propios:
            print(f"{p.pid}\t{p.name}\t{p.cmdline or p.exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
