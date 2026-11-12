---
name: mirofish-evaluacion
description: Evaluación (2026-11-12) del repo 666ghj/MiroFish pedida por Oscar — qué es, qué no es, riesgos y cómo probarlo en la Fase 3 sin contaminar el laboratorio
metadata:
  type: reference
---

Oscar pidió estudiar a fondo https://github.com/666ghj/MiroFish para usarlo en la investigación.
Revisión del código (commit del 2026-10-02):

## Qué es
- "Motor de inteligencia de enjambre": se le suben documentos semilla (noticias, informes) y una
  pregunta. Un LLM arma un grafo de conocimiento (Zep Cloud, GraphRAG), genera cientos o miles de
  personajes y los simula con **OASIS (CAMEL-AI)** en un **Twitter y un Reddit simulados**
  (publicar, dar like, repostear, seguir; rondas de ~1 h simulada). Al final, un ReportAgent
  (patrón ReACT) escribe un informe de predicción, y se puede chatear con los agentes.
- Stack: Flask + Vue, Python 3.11–3.12 (no 3.14), `camel-oasis`, `camel-ai`, `zep-cloud`, `openai`
  (cualquier LLM compatible). Licencia **AGPL-3.0**.

## Qué NO es
- No tiene nada de mercados: ni precios, ni velas, ni órdenes, ni backtesting. "Financial
  prediction" figura como "coming soon". Simula **opinión pública y narrativas**, no la formación
  de precios.

## Riesgos para nosotros
1. **No se puede validar con historia:** el LLM ya sabe qué pasó en 2015–2025. Una "predicción"
   de un evento pasado está contaminada, así que solo vale probarlo **hacia delante**.
2. **Caro y no determinista:** consume muchos tokens (avisan de usar menos de 40 rondas) y dos
   corridas iguales dan resultados distintos.
3. **Dependencia de Zep Cloud** (servicio externo, cuota gratis limitada).
4. **AGPL:** usarlo como herramienta aparte está bien. Si se copia su código dentro de nuestro bot,
   el bot hereda la AGPL. Por eso: **instalarlo aparte, nunca mezclarlo con el repo**.

## Decisión de Oscar (2026-11-12)
- **No se fuerza.** La idea era aprovechar sus piezas para un sandbox propio. Como es limitado (no
  tiene nada de mercados), solo se retoma si en la Fase 3 aparece una pieza concreta que valga la pena.

## Cómo encajaría si se retoma
- **Fase 2: nada que integrar.** Solo hay que asegurarse de que el calendario y los precios
  tengan `ts` UTC fiable, que es lo que necesitará la evaluación.
- **Fase 3: experimento acotado, aparte** (su propia carpeta o Docker en el PC de Oscar). Antes de
  cada evento rojo (FOMC, NFP, CPI) se le da contexto y se le pide una dirección y una intensidad
  del oro, y se **registra la predicción con su hora antes del evento**. Después se puntúa con
  nuestros ticks (Fase 2) contra una referencia tonta (p. ej. "sin cambio" o "lo que dice la
  previsión del consenso"). Con unos 20–30 eventos se decide si aporta algo. Costo de tokens
  acotado por evento.
- **Ideas reutilizables** aunque no sirva como predictor: el grafo de conocimiento sobre
  documentos de investigación y el agente de informes con herramientas, construidos con
  nuestras propias piezas para los agentes de la Fase 3.
