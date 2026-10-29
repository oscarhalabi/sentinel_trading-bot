# Sentinel — bot de trading nuevo (fork)

Este repo es el fork `oscarhalabi/sentinel_trading-bot`. Aquí se construye un **bot de
trading nuevo** para MetaTrader 5 (empezando por oro, XAU). La estrategia vieja (exp-1,
exp-2, e2) ya no está: solo se quedó la infraestructura (lanzar instancias con un `.env`,
un terminal MT5 por cuenta, la app web con Supabase).

**Estado:** Fase 1 (base del fork). Hoy el bot tiene dos motores "nulos" (`n1` y `n2`):
se conectan a MT5 y a Supabase, mandan latido, estado y posiciones, y obedecen
encendido/apagado y cierre forzado desde la app web, pero **no abren operaciones**. La
estrategia real llega en las fases siguientes (datos, investigación, laboratorio, bot nuevo).

Este bot **convive en el mismo servidor** con el bot de producción (e2) sin tocarlo. Antes
de hacer nada con procesos o tareas, lee [`docs/convivencia-y-reemplazo.md`](docs/convivencia-y-reemplazo.md).

## Documentos

| Documento | Para qué |
|-----------|----------|
| [`docs/como-funciona.md`](docs/como-funciona.md) | Las piezas del sistema y cómo se hablan |
| [`docs/convivencia-y-reemplazo.md`](docs/convivencia-y-reemplazo.md) | Cómo convive con producción y cómo se hará el cambio (y la vuelta atrás) |
| [`CLAUDE.md`](CLAUDE.md) | Reglas de trabajo del proyecto (para personas y para Claude) |
| [`docs/memory/`](docs/memory/MEMORY.md) | Memoria del proyecto: decisiones y datos importantes |
| `.claude/skills/` | Recetas paso a paso (lanzar el bot, migraciones, flujo git, memoria) |

## Arrancar en 5 pasos

Todo se corre desde la carpeta del repo, en PowerShell, en el servidor (Windows).

1. **Instalar las dependencias**
   ```powershell
   python -m pip install -r requirements.txt
   ```
2. **Preparar el Postgres local** (una sola vez; guía en
   [`docs/como-funciona.md`](docs/como-funciona.md), sección "Postgres local: preparación"),
   y crear las tablas:
   ```powershell
   python -m scripts.localdb_migrate
   ```
3. **Dar de alta una cuenta demo** (necesitas una cuenta demo de Vantage y un usuario
   nuevo de Supabase). Primero en modo prueba (`--dry-run`), luego de verdad:
   ```powershell
   python -m scripts.provision alta --user-id <uuid> --email <correo> --login <numero> `
       --server <servidor> --password <clave> --bots n1,n2 --dry-run
   ```
   Esto clona MetaTrader 5 en `C:\MT5_instances_n1` y escribe `instances\<uuid>.env`
   (la plantilla es [`instances/example.env`](instances/example.env)). Para la primera
   vuelta pon `SHADOW_MODE=true` en ese `.env`.
4. **Arrancar los motores**
   ```powershell
   scripts\start_all.ps1 -DryRun      # revisa todo y dice qué abriría
   scripts\start_all.ps1 -Lite        # arranca de verdad (una ventana por motor)
   ```
5. **Dejar el vigilante** que relanza lo que se caiga (lo registra una persona, una vez):
   ```powershell
   scripts\instalar_tareas.ps1 -DryRun
   scripts\instalar_tareas.ps1
   ```

Para reiniciar solo lo nuestro: `powershell -File scripts\restart_motor.ps1 -DryRun`
(mira antes, sin matar nada) y luego sin `-DryRun`.

## Tests

```powershell
python -m pytest -q
```

Los tests no tocan MetaTrader 5 ni Supabase reales.

## Cómo se trabaja en equipo

- `main` está protegida: no se sube nada directo. Una rama por tema y un PR revisado por la
  otra persona (Oscar: estrategia y análisis; Daniel: app web y servidor).
- Commits en español con formato `tipo(ámbito): mensaje`.
- Detalle en [`CLAUDE.md`](CLAUDE.md) y en la skill `flujo-git-equipo`.

## Más información

La app web (Next.js en Vercel) vive en otro repo: `lozadaitm/sentinel_trading-webapp`. El
contrato entre el bot y la app web está en [`docs/dashboard-handoff-multibot.md`](docs/dashboard-handoff-multibot.md).
El diseño completo de la Fase 1 está en `docs/superpowers/specs/2026-10-28-fase1-base-del-fork-design.md`.
