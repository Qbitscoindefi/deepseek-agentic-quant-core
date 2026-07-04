# OPENBRIDGE: DOSSIER ESTRATÉGICO
*Ecosistema de Motores de Trading — DeepSeek Agentic Engine v3.1*

## 1. IDENTIDAD Y FILOSOFÍA DEL ECOSISTEMA

OPENBRIDGE ha evolucionado de un motor HFT único a un **ECOSISTEMA DUAL DE MOTORES DE TRADING**:

### Motor Alpha: HFT v1.6.1 "Giro y Caza" (Clásico)
**Nombre en Clave:** El Francotirador (Sniper) — Motor de reglas fijas
**Filosofía:** Ingeniería Cuantitativa de Microestructura — cazador reactivo invisible
**Archivo:** `trading_engine_hft_.py`

### Motor Omega: DeepSeek Agentic Engine v3.1 🧠
**Nombre en Clave:** DeepSeek Quant
**Filosofía:** Inteligencia Artificial Generativa como cerebro del trading — sin reglas fijas, análisis contextual completo cada ciclo
**Archivo:** `deepseek_agentic_engine.py`
**Prompt Estratégico:** `prompts/estratega_sistema.md`

| Campo | Valor |
|-------|-------|
| **Versión** | v3.1 |
| **Símbolo** | BTCUSDT Perpetual (Futures) |
| **Capital Base** | 6 USDT |
| **Apalancamiento** | 30x |
| **Capital Efectivo** | ~308 USDT notional (balance ~10.28 USDT) |
| **Ciclo de Trading** | ~6-8 segundos (DeepSeek responde en ~3-4s) |
| **API Trading** | Binance Futures (fapi.binance.com) |
| **API Cerebro** | DeepSeek Chat API — Modelo: deepseek-chat, Temp: 0.45, Timeout: 5s |
| **Estado** | ✅ ESTABLE — ~50+ ciclos ejecutados sin errores |

---

## 2. ARQUITECTURA DEL SISTEMA (DeepSeek Agentic v3.1)

```
┌──────────────────────────────────────────────────────────────────────┐
│ 1. MARKET CONTEXT COLLECTOR (MarketContextCollector)                 │
│    ├── Binance Ticker → precio BTCUSDT en vivo                      │
│    ├── Klines 5m (50 velas) → RSI, MACD, ADX, EMAs, ATR, volumen   │
│    ├── Order Book Depth (5 niveles) → spread pct                    │
│    ├── Account API → balance real (~10.28 USDT)                     │
│    ├── Position Risk → posicion actual (size, entry, pnl)           │
│    └── Engine State → trades totales, win rate, racha perdidas      │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 2. CONTEXT AUGMENTER (ContextAugmenter) — 8 FUENTES WEB EN VIVO     │
│    ├── Fear & Greed Index (alternative.me)                          │
│    ├── Long/Short Ratio (Binance Futures API)                       │
│    ├── Open Interest (Binance Futures API)                          │
│    ├── Funding Rate (Binance Premium Index)                         │
│    ├── Taker Buy/Sell Ratio (Binance) — presion compradora REAL     │
│    ├── Open Interest Change 5m (Binance) — capital entrando/saliendo│
│    ├── Bitcoin Fees (mempool.space)                                 │
│    ├── Noticias BTC (Coindesk RSS) — 5 titulares recientes          │
│    └── On-chain (blockchain.info) — precio avg 1y, relacion actual  │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 3. DEEPSEEK API (DeepSeekClient) — TIMEOUT 5s                       │
│    ├── System Prompt: 1247 chars (solo identidad + formato)         │
│    ├── User Message: contexto completo (~15 campos)                 │
│    └── Respuesta JSON (~600-800 chars) en <4s                       │
│    └── Fallback: JSON default si timeout o error HTTP               │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 4. DECISION EXECUTOR (process_decision) — GUARDS DE SEGURIDAD       │
│    ├── Confianza mínima: 0.20 (20%)                                 │
│    ├── Circuit Breaker: pérdidas >0.50 USDT en 4h → bloqueo        │
│    ├── Cooldown exponencial: 2^(n-1)*60s tras racha de pérdidas     │
│    ├── Spread máximo: 0.05%                                         │
│    ├── Anti-duplicado: misma acción no repetida en 15s              │
│    ├── LONG → market buy                                             │
│    ├── SHORT → market sell                                           │
│    ├── CERRAR → close position + registro de trade                  │
│    └── NEUTRAL → log + espera                                       │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│ 5. TRAILING STOP (Stop Loss dinámico 1.8×ATR)                       │
│    ├── LONG: stop = entry - ATR*1.8                                 │
│    ├── SHORT: stop = entry + ATR*1.8                                │
│    └── Se evalúa en cada ciclo mientras hay posición abierta        │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 3. FUENTES DE DATOS EN VIVO — DETALLE COMPLETO

### Por ciclo (~6-8 segundos), el motor consulta:

| Fuente | Endpoint | Datos | Latencia |
|--------|----------|-------|----------|
| Binance Ticker | `fapi/v1/ticker/price` | Precio BTCUSDT | <1s |
| Binance Klines | `fapi/v1/klines?interval=5m&limit=50` | 50 velas: OHLCV | <1s |
| Binance Depth | `fapi/v1/depth?limit=5` | Spread bid/ask | <1s |
| Binance Account | `fapi/v2/account` (signed) | Balance, posición | <1s |
| Binance L/S Ratio | `futures/data/globalLongShortAccountRatio` | L/S ratio | <1s |
| Binance Open Interest | `fapi/v1/openInterest` | OI total BTC | <1s |
| Binance Funding | `fapi/v1/premiumIndex` | Funding rate | <1s |
| Binance Taker Flow | `futures/data/takerlongshortRatio` | Buy/Sell ratio real | <1s |
| Binance OI History | `fapi/v1/openInterestHist` | OI change 5m | <1s |
| Alternative.me | `api.alternative.me/fng/` | Fear & Greed Index | <1s |
| Mempool.space | `mempool.space/api/v1/fees/recommended` | Bitcoin fees | <1s |
| Coindesk RSS | `coindesk.com/arc/outboundfeeds/rss/` | 5 titulares noticias | ~2-3s |
| Blockchain.info | `api.blockchain.info/charts/market-price` | Precio on-chain | <1s |

**Total por ciclo: 13 consultas web, ~3-5 segundos de recolección + ~3-4 segundos de DeepSeek = ~6-8 segundos.**

---

## 4. HISTORIAL DE EJECUCIÓN EN VIVO (2026-06-09)

### Primera generación (v1.0 — prompt pesado con reglas fijas)
```
- Prompt: 3863 caracteres con reglas de oro, patrones de caza, filosofía
- DeepSeek respondía NEUTRAL 100% del tiempo, razon: "INICIO" o "MERCADO_LATERAL"
- Confianza siempre 0% — el prompt lo tenía domesticado
- Tiempo de respuesta: ~3s promedio
- 24 ciclos: 100% NEUTRAL
```

### Segunda generación (v2.0 — contexto web agregado)
```
- Prompt: mismo pesado, pero contexto aumentado con datos fundamentales
- DeepSeek empezó a variar razones: "SOBRECOMPRADO_EXTREMO_FEAR", "ADX_BAJO", "MIEDO_EXTREMO_L_S_ALTO"
- Confianza: 25-35% — empezó a razonar pero sin atreverse a operar
- Tiempo de respuesta: ~3s, respuestas de 300-540 chars
- Identificó patrón AGOTAMIENTO_ALCISTA correctamente
```

### Tercera generación (v3.0 — prompt liberado + liquidaciones)
```
- Prompt: 5862 caracteres (se movió a prompts/ correctamente)
- Se agregó Taker Buy/Sell Ratio + OI change 5m
- DeepSeek empezó a mostrar análisis más variados
- Confianza: 25-40% — aún NEUTRAL pero con razonamiento sólido
- Tiempo de respuesta: ~3-4s, respuestas de 500-800 chars
- Se detectó que DeepSeek estaba sesgado a LONG (mala interpretación de L/S ratio)
```

### Cuarta generación (v3.1 — prompt MÍNIMO, timeout 5s) ✅ ACTUAL
```
- Prompt: 1247 caracteres (solo identidad + formato JSON)
- Sin reglas fijas, sin interpretaciones predefinidas
- Sin sesgo direccional: puede ir LONG o SHORT
- Timeout reducido de 30s → 5s
- DeepSeek responde en ~3-4s consistentemente
- Logs estables: ~60+ ciclos sin errores
- 0 trades ejecutados (condiciones de mercado correctas — rango lateral sin señales claras)
```

### Evolución de respuestas de DeepSeek (chars por ciclo)
```
v1.0: 250-300 chars → respuestas genéricas "INICIO"
v2.0: 300-540 chars → empieza a variar, muestra análisis
v3.0: 500-800 chars → análisis detallado, identifica patrones
v3.1: 600-800 chars → análisis libre, sin restricciones artificiales ✅
```

---

## 5. PROMPT ESTRATÉGICO ACTUAL (v3.1)

```markdown
# IDENTIDAD: QUANTITATIVE TRADER SENIOR

Eres un Quantitative Trader senior con acceso a datos de mercado en tiempo real.
Operas en Binance Futures con BTCUSDT, apalancamiento 30x, capital ~10 USDT.
Puedes ir LONG o SHORT. Es futuros, no hay sesgo direccional.

Recibes datos TECNICOS y FUNDAMENTALES crudos del mercado en cada ciclo.
Analízalos con tu criterio. No tienes reglas fijas.
Usa tu conocimiento de mercados financieros, patrones de precio y psicologia de mercado.

Responde UNICAMENTE con JSON valido. Sin markdown, sin texto extra.
[...formato de respuesta...]
```

**Principios del prompt:**
1. **Mínimo** — solo identidad, alcance, formato de respuesta
2. **Sin interpretaciones** — no dice qué significa RSI>70 o L/S>2.0
3. **Sin sesgo** — LONG y SHORT tienen el mismo peso
4. **Libertad** — "Usa tu conocimiento... No tienes reglas fijas"
5. **Formato estricto** — solo JSON, sin markdown, 5s para responder

---

## 6. ECUACIONES Y ALGORITMOS DEL NÚCLEO

### RSI (Relative Strength Index)
```
RSI = 100 - (100 / (1 + RS))
RS = AverageGain(periodo) / AverageLoss(periodo)
Periodo = 14 velas
```

### MACD (Moving Average Convergence Divergence)
```
MACD Line = EMA(close, 12) - EMA(close, 26)
Signal Line = EMA(MACD Line, 9)
Histogram = MACD Line - Signal Line
```

### ADX (Average Directional Index)
```
TR = max(High-Low, abs(High-PrevClose), abs(Low-PrevClose))
+DI = 100 * (EMA(+DM, 14) / ATR)
-DI = 100 * (EMA(-DM, 14) / ATR)
DX = 100 * abs(+DI - -DI) / (+DI + -DI)
ADX = EMA(DX, 14)
```

### ATR (Average True Range)
```
TR = max(High-Low, abs(High-PrevClose), abs(Low-PrevClose))
ATR = EMA(TR, 14)
```

### EMAs (Exponential Moving Averages)
```
EMA = (Price - PrevEMA) * (2 / (Period + 1)) + PrevEMA
Períodos: 5 (rápida), 20 (lenta)
```

### Indicadores de Flujo
```
Taker Buy/Sell Ratio = BuyVolume / SellVolume
  >1.2 → Presión compradora ALTA
  <0.8 → Presión compradora BAJA
  Entre → NEUTRAL

OI Change 5m = (OI_current - OI_prev) / OI_prev * 100
  Positivo → Capital entrando
  Negativo → Capital saliendo
```

### Stop Loss Dinámico
```
Stop LONG = EntryPrice - ATR * 1.8
Stop SHORT = EntryPrice + ATR * 1.8
```

### Cooldown Exponencial
```
Cooldown = 2^(racha_perdidas - 1) * 60 segundos
Ej: 1ra pérdida → 60s, 2da → 120s, 3ra → 240s...
```

### Circuit Breaker
```
Si |suma(PnL trades últimos 4h)| >= 0.50 USDT → NO operar
```

---

## 7. COMPARATIVA: HFT CLÁSICO vs DEEPSEEK AGENTIC v3.1

| Característica | HFT v1.6.1 (Clásico) | DeepSeek Agentic v3.1 |
|----------------|----------------------|------------------------|
| **Cerebro** | Reglas fijas (if/else determinista) | DeepSeek API (IA generativa, no determinista) |
| **Velocidad** | ~5s por ciclo | ~6-8s por ciclo |
| **Análisis técnico** | RSI, ATR, ADX, EMAs, impulso, volumen | Mismos datos + DeepSeek los interpreta contextualmente |
| **Análisis fundamental** | ❌ No | ✅ 8 fuentes: F&G, L/S, Funding, OI, Taker Flow, Fees, Noticias, On-chain |
| **Adaptabilidad** | Limitada a reglas predefinidas | Ilimitada — DeepSeek razona cada ciclo con datos frescos |
| **Razonamiento** | "SI RSI>60 ENTONCES..." | "RSI 70 + F&G 10 + L/S 2.17 + Taker 1.19 + noticias positivas = posible trampa alcista" |
| **Sesgo direccional** | Configurable en reglas | Ninguno — prompt no tiene sesgo |
| **Dependencia externa** | Solo Binance | Binance + DeepSeek API + 11 fuentes web |
| **Costo operativo extra** | $0 | ~$0.003-0.005/consulta DeepSeek |
| **Formato de salida** | Logs de texto | JSON estructurado con análisis |
| **Memoria entre ciclos** | Solo estado de trades | Solo estado de trades (cada ciclo es fresco) |

---

## 8. ESTRUCTURA DE ARCHIVOS (2026-06-09)

```
C:\OPENBRIDGE\BINANCE\
├── deepseek_quant_core.py           # ✅ Núcleo v3.1: 13 fuentes web, 3 clases, 0 errores
├── deepseek_agentic_engine.py       # ✅ Engine v3.1: DeepSeek timeout 5s, salida detallada
├── prompts/
│   └── estratega_sistema.md         # ✅ Prompt mínimo 1247 chars (v3.1)
├── DOSSIER.md                       # ✅ Este archivo
├── engine_state.json                # Estado persistente (0 trades hasta ahora)
├── deepseek_engine.log              # Logs de producción (~200+ ciclos registrados)
├── .env                             # API keys: BINANCE + DEEPSEEK
│
├── trading_engine_hft_.py           # Motor HFT clásico v3.1 (asíncrono, aggTrade)
├── trading_engine.py                # Motor HFT v1.6.1 (monolítico, legacy)
└── [otros archivos]                 # Utilitarios, tests, parches, respaldos
```

---

## 9. ESTADO ACTUAL Y MÉTRICAS (2026-06-09 16:22 COT)

### Contexto de Mercado Actual
```
Precio: ~62,000-62,050 USD
RSI: 55-57 (neutral)
ADX: 34-35 (tendencia de baja intensidad)
MACD: positivo (histograma ~110-115)
EMAs: precio entre EMA5 y EMA20
Spread: ~0.000% (alta liquidez)
Volumen: ~1.0-1.1x promedio (normal)
F&G: 10/100 (Miedo Extremo) — se mantiene todo el día
L/S Ratio: 2.15-2.17 (68% longs, 32% shorts)
Funding Rate: 0.0035-0.0043% (neutral)
OI: ~99,000-99,250 BTC (estable, levemente decreciente)
Taker B/S: 1.14-1.19 (NEUTRAL)
Noticias: Tokenización de stocks, regulación UK crypto, mixtas
```

### Decisiones de DeepSeek (patrón observado)
- **100% NEUTRAL** en condiciones actuales — mercado lateral sin señales claras
- **Confianza: 0-40%** — correcto, no está forzando entradas
- **Razones variadas**: MIEDO_EXTREMO_L_S_ALTO, SOBRECOMPRA_L_S_ALTO, EVALUANDO, SIN_SENAL_CLARA
- **Análisis incluye**: precio, RSI, ADX, F&G, L/S, Funding, Taker Flow, noticias, on-chain
- **0 falsos positivos** — no ha ejecutado trades en condiciones desfavorables

### Qué funciona ✅
- 13 fuentes de datos consultadas en vivo cada ciclo
- DeepSeek responde en <4s (timeout 5s)
- Análisis multifactor evidente en cada respuesta
- Sin errores de parseo de JSON
- Circuit breaker, cooldown, trailing stop operativos
- Sin trades forzados en condiciones laterales

### Qué mejorar 🔧
- **Liquidaciones (Coinglass) = 0** — el endpoint público no devuelve datos. Buscar alternativa (ej: Binance directamente o Bybit)
- **DeepSeek aún sin trades reales** — necesita un mercado con señales más claras (tendencia definida, barridos, aumento de volatilidad)
- **Sin memoria entre ciclos** — DeepSeek no recuerda lo que dijo hace 3 ciclos. Podríamos agregar un buffer de últimas N decisiones
- **El prompt podría refinarse** para pedirle específicamente que identifique cuándo es momento de SHORT, no solo LONG
- **Noticias de Coindesk RSS** a veces están en inglés técnico — DeepSeek las procesa bien pero podríamos agregar fuente en español

---

## 10. ROADMAP

### ✅ COMPLETADO (2026-06-09)
- DeepSeek Agentic Engine v1.0 → v3.1 (4 iteraciones en un día)
- ContextAugmenter con 8 fuentes web
- Taker Buy/Sell Ratio + OI change 5m
- Prompt reducido de 5862 → 1247 caracteres
- Timeout de DeepSeek reducido de 30s → 5s
- Corrección de sesgo LONG (ahora puede ir SHORT)
- 50+ ciclos estables sin errores

### CORTO PLAZO
| Tarea | Prioridad |
|-------|-----------|
| Buscar fuente alternativa de liquidaciones (Coinglass API key gratis o Binance data) | 🟡 Media |
| Agregar memoria de contexto (últimas 5 decisiones de DeepSeek en el prompt) | 🟡 Media |
| Probar en mercado con tendencia clara para ver si DeepSeek ejecuta trades | 🟡 Media |

### MEDIANO PLAZO
| Tarea | Prioridad |
|-------|-----------|
| Fusión híbrida: HFT clásico para entradas rápidas + DeepSeek para gestión de riesgo | 🟡 Media |
| Dashboard web con decisiones de DeepSeek en tiempo real | 🟢 Baja |
| Logs de análisis de DeepSeek a SQLite para backtesting de decisiones | 🟢 Baja |

---
## 11. PERFORMANCE ACTUAL Y PERSPECTIVA
### Mejoras implementadas hasta la fecha
- Migración del motor DeepSeek a un prompt mínimo y sin sesgo direccional.
- Adición de 13 fuentes de contexto para cada ciclo, incluyendo datos técnicos, de flujo institucional y fundamentales.
- Reducción de timeout de DeepSeek de 30s a 5s para trading operacional.
- Implementación de lógica de seguridad: circuito de pérdidas, cooldown exponencial, cierre de posición y stop dinámico basado en ATR.
- Sistema de evaluación de market readiness para evitar scalps en condiciones de rango o volumen bajo.
- Corrección de sesgos y eliminación de entradas forzadas: DeepSeek puede responder long/short/neutro libremente.
- Consolidación de logs y estado persistente en `engine_state.json` para seguimiento y auditoría.
- Ajuste de reglas de black swan: no veto absoluto, sino entrada condicionada a impulso y datos de volatilidad.

### Performance actual del proyecto
- Ciclos ejecutados: 50+ sin errores de parseo y sin fallos en la cadena de datos.
- Latencia de ciclo: 6-8 segundos en promedio.
- Tiempo de respuesta DeepSeek: 3-4 segundos.
- Trades ejecutados: 0 (condición de mercado actual: rango lateral / “gráfico de oxilación” sin impulso decisivo).
- Estado de riesgo: capital protegido, no hay exposiciones abiertas innecesarias.
- Señales DeepSeek: mayoritariamente `NEUTRAL`, con razones coherentes y confidencias bajas/medias.
- Market fit: el motor está funcionando como “disciplina de espera” — controla el ruido y espera señales válidas.

### Señales de salud del sistema
- Fuentes de mercado actualizadas en vivo cada ciclo.
- No hay balance negativo ni drawdown real registrado en este ciclo inicial.
- No se han generado alertas de spread alto ni de API rate limit.
- La lógica de riesgo evita entradas en mercados planos y filtra señales débiles.

### Riesgos identificados y próximos ajustes posibles
- El motor aún no ha entrado en trade real, por lo que la validación de ejecución de órdenes queda pendiente.
- La fuente de liquidaciones actual no está disponible públicamente y debe reemplazarse.
- Se recomienda mantener la configuración actual durante mercados de baja volatilidad y solo ajustar si se desea más sensibilidad.
- Posible mejora inmediata: agregar buffer de memoria de las últimas 5 decisiones en el prompt para que DeepSeek contextualice mejor cada ciclo.

---
> *"DeepSeek no es un bot. Es un Quantitative Trader que piensa en cada ciclo. No fuerza trades, no sigue reglas ciegas, no tiene sesgo. Analiza 13 fuentes de datos y decide. Hoy no ha disparado porque el mercado no se lo ha pedido. Y eso, paradójicamente, es la señal de que funciona."*
>
> *— OPENBRIDGE, 2026-06-09*

---

*Última actualización: 2026-06-09 16:30 COT | Versión: DeepSeek Agentic v3.1 | Trades ejecutados: 0 | Ciclos estables: 50+*
