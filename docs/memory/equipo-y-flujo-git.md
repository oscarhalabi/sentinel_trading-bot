---
name: equipo-y-flujo-git
description: Quién trabaja en el proyecto (Oscar: estrategia y análisis; Daniel: app web y servidor) y el flujo git acordado el 2026-10-28
metadata:
  node_type: memory
  type: user
---

**Personas.** Oscar (quien dirige el proyecto) se ocupa de estrategia y análisis, y también de
infraestructura cuando la domina; consulta a Daniel por llamada. Daniel trabaja sobre todo en la
app web y en el servidor/VPS. Se comunica en español, en lenguaje sencillo. Por eso los PRs de
servidor y app web llevan una explicación clara de qué cambia y por qué.

**Flujo git acordado (2026-10-28):**
- `main` del fork (`oscarhalabi/sentinel_trading-bot`) se mantiene estable; **sin push directo**
  (proteger la rama en GitHub: Settings, Branches, "Require a pull request").
- Una rama por tema (`faseN/<tema>`, `datos/<tema>`) y un PR revisado por la otra persona (o
  primero revisión de Claude).
- Commits en español, `tipo(ámbito): mensaje`.
- Tags en cada paso a producción: `vN-AAAA-MM-DD`.
- Identidad por commit: el VPS no tiene identidad global de git; cada commit lleva la de quien
  lo pide (`git -c user.name=... -c user.email=...`; Oscar: `Oscar <oscarhalabi@proton.me>`).
- **Cuentas de GitHub en el VPS** (2026-11-02): `gh` tiene guardadas `oscarhalabi` (puede subir
  al fork) y `lozadaitm` (Daniel; repo original y app web, sin permiso de escritura en el fork).
  Antes de un push: `gh auth status` y, si hace falta, `gh auth switch --user <cuenta>`.
- **Una carpeta por repo** en el VPS (pedido de Daniel, 2026-10-30): `Documents\sentinel_trading`
  (producción, no tocar), `Documents\sentinel_trading-bot` (fork) y `Documents\sentinel_trading-webapp`
  (app web, se queda en `main`). Se cambia de rama dentro de la carpeta, sin worktrees extra.
- Lo bueno se manda después como PR al repo original `lozadaitm/sentinel_trading-bot`.
- Webapp (`lozadaitm/sentinel_trading-webapp`): rama y PR revisado por Daniel, probado en el
  preview de Vercel; nunca push directo a su `main`.

Receta paso a paso: skill `flujo-git-equipo`. Contexto: [[fork-fresh-start]].
