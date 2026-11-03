## Fork y Fase 1 (bot nuevo)
- [Fork desde cero](fork-fresh-start.md) — 2026-10-28: se reconstruye la estrategia desde cero; qué se conserva/descarta, orden de las 5 fases; no usar conclusiones de exp-2
- [Decisiones de la Fase 1](fork-fase1-decisiones.md) — motor/, registro de motores, aislamiento de producción, vigilante, altas a mano, migración n1_001 (aplicada 2026-11-02) y pendientes de Oscar
- [Checkpoint Fase 1 (2026-11-03)](estado-fase1-checkpoint.md) — qué cuentas corren (correo ↔ cuenta ↔ bot), prueba punta a punta (Fase 1 cerrada) y próximos pasos
- [Equipo y flujo git](equipo-y-flujo-git.md) — Oscar (estrategia/análisis) y Daniel (app web/servidor); ramas, PR, revisión cruzada, tags, identidad por commit

## Infraestructura heredada (describen el bot de producción exp-1/e2; la mecánica sigue valiendo)
- [Logging: taxonomía y ticket](logging-taxonomy-and-ticket.md) — log_types de bot_logs, columna ticket; las aperturas se etiquetan por comment `ETIQUETA|texto`
- [Supabase + multi-instancia](supabase-multi-instance-model.md) — 1 proceso=1 usuario=1 cuenta; service-role bypassa RLS; is_active=false es close-only (no pause duro)
- [NewsGuard Fase 1](newsguard-phase1.md) — bloqueo duro por rojas USD vía JSON de calendario; observación por default (`news_enforce=false`)
- [Exportador de noticias en Python](news-exporter-python.md) — scripts/news_exporter.py alimenta el JSON del NewsGuard; lo corre producción, el bot nuevo solo lo lee
- [Objetivo de ganancia + cierre forzado](profit-target-and-force-close.md) — account_settings (% objetivo, claim atómico → email + close-only) y bot_instances.force_close; migración 004
- [Retiros + comisión USDT](withdrawals-commission-usdt.md) — retiro declarado → comisión → pago USDT BEP20 verificado on-chain → desbloqueo; deuda PENDING bloquea reactivación; migración 007
- [Signup privado + provisioner](private-signup-provisioning.md) — invitación de un solo uso → /signup → cola provision_requests → provisioner clona MT5 y escribe el .env; migración 008 (el bot nuevo usa `provision alta` a mano)
- [Clon MT5: sembrar perfil IPC](mt5-clone-ipc-profile-seed.md) — los clones dan IPC timeout (-10005) si falta el perfil de runtime; provision siembra el config
