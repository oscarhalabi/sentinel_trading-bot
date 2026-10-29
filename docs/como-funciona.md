# Cómo funciona el sistema

Explicado sin jerga. Para el plan de convivencia con producción ve a
[`convivencia-y-reemplazo.md`](convivencia-y-reemplazo.md).

## Las piezas

- **La app web** (otro repo, `sentinel_trading-webapp`, en Vercel): donde la persona
  enciende y apaga su bot, ve el historial, fija metas y retira dinero.
- **Supabase**: la base de datos en la nube. Es el "buzón" entre la app web y el bot. La app
  escribe órdenes (encender, apagar, cierre forzado, meta) y el bot escribe lo que está
  pasando (latido, estado, posiciones, logs).
- **El motor** (carpeta `motor/`): el programa Python que opera. Cada motor tiene un nombre
  (`bot_id`, hoy `n1` y `n2`) y un número de identificación en el broker (el *magic*).
  Hoy son motores **nulos**: no operan, solo prueban que todo lo demás funciona.
- **MetaTrader 5 (MT5)**: el terminal del broker. Hay **uno por cuenta**, en una carpeta
  propia (`C:\MT5_instances_n1\mt5_<login>`). El motor le habla con la librería `MetaTrader5`.
- **Postgres local**: base de datos en el propio servidor, para datos del bot de alta
  frecuencia. En Fase 1 solo registra eventos del motor (tabla `engine_events`).
- **El vigilante** (tarea programada `SentinelN1Vigilante`): cada 3 minutos mira qué motores
  deberían estar vivos y relanza los que falten. Además, cada motor reconecta MT5 por sí solo
  si el terminal deja de responder (y si no lo logra en 5 minutos, sale para que lo relance el vigilante).
- **El servidor (VPS)**: el Windows donde corre todo, incluido el bot de producción (e2),
  que **no** es parte de este repo.

## Diagrama

```
   Persona --> App web (Vercel) <---------> Supabase (nube)
                                              ^   |
                      encendido/apagado,      |   | latido, estado, posiciones, logs
                      cierre forzado, metas   |   v   (cada 3 s lee / cada 15 s escribe)
                                        +-----+-------------+
                                        |  Motor n1 / n2    |  <-- scripts\run_motor.ps1
                                        |  (motor/, Python) |      (lo lanza start_all.ps1
                                        +--+-------------+--+       o el vigilante)
                         ordenes/precios  |             |  eventos (best-effort)
                                          v             v
                               Terminal MT5        Postgres local
                         (C:\MT5_instances_n1\...)   (engine_events)
                                          |
                                          v
                                       Broker

   Noticias: el motor LEE el archivo news_calendar.json que ya escribe producción
             (no lanza su propio exportador).
   Vigilante (SentinelN1Vigilante, cada 3 min) --> relanza motores caídos.
```

## Cómo se habla todo

1. **Control (app web a bot).** El motor lee Supabase cada ~3 segundos:
   `bot_instances.is_active` (encendido/apagado), `force_close` (cierre forzado) y
   `account_settings` (metas). `is_active=false` no es "pausa dura": el bot deja de abrir
   cosas nuevas pero sigue cerrando lo que tenga abierto (modo *close-only*).
2. **Estado (bot a app web).** Cada ~15 segundos el motor escribe su latido
   (`bot_instances.last_heartbeat`, `bot_status`), su estado (`bot_state`), sus posiciones
   (`bot_positions`) y sus logs (`bot_logs`). Si el latido tiene más de ~60 s, la app web
   considera el proceso caído.
3. **Configuración del motor.** Parámetros del motor en la tabla `engine_config` de Supabase
   (campo JSON `params`). **La migración `n1_001` todavía no está aplicada** (espera a que
   Daniel la revise); hasta entonces el motor corre con configuración vacía.
4. **Identidad.** Todo se filtra por `user_id` + `bot_id`. Dos motores en la misma cuenta se
   distinguen en MT5 por su magic (`MAGIC_N1=300100`, `MAGIC_N2=300200`) y nunca se ven las
   posiciones del otro.
5. **Registro de motores.** `motor/engines.json` es la única lista de motores que existen:
   `bot_id`, clase de Python, variable de su magic, magic por defecto, símbolos opcionales.
   Añadir un motor = añadir una entrada ahí (más su fila en Supabase). El lanzador, el
   provisionador y el vigilante leen de aquí.
6. **Guardián de riesgo.** `motor/risk.py` es un punto de enchufe: antes de abrir cualquier
   operación el broker le pregunta si se permite. Hoy siempre dice que sí; después mirará el
   riesgo de toda la cuenta.

## Una instancia = un `.env`

Cada usuario tiene `instances\<usuario>.env`: credenciales de Supabase, `USER_ID`, la cuenta
MT5, y `BOTS=n1,n2` (los motores que corren sobre esa cuenta). Plantilla:
[`instances/example.env`](../instances/example.env). No se sube a git (tiene contraseñas).

`scripts\run_motor.ps1` carga ese `.env`, fija `BOT_ID` y `MAGIC_NUMBER` (pidiéndoselo a
`python -m motor.registry magic <bot_id>`) y ejecuta `python -m motor.run`.

## Los scripts

| Script | Qué hace |
|--------|----------|
| `scripts\start_all.ps1` | Arranque completo: revisa los `.env`, corre los tests, abre un motor por ventana. `-DryRun`, `-Lite`, `-SkipTests` |
| `scripts\run_motor.ps1` | Arranca UN motor (`-EnvFile`, `-BotId`, `-Mode console/lite/tui`) |
| `scripts\restart_motor.ps1` | Reinicia solo los procesos de este repo y relanza `start_all`. `-DryRun` para mirar antes |
| `python -m scripts.vigilante` | Relanza motores caídos (lo ejecuta la tarea `SentinelN1Vigilante`). `--dry-run` |
| `scripts\instalar_tareas.ps1` | Registra (o `-Desinstalar`) la tarea `SentinelN1Vigilante`. `-DryRun` |
| `python -m scripts.provision alta ...` | Da de alta una cuenta a mano: clona MT5, escribe el `.env`, siembra filas en Supabase |
| `python -m scripts.procs list --json` | Lista los procesos que son de este repo (lo usa `restart_motor.ps1`) |
| `python -m scripts.localdb_migrate` | Crea/actualiza las tablas del Postgres local |
| `python -m motor.registry check <env>` | Valida un `.env` (motores conocidos, magics distintos) |
| `scripts\news_exporter.py` | Exportador de noticias. **Lo corre producción; este repo no lo lanza** |

## Postgres local: preparación (una vez)

Lo hace Oscar (o quien administre el servidor) como superusuario `postgres`. Cambia `<pass>`
por una contraseña real:

```powershell
psql -U postgres -c "CREATE ROLE sentinel_n1 LOGIN PASSWORD '<pass>';"
psql -U postgres -c "CREATE DATABASE sentinel_n1 OWNER sentinel_n1;"
psql -U postgres -c "CREATE DATABASE sentinel_n1_test OWNER sentinel_n1;"
```

Luego dile al bot dónde está la base:

- **`LOCAL_PG_DSN`**: la base real. Ponla en el `.env` de la instancia, o como variable de
  entorno:
  ```
  LOCAL_PG_DSN=postgresql://sentinel_n1:<pass>@localhost:5432/sentinel_n1
  ```
  Sin esta variable el bot arranca igual, solo avisa que no hay base local.
- **`LOCAL_PG_DSN_TEST`**: solo para los tests de la base. **Debe apuntar a una base cuyo
  nombre termine en `_test`** (los tests BORRAN `engine_events` y `schema_migrations` de ahí; si
  el nombre no acaba en `_test` o es igual a `LOCAL_PG_DSN`, el test se niega a correr):
  ```powershell
  setx LOCAL_PG_DSN_TEST postgresql://sentinel_n1:<pass>@localhost:5432/sentinel_n1_test
  ```
  (abre una consola nueva para que `setx` surta efecto) y prueba con
  `python -m pytest tests/test_localdb.py -v`; deben correr también los 2 tests de base real.

Crear las tablas (con `LOCAL_PG_DSN` definido):

```powershell
python -m scripts.localdb_migrate
```

Aplica en orden las migraciones de `localdb\migrations\` (hoy `001_base.sql`: `schema_migrations`
y `engine_events`) y es seguro repetirlo.

Si el Postgres local falla, el motor sigue funcionando y avisa una sola vez en el log.

## Qué hay en cada carpeta

| Carpeta | Contenido |
|---------|-----------|
| `motor/` | El bot: broker (MT5), base de datos, registro, bucle principal, motores en `motor/engines/` |
| `scripts/` | Lanzadores, vigilante, alta de cuentas, exportador de noticias |
| `instances/` | `.env` por usuario (solo `example.env` va a git) |
| `migrations/` | SQL de Supabase. Las nuestras llevan prefijo `n1_` |
| `localdb/migrations/` | SQL del Postgres local |
| `logs/` | Logs del bot, del vigilante y de los reinicios (no va a git) |
| `docs/` | Esta documentación, memoria en `docs/memory/`, diseños en `docs/superpowers/` |
| `.claude/skills/` | Recetas paso a paso para Claude y para las personas |
| `tests/` | Pruebas automáticas (`python -m pytest -q`) |

## Dónde seguir leyendo

- Contrato bot a app web (tablas, columnas): [`dashboard-handoff-multibot.md`](dashboard-handoff-multibot.md).
- Memoria del proyecto: [`memory/MEMORY.md`](memory/MEMORY.md).
