# Datos de mercado (Fase 2A)

El paquete `datos/` baja y guarda datos históricos de mercado (ticks de Dukascopy) y los
sirve a la investigación y al laboratorio. Corre en la **laptop de Oscar**, no en el VPS.
Supabase no se usa para esto.

Diseño completo: `docs/superpowers/specs/2026-11-12-fase2a-nucleo-datos-design.md`.

## Qué hay (2026-11-18)

- **XAUUSD completo**: 7.328 días con ticks, del 2003-05-05 al 2026-11-16 (744 millones de
  ticks, ~6 GB), más sus velas M1 (283 meses, 344 MB).
- El registro `datos/simbolos.json` tiene **1.599 símbolos** de Dukascopy: 38 de
  prioridad 1 (lo operable en Vantage) y el resto de prioridad 2. Solo XAUUSD está bajado.

## Dónde está

Todo cuelga de la carpeta `DATA_DIR` (en la laptop, `C:\sentinel_datos`):

| Carpeta | Qué guarda |
|---|---|
| `ticks/fuente=dukascopy/simbolo=XAUUSD/anio=2024/2024-05-13.parquet` | Ticks de un día (bid, ask, volúmenes) |
| `m1/fuente=dukascopy/simbolo=XAUUSD/anio=2024/2024-05.parquet` | Velas de 1 minuto de un mes |
| `_manifiestos/` | Una ficha por archivo: estado (`ok`/`vacio`), filas, hash, de qué ticks salió |
| `_registro/` | Candado de descarga, accesos a la reserva y las salidas de los lanzadores |
| `_informes/` | Informe HTML de cada `verificar` |

**La verdad son los archivos y sus manifiestos.** El catálogo de Postgres (`DATOS_PG_DSN`,
base `sentinel_datos`) es solo un espejo para consultar el estado rápido; si se pierde, se
rehace con `python -m datos catalogo reconstruir`.

## Variables

En el `.env` de la raíz del repo o como variables de usuario de Windows. **Nunca** se
guardan claves en el repo.

| Variable | Para qué | Por defecto |
|---|---|---|
| `DATA_DIR` | Carpeta de los datos | (obligatoria) |
| `DATOS_PG_DSN` | Postgres del catálogo | sin catálogo |
| `DATOS_PG_DSN_TEST` | Postgres para los tests | se omiten esos tests |
| `DATOS_DISCO_MIN_GB` | La descarga para si queda menos espacio | 50 |
| `DATOS_CORTE_RESERVA` | Fija el inicio de la reserva (AAAA-MM-DD) | hoy − 365 días |
| `DUKAS_MAX_PARALELO` | Peticiones a la vez a Dukascopy | 3 |
| `DUKAS_MAX_POR_SEGUNDO` | Peticiones por segundo a Dukascopy | 3 |

## Cómo se baja

Todo desde la raíz del repo, con el `.venv` activo:

```powershell
python -m datos catalogo migrar                  # una vez: crea las tablas del catálogo
python -m datos simbolos descubrir               # mira qué ofrece Dukascopy (añade --aplicar)
python -m datos bajar --simbolo XAUUSD           # de hoy − 2 días hacia atrás
python -m datos bajar --prioridad 1 --max-horas 6
python -m datos velas                            # arma las M1 de los meses que cambiaron
python -m datos verificar                        # busca problemas y escribe el informe
python -m datos catalogo estado                  # resumen por símbolo
```

- **Ritmo:** con 4 en paralelo y 20 por segundo, Dukascopy respondió con errores 429 y 503. A 3 y 3
  baja unos 116 días con ticks por hora sin bloqueos. Un símbolo con 23 años tarda ~2,5 días.
- **Se puede cortar y retomar:** `bajar` mira los archivos para saber qué falta. Los días con
  error (503 o cortes sueltos, ~5–8 %) se reintentan en la siguiente corrida; con 3–5 pasadas
  no queda ninguno.
- **Sabe dónde empieza cada símbolo:** al encontrar 8 semanas seguidas vacías se para y anota el
  inicio en el catálogo.
- **Para no depender de la app de Claude**, la descarga larga se lanza con doble clic en
  `C:\sentinel_datos\bajar_XAUUSD.cmd` (fija el ritmo a 3/3 y la prioridad baja). Si se lanza
  desde la app, muere cuando la app se reinicia. Conviene pausar Windows Update mientras dura.

## Cómo se consulta

```python
from datetime import date
from datos import consulta

h1 = consulta.cargar("XAUUSD", date(2024, 1, 1), date(2025, 1, 1), tf="h1")
varios = consulta.cargar_varios(["XAUUSD", "EURUSD"], date(2024, 1, 1), date(2024, 6, 1),
                                tf="m15", rellenar="ultimo_conocido")
t = consulta.ticks("XAUUSD", date(2024, 5, 13), date(2024, 5, 14))
con = consulta.conectar()   # DuckDB con las vistas `ticks` y `m1`, para SQL libre
```

- Temporalidades: `m1`, `m5`, `m15`, `m30`, `h1`, `h4`, `d1`. El día (`d1`) va de 17:00 a 17:00
  de Nueva York (o `corte_diario="utc"`).
- Columnas de las velas: `ts`, `ts_cierre`, `open`, `high`, `low`, `close` (bid), `ask_close`,
  `spread_min`, `spread_medio`, `spread_max`, `ticks`, `bid_vol`, `ask_vol`.
- **Sin ver el futuro:** una vela solo existe a partir de su `ts_cierre`. `cargar` nunca devuelve
  una vela que cierre después de `hasta`, y `cargar_varios` junta mercados por `ts_cierre`.
- Las fechas van como `date` o `datetime` (no texto).
- Velocidad medida: un año en H1 en 0,1 s; 22 años en H1 en ~1,3 s.

## La reserva

Los **últimos 12 meses** están guardados aparte para la prueba final de cualquier estrategia:
si se usan para investigar, la prueba deja de ser honesta. Por eso `cargar`, `ticks`,
`cargar_varios` y `conectar` los recortan solos. Para verlos hay que pedir
`incluir_reservado=True`; cada acceso queda anotado en `_registro/reserva.jsonl` y en el catálogo.

## El informe de `verificar`

| Tipo | Qué es | Gravedad |
|---|---|---|
| `coherencia` | bid > ask, precios ≤ 0, tiempos que retroceden, duplicados | grave |
| `hueco` | Horas con el mercado abierto y sin ningún tick | grave si son 3 h o más |
| `conteo` | Día con menos del 20 % de los ticks de los días cercanos (±45 días) | aviso |
| `salto` | Un tick que se aleja mucho y vuelve enseguida (pico de un tick) | aviso |
| `posible_split` | Acciones: apertura que es 2, 3, 4, 5 o 10 veces el cierre anterior | aviso |

Los horarios (`datos/horarios.py`) conocen los fines de semana, la pausa diaria y los festivos
de EE. UU. por reglas: la CME para oro, energía y materias primas, y la NYSE para índices y
acciones. No conocen cierres excepcionales (funerales de Estado, huracanes) ni festivos de otros
países: esos salen como huecos y se explican a mano.

En XAUUSD quedan 6 huecos graves explicados: el Columbus Day de 2013 y 2014, un corte el
2012-06-13 y tres de 2003–2005. Los avisos de `salto` (~36.500) son picos de un tick de $0,2–1,8;
el umbral está pendiente de afinar.
