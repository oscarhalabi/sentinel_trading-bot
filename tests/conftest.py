"""Arnés de tests: fija la identidad ANTES de importar motor.config."""
import os

os.environ.setdefault("USER_ID", "00000000-0000-0000-0000-000000000000")
os.environ.setdefault("BOT_ID", "n1")
os.environ.setdefault("MAGIC_NUMBER", "300100")
