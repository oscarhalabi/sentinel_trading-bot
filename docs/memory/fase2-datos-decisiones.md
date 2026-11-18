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

## Estado de 2A (2026-11-12)

- Código de 2A hecho en la rama `datos/base-almacen` (tareas 1–10 del plan, por subagentes con revisión por
  tarea y revisión final). Tests de `datos/`: 168 pasan con Postgres de prueba (155 + 13 omitidos sin él).
- **Task 11 en la laptop (2026-11-12 → 18):** hecha salvo la prioridad 1 completa (solo XAUUSD; el
  resto espera a decidir la vía de S3) y la comparación de un día con TradingView (la hace Oscar).
  Hay `docs/datos-de-mercado.md`, la skill `datos-mercado` y la línea de `datos/` en CLAUDE.md. La
  suite completa pasa en Windows (323 + 2 omitidas).
- Decisiones tomadas al implementar (Rulings del ledger de ejecución):
  - `bajar` decide qué falta mirando siempre los archivos y manifiestos; el catálogo solo se escribe.
  - La consulta nunca devuelve una vela con `ts_cierre` posterior a `hasta`, y `conectar()` también respeta
    la reserva (con `incluir_reservado=True` queda registrado).
  - `hasta` por defecto en `bajar` = hoy UTC − 2 días (Dukascopy puede tardar en publicar).
  - La comprobación de escala atrapa decimales muy mal puestos (×1000), no un ×100 dentro de la clase: eso
    se revisa con `descubrir` (decimales oficiales) y el gráfico de referencia.
  - Se corta un símbolo tras 10 días seguidos con error, o si llega a 8 semanas vacías sin ningún día bueno
    ("¿código de fuente mal puesto?"). Parar por disco bajo sale con código 1.
  - Un símbolo que reaparece en Dukascopy vuelve a activo salvo que `activo` esté en `ajustado_a_mano`.
  - `verificar` marca como hueco grave un día laborable vacío posterior al primer día bueno del símbolo, y
    normaliza el conteo de ticks por hora abierta (los domingos no dan falsas alarmas).
- Pendientes menores anotados: `verificar` con comprobación de hash; una línea cortada de
  `_registro/reserva.jsonl` hace fallar `verificar`/`catalogo estado`; huecos que cruzan medianoche se
  parten en dos; posibles colisiones de nombre canónico (revisar con la lista real).

## Task 11 en la laptop (2026-11-12): lo que salió distinto

- **Lista de instrumentos:** la URL y el Referer de `URL_INSTRUMENTOS` funcionan (JSONP de ~1 MB,
  1604 instrumentos). Los grupos cuelgan unos de otros (`parent`): acciones por país bajo `STCK_CFD`,
  índices bajo `IDX`, materias primas bajo `CMD`. No existe `FX_EXOTICS`: los exóticos van en
  `FX_CROSSES`. Meta sigue como `FB.US`. El lector ya está corregido y hay 1599 símbolos en el registro.
- **Supuesto roto: descarga libre de `datafeed.dukascopy.com`.** El primer día de XAUUSD
  (2024-05-13) falló con HTTP 503. Al seguir probando, el saludo TLS tardaba 17–20 s o fallaba,
  y después llegaron 429 ("Too Many Requests", que remite a
  `wiki.dukascopy.com/en/development/data-export/`) y 503 en peticiones sueltas. Esa página no da
  límites para `datafeed`: describe una exportación por **AWS S3 con "Requester Pays"** (bucket
  `cfg-public-proper-wallaby`, eu-west-1), donde quien descarga paga el tráfico. Falta saber si el
  bloqueo lo provocó nuestra primera ráfaga (4 en paralelo × 24 horas, con reintentos) o si ahora
  limitan a todo el mundo.
- **Despacio sí funciona** (decisión de Oscar, 2026-11-12: "probar más despacio"). Unas horas después,
  con `DUKAS_MAX_PARALELO=1` y `DUKAS_MAX_POR_SEGUNDO=1`, XAUUSD del 2024-05-13 bajó en 55 s
  (181.910 ticks, Parquet de 1,6 MB, precios de 2.332 a 2.364, spread medio de 0,35). Con
  `2` y `2`, el 2024-05-14 bajó en 30 s (218.729 ticks), sin errores. Cada petición tarda ~2 s en
  ida y vuelta: con poco paralelismo, el límite es esa espera.
- **Lo que tardaría la prioridad 1** (38 símbolos desde el inicio de cada uno): ~200.000 días, unos
  69 días de reloj a 30 s/día. XAUUSD solo (desde 2003-05-05, 8.591 días) son unos 3 días.
  Todavía no se ha probado si 4 en paralelo sin ráfagas vuelve a provocar el bloqueo.
- **Descarga completa de XAUUSD (2026-11-14):** a 2/2 guardó ~98 días con ticks por hora, y a
  3/3 ~116 (+18 %) sin 429. Decisión de Oscar: quedarse en 3/3 (4/4 no se prueba por riesgo
  de bloqueo). Salen ~3 % de días con 503 o cortes sueltos, que se reintentan en el siguiente
  lanzamiento. La descarga corre desde `C:\sentinel_datos\bajar_XAUUSD.cmd` abierto a mano: si se
  lanza desde la app de Claude, muere cuando la app se reinicia. Para la prioridad 1 hay que
  estudiar la exportación por S3 (Requester Pays).
- **XAUUSD completo (2026-11-18):** 7.328 días ok (2003-05-05 → 2026-11-16, 744 M ticks, ~6 GB),
  1.538 vacíos y 0 errores en el rango, tras 5 pasadas (los 503 sueltos salen al reintentar). El
  catálogo fijó `inicio=2003-05-05`. Antes de esa fecha la descarga anduvo 8 semanas vacías y se
  paró sola, como debe.
- **Velas M1:** 283 meses en 84 s (344 MB). Al principio `velas` no anotaba las M1 en el
  catálogo; corregido el 2026-11-18: ahora anota cada mes, también los que no cambian.
- **Consulta:** un año H1 en 0,1 s; un año M1 (356 mil velas) en 0,07 s; 22 años H1 o D1 en ~1,4 s.
- **`catalogo reconstruir`** en `sentinel_datos_test`: 9.149 particiones en 2 s; los conteos de
  ticks coinciden con el catálogo real (los 5 errores de antes de 2003 no tienen manifiesto).
- **`verificar` XAUUSD (474 s):** 37.778 problemas, 125 graves. Los graves son casi todos
  **festivos y cierres tempranos de EE. UU.** (MLK, Presidents, Memorial, 4 de julio, Labor,
  Acción de Gracias, Nochebuena, Viernes Santo, Navidad, Año Nuevo, Juneteenth): la plantilla de
  horario no sabe de festivos. En 2003 los domingos no traen datos. Pocos huecos reales posibles
  (2003-07-28, 2003-08-04, 2005-01-18). Los avisos de `salto` (36.505, de un tick y $0,2–1,8) y de
  `conteo` (983, todos antes de 2023, porque la mediana es la de los 23 años y los años viejos
  tienen muchos menos ticks) son ruido de umbral.
- **Arreglos de `verificar` (Oscar, 2026-11-18: "sí, corrígelos"):**
  - `datos/horarios.py` conoce los festivos de EE. UU. por reglas: la CME (metal, energía y
    materias primas) cierra Viernes Santo, Navidad y Año Nuevo observados (sin cerrar el viernes
    si el 1 de enero cae en sábado), y corta a las 13:00 NY en los festivos federales, el viernes
    tras Acción de Gracias y Nochebuena. NYSE (`indice_us`, `accion_us`) cierra en los festivos
    federales. No incluye cierres excepcionales.
  - Un día vacío con menos de 3 horas abiertas (domingo) es aviso, no grave.
  - `conteo` compara con la mediana de los días a ±45 días.
  - Resultado en XAUUSD: **6 graves** (antes 125). Son el Columbus Day de 2013 y 2014 (la CME
    cortaba antes ese día; no está en las reglas), 2012-06-13 de 0 a 3 UTC, y tres de 2003–2005.
    Hay 53 avisos de conteo, todos domingos flojos. Los 36.505 avisos de `salto` siguen: el umbral
    de 8 desviaciones es muy sensible para el oro (pendiente afinarlo).

## Pendientes

1. ~~Spec y plan de 2A~~ (PR #3 mergeado). Código de 2A en `datos/base-almacen`, con la Task 11 hecha:
   falta el PR a `main`, comparar un día de XAUUSD con TradingView y bajar el resto de la prioridad 1
   (antes, decidir si se usa la exportación de Dukascopy por S3: ~69 días de reloj por HTTP).
2. Claves de R2 y FRED en el `.env` de la laptop (se usan en 2B y 2C, no en 2A).
3. El PR #3 lo abrió Claude cuando Oscar reconectó GitHub (antes daba 403). La rama
   `claude/fase2-historical-data-xau-rp4wa5` no se pudo borrar desde la sesión: hay que borrarla en GitHub.
4. Remote Control con la laptop: la carpeta aparece en la lista pero no se ofrece a las sesiones
   en la nube (app de Windows). Mientras tanto, lo que exige la laptop se hace en una sesión local.
5. Afinar el umbral de `salto` de `verificar` (36.505 avisos en XAUUSD, picos de un tick).

**Cómo aplicar:** antes de tocar `datos/`, leer la spec del subproyecto. Si un supuesto resulta
falso (URL de instrumentos, decimales, velocidad, tamaños), anotarlo aquí.
