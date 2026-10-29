"""Seleccion de procesos propios: nunca debe incluir los de produccion."""

from scripts.procs import Proc, select_ours

REPO = r"C:\Users\Administrator\Documents\sentinel_trading-bot-fase1"
PROD = r"C:\Users\Administrator\Documents\sentinel_trading"
CLONES = r"C:\MT5_instances_n1"


def _lista():
    return [
        Proc(100, "python.exe", rf'python -m bot.lite_e2 "{PROD}"', rf"{PROD}\venv\python.exe"),
        Proc(101, "python.exe", rf'python -m scripts.news_exporter --root "{PROD}"', ""),
        Proc(102, "terminal64.exe", "", r"C:\MT5_instances\mt5_26329953\terminal64.exe"),
        Proc(200, "python.exe", rf'python -m motor.run --mode lite --root "{REPO}"', ""),
        Proc(201, "terminal64.exe", "", r"C:\MT5_instances_n1\mt5_123\terminal64.exe"),
    ]


def test_seleccion_ignora_produccion():
    pids = sorted(p.pid for p in select_ours(_lista(), REPO, CLONES))
    assert pids == [200, 201]


def test_rutas_case_insensitive():
    procs = [
        Proc(1, "python.exe", "python -m motor.run --mode lite --root c:/users/administrator/documents/SENTINEL_TRADING-BOT-FASE1", ""),
        Proc(2, "terminal64.exe", "", r"c:\mt5_instances_N1\mt5_9\TERMINAL64.EXE"),
    ]
    assert sorted(p.pid for p in select_ours(procs, REPO, CLONES)) == [1, 2]


def test_powershell_run_motor_incluido():
    procs = [
        Proc(1, "powershell.exe", rf'powershell -File "{REPO}\scripts\run_motor.ps1" -BotId n1', ""),
        Proc(2, "powershell.exe", rf'powershell -File "{PROD}\scripts\run_instance.ps1"', ""),
        Proc(3, "powershell.exe", r'powershell -File "C:\otro\run_motor.ps1"', ""),
    ]
    assert [p.pid for p in select_ours(procs, REPO, CLONES)] == [1]


def test_prefijos_de_carpeta_no_se_confunden():
    # C:\MT5_instances no es prefijo valido de C:\MT5_instances_n1 ni al reves.
    t_prod = Proc(1, "terminal64.exe", "", r"C:\MT5_instances\mt5_1\terminal64.exe")
    t_n1 = Proc(2, "terminal64.exe", "", r"C:\MT5_instances_n1\mt5_1\terminal64.exe")
    assert select_ours([t_prod, t_n1], REPO, CLONES) == [t_n1]
    assert select_ours([t_prod], REPO, CLONES) == []


def test_repo_con_prefijo_parecido_no_coincide():
    otro = Proc(1, "python.exe", rf'python -m motor.run --mode lite --root "{REPO}-copia"', "")
    assert select_ours([otro], REPO, CLONES) == []


def test_vigilante_incluido():
    p = Proc(1, "python.exe", rf'python -m scripts.vigilante "{REPO}"', "")
    assert select_ours([p], REPO, CLONES) == [p]


# --- mini-CLI de motor.registry (lo usan los .ps1) ---

def test_registry_cli_magic(monkeypatch, capsys):
    from motor import registry
    monkeypatch.delenv("MAGIC_N1", raising=False)
    assert registry.main(["magic", "n1"]) == 0
    assert capsys.readouterr().out.strip() == "300100"
    monkeypatch.setenv("MAGIC_N1", "300111")
    registry.main(["magic", "n1"])
    assert capsys.readouterr().out.strip() == "300111"


def test_registry_cli_check(tmp_path):
    import pytest
    from motor import registry
    ok = tmp_path / "a.env"
    ok.write_text("# c\nBOTS=n1,n2\nMAGIC_N1=300100\n", encoding="utf-8")
    assert registry.main(["check", str(ok)]) == 0
    malo = tmp_path / "b.env"
    malo.write_text("BOTS=zzz\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        registry.main(["check", str(malo)])
    vacio = tmp_path / "c.env"
    vacio.write_text("USER_ID=x\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        registry.main(["check", str(vacio)])


# --- endurecimiento ---

def test_clones_dir_de_produccion_se_rechaza():
    import pytest
    for malo in (r"C:\MT5_instances", r"c:/mt5_instances/", "C:\\"):
        with pytest.raises(SystemExit):
            select_ours([], REPO, malo)


def test_terminal_de_produccion_nunca_se_selecciona():
    # Aunque clones_dir fuera una subcarpeta de produccion, no se toca.
    t = Proc(1, "terminal64.exe", "", r"C:\MT5_instances\mt5_1\terminal64.exe")
    assert select_ours([t], REPO, r"C:\MT5_instances\sub") == []


def test_marca_python_exige_fin_de_palabra():
    runner = Proc(1, "python.exe", rf'python -m motor.runner --root "{REPO}"', "")
    pytest_ = Proc(2, "python.exe", rf'python -m pytest -k motor.run "{REPO}\tests"', "")
    assert select_ours([runner, pytest_], REPO, CLONES) == []


def test_motor_run_sin_ruta_del_repo_no_se_selecciona():
    prod = Proc(1, "python.exe", "python -m motor.run --mode lite", "")
    assert select_ours([prod], REPO, CLONES) == []


def test_vigilante_de_la_tarea_programada_incluido():
    # Linea de comandos que registra instalar_tareas.ps1 (pythonw, --root con la ruta).
    p = Proc(1, "pythonw.exe",
             rf'"C:\Python\pythonw.exe" -m scripts.vigilante --root "{REPO}"', "")
    assert select_ours([p], REPO, CLONES) == [p]
    sin_root = Proc(2, "pythonw.exe", r'"C:\Python\pythonw.exe" -m scripts.vigilante', "")
    assert select_ours([sin_root], REPO, CLONES) == []
