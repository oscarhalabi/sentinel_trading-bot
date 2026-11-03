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
2. **Pendiente (Daniel):** desde el dashboard con prueba@sentinel.test encender/apagar n1 y n2, cierre
   forzado y meta; luego verificar en Supabase y logs, y que el primer login no creó filas m15/m5.
3. Producción intacta — OK (mismos PIDs antes y después).
4. Recuperación — OK: el vigilante relanzó n1 en 43 s; n2 reconectó el terminal en 47 s; tras un
   reinicio de producción (que mata también nuestros terminales) ambos volvieron en menos de 1 min.
5. Evento "vivo" en `engine_events` — OK.
6. Tests — OK (145 pasan con `LOCAL_PG_DSN` cargado).

## Próximos pasos

1. Daniel hace la prueba 2; se anota el resultado aquí.
2. Mergear el PR #1 del fork (`fase1/limpieza-multimotor`, ya aprobado) y poner tag `fase1-base-<fecha>`.
3. Alta de lozada.console+n1 cuando Daniel cree la demo.
4. Detalles: AutoTrading apagado en los clones nuevos (Ctrl+E; `provision` no lo activa si el
   `common.ini` ya existe); proteger `main` en GitHub.
5. Después: brainstorming de la Fase 2 (datos históricos).

**Cómo aplicar:** al retomar, empezar por el paso 1 o el que el equipo indique; antes de tocar procesos,
foto de PIDs de producción (skill `lanzar-bot-nuevo`).
