"""Constantes de conexion y de mercado.

Los ~60 parametros de estrategia NO viven aqui: se leen desde la tabla
bot_config (PostgreSQL). Aqui solo van las constantes de infraestructura
(conexion DB, simbolo, magic, timeframes) y la identidad del bot.
"""

import os

import MetaTrader5 as mt5

from . import registry

# Carga .env si existe (sin dependencia dura de python-dotenv).
def _load_dotenv():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()

# ==================================================================
# CONEXION SUPABASE (cloud) - secretos via entorno/.env por instancia.
# El bot usa la service-role key (bypassa RLS); filtra por USER_ID.
# ==================================================================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# Postgres local (opcional). Vacio = base local desactivada; el bot sigue igual.
# Ej.: postgresql://sentinel_n1:<pass>@localhost:5432/sentinel_n1
LOCAL_PG_DSN = os.environ.get("LOCAL_PG_DSN", "")

# ==================================================================
# MERCADO / IDENTIDAD  (por-instancia via entorno)
# ==================================================================
SYMBOL = os.environ.get("SYMBOL", "XAUUSD+")   # sufijo del broker (XAUUSD.v, etc.)
MAGIC_NUMBER = int(os.environ.get("MAGIC_NUMBER", "100100"))

# ==================================================================
# MULTI-BOT SOBRE UNA MISMA CUENTA
# Un usuario puede correr varios motores (M15 Sentinel + M5 Grinder) contra
# la MISMA cuenta MT5. Se distinguen por:
#   - BOT_ID  -> discriminador de las filas en Supabase (bot_config, bot_state,
#                bot_instances, bot_positions, bot_logs).
#   - MAGIC   -> discriminador de las posiciones en el broker. Es lo que permite
#                que cada proceso vea SOLO lo suyo (Broker.positions) y a la vez
#                pueda leer la cesta del vecino (Broker.positions_of_magic).
# ==================================================================
BOT_ID = os.environ.get("BOT_ID", "m15")

# Motor que publica las velas del dashboard: el primero de BOTS. Sin BOTS
# (una instancia con un solo motor) es el propio BOT_ID.
_bots_env = os.environ.get("BOTS", "")
CANDLES_PUBLISHER = (registry.parse_bots(_bots_env)[0]
                     if any(b.strip() for b in _bots_env.split(",")) else BOT_ID)


def validate_identity():
    """Aborta si la identidad de la instancia no cuadra con el registro.

    Comprueba que USER_ID no este vacio, que BOT_ID exista en engines.json y
    que MAGIC_NUMBER sea el magic que el registro asigna a ese bot.
    Devuelve una lista de avisos (vacia si todo esta bien).
    """
    if not USER_ID:
        raise SystemExit(
            "[ABORTADO] Falta USER_ID en el .env de la instancia. "
            "Es el id del usuario en Supabase (auth.users.id) y es obligatorio."
        )
    reg = registry.load_registry()
    if BOT_ID not in reg:
        raise SystemExit(
            f"[ABORTADO] BOT_ID '{BOT_ID}' no esta en engines.json. "
            f"Disponibles: {', '.join(sorted(reg))}."
        )
    esperado = registry.magic_for(reg[BOT_ID], os.environ)
    if MAGIC_NUMBER != esperado:
        raise SystemExit(
            f"[ABORTADO] MAGIC_NUMBER={MAGIC_NUMBER} no coincide con el magic "
            f"{esperado} que el registro asigna al bot '{BOT_ID}'."
        )
    return []


# Usuario que identifica la fila de bot_config/bot_instances a cargar.
# = auth.users.id en Supabase. Obligatorio: se pasa por .env de la instancia.
USER_ID = os.environ.get("USER_ID", "")

# ==================================================================
# ATTACH A METATRADER 5  (por-instancia; vacio = terminal por defecto)
# Permite que cada proceso ataque el terminal/cuenta Vantage correcta.
# ==================================================================
MT5_PATH = os.environ.get("MT5_PATH", "")       # ruta al terminal64.exe de esa instancia
MT5_LOGIN = os.environ.get("MT5_LOGIN", "")     # login numerico de la cuenta
MT5_SERVER = os.environ.get("MT5_SERVER", "")   # servidor del broker
MT5_PASSWORD = os.environ.get("MT5_PASSWORD", "")
# true = terminal en modo /portable (clones creados por scripts/provision.py:
# los datos viven dentro de la carpeta del clon, no en AppData).
MT5_PORTABLE = os.environ.get("MT5_PORTABLE", "").lower() in ("1", "true", "yes")

# Timeout de mt5.initialize() en ms. El default de la libreria (60 s) se queda
# corto en el PRIMER arranque de un clon portable (crea perfil, sincroniza).
MT5_TIMEOUT_MS = int(os.environ.get("MT5_TIMEOUT_MS", "120000"))


def mt5_init_kwargs():
    """Argumentos de mt5.initialize() para atacar el terminal de ESTA instancia.

    UNICA fuente de verdad: la usan motor/main.py y los scripts de auditoria.
    Olvidar `portable` en un clon portable lanza el terminal en modo normal
    (perfil nuevo en AppData + asistente de primera ejecucion => IPC timeout).

    Vacio => terminal por defecto (comportamiento legacy). Con MT5_LOGIN se
    conecta a la cuenta concreta; con MT5_PATH se abre ese terminal64.exe.
    """
    kw = {"timeout": MT5_TIMEOUT_MS}
    if MT5_PATH:
        kw["path"] = MT5_PATH
    if MT5_LOGIN:
        kw["login"] = int(MT5_LOGIN)
        kw["server"] = MT5_SERVER
        kw["password"] = MT5_PASSWORD
    if MT5_PORTABLE:
        kw["portable"] = True
    return kw

# ==================================================================
# RESEND (motor/notify.py) - notificaciones por correo al usuario.
# Sin RESEND_API_KEY el modulo queda inerte (el evento igual queda en DB).
# El from por defecto (onboarding@resend.dev) solo entrega al dueño de la
# cuenta Resend; para usuarios reales, verificar dominio y setear RESEND_FROM.
# ==================================================================
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "Sentinel <onboarding@resend.dev>")
# URL publica del dashboard (CTA de los correos).
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "https://sentinel-trading-webapp.vercel.app/")

# ==================================================================
# NEWSGUARD (motor/news.py)  - snapshot del calendario economico
# Lo escribe scripts/news_exporter.py (el de produccion) en la carpeta COMUN
# de MetaQuotes: una sola ruta que sirve a todas las instancias de esta PC.
# ==================================================================
NEWS_CALENDAR_PATH = os.environ.get(
    "NEWS_CALENDAR_PATH",
    os.path.join(os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal",
                 "Common", "Files", "news_calendar.json"))

TIMEFRAME_CORE = mt5.TIMEFRAME_M15

# Frecuencia del bucle principal (segundos). 1 = comportamiento tipo OnTick.
LOOP_SLEEP = 1

# Cada cuantos segundos releer config + instancia (bot_config/bot_instances)
# desde Supabase. Desacopla el control de la frecuencia del on_tick para no
# pegarle a la REST API cada segundo.
CONTROL_REFRESH = 3

# Cada cuantos segundos escribir heartbeat + bot_status en bot_instances.
HEARTBEAT_INTERVAL = 15

# Modo sombra: si True, on_tick loguea decisiones pero NO envia ordenes.
# Se activa por entorno/.env (SHADOW_MODE=true), por instancia.
SHADOW_MODE = os.environ.get("SHADOW_MODE", "").lower() in ("1", "true", "yes")
