"""Alta manual de cuentas del bot nuevo (provisionador).

Da de alta UNA cuenta a mano desde la linea de comandos:

  1. Clona la instalacion BASE de MetaTrader 5 a una carpeta propia
     (MT5_CLONES_DIR, por defecto C:\\MT5_instances_n1; modo portable).
  2. Siembra el perfil IPC y habilita Algo Trading en el clon.
  3. Escribe instances\\<user_id>.env (con la contrasena MT5; nunca se imprime).
  4. Hace upsert en Supabase de bot_instances (is_active=false) y engine_config
     (params={}) por cada motor, y crea account_settings solo si no existe.

Este script NO lanza motores ni reinicia nada: las instancias nuevas las
arranca el vigilante (scripts/vigilante.py). Tampoco toca la tarea programada
de reinicio de produccion.

Modo PENDING / --watch ELIMINADO: mientras convivan los dos bots, el
provisionador de produccion ya consulta provision_requests cada 5 minutos.
Si este script tambien las procesara, los dos se pelearian por las mismas
filas. Ese modo vuelve cuando el bot nuevo reemplace a produccion.

Uso (desde la raiz del repo):

    python -m scripts.provision alta --user-id <uuid> --email <e> --login <n> \\
        --server <s> --password <p> --bots n1,n2 [--symbol XAUUSD+] [--label <t>] [--dry-run]

Config por entorno:
    MT5_BASE_DIR    instalacion base a clonar (default: C:\\Program Files\\MetaTrader 5)
    MT5_CLONES_DIR  carpeta raiz de los clones (default: C:\\MT5_instances_n1)
    SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY  (o en el .env raiz / algun instances/*.env)
"""

import argparse
import datetime
import hashlib
import os
import shutil

from motor.registry import load_registry, magic_for, parse_bots, validate_instance
from scripts.procs import CLONES_PRODUCCION, _es_o_contiene

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTANCES_DIR = os.path.join(ROOT, "instances")

MT5_BASE_DIR = os.environ.get("MT5_BASE_DIR", r"C:\Program Files\MetaTrader 5")
MT5_CLONES_DIR = os.environ.get("MT5_CLONES_DIR", r"C:\MT5_instances_n1")

ENV_TEMPLATE = """# Usuario: {label} / {email}
# Generado por scripts/provision.py el {stamp}.
# Una instancia = un usuario = una cuenta = un terminal MT5 (clon portable).

# --- Supabase ---
SUPABASE_URL={supabase_url}
SUPABASE_SERVICE_ROLE_KEY={supabase_key}

# --- Identidad de ESTE usuario ---
USER_ID={user_id}
SYMBOL={symbol}

# --- Motores a levantar sobre esta cuenta ---
BOTS={bots}
{magics}

# --- Terminal MetaTrader 5 (clon portable propio de este usuario) ---
MT5_PATH={mt5_path}
MT5_LOGIN={mt5_login}
MT5_SERVER={mt5_server}
MT5_PASSWORD={mt5_password}
MT5_PORTABLE=true
MT5_CLONES_DIR={clones_dir}
"""


def render_env(values: dict) -> str:
    """Genera el texto del .env de la instancia (funcion pura).

    values["magics"] = {bot_id: magic}; sale como MAGIC_<BOT_ID en mayusculas>=<n>.
    """
    magics = "\n".join(f"MAGIC_{b.upper()}={m}" for b, m in values["magics"].items())
    return ENV_TEMPLATE.format(**{**values, "magics": magics})


def clone_dir_for(login: str, clones_dir: str) -> str:
    """Carpeta del clon MT5 de un login, dentro de clones_dir."""
    return os.path.join(clones_dir, f"mt5_{login}")


def _read_env_file(path):
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def _find_supabase_creds():
    """SUPABASE_URL/KEY del entorno, del .env raiz, o de algun instances/*.env."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if url and key:
        return url, key
    candidates = [os.path.join(ROOT, ".env")]
    if os.path.isdir(INSTANCES_DIR):
        for name in sorted(os.listdir(INSTANCES_DIR)):
            if name.endswith(".env") and not name.startswith(("example", "template", "_")):
                candidates.append(os.path.join(INSTANCES_DIR, name))
    for path in candidates:
        vals = _read_env_file(path)
        if vals.get("SUPABASE_URL") and vals.get("SUPABASE_SERVICE_ROLE_KEY"):
            return vals["SUPABASE_URL"], vals["SUPABASE_SERVICE_ROLE_KEY"]
    return "", ""


def _enable_algo_trading(clone_dir):
    """Deja el Algo Trading (AutoTrading) habilitado desde el primer arranque.

    En modo portable el terminal lee su configuracion de <clon>\\config\\
    common.ini. Sin el boton activo, mt5.order_send() devuelve retcode 10027
    (client disables autotrading) y el bot puede leer pero no operar. Se
    escribe la seccion [Experts] ANTES del primer arranque; si el archivo ya
    existe (clon ya arrancado alguna vez) no se pisa: en ese caso el estado
    del boton ya es el que dejo el operador.
    """
    cfg_dir = os.path.join(clone_dir, "config")
    ini_path = os.path.join(cfg_dir, "common.ini")
    if os.path.exists(ini_path):
        return
    os.makedirs(cfg_dir, exist_ok=True)
    with open(ini_path, "w", encoding="utf-16") as f:  # MT5 usa UTF-16 en sus .ini
        f.write("[Experts]\nAllowLiveTrading=1\nAllowDllImport=0\nEnabled=1\n")
    print("    Algo Trading habilitado (config/common.ini del clon).")


# Ficheros del perfil ESPECIFICOS de la cuenta: cada clon conserva los suyos
# (su almacen de cuentas y su common.ini con el boton de Algo Trading). El
# resto del perfil base maduro se siembra tal cual.
_ACCOUNT_LOCAL_FILES = {"accounts.dat", "common.ini"}


def _base_runtime_config():
    """Ruta al `config` del PERFIL DE RUNTIME maduro del terminal base.

    El estado 'listo para la API Python' (IPC) del terminal NO vive en la
    carpeta de instalacion sino en el perfil de datos que MT5 crea bajo
    %APPDATA%\\MetaQuotes\\Terminal\\<hash>. Un clon recien copiado con
    copytree solo trae los binarios + un config minimo: ARRANCA y CONECTA al
    broker, pero su terminal nunca atiende el pipe IPC de Python y
    mt5.initialize() expira con (-10005, 'IPC timeout').

    Devuelve <perfil>\\config del terminal base, o None si el base nunca se ha
    ejecutado interactivamente (no hay perfil maduro que sembrar).
    """
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return None
    terminals = os.path.join(appdata, "MetaQuotes", "Terminal")
    if not os.path.isdir(terminals):
        return None
    base = os.path.normpath(MT5_BASE_DIR)
    # 1) Nombre canonico del perfil: MD5(ruta en MAYUSCULAS, UTF-16-LE).
    want = hashlib.md5(base.upper().encode("utf-16-le")).hexdigest().upper()
    cand = os.path.join(terminals, want, "config")
    if os.path.isdir(cand):
        return cand
    # 2) Fallback robusto: el perfil cuyo origin.txt apunta al base.
    for name in os.listdir(terminals):
        try:
            with open(os.path.join(terminals, name, "origin.txt"), "rb") as f:
                raw = f.read()
        except OSError:
            continue
        origin = raw.decode("utf-16", "ignore").strip() or raw.decode("utf-8", "ignore").strip()
        if os.path.normpath(origin).lower() == base.lower():
            cfg = os.path.join(terminals, name, "config")
            if os.path.isdir(cfg):
                return cfg
    return None


def _seed_ipc_profile(clone_dir):
    """Siembra el config del perfil base maduro en el clon (idempotente).

    Sin esto el clon conecta al broker pero su terminal no atiende el pipe IPC
    de Python y mt5.initialize() da (-10005, 'IPC timeout'). Ver
    _base_runtime_config(). Se excluyen los ficheros de cuenta para que cada
    clon mantenga su login y su common.ini. Marca: si ya hay settings.ini el
    perfil ya esta sembrado/maduro y NO se pisa (respeta ajustes del operador).
    """
    dst = os.path.join(clone_dir, "config")
    if os.path.exists(os.path.join(dst, "settings.ini")):
        return
    src = _base_runtime_config()
    if not src:
        print("    [AVISO] el terminal base no tiene perfil de runtime maduro: "
              "abrelo una vez e inicia sesion, o el clon dara IPC timeout.")
        return
    os.makedirs(dst, exist_ok=True)
    copied = 0
    for name in os.listdir(src):
        if name in _ACCOUNT_LOCAL_FILES:
            continue
        s, d = os.path.join(src, name), os.path.join(dst, name)
        try:
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
            copied += 1
        except OSError as e:  # noqa: BLE001
            print(f"    [AVISO] no se pudo sembrar {name}: {e}")
    print(f"    perfil IPC sembrado desde el base ({copied} entradas).")

def _clone_mt5(login, clones_dir):
    """Copia la instalacion base a un clon propio del login. Idempotente."""
    clone_dir = clone_dir_for(login, clones_dir)
    exe = os.path.join(clone_dir, "terminal64.exe")
    if os.path.exists(exe):
        print(f"    clon MT5 ya existe: {clone_dir}")
        _seed_ipc_profile(clone_dir)
        _enable_algo_trading(clone_dir)
        return exe
    base_exe = os.path.join(MT5_BASE_DIR, "terminal64.exe")
    if not os.path.exists(base_exe):
        raise RuntimeError(
            f"No existe la instalacion base de MT5 en {MT5_BASE_DIR} "
            f"(define MT5_BASE_DIR).")
    print(f"    clonando {MT5_BASE_DIR} -> {clone_dir} (puede tardar)...")
    os.makedirs(clones_dir, exist_ok=True)
    shutil.copytree(MT5_BASE_DIR, clone_dir)
    _seed_ipc_profile(clone_dir)
    _enable_algo_trading(clone_dir)
    return exe


def _upsert_supabase(client, user_id, bots, login, label):
    """Crea las filas del usuario. Nunca pisa account_settings si ya existe."""
    for bot_id in bots:
        client.table("bot_instances").upsert(
            {"user_id": user_id, "bot_id": bot_id, "is_active": False,
             "label": label, "account_login": int(login)},
            on_conflict="user_id,bot_id").execute()
        client.table("engine_config").upsert(
            {"user_id": user_id, "bot_id": bot_id, "params": {}},
            on_conflict="user_id,bot_id").execute()
    existe = (client.table("account_settings").select("user_id")
              .eq("user_id", user_id).limit(1).execute().data)
    if not existe:
        client.table("account_settings").insert({"user_id": user_id}).execute()


def alta(args, *, client=None, instances_dir=INSTANCES_DIR, clones_dir=None):
    """Da de alta una cuenta. Con args.dry_run solo muestra el plan (nada se escribe)."""
    clones_dir = clones_dir or MT5_CLONES_DIR
    if _es_o_contiene(clones_dir, CLONES_PRODUCCION):
        raise SystemExit(
            f"[ABORTADO] MT5_CLONES_DIR '{clones_dir}' coincide con la carpeta de "
            f"produccion {CLONES_PRODUCCION} o la contiene.")
    registro = load_registry()
    bots = parse_bots(args.bots)
    validate_instance(registro, bots, os.environ)
    magics = {b: magic_for(registro[b], os.environ) for b in bots}
    clone = clone_dir_for(args.login, clones_dir)
    label = args.label or f"Cuenta {args.login}"
    env_path = os.path.join(instances_dir, f"{args.user_id}.env")

    if args.dry_run:
        print("[dry-run] no se clona, no se escribe y no se toca Supabase.")
        print(f"  clon MT5 : {clone}")
        print(f"  .env     : {env_path}")
        print(f"  motores  : {', '.join(bots)}  magics: {magics}")
        print(f"  Supabase : upsert bot_instances/engine_config x{len(bots)}, "
              f"account_settings si falta (usuario {args.user_id})")
        print(f"  cuenta   : login={args.login} server={args.server} password=******")
        return

    url, key = _find_supabase_creds()
    if client is None:
        if not url or not key:
            raise SystemExit("[ABORTADO] Faltan SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY "
                             "(entorno, .env raiz o algun instances/*.env).")
        from supabase import create_client  # import tardio: mismo venv que el bot
        client = create_client(url, key)

    mt5_path = _clone_mt5(args.login, clones_dir)
    contenido = render_env({
        "label": label, "email": args.email,
        "stamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "supabase_url": url, "supabase_key": key, "user_id": args.user_id,
        "symbol": args.symbol, "bots": ",".join(bots), "magics": magics,
        "mt5_path": mt5_path, "mt5_login": args.login, "mt5_server": args.server,
        "mt5_password": args.password, "clones_dir": clones_dir,
    })
    if os.path.exists(env_path):
        # No pisar un .env existente (podria tener ajustes manuales).
        env_path += ".new"
    os.makedirs(instances_dir, exist_ok=True)
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(contenido)
    print(f"    .env escrito: {env_path}")
    _upsert_supabase(client, args.user_id, bots, args.login, label)
    print("    filas de Supabase listas (bot_instances inactivas). "
          "El vigilante lanzara la instancia.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Alta manual de cuentas del bot nuevo")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("alta", help="da de alta una cuenta")
    a.add_argument("--user-id", required=True)
    a.add_argument("--email", required=True)
    a.add_argument("--login", required=True)
    a.add_argument("--server", required=True)
    a.add_argument("--password", required=True)
    a.add_argument("--bots", required=True, help="motores separados por coma, ej. n1,n2")
    a.add_argument("--symbol", default="XAUUSD+")
    a.add_argument("--label", default=None)
    a.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="muestra el plan sin clonar, escribir ni tocar Supabase")
    args = parser.parse_args(argv)
    alta(args)


if __name__ == "__main__":
    main()
