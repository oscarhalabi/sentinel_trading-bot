"""instalar_tarea_ff.ps1 registra solo SentinelDatosFF (se revisa el texto; no hace falta PowerShell)."""
import os
import re

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "scripts", "instalar_tarea_ff.ps1")


def _texto():
    with open(SCRIPT, encoding="utf-8") as f:
        return f.read()


def test_nombre_de_la_tarea():
    assert '$NombreTarea = "SentinelDatosFF"' in _texto()


def test_parametros_dryrun_y_horas_con_default_4():
    t = _texto()
    assert "[switch]$DryRun" in t
    assert re.search(r"\[double\]\$Horas\s*=\s*4\b", t)
    assert "$Horas -le 0" in t


def test_comando_y_carpeta_del_repo():
    t = _texto()
    assert "-m datos contexto bajar ff" in t
    assert "Split-Path -Parent $PSScriptRoot" in t


def test_reintenta_si_el_portatil_estaba_apagado():
    assert "-StartWhenAvailable" in _texto()


def test_log_en_registro_de_data_dir():
    t = _texto()
    assert "DATA_DIR" in t
    assert "_registro" in t and "ff.log" in t
    assert ">>" in t


def test_solo_toca_su_tarea():
    t = _texto()
    codigo = t.split("#>", 1)[1]  # sin el comentario de cabecera
    assert "Sentinel*" not in codigo
    assert "Unregister-ScheduledTask" not in codigo
    assert set(re.findall(r"Sentinel\w+", t)) == {"SentinelDatosFF"}
    # la unica instruccion de quitar tareas (en la cabecera) nombra solo SentinelDatosFF
    for linea in t.splitlines():
        if "Unregister-ScheduledTask" in linea:
            assert "-TaskName SentinelDatosFF" in linea
