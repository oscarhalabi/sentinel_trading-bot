"""Pruebas de la configuración del paquete datos y de su CLI base."""
import ast
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from datos.config import ConfigInvalida, cargar_config

RAIZ = Path(__file__).resolve().parents[2]


def test_falta_data_dir_aborta():
    with pytest.raises(ConfigInvalida) as e:
        cargar_config({})
    assert "DATA_DIR" in str(e.value)


def test_data_dir_inexistente_aborta(tmp_path):
    ruta = tmp_path / "no_existe"
    with pytest.raises(ConfigInvalida) as e:
        cargar_config({"DATA_DIR": str(ruta)})
    assert str(ruta) in str(e.value)


def test_data_dir_que_es_archivo_aborta(tmp_path):
    archivo = tmp_path / "archivo.txt"
    archivo.write_text("x")
    with pytest.raises(ConfigInvalida):
        cargar_config({"DATA_DIR": str(archivo)})


def test_valores_por_defecto(tmp_path):
    cfg = cargar_config({"DATA_DIR": str(tmp_path)})
    assert cfg.data_dir == tmp_path
    assert cfg.disco_min_gb == 50
    assert cfg.corte_reserva is None
    assert cfg.pg_dsn == ""


def test_valores_explicitos(tmp_path):
    cfg = cargar_config({"DATA_DIR": str(tmp_path), "DATOS_PG_DSN": "dsn",
                         "DATOS_DISCO_MIN_GB": "12.5"})
    assert cfg.pg_dsn == "dsn"
    assert cfg.disco_min_gb == 12.5


def test_disco_min_invalido_aborta(tmp_path):
    with pytest.raises(ConfigInvalida) as e:
        cargar_config({"DATA_DIR": str(tmp_path), "DATOS_DISCO_MIN_GB": "mucho"})
    assert "DATOS_DISCO_MIN_GB" in str(e.value)


def test_corte_reserva_se_parsea(tmp_path):
    cfg = cargar_config({"DATA_DIR": str(tmp_path), "DATOS_CORTE_RESERVA": "2025-11-01"})
    assert cfg.corte_reserva == date(2025, 11, 1)
    with pytest.raises(ConfigInvalida) as e:
        cargar_config({"DATA_DIR": str(tmp_path), "DATOS_CORTE_RESERVA": "2025-13-01"})
    assert "DATOS_CORTE_RESERVA" in str(e.value)


def test_config_invalida_es_systemexit():
    assert issubclass(ConfigInvalida, SystemExit)


def test_motor_no_importa_datos():
    for archivo in (RAIZ / "motor").glob("*.py"):
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                nombres = [a.name for a in nodo.names]
            elif isinstance(nodo, ast.ImportFrom):
                nombres = [nodo.module or ""]
            else:
                continue
            for n in nombres:
                assert not n.startswith("datos"), f"{archivo.name} importa {n}"


def test_cli_lista_ordenes():
    r = subprocess.run([sys.executable, "-m", "datos", "--help"], cwd=RAIZ,
                       capture_output=True, text=True)
    assert r.returncode == 0
    for orden in ("simbolos", "bajar", "velas", "verificar", "catalogo"):
        assert orden in r.stdout


def test_cli_orden_no_disponible():
    r = subprocess.run([sys.executable, "-m", "datos", "verificar"], cwd=RAIZ,
                       capture_output=True, text=True)
    assert r.returncode == 2
    assert "Orden aún no disponible" in r.stderr
