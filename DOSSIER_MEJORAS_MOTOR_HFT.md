# DOSSIER — Mejoras Motor HFT v3.1 (Opción 2 Audit)

**Fecha:** 2026-06-08  
**Motor:** `trading_engine_hft.py` (LIVE Binance Futures)  
**Objetivo:** Corregir desincronización ciclo 7s vs bursts WebSocket aggTrade

---

## Diagnóstico (audit)

| Problema | Causa raíz |
|----------|------------|
| Entradas perdidas en bursts | Ciclo polling 7s no coincide con ventanas aggTrade de 5s |
| Umbrales demasiado estrictos | 2 BTC / 70% imbalance filtran señales válidas |
| Abortos post-fill | MAX_SLIPPAGE 0.020% demasiado ceñido |
| Re-entrada lenta tras pérdida | Cooldown 480s (3 pérdidas) + confianza 50% |

---

## Registro de cambios

### 1. `trading_engine_hft.py` — Event-driven + parámetros HFT

| Parámetro | Antes | Después | Rationale |
|-----------|-------|---------|-----------|
| `aggression_min_volume` | 2.0 BTC | **1.0 BTC** | Captar bursts medianos reales |
| `aggression_min_imbalance` | 0.70 | **0.60** | SHORT umbral: 0.30 → **0.40** |
| `window_seconds` | 5.0 | **3.0** | Alinear ventana con latencia de burst |
| `CYCLE_TIME` (HFT) | 7.0 | **5.0** (configurable `.env`) | Ciclo más rápido sin saturar API |
| `on_aggression_spike` | No conectado | **Cola asyncio + handler** | Entrada inmediata en burst |
| `tech_analysis_ttl` | N/A | **15s** | Cache técnico para fusión event-driven |
| `tech_opposition_threshold` | N/A | **0.35** | Bloquear solo si técnica opuesta fuerte |

**Arquitectura brain/eyes/hands:**
- **Eyes** (`aggression_analyzer.py`): detecta burst, llama callback sync (solo encola).
- **Brain** (`hft_run_cycle`): análisis técnico cada ciclo, cachea `latest_analysis`.
- **Hands** (`open_position` heredado): ejecuta con guards existentes (cooldown, CB, slippage).

**Efecto esperado:** Entradas en <100ms tras burst aggTrade cuando técnica no contradice con confianza ≥35%.

---

### 2. `aggression_analyzer.py` — Umbrales burst alineados

| Parámetro | Antes | Después |
|-----------|-------|---------|
| `burst_threshold` | 0.75 (hardcoded) | **0.60** (configurable) |
| `burst_min_volume_btc` | 1.0 (hardcoded) | **1.0** (configurable) |
| `burst_cooldown_seconds` | 2.0 | **2.0** (sin cambio) |

---

### 3. `trading_engine.py` — Parámetros base LIVE

| Parámetro | Antes | Después | Rationale |
|-----------|-------|---------|-----------|
| `MAX_SLIPPAGE_PCT` | 0.020% | **0.040%** | Evitar cierres falsos por ruido book |
| `MIN_CONFIDENCE_AFTER_LOSES` | 0.50 | **0.40** | Re-entrada más ágil tras racha |
| Cooldown 3 pérdidas | 480s | **240s** | Exponente `losses-2` en vez de `losses-1` |

**Cooldown efectivo tras cambio:**

| Pérdidas consecutivas | Antes | Después |
|----------------------|-------|---------|
| 0-1 | 120s | 120s |
| 2 | 240s | 120s |
| 3 | 480s | **240s** |
| 4+ | 960s | 480s |

---

### 4. `.env` — Variables HFT configurables

Nuevas claves (opcionales, con defaults en código):

```
HFT_AGGRESSION_MIN_VOLUME=1.0
HFT_AGGRESSION_MIN_IMBALANCE=0.60
HFT_AGGRESSION_WINDOW_SEC=3.0
HFT_CYCLE_TIME=5.0
HFT_BURST_THRESHOLD=0.60
HFT_BURST_MIN_VOLUME=1.0
HFT_TECH_ANALYSIS_TTL=15.0
HFT_TECH_OPPOSITION_THRESHOLD=0.35
```

---

## Log de implementación

- [x] 2026-06-08 — Lectura código: `on_aggression_spike` existía pero sin wiring en motor
- [x] 2026-06-08 — `aggression_analyzer.py`: burst thresholds configurables
- [x] 2026-06-08 — `trading_engine.py`: slippage, confianza post-pérdida, cooldown
- [x] 2026-06-08 — `trading_engine_hft.py`: cola event-driven + parámetros .env
- [x] 2026-06-08 — `.env`: variables HFT añadidas
- [x] 2026-06-08 — **v3.2 Rentabilidad**: burst snapshot + alineación temporal, event-driven NEUTRAL, RANGE más estricto, OPPOSITE_IMPULSE menos agresivo
- [x] 2026-06-08 — **v3.3 Rentabilidad**: fix análisis obsoleto al boot, precalentado técnico, SIGNAL_DECAY desactivado en AGG_SPIKE, salida AGG_REVERSAL, skip fire-check en bursts
- [x] 2026-06-08 — **v3.4 Rentabilidad**: guards FOMO/ATR en spike handler, fuerza NEUTRAL 1.25+ (1.5 en RECOVERY), TIMEOUT_PROFIT min 3.5% margen, fix registro SLIPPAGE_ABORT
- [ ] Pendiente post-deploy: verificar logs `[SPIKE]` y `⚡ EVENT-DRIVEN` en LIVE

---

## Diagnóstico rentabilidad (últimas 1000 líneas log — 2026-06-08)

| Métrica | Valor |
|---------|-------|
| Trades sesión | 14 |
| Win rate | 14.3% (2W / 12L) |
| PnL acumulado | +11.10 USDT (1 outlier +10.82) |
| Modo mercado | RANGE (ADX ~20.88) |
| Bursts detectados | Cientos |
| EVENT-DRIVEN disparos | **0** (motor corriendo versión pre-v3.1) |

### Causas raíz de pérdidas

1. **Desync técnica vs aggTrade**: `SHORT_RANGE` dispara por impulso negativo en velas 5m, pero aggTrade en ventana 3s muestra compra (Imbalance 90%+).
2. **Event-driven nunca activo**: Bursts con técnica NEUTRAL — oportunidad perdida. Reiniciar con `trading_engine_hft.py` v3.1+ obligatorio.
3. **Slippage abort con umbral viejo**: Trade 21:18:59 abortado a 0.0244% > 0.02%.
4. **OPPOSITE_IMPULSE demasiado sensible**: LONG cerrado a -0.22% ROI por impulso opuesto mínimo.
5. **SHORT_RANGE over-trading**: 8+ señales SHORT/7min en rango, casi todas bloqueadas.

### Cambios v3.2 aplicados

| Cambio | Efecto |
|--------|--------|
| `get_recent_burst()` + `HFT_BURST_ALIGNMENT_SEC=5` | Ciclo polling acepta burst reciente alineado |
| Event-driven con técnica NEUTRAL + fuerza ≥1.0 | Captura bursts sin señal técnica previa |
| `RANGE_CONFIDENCE` 0.35→0.42, `RANGE_IMPULSE` 0.12→0.22 | Menos SHORT_RANGE falsos |
| `OPPOSITE_IMPULSE` 0.15→0.30 ATR, min loss -1% | No cerrar en micro-fluctuaciones |

---

## Reinicio del motor

```powershell
cd C:\OPENBRIDGE\BINANCE
# Detener instancia actual (Ctrl+C en terminal del motor)
python trading_engine_hft.py
```

## Qué vigilar en logs

| Patrón | Significado |
|--------|-------------|
| `🟢/🔴 [BURST DETECTADO]` | Eyes detectó burst aggTrade |
| `⚡ EVENT-DRIVEN LONG/SHORT` | Hands disparó por evento (no ciclo 7s) |
| `[SPIKE] ... bloqueado — técnica opuesta` | Guard brain: no entrar contra señal fuerte |
| `[SPIKE] Sin análisis técnico fresco` | Motor aún calentando; normal primeros 15s |
| `Slippage OK: adverso=... <= 0.04%` | Nuevo umbral slippage activo |
| `Entrada bloqueada por cooldown: ...s` | Cooldown reducido (240s max en 3 pérdidas) |
