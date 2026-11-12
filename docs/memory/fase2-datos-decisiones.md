---
name: fase2-datos-decisiones
description: Decisiones del brainstorming de la Fase 2 (datos históricos, 2026-11-12) — fuentes, almacén, máquinas, universo, calendario, tipo de cuenta — y pendientes
metadata:
  type: project
---

Diseño: `docs/superpowers/specs/2026-11-12-fase2-datos-historicos-design.md`.
Plan: `docs/superpowers/plans/2026-11-12-fase2-datos-historicos.md`. Rama del diseño: `fase2/datos-historicos`.

## Decisiones de Oscar (2026-11-12)

- **Resolución:** todos los estilos, scalping incluido, así que se guardan **ticks bid/ask**; las velas M1 salen de los ticks y las temporalidades mayores se calculan al vuelo.
- **Fuentes de precio:** Vantage MT5 (lo reciente y lo que llegue en adelante) + Dukascopy (historia larga, gratis). Queda un hueco para futuros de pago (GC del CME...) si la Fase 3 los pide.
- **Almacén:** Parquet + DuckDB; catálogo en Postgres (`sentinel_datos`, en el PC), reconstruible desde los manifiestos. **Varios pares desde el principio** (Oscar no quiere depender de uno solo).
- **Máquina de investigación:** el **PC de Oscar con Windows** (1 TB o más de SSD). El VPS solo captura Vantage. (La sesión de Claude en la nube es temporal y no sirve para guardar datos.)
- **Traspaso y respaldo:** bucket Cloudflare R2 (unos 6 USD/mes con 400 GB). Se respalda todo.
- **Contexto:** calendario histórico (API de pago, se elige tras probar; unos 20–30 USD/mes) + foto diaria del JSON de Forex Factory; FRED con fechas de publicación; mercados relacionados; COT y GLD.
- **Universo:** lo más amplio posible: unos 30 símbolos (metales, 7 mayores, 12 cruces, 6 índices, WTI/Brent, DXY).
- **Captura VPS:** clon propio `C:\MT5_instances_n1\mt5_datos` con una demo RAW nueva (la crea Daniel), tarea `SentinelN1Datos` cada hora que no se queda corriendo. Sin Supabase.
- **RAW o Standard:** Oscar creía que RAW era un "costo fijo de una vez". Se le aclaró que es una **comisión por lote** con spread casi nulo. Se decidió **medirlo**: un clon Standard opcional para que la Fase 4 compare los costos con spreads reales.
- **Hora:** todo en UTC; la regla de Vantage (GMT+2/+3) se confirma con un desfase 0 frente a Dukascopy.

## Pendientes

1. Revisión del PR del diseño por Oscar (y Daniel para lo del VPS).
2. Task 0 del plan: Python y Postgres en el PC, cuenta R2 + clave FRED (Oscar), verificar los símbolos en cada fuente.
3. **Demo de datos creada (2026-11-12, por Oscar): cuenta 26415345** en Vantage. Falta confirmar que es RAW
   y su servidor; la clave no se anota aquí. Se usará con `provision clon-datos` (Task 7 / Task 13) en
   `C:\MT5_instances_n1\mt5_datos`. La demo Standard opcional (7.10) sigue sin crear.
4. R2 va en la cuenta de Cloudflare que ya tiene Oscar (bucket `sentinel-datos`).
5. El PR del diseño lo abre Oscar a mano: desde la sesión de Claude en la nube, GitHub devuelve 403 al crear PRs.

**Cómo aplicar:** antes de implementar cualquier parte de `datos/`, leer el spec. Si un supuesto del spec resulta falso (profundidad, nombres, regla de hora), anotarlo aquí.
