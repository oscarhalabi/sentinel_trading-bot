# Convivencia con producción y camino de reemplazo

El bot nuevo vive en el mismo servidor (VPS) que el bot de producción (e2), usa la misma app
web y el mismo Supabase. Este documento explica cómo se evita que se estorben y cómo se haría
el cambio de uno a otro (y la vuelta atrás).

## 1. Qué es "producción"

- Carpeta: `C:\Users\Administrator\Documents\sentinel_trading` (rama `experimento-2-rediseno`).
- Terminales MT5: `C:\MT5_instances\mt5_263299{53,55,57}` (cuentas 26329953, 55 y 57).
- Procesos: `python -m bot.lite_e2` (uno por motor/cuenta) y `scripts.news_exporter`.
- Tareas programadas: `SentinelRestart`, `SentinelProvisioner`, `SentinelDailyReport`,
  `SentinelTickCollector`.
- En Supabase usa `bot_id = 'e2'` (y los viejos `m15`, `m5`).

**Regla: nada de este repo modifica esa carpeta, esas tareas ni esas cuentas.**

## 2. Cómo está aislado el bot nuevo

| Tema | Producción | Bot nuevo |
|------|-----------|-----------|
| Código | `...\sentinel_trading` (paquete `bot/`) | este repo (paquete `motor/`) |
| Lanzador | `run_instance.ps1` | `scripts\run_motor.ps1` |
| Terminales MT5 | `C:\MT5_instances` | `C:\MT5_instances_n1` (`MT5_CLONES_DIR`) |
| Cuentas MT5 | 26329953/55/57 (reales) | cuentas **demo** propias |
| `bot_id` | `e2` (`m15`, `m5`) | `n1`, `n2`, ... |
| Magics | `MAGIC_M15`, `MAGIC_M5`, `MAGIC_E2` | `MAGIC_N1=300100`, `MAGIC_N2=300200` |
| Tareas programadas | `Sentinel*` | `SentinelN1*` (hoy `SentinelN1Vigilante`) |
| Usuario de Supabase | los de siempre | **uno nuevo por cuenta demo** |
| Noticias | corre `scripts.news_exporter` | solo **lee** el JSON (`NEWS_CALENDAR_PATH`) |

Cómo se garantiza que nuestros scripts no tocan producción:

- **`select_ours`** (`scripts/procs.py`): único filtro de "qué procesos son nuestros". Un proceso
  Python/PowerShell solo cuenta si lleva `motor.run`, `scripts.vigilante` o `run_motor.ps1`
  **y** la ruta de este repo en su línea de comandos. Un `terminal64.exe` solo cuenta si está
  bajo `MT5_CLONES_DIR`. `restart_motor.ps1` y el vigilante usan solo ese filtro.
- **Se niega a trabajar** si `MT5_CLONES_DIR` coincide con `C:\MT5_instances`, la contiene o
  está dentro de ella (`select_ours` y `provision alta` abortan).
- `motor.registry` rechaza un magic nuestro que choque con `MAGIC_M15`/`MAGIC_M5`/`MAGIC_E2`.
- `scripts\instalar_tareas.ps1` solo toca la tarea `SentinelN1Vigilante`.
- Antes de reiniciar, `restart_motor.ps1 -DryRun` muestra qué mataría. En la verificación de la
  Fase 1 debe listar **cero** procesos de producción.

## 3. Lo que NO se puede evitar: los reinicios de producción nos afectan

El `restart_all` de producción (lo dispara `SentinelRestart` tras cada alta) mata **todos** los
`terminal64.exe` del servidor, también los nuestros, y no los relanza. Producción no se
modifica, así que el bot nuevo se defiende solo:

- Nuestro código se llama `motor` y nuestro lanzador `run_motor.ps1`, para que el filtro de
  producción (`bot.main|tui|lite`, `run_instance.ps1`) no mate los procesos Python.
- Los terminales sí mueren. El **vigilante** (`SentinelN1Vigilante`, cada 3 minutos) detecta
  los motores que faltan y los relanza (con tope de 5 relanzamientos en 30 minutos por motor,
  para no entrar en un bucle si un motor se cae al arrancar). Además, el
  motor **reconecta MT5 por sí solo**: si el terminal deja de responder, tras 3 latidos
  fallidos intenta reconectar (esperas de 5, 15, 30 y 60 s) y mientras tanto no opera. Si en
  5 minutos (`MT5_GIVEUP_S`) no lo logra, sale con código 3 y es el vigilante quien lo
  vuelve a levantar.

Consecuencia práctica: si un motor "desaparece" por unos minutos justo después de un alta en
producción, es normal. Si pasa seguido, avisar a Daniel.

## 4. Provisionador y cuentas nuevas

El provisionador de producción (`SentinelProvisioner`, cada 5 minutos) toma **toda** fila
`provision_requests` en estado PENDING, sin mirar para qué motor es. Por eso, mientras
convivan:

- El bot nuevo **no usa** `provision_requests`.
- Cada cuenta demo se da de alta **a mano**:
  `python -m scripts.provision alta --user-id <uuid> --email <correo> --login <n> --server <s> --password <p> --bots n1,n2`
  (con `--dry-run` primero). Clona MT5 en `C:\MT5_instances_n1`, escribe `instances\<uuid>.env`
  y crea solo las filas que falten en Supabase (`bot_instances` inactivas, `engine_config`,
  `account_settings`).
- Cada cuenta demo es un **usuario de Supabase nuevo**, creado a mano. Así `account_settings`
  (metas, base de cálculo), que es por usuario y no por motor, nunca se cruza con producción.

## 5. Supabase y la app web

- Solo cambios **aditivos**. La migración del bot nuevo es
  `migrations/n1_001_engine_config.sql` (tabla `engine_config`).
  **Todavía NO está aplicada**: se aplica cuando Daniel apruebe el PR. Antes de aplicar hay
  que correr las consultas de solo lectura de la sección "ANTES DE APLICAR" de ese archivo
  (que la tabla no exista ya, que no haya restricciones sobre `bot_id`/`op_type`/`log_type`/`bots`,
  etc.), porque producción aplicó migraciones 010-017 que no están en este repo.
- La app web (`lozadaitm/sentinel_trading-webapp`) tiene partes fijas a `m15`/`m5` (métricas de
  cuenta, conteos por motor, el alta de usuarios). Los cambios para que un usuario solo-`n1`
  se vea completo van en un PR aparte en ese repo (revisado por Daniel y probado en el preview
  de Vercel). Lo que ven hoy los usuarios de producción no cambia.

## 6. Pasar una cuenta del motor viejo al nuevo (reemplazo)

Esto es para el día en que se decida que el bot nuevo sustituye al de producción. Se hace
después del PR al repo original (sección 8). Cuenta por cuenta, nunca todas a la vez.

1. Avisar a Daniel y elegir una cuenta con poco riesgo (idealmente sin posiciones abiertas).
2. **Dejar plana la cuenta**: apagar el motor viejo desde la app web (`is_active=false`, se
   queda en close-only) y esperar a que cierre todo. Si hace falta, cierre forzado desde la app.
3. Parar el motor viejo de esa cuenta (cuando ya no hay posiciones).
4. En el `.env` de la instancia, cambiar `BOTS=e2` por `BOTS=n1` (el motor nuevo que corresponda)
   y añadir su magic (`MAGIC_N1=...`). Ese cambio solo aplica después del PR al repo original:
   los `.env` de producción viven fuera de este repo y nunca se editan desde aquí.
5. Comprobar que existan las filas del motor nuevo para ese usuario en Supabase
   (`bot_instances` y `engine_config` con `bot_id='n1'`). Pueden crearse con `provision alta`
   o a mano.
6. Reiniciar esa instancia (`restart_motor.ps1` en el repo unificado) y vigilar el latido en
   la app web. Primera vuelta con `SHADOW_MODE=true`.
7. Encender el motor nuevo desde la app web cuando todo se vea bien.

## 7. Vuelta atrás

Es el mismo procedimiento al revés, y no se borra nada:

1. Apagar el motor nuevo desde la app web y esperar a que quede plano.
2. En el `.env`, volver a `BOTS=e2` (el motor viejo).
3. Reiniciar la instancia y encender el motor viejo.

Las filas del motor nuevo en Supabase se quedan (inactivas, sin molestar). Si la migración
`n1_001` ya estaba aplicada, tampoco hay que deshacerla: es una tabla aparte que nadie más usa.

## 8. Qué unificar en el PR al repo original

Cuando los resultados lo justifiquen, se manda un PR a `lozadaitm/sentinel_trading-bot`. Para
que arranque, el provisionador, las tareas y la app web sigan funcionando igual, hay que
unificar:

1. **Filtros de reinicio por ruta, no por nombre.** El `restart_all.ps1` de producción mata por
   nombre (`bot.*`, `run_instance.ps1`, todo `terminal64.exe`). Debe pasar a usar el filtro
   basado en la ruta del repo y la carpeta de clones (`scripts/procs.py::select_ours`).
2. **Provisionador PENDING por motor.** Que `provision_requests` lleve (o se lea de) la columna
   `bots` y cada provisionador solo tome las solicitudes de sus motores; entonces vuelve el modo
   automático (`--watch`) que aquí se eliminó.
3. **Dueño del `news_exporter`.** Hoy lo lanza y mata producción. Dejar uno solo, con un dueño
   claro (quien lo arranca en `start_all`), y que el resto solo lea el JSON.
4. **Siembra en el signup de la app web.** El signup siembra `m15`+`m5`; debe sembrar los
   motores que correspondan (leídos del registro de motores) y `engine_config`.
5. **Registro de motores único** (`engines.json`) con los motores viejos y los nuevos; el
   lanzador, el provisionador y el vigilante leen de ahí.
6. **Migraciones**: renumerar `n1_NNN` dentro de la serie del original (después de la 017)
   conservando su contenido.
7. **Tarea del vigilante** (`SentinelN1Vigilante`) pasa a llamarse con el prefijo del proyecto
   unificado, o se integra con `SentinelRestart`.

## 9. Resumen de comprobaciones antes de tocar procesos o tareas

- `Get-CimInstance Win32_Process` antes y después: mismos PIDs de `bot.lite_e2` y `news_exporter`.
- `powershell -File scripts\restart_motor.ps1 -DryRun`: nada de `...\sentinel_trading` ni de
  `C:\MT5_instances`.
- `scripts\instalar_tareas.ps1 -DryRun`: solo nombra `SentinelN1Vigilante`.
- Skill `lanzar-bot-nuevo` tiene los pasos completos.
