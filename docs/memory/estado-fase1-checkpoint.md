---
name: estado-fase1-checkpoint
description: Foto al 2026-11-03 de la Fase 1 — qué cuentas corren en el VPS (correo ↔ cuenta ↔ bot), estado de la prueba punta a punta y próximos pasos
metadata:
  type: project
---

Checkpoint del 2026-11-03. Leer junto a [[fork-fase1-decisiones]] (allí están las decisiones y los pendientes).

## Qué corre en el VPS

| Correo (Supabase) | Cuenta MT5 | Bot | Dónde |
|---|---|---|---|
| osgamingpay+1 / +2 / +3 | 26329953 / 55 / 57 | e2 (producción) | `C:\MT5_instances` — no tocar |
| prueba@sentinel.test | 26358022 (demo) | n1, n2 | `C:\MT5_instances_n1\mt5_26358022` |
| webind.ve+n1@gmail.com | 26374402 (demo) | n1, n2 | `C:\MT5_instances_n1\mt5_26374402` |
| lozada.console+n1@gmail.com | falta la demo | — | usuario creado, sin filas |

- Instancias de producción desactivadas (`_*.env` en la carpeta de producción): lozada.console 25556247,
  oscarhalabiofficial 25912766, webind.ve 25924397, oscarmartinfx 26275217, osgamingpay 26275221,
  yarimarcanelones 26275222 y **una cuenta REAL** 34087418 (daniel@webind.site). Las demos de Vantage
  caducan: 25556247 y 25924397 ya dan "Invalid account".
- Usuarios de Supabase sin instancia: oscarhalabi@proton.me y claude@test.com (origen desconocido).
- Algunas filas de producción desactivadas siguen marcando RUNNING (latido viejo): decide Daniel.
- El `.env` raíz del repo solo trae `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY`. Postgres local y
  vigilante, ver [[fork-fase1-decisiones]].

## Prueba punta a punta (Fase 1)

1. Latido en menos de 30 s — OK.
2. Dashboard — OK (Oscar, 2026-11-03, con prueba@sentinel.test): encender/apagar n1 y n2 por
   separado, meta guardada (200 %), sin filas m15/m5. El botón "Forzar cierre" sale desactivado porque
   no hay posiciones (los motores nulos no abren); el lado del bot se probó escribiendo en Supabase lo
   mismo que el botón (`is_active=false, force_close=true`): n1 y n2 lo consumieron en 4 s.
   Probar el botón de verdad cuando haya un motor que opere.
3. Producción intacta — OK (mismos PIDs antes y después).
4. Recuperación — OK: el vigilante relanzó n1 en 43 s; n2 reconectó el terminal en 47 s; tras un
   reinicio de producción (que mata también nuestros terminales) ambos volvieron en menos de 1 min.
5. Evento "vivo" en `engine_events` — OK.
6. Tests — OK (145 pasan con `LOCAL_PG_DSN` cargado).

## Próximos pasos

1. ~~Prueba 2~~ y ~~merge del PR #1 + tag `fase1-base-2026-11-03`~~: hechos el 2026-11-03. Fase 1 cerrada.
2. Pueden esperar (los motores nulos no operan): alta de lozada.console+n1 cuando Daniel cree la demo,
   y AutoTrading apagado en los clones nuevos (Ctrl+E; `provision` no lo activa si el `common.ini` ya
   existe). Hay que hacerlos antes de que un motor opere.
3. ~~Brainstorming de la Fase 2 (datos históricos)~~: hecho el 2026-11-12, ver [[fase2-datos-decisiones]].

## Avisar a Daniel la próxima vez que escriba (pedido por Oscar, 2026-11-03)

Oscar pidió que, cuando Daniel escriba, se le cuente esto (y luego se borre esta sección):
- La Fase 1 está cerrada: Oscar hizo la prueba del dashboard, el cierre forzado se probó del lado del
  bot, el PR #1 del fork se mergeó (`450fc60`) y existe el tag `fase1-base-2026-11-03`.
- **`main` del fork está protegido**: todo entra por PR, sin force-push ni borrado. Sin aprobación
  obligatoria porque Daniel no es colaborador del fork (su aprobación no cuenta para GitHub y Oscar
  no puede aprobar sus propios PR). Si Oscar lo añade como colaborador, se sube a 1 aprobación.
- **Logs CSV de los motores** (PR `fix/logs-por-cuenta`): `Motor_N1_XAUUSD+.csv` y `Motor_N2_...` se
  habían colado en git por error y además mezclaban cuentas (prueba y webind.ve escribían en el mismo
  archivo). Ahora van a `logs\Motor_<MOTOR>_<LOGIN>_<SÍMBOLO>.csv`, uno por motor y cuenta, y no se
  versionan. Los CSV viejos de la raíz quedan en disco (ignorados); se pueden borrar cuando se quiera.
  Los motores usan el nombre nuevo al reiniciarse.

**Cómo aplicar:** al retomar, ver la sección "Avisar a Daniel" y los próximos pasos; antes de tocar procesos,
foto de PIDs de producción (skill `lanzar-bot-nuevo`).
