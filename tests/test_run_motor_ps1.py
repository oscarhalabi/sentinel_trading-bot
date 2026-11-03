"""run_motor.ps1 propaga el codigo de salida de python (para que el vigilante relance)."""
import os
import subprocess

import pytest

RUN_MOTOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "scripts", "run_motor.ps1")


def _bloque_de_salida():
    """Lo que run_motor.ps1 ejecuta justo despues de python -m motor.run."""
    with open(RUN_MOTOR, encoding="utf-8") as f:
        texto = f.read()
    llamada = 'python -m motor.run --mode $Mode --root "$projectRoot" --env-file "$EnvFile" --bot-id $BotId'
    assert llamada in texto
    return texto.split(llamada, 1)[1]


def test_run_motor_propaga_codigo_de_error():
    bloque = _bloque_de_salida()
    assert "$codigo = $LASTEXITCODE" in bloque
    assert "if ($codigo -ne 0)" in bloque
    assert "[Environment]::Exit($codigo)" in bloque


def _lanzar_con_noexit(tmp_path, codigo_python):
    """Reproduce el patron de start_all: powershell -NoExit -File <script>."""
    script = tmp_path / "simula.ps1"
    script.write_text(f"$BotId = 'n1'\ncmd /c exit {codigo_python}\n" + _bloque_de_salida(),
                      encoding="utf-8")
    return subprocess.Popen(
        ["powershell", "-NoProfile", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


@pytest.mark.skipif(os.name != "nt", reason="solo Windows")
def test_noexit_se_cierra_si_python_falla(tmp_path):
    p = _lanzar_con_noexit(tmp_path, 3)
    try:
        assert p.wait(timeout=60) == 3
    finally:
        if p.poll() is None:
            p.kill()
        p.communicate()


@pytest.mark.skipif(os.name != "nt", reason="solo Windows")
def test_noexit_sigue_abierta_si_python_sale_limpio(tmp_path):
    p = _lanzar_con_noexit(tmp_path, 0)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            p.wait(timeout=8)
    finally:
        p.kill()  # proceso propio del test
        p.communicate()


def test_run_motor_lee_el_env_como_utf8():
    with open(RUN_MOTOR, encoding="utf-8") as f:
        texto = f.read()
    assert "Get-Content -LiteralPath $EnvFile -Encoding UTF8" in texto


@pytest.mark.skipif(os.name != "nt", reason="solo Windows")
def test_get_content_utf8_conserva_enie_y_euro(tmp_path):
    """Mismo patron de lectura que run_motor.ps1, contra un .env UTF-8 sin BOM."""
    env = tmp_path / "a.env"
    env.write_bytes("MT5_PASSWORD=contraseña€\n".encode("utf-8"))
    salida = tmp_path / "out.txt"
    ps = (f"$v = @(Get-Content -LiteralPath '{env}' -Encoding UTF8)[0].Split('=',2)[1]; "
          f"[IO.File]::WriteAllText('{salida}', $v, [Text.UTF8Encoding]::new($false))")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert salida.read_text(encoding="utf-8") == "contraseña€"
