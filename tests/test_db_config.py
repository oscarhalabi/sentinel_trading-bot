"""Tests de Database.load_config contra engine_config (cliente Supabase falso)."""
from motor import config
from motor.db import Database


class _Consulta:
    """Registra la cadena .table().select().eq()... y devuelve filas fijas."""

    def __init__(self, filas=None, falla=False):
        self.tabla = None
        self.filtros = {}
        self._filas = filas or []
        self._falla = falla

    def table(self, nombre):
        self.tabla = nombre
        return self

    def select(self, *_a):
        return self

    def eq(self, col, val):
        self.filtros[col] = val
        return self

    def limit(self, _n):
        return self

    def execute(self):
        if self._falla:
            raise ConnectionError("sin red")
        return type("R", (), {"data": self._filas})()


def _db(consulta):
    db = Database()
    db.client = consulta
    return db


def test_load_config_lee_engine_config():
    q = _Consulta([{"id": 1, "bot_id": config.BOT_ID, "params": {"riesgo": 0.5}, "is_active": True}])
    cfg, status = _db(q).load_config()
    assert q.tabla == "engine_config"
    assert q.filtros["bot_id"] == config.BOT_ID
    assert q.filtros["user_id"] == config.USER_ID
    assert q.filtros["is_active"] is True
    assert "symbol" not in q.filtros
    assert cfg["params"] == {"riesgo": 0.5}
    assert status == "ACTIVE"


def test_sin_fila_activa_es_inactive():
    cfg, status = _db(_Consulta([])).load_config()
    assert cfg == {} and status == "INACTIVE"


def test_error_de_red_reusa_cache():
    db = _db(_Consulta([{"params": {"a": 1}, "is_active": True}]))
    db.load_config()
    db.client = _Consulta(falla=True)
    cfg, status = db.load_config()
    assert cfg["params"] == {"a": 1}
    assert status == "ACTIVE"


def test_params_nulo_llega_como_dict():
    cfg, _ = _db(_Consulta([{"params": None, "is_active": True}])).load_config()
    assert cfg["params"] == {}
