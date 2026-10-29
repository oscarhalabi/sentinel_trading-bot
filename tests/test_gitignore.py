"""Los .env de instancia (con contrasenas) nunca entran en git."""
import os
import shutil
import subprocess

import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.skipif(shutil.which("git") is None, reason="sin git")
@pytest.mark.parametrize("ruta,ignorado", [
    ("instances/daniel.env", True),
    ("instances/daniel.env.new", True),
    ("instances/example.env", False),
])
def test_env_de_instancias_ignorados(ruta, ignorado):
    r = subprocess.run(["git", "check-ignore", "-q", "--no-index", ruta], cwd=RAIZ)
    assert (r.returncode == 0) is ignorado
