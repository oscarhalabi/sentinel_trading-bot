---
name: fork-fase1-decisiones
description: Decisiones de la Fase 1 (base del fork) y pendientes conocidos para Oscar; léela antes de tocar el aislamiento con producción
metadata:
  node_type: memory
  type: project
---

Diseño completo: `docs/superpowers/specs/2026-10-28-fase1-base-del-fork-design.md`.
Plan: `docs/superpowers/plans/2026-10-28-fase1-base-del-fork.md`.
Guías: `docs/como-funciona.md` y `docs/convivencia-y-reemplazo.md`.

## Decisiones

- **Rama de trabajo:** `fase1/limpieza-multimotor`, PR a `main` del fork.
- **Paquete `motor/` (no `bot/`) y lanzador `run_motor.ps1`:** para que el filtro de reinicio de
  producción (`bot.main|tui|lite`, `run_instance.ps1`) no mate nuestros procesos Python.
- **Registro de motores** `motor/engines.json`: fuente única de `bot_id`, clase, variable del
  magic, magic por defecto y símbolos. Motores iniciales `n1` y `n2` (nulos), magics 300100 y 300200.
  Añadir un motor = una entrada. Pueden convivir varios motores en una cuenta (`BOTS=n1,n2`).
- **`bot_id` distinto de `m15`, `m5`, `e2`;** magics que no chocan con `MAGIC_M15/M5/E2`.
- **Aislamiento de producción:** clones MT5 en `C:\MT5_instances_n1` (`MT5_CLONES_DIR`); cuentas demo
  propias; `scripts/procs.py::select_ours` (ruta del repo + carpeta de clones) es el único filtro
  de "qué es nuestro"; se niega a trabajar si la carpeta de clones es la de producción.
- **Vigilante** (`SentinelN1Vigilante`, cada 3 min, tope 5 relanzamientos en 30 min): porque un
  reinicio de producción mata todos los `terminal64.exe` del servidor, también los nuestros.
- **No hay `news_exporter` propio:** se lee el JSON de `NEWS_CALENDAR_PATH` que escribe producción.
- **Altas a mano** (`python -m scripts.provision alta`), sin `provision_requests`: el provisionador
  de producción toma toda fila PENDING. Insertar-si-falta (nunca pisa filas existentes). Cada cuenta
  demo = usuario de Supabase nuevo, para que `account_settings` no se cruce.
- **Supabase solo aditivo:** `migrations/n1_001_engine_config.sql` (tabla `engine_config`, `params`
  JSONB por usuario y motor). `bot_config` no se toca. **Aplicada el 2026-11-02 por Daniel.**
- **Postgres local** (`sentinel_n1`, rol `sentinel_n1`): `motor/localdb.py` registra eventos
  (`engine_events`), best-effort: si falla, el bot sigue. Migraciones en `localdb/migrations/`,
  se aplican con `python -m scripts.localdb_migrate`. El sincronizador a Supabase y el "local-primero"
  completo quedan para la Fase 5.
- **Punto de enchufe de riesgo** `motor/risk.py::AccountRiskGuard` (hoy siempre permite).
- **Aparcado para la Fase 5 (spec 7.5):** "si el Postgres local falla, el bot no abre operaciones
  nuevas". En la Fase 1 la base local es best-effort y no bloquea nada (los motores son nulos y no
  abren). Cuando haya motores que operen, se engancha en `AccountRiskGuard`: negar aperturas si
  `engine.localdb` no está conectada.
- **Guardas añadidas en la revisión final:** un motor por (`USER_ID`, `BOT_ID`) con candado en
  `motor.run`; un usuario con instancias de producción se rechaza (`registry.assert_usuario_propio`,
  en `provision alta` y al arrancar); `run_motor.ps1` cierra su ventana si python sale con error
  para que el vigilante lo vea caído; los magics de producción por defecto (100100/100200/100300)
  cuentan aunque el `.env` no los traiga.
- **Etiquetas de operación** por `comment` `ETIQUETA|texto` (ver [[logging-taxonomy-and-ticket]]).
- **Memoria y skills en el repo:** `docs/memory/` y `.claude/skills/` (4 skills iniciales).
- **Limpieza de memoria:** se borraron las notas de estrategia vieja (Healer, Hedge, OP1-OP4, M5 v2,
  margen multi-bot, ideas y pendientes de exp-2, workflow de push directo). Siguen en el historial de git.
- **Reconexión MT5:** `motor/mt5_watch.py` detecta que el terminal no responde (3 latidos), reconecta con
  esperas crecientes y, a los 5 min (`MT5_GIVEUP_S`), el motor sale con código 3 para que el vigilante lo relance.
- **Arreglos de la revisión de código del PR #1 (2026-11-02, pedidos por Daniel):**
  1. El motor nunca se conecta a un terminal (`C:\MT5_instances`) ni a una cuenta (26329953/55/57)
     de producción, y exige `MT5_PATH` y `MT5_LOGIN` (`registry.validate_mt5_target`).
  2. Una sola validación de `.env` (`registry.validate_env`) para start_all, vigilante y motor.
  3. La TUI sale con código 1 si falla al arrancar (antes 0: el vigilante no la relanzaba).
  4. `provision alta`: primero Supabase y al final el `.env` (escritura atómica); `--user-id` solo UUID.
  5. `run_motor.ps1` y `start_all.ps1` leen el `.env` como UTF-8; Python acepta BOM (`utf-8-sig`).
  6. El vigilante reconoce motores lanzados a mano (`--env-file`/`--bot-id` en `motor.run`) y no
     duplica un mismo `USER_ID` + motor de dos `.env`.
  7. Reconexión MT5 con timeout de 30 s y latido `MT5_DOWN` antes de cada intento.
  8. Si MT5 muere entre latidos, se deja de operar al momento con un solo aviso.
  9. En modo sombra el cierre forzado se da por hecho (antes reintentaba para siempre).
  10. Un solo lector de `.env` (`registry.read_env_file`) y un solo normalizador de rutas (`procs._norm`).

- **Cuentas viejas desactivadas al bot nuevo (2026-11-03, decisión de Daniel):** las cuentas demo de
  instancias de producción desactivadas (`instances\_*.env`, p.ej. 25556247 `lozada.console` y 25924397
  `webind.ve`) se pasan al bot nuevo con **un usuario de Supabase nuevo por cuenta** (correo `+n1`) y
  la misma cuenta MT5 en un clon propio de `C:\MT5_instances_n1`. Las instancias viejas no se van a
  reactivar, así que no se añadió guarda extra. Antes de cada alta se revisa (solo lectura) que la
  cuenta no tenga posiciones abiertas de los bots viejos (magics 100100/100200).

## Pendientes conocidos (para Oscar)

1. ~~Crear las bases de Postgres local~~ Hecho el 2026-11-03 (Daniel): rol `sentinel_n1`, bases
   `sentinel_n1` y `sentinel_n1_test` en Postgres 18 del VPS, variables de usuario de Windows
   `LOCAL_PG_DSN` / `LOCAL_PG_DSN_TEST` y `001_base.sql` aplicada. Los tests de base real pasan.
   Ojo: si la clave lleva signos (`#`, `!`, `$`, `@`...), en el DSN van codificados (`%23`...), y
   en PowerShell `setx` con comillas simples. pgAdmin a veces muestra "Circular reference detected"
   en `CREATE ROLE`/`ALTER ROLE`: comprobar siempre con `pg_roles` o probando la conexión.
2. **Cuenta demo de Vantage + usuario nuevo de Supabase**, y darla de alta con
   `python -m scripts.provision alta ... --bots n1,n2`.
3. ~~Aplicar `migrations/n1_001_engine_config.sql`~~ Hecho: aplicada en Supabase el 2026-11-02 por Daniel, tras correr las consultas "ANTES DE APLICAR" (todas dieron lo esperado: sin CHECK/enum/trigger sobre bot_id, op_type, log_type ni bots). Verificado después: tabla con
   sus 6 columnas, RLS activo y política `engine_config_owner`.
4. ~~PR de la app web (Tarea 11)~~ Hecho: lozadaitm/sentinel_trading-webapp#1 aprobado por Daniel y
   mergeado el 2026-11-02 (desplegado en Vercel). El preview del PR no se generó porque el autor de los
   commits (`oscarhalabi`) no tiene acceso al proyecto en Vercel.
5. **Proteger `main` en GitHub** (Settings, Branches, Require a pull request before merging).
6. Registrar la tarea del vigilante en el servidor con `scripts\instalar_tareas.ps1` (una persona, tras `-DryRun`).
7. Verificación final de Fase 1 con producción viva: `restart_motor.ps1 -DryRun` lista cero procesos de producción.

Contexto: [[fork-fresh-start]], [[equipo-y-flujo-git]].
