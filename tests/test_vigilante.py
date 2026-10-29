"""Vigilante: calcula que motores faltan sin lanzar nada."""

from scripts.procs import Proc
from scripts.vigilante import expected_instances, missing, running_instances

REPO = r"C:\Users\Administrator\Documents\sentinel_trading-bot-fase1"
RUNNER = REPO + r"\scripts\run_motor.ps1"


def _env(carpeta, nombre, contenido):
    ruta = carpeta / nombre
    ruta.write_text(contenido, encoding="utf-8")
    return str(ruta)


def _proc_motor(pid, env_path, bot):
    cmd = (f'powershell -NoProfile -ExecutionPolicy Bypass -File "{RUNNER}" '
           f'-EnvFile "{env_path}" -BotId {bot} -Mode lite')
    return Proc(pid, "powershell.exe", cmd, "")


def test_vigilante_relanza_faltantes(tmp_path):
    env_a = _env(tmp_path, "a.env", "BOTS=n1,n2\n")
    env_b = _env(tmp_path, "b.env", "BOTS=n1\n")
    esperadas = expected_instances(str(tmp_path))
    assert esperadas == {(env_a, "n1"), (env_a, "n2"), (env_b, "n1")}
    corriendo = running_instances([_proc_motor(1, env_a, "n1")])
    assert missing(esperadas, corriendo) == {(env_a, "n2"), (env_b, "n1")}


def test_ignora_example_env(tmp_path):
    _env(tmp_path, "example.env", "BOTS=n1\n")
    _env(tmp_path, "_borrador.env", "BOTS=n1\n")
    assert expected_instances(str(tmp_path)) == set()


def test_env_sin_bots_o_bot_desconocido_se_salta(tmp_path):
    _env(tmp_path, "vacio.env", "FOO=1\n")
    _env(tmp_path, "raro.env", "BOTS=zzz\n")
    ok = _env(tmp_path, "ok.env", "BOTS=n1\n")
    assert expected_instances(str(tmp_path)) == {(ok, "n1")}


def test_running_compara_rutas_sin_mayusculas_y_botid_exacto(tmp_path):
    env_a = _env(tmp_path, "a.env", "BOTS=n1,n2\n")
    corriendo = running_instances([_proc_motor(1, env_a.upper().replace("\\", "/"), "n1")])
    assert missing({(env_a, "n1"), (env_a, "n2")}, corriendo) == {(env_a, "n2")}
    # un -BotId n10 no cuenta como n1
    assert missing({(env_a, "n1")}, running_instances([_proc_motor(2, env_a, "n10")])) == {(env_a, "n1")}


def test_running_ignora_procesos_que_no_son_run_motor():
    p = Proc(3, "python.exe", "python -m motor.run --root x", "")
    assert running_instances([p]) == set()
