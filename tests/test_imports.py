import importlib
import pkgutil


def test_paquete_importa():
    for nombre in ("config", "broker", "db", "logger", "news", "notify", "main"):
        importlib.import_module(f"motor.{nombre}")

    from motor import config
    assert not hasattr(config, "MAGIC_M15")
    assert not hasattr(config, "LEDGER_PATH")

    import motor
    modulos = [m.name for m in pkgutil.iter_modules(motor.__path__)]
    assert not [m for m in modulos if m.startswith("strategy")]
