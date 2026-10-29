---
name: fork-fresh-start
description: "2026-10-28: el equipo decidió bifurcar sentinel_trading-bot y reconstruir la lógica de trading desde cero; alcance, restricciones y orden de fases acordado"
metadata:
  node_type: memory
  type: project
---

El 2026-10-28 Oscar (equipo de 2 o más personas) decidió hacer un fork de
https://github.com/lozadaitm/sentinel_trading-bot y reconstruir la estrategia de trading casi
desde cero, conservando el flujo de trabajo y la infraestructura.

**Se conserva:** núcleo en Python; lanzamiento de instancias con `.env` y una instancia MT5
separada por cuenta; Postgres local en el VPS con sincronización esporádica a Supabase; la app
web (encendido/apagado, historial, metas); un flujo git/GitHub sólido para 2 o más personas;
documentación en lenguaje sencillo; skills compartidas dentro del repo (`.claude/skills/`);
`CLAUDE.md` y memoria siempre al día.

**Se descarta:** la lógica de trading de exp-1/exp-2/e2 y sus reglas rígidas (por ejemplo
"nunca usar SL", "apegarse rígidamente al diseño").

**Por qué:** exp-1 gustó por su agresividad pero tenía un bug (cuentas con criterios idénticos
se comportaban distinto) y, según el análisis de exp-2, "se alimentaba del caos" sin protección
contra ciclos malos; exp-2 derivó en una estrategia que no les gusta.

**Meta:** bot autosostenible que opere como un trader humano experto; aspiración de ~2x mensual
(se le advirtió a Oscar que es extremadamente ambicioso). El drawdown máximo no está fijado: se
investigará y se adaptará al régimen de mercado. Se empieza con XAU y luego se extiende a otros pares.

**Broker:** Vantage RAW 1:500 hoy; abierto a analizar otros brokers y tipos de cuenta. Dispuestos
a registrarse en APIs y comprar frameworks/herramientas de análisis en tiempo real.

**Orden de fases acordado** (cada una se diseña por separado con brainstorming):
1) base del fork (qué se queda, qué se va); 2) recolección de datos históricos; 3) investigación
(SMC, order blocks, BOS, soportes/resistencias, velas, regímenes, riesgo, fundamentales con
agentes de IA); 4) laboratorio: econometría, simulación y prueba masiva de variantes;
5) bot nuevo adaptable al régimen.

**Actualización 2026-10-28:** Oscar desconfía de TODAS las simulaciones e investigaciones de exp-2
(sesgadas por las instrucciones de ese repo): no se citan ni se construye sobre ellas; se
investiga desde cero. La misma app web (`lozadaitm/sentinel_trading-webapp`) y el mismo Supabase
deben controlar el bot viejo y el nuevo en paralelo hasta decidir cuál gana.

Ver también [[fork-fase1-decisiones]] y [[equipo-y-flujo-git]].
