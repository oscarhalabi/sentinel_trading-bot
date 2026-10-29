"""Tests del alta manual de cuentas (scripts/provision.py). Sin Supabase ni MT5 reales."""

import os

import pytest

from scripts import provision


class FakeTable:
    def __init__(self, client, name):
        self.client, self.name = client, name
        self._op = None

    def select(self, *_a, **_k):
        self._op = ("select",)
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def upsert(self, row, on_conflict=None):
        self._op = ("upsert", row, on_conflict)
        return self

    def insert(self, row):
        self._op = ("insert", row)
        return self

    def execute(self):
        class R:
            data = []
        r = R()
        if self._op[0] == "select":
            r.data = list(self.client.existing.get(self.name, []))
        else:
            self.client.calls.append((self.name,) + self._op)
        return r


class FakeClient:
    def __init__(self, existing=None):
        self.calls = []
        self.existing = existing or {}

    def table(self, name):
        return FakeTable(self, name)


VALORES = {
    "label": "Cuenta X", "email": "a@b.c", "stamp": "2026-01-01 00:00",
    "supabase_url": "https://x.supabase.co", "supabase_key": "KEY",
    "user_id": "11111111-1111-1111-1111-111111111111", "symbol": "XAUUSD+",
    "bots": "n1,n2", "mt5_path": r"C:\MT5_instances_n1\mt5_123\terminal64.exe",
    "mt5_login": "123", "mt5_server": "Srv", "mt5_password": "secreto",
    "clones_dir": r"C:\MT5_instances_n1", "magics": {"n1": 300100, "n2": 300200},
}


def test_render_env_incluye_bots_y_magics():
    txt = provision.render_env(VALORES)
    assert "BOTS=n1,n2" in txt
    assert "MAGIC_N1=300100" in txt
    assert "MAGIC_N2=300200" in txt
    assert "MT5_PORTABLE=true" in txt
    assert "SYMBOL=XAUUSD+" in txt
    assert "MT5_PASSWORD=secreto" in txt
    assert "MT5_CLONES_DIR=C:\MT5_instances_n1" in txt


def test_clone_dir_fuera_de_produccion():
    d = provision.clone_dir_for("123", r"C:\MT5_instances_n1")
    assert d.lower().startswith("c:\mt5_instances_n1")
    assert not d.lower().startswith("c:\mt5_instances\\")


def _args(**kw):
    base = dict(user_id=VALORES["user_id"], email="a@b.c", login="123", server="Srv",
                password="secreto", bots="n1,n2", symbol="XAUUSD+", label=None, dry_run=False)
    base.update(kw)
    return type("A", (), base)()


def test_alta_dry_run_no_escribe(tmp_path, capsys):
    cliente = FakeClient()
    inst = tmp_path / "instances"
    provision.alta(_args(dry_run=True), client=cliente, instances_dir=str(inst),
                   clones_dir=str(tmp_path / "clones"))
    assert cliente.calls == []
    assert not inst.exists()
    assert not (tmp_path / "clones").exists()
    out = capsys.readouterr().out
    assert "secreto" not in out


def test_alta_rechaza_carpeta_de_produccion(tmp_path):
    with pytest.raises(SystemExit):
        provision.alta(_args(), client=FakeClient(), instances_dir=str(tmp_path),
                       clones_dir=r"C:\MT5_instances")


def test_alta_bot_desconocido_aborta_antes_de_nada(tmp_path):
    cliente = FakeClient()
    with pytest.raises(SystemExit):
        provision.alta(_args(bots="zz"), client=cliente, instances_dir=str(tmp_path / "i"),
                       clones_dir=str(tmp_path / "c"))
    assert cliente.calls == []


def test_alta_real_upserts_y_env(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(provision, "_clone_mt5", lambda login, clones_dir: os.path.join(
        clones_dir, f"mt5_{login}", "terminal64.exe"))
    monkeypatch.setattr(provision, "_find_supabase_creds", lambda: ("https://x", "KEY"))
    cliente = FakeClient()
    inst = tmp_path / "instances"
    provision.alta(_args(), client=cliente, instances_dir=str(inst),
                   clones_dir=str(tmp_path / "clones"))
    env = (inst / f"{VALORES['user_id']}.env").read_text(encoding="utf-8")
    assert "MT5_PASSWORD=secreto" in env and "MAGIC_N2=300200" in env
    assert "secreto" not in capsys.readouterr().out
    tablas = [(c[0], c[1]) for c in cliente.calls]
    assert tablas.count(("bot_instances", "upsert")) == 2
    assert tablas.count(("engine_config", "upsert")) == 2
    assert ("account_settings", "insert") in tablas
    bi = [c for c in cliente.calls if c[0] == "bot_instances"][0]
    assert bi[2]["is_active"] is False and bi[3] == "user_id,bot_id"
    ec = [c for c in cliente.calls if c[0] == "engine_config"][0]
    assert ec[2]["params"] == {}


def test_alta_no_pisa_account_settings_existente(tmp_path, monkeypatch):
    monkeypatch.setattr(provision, "_clone_mt5", lambda login, clones_dir: "x")
    monkeypatch.setattr(provision, "_find_supabase_creds", lambda: ("https://x", "KEY"))
    cliente = FakeClient(existing={"account_settings": [{"user_id": VALORES["user_id"]}]})
    provision.alta(_args(), client=cliente, instances_dir=str(tmp_path / "i"),
                   clones_dir=str(tmp_path / "c"))
    assert not [c for c in cliente.calls if c[0] == "account_settings"]
