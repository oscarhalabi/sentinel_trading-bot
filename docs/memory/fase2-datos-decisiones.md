---
name: fase2-datos-decisiones
description: Decisiones de la Fase 2 (datos históricos, 2026-11-12, rehecha con superpowers) — subproyectos 2A–2E, universo con ticks para todo, laptop de investigación, sin ver el futuro, reserva de 12 meses; pendientes
metadata:
  type: project
---

Mapa de la fase: `docs/superpowers/specs/2026-11-12-fase2-datos-historicos-design.md`.
Spec de 2A: `docs/superpowers/specs/2026-11-12-fase2a-nucleo-datos-design.md`.
Rama `fase2/datos-historicos`, PR oscarhalabi/sentinel_trading-bot#3.

## Decisiones (Oscar, 2026-11-12)

- **Brainstorming rehecho con superpowers** (pedido de Oscar). La Fase 2 se partió en
  subproyectos con su propia spec, plan y PR: 2A núcleo (laptop), 2B Vantage + R2, 2C contexto,
  2D mantenimiento y 2E libro de órdenes.
- **Universo "todo lo posible"** (acciones como NVDA, ETFs y cripto incluidos), con **ticks para
  todo**, también para lo que Vantage no ofrece hoy. La prioridad 1 (lo operable en Vantage)
  solo decide el orden de descarga. Supabase no se usa para guardar mercado.
- **Máquina:** el bot en el VPS; los datos y la simulación en la **laptop de Oscar**, con su clon
  en `C:\Users\oscar\code\sentinel_trading-bot` y Python + Postgres instalados (2026-11-12).
- **Sin ver el futuro:** las velas llevan `ts_cierre`; las uniones entre mercados se hacen sobre él.
- **Reserva de 12 meses con corte móvil**; acceder exige `incluir_reservado=True` y queda anotado.
- **Descargador propio** de Dukascopy (frente a una librería de terceros o comprar un histórico).
- **Sumar ventajas pequeñas:** no se descarta una estrategia por rendir poco si suma sin moverse
  igual que las demás.
- **Rentabilidad:** aspiración de ~200 % mensual con días buenos de ~10 % (Oscar bajó desde el
  "10 % diario"). Se le advirtió que está muy por encima de lo sostenible; el laboratorio medirá
  con costos, drawdown y probabilidad de ruina, sin martingalas.
- **Proyección como un trader humano** y **librería única de indicadores** (la misma en el
  laboratorio y en el bot): Fases 3–5.
- **Biblioteca científica** (papers y libros serios): primer bloque de la Fase 3.
- **Libro de órdenes:** MT5/Vantage no tiene uno central para el oro y las divisas. Las fuentes
  reales (CME, Nasdaq, Binance) se evalúan en 2E.
- **MiroFish:** no se fuerza (ver [[mirofish-evaluacion]]).
- **Vantage para datos:** demo 26415345, creada por Oscar (falta confirmar que es RAW y su
  servidor). Las demos se cierran por inactividad (~2 meses, según Oscar); para 2B se prefiere
  una **cuenta real mínima** solo para datos.
- **R2:** en la cuenta de Cloudflare de Oscar, con dos buckets (`sentinel-datos` y
  `sentinel-datos-vps`), porque las claves de R2 se limitan por bucket.

## Pendientes

1. Oscar revisa la spec de 2A; después, plan de 2A con superpowers:writing-plans.
2. Claves de R2 y FRED en el `.env` de la laptop (se usan en 2B y 2C, no en 2A).
3. El PR #3 lo abrió Claude cuando Oscar reconectó GitHub (antes daba 403). La rama
   `claude/fase2-historical-data-xau-rp4wa5` no se pudo borrar desde la sesión: hay que borrarla en GitHub.
4. Remote Control con la laptop: la carpeta aparece en la lista pero no se ofrece a las sesiones
   en la nube (app de Windows). Mientras tanto, lo que exige la laptop se hace en una sesión local.

**Cómo aplicar:** antes de tocar `datos/`, leer la spec del subproyecto. Si un supuesto resulta
falso (URL de instrumentos, decimales, velocidad, tamaños), anotarlo aquí.
