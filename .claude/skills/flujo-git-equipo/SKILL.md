---
name: flujo-git-equipo
description: Usar cuando haya que crear una rama, hacer commit, abrir un PR, pedir o dar revisión, mergear o poner un tag de versión en este repo (equipo de Oscar y Daniel).
---

# Flujo git del equipo

Reglas: `main` estable, nunca push directo, una rama por tema, PR revisado por la otra persona.

## 1. Rama
Una sola carpeta por repo en el VPS (`Documents\sentinel_trading-bot`, `Documents\sentinel_trading-webapp`):
se cambia de rama dentro de esa carpeta, sin crear carpetas extra (worktrees).
```bash
git switch main && git pull
git switch -c fase1/<tema>        # o datos/<tema>, fix/<tema>, docs/<tema>
```

## 2. Commits
Español, `tipo(ámbito): mensaje` (feat, fix, docs, refactor, test, chore). El VPS no tiene
identidad global de git: cada commit lleva la de quien lo pide.
```bash
git -c user.name="Oscar" -c user.email="oscarhalabi@proton.me" commit -m "docs(fase1): ..."
```
(Para Daniel, su nombre y correo.) Terminar el mensaje con la línea `Co-Authored-By` que
indique el entorno. Antes de commitear: `python -m pytest -q`.

## 3. PR
Antes de subir, comprobar con qué cuenta de GitHub está la terminal. En el VPS hay dos
guardadas: `oscarhalabi` (dueño del fork, el único que puede subir aquí) y `lozadaitm`
(Daniel, dueño del repo original y de la app web).
```bash
gh auth status                    # muestra cuál está activa
gh auth switch --user oscarhalabi # para el fork; --user lozadaitm para la app web
```
```bash
git push -u origin <rama>
gh pr create --base main --title "..." --body "..."
```
El cuerpo explica en lenguaje sencillo qué cambia y por qué. Si toca servidor, tareas
programadas, Supabase o la app web, decir exactamente qué cambia en el servidor.

## 4. Revisión
Primero una revisión automática (`/code-review`) y luego la otra persona. Cambios de servidor
o app web: Daniel. Estrategia/análisis: Oscar. Responder a los comentarios con commits nuevos
en la misma rama.

## 5. Merge y tag
Merge desde GitHub (PR aprobado). Si el merge pasa algo a producción:
```bash
git switch main && git pull
git tag v<N>-AAAA-MM-DD && git push origin v<N>-AAAA-MM-DD
```
Después borrar la rama y anotar en `docs/memory/` lo que haya que recordar.

## 6. Proteger `main` (una vez, en GitHub)
Settings, Branches, Add branch protection rule para `main`, marcar "Require a pull request
before merging" (y, si se quiere, "Require approvals").

## Webapp
`lozadaitm/sentinel_trading-webapp`: mismo flujo, PR revisado por Daniel y probado en el preview
de Vercel antes de mergear. Nunca push directo a su `main`.

## Hacia el repo original
Lo bueno se manda más adelante como PR a `lozadaitm/sentinel_trading-bot`
(ver `docs/convivencia-y-reemplazo.md`, sección "Qué unificar en el PR al repo original").
