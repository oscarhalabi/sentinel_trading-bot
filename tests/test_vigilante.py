"""Vigilante: calcula que motores faltan sin lanzar nada."""

from scripts.procs import Proc
from scripts.vigilante import expected_instances, missing, running_instances

REPO = r"C:\Users\Administrator\Documents\sentinel_trading-bot"
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
    # Misma regla que start_all.ps1 (motor.registry list): example*, template*, _*.
    _env(tmp_path, "Example-m5.env", "BOTS=n1\n")
    _env(tmp_path, "template_daniel.env", "BOTS=n1\n")
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


def test_should_relaunch_tope_y_poda():
    from scripts.vigilante import should_relaunch
    ahora = 10000.0
    assert not should_relaunch([ahora - 60 * i for i in range(5)], ahora)
    # 5 marcas pero 3 fuera de la ventana de 30 min: se relanza
    viejas = [ahora - 100, ahora - 200, ahora - 2000, ahora - 3000, ahora - 4000]
    assert should_relaunch(viejas, ahora)


def _preparar(tmp_path, monkeypatch):
    import scripts.vigilante as v
    env_a = _env(tmp_path, "a.env", "BOTS=n1,n2\n")
    monkeypatch.setattr(v, "_raiz_repo", lambda: str(tmp_path))
    monkeypatch.setattr(v, "list_processes", lambda: [_proc_motor(1, env_a, "n1")])
    monkeypatch.setattr(v, "select_ours", lambda procs, repo, clones: procs)
    llamadas = []
    monkeypatch.setattr(v.subprocess, "Popen", lambda cmd, **kw: llamadas.append(cmd))
    return v, env_a, llamadas


def test_main_lanza_solo_faltantes(tmp_path, monkeypatch):
    v, env_a, llamadas = _preparar(tmp_path, monkeypatch)
    assert v.main(["--instances", str(tmp_path)]) == 0
    assert len(llamadas) == 1
    cmd = llamadas[0]
    assert cmd[-6:] == ["-EnvFile", env_a, "-BotId", "n2", "-Mode", "lite"]
    assert cmd[cmd.index("-File") + 1].endswith("run_motor.ps1")
    assert (tmp_path / "logs" / "vigilante_estado.json").exists()


def test_main_dry_run_no_lanza_ni_escribe_estado(tmp_path, monkeypatch):
    v, _, llamadas = _preparar(tmp_path, monkeypatch)
    assert v.main(["--dry-run", "--instances", str(tmp_path)]) == 0
    assert llamadas == []
    assert not (tmp_path / "logs" / "vigilante_estado.json").exists()


def test_main_tope_de_reintentos(tmp_path, monkeypatch):
    import json, time
    v, env_a, llamadas = _preparar(tmp_path, monkeypatch)
    clave = f"{v._norm(env_a)}|n2"
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "vigilante_estado.json").write_text(
        json.dumps({clave: [time.time() - 60 * i for i in range(1, 6)]}))
    v.main(["--instances", str(tmp_path)])
    assert llamadas == []


def test_main_root_valido_e_invalido(tmp_path, monkeypatch):
    import pytest
    v, _, _ = _preparar(tmp_path, monkeypatch)
    assert v.main(["--dry-run", "--root", str(tmp_path).upper(), "--instances", str(tmp_path)]) == 0
    with pytest.raises(SystemExit):
        v.main(["--dry-run", "--root", str(tmp_path / "otro"), "--instances", str(tmp_path)])


def test_tarea_programada_pasa_root():
    import os
    ruta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", "instalar_tareas.ps1")
    with open(ruta, encoding="utf-8") as f:
        texto = f.read()
    assert '-m scripts.vigilante --root' in texto
