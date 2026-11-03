"""Candado de una sola instancia por (USER_ID, BOT_ID) en motor.run."""
import uuid

import pytest

from motor import config, main as botmain
from motor import run as motor_run


def _uid():
    return f"test-{uuid.uuid4()}"


def test_segundo_candado_mismo_usuario_y_bot_esta_ocupado():
    uid = _uid()
    primero = motor_run.adquirir_candado(uid, "n1")
    assert primero is not None
    try:
        assert motor_run.adquirir_candado(uid, "n1") is None
        # Otro motor del mismo usuario, u otro usuario, no chocan.
        otro_bot = motor_run.adquirir_candado(uid, "n2")
        otro_user = motor_run.adquirir_candado(_uid(), "n1")
        assert otro_bot is not None and otro_user is not None
        motor_run.liberar_candado(otro_bot)
        motor_run.liberar_candado(otro_user)
    finally:
        motor_run.liberar_candado(primero)
    # Liberado: se puede volver a tomar.
    de_nuevo = motor_run.adquirir_candado(uid, "n1")
    assert de_nuevo is not None
    motor_run.liberar_candado(de_nuevo)


def test_candado_por_archivo_si_falla_el_mutex(monkeypatch, tmp_path):
    monkeypatch.setattr(motor_run, "_mutex", lambda nombre: (_ for _ in ()).throw(OSError("sin mutex")))
    monkeypatch.setattr(motor_run, "_carpeta_candados", lambda: str(tmp_path))
    uid = _uid()
    primero = motor_run.adquirir_candado(uid, "n1")
    assert primero is not None
    assert motor_run.adquirir_candado(uid, "n1") is None
    motor_run.liberar_candado(primero)
    segundo = motor_run.adquirir_candado(uid, "n1")
    assert segundo is not None
    motor_run.liberar_candado(segundo)


def test_main_sale_limpio_si_ya_hay_motor(monkeypatch, capsys):
    uid = _uid()
    monkeypatch.setattr(config, "USER_ID", uid)
    monkeypatch.setattr(config, "BOT_ID", "n1")
    llamado = []
    monkeypatch.setattr(botmain, "run", lambda engine_cls=None: llamado.append(1))
    ocupado = motor_run.adquirir_candado(uid, "n1")
    try:
        assert motor_run.main([]) == 0
    finally:
        motor_run.liberar_candado(ocupado)
    assert llamado == []
    assert "ya hay un motor n1 corriendo para este usuario" in capsys.readouterr().out


def test_main_libera_el_candado_al_terminar(monkeypatch):
    uid = _uid()
    monkeypatch.setattr(config, "USER_ID", uid)
    monkeypatch.setattr(config, "BOT_ID", "n1")
    monkeypatch.setattr(botmain, "run", lambda engine_cls=None: None)
    motor_run.main([])
    motor_run.main([])  # el primero ya solto el candado
    h = motor_run.adquirir_candado(uid, "n1")
    assert h is not None
    motor_run.liberar_candado(h)
