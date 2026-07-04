# Trading Engine v2.0 - Registro de Cambios

## Fecha: 2026-06-07
## Motor: OpenBridge BTC/USDT Futures Trading Engine

---

## 🚀 MEJORAS PRINCIPALES v2.0

### 1. ADAPTIVE ADX THRESHOLD
**Problema original:** El motor usaba un umbral fijo de ADX=10, lo que en mercados de baja volatilidad bloqueaba oportunidades y en mercados volátiles permitía demasiado ruido.

**Solución implementada:**
- Umbral base reducido de 10 a 8
- Sistema de historial de volatilidad (24 horas)
- Cálculo dinámico: si la volatilidad actual < promedio × 0.7, umbral baja un 20%
- Si volatilidad actual > promedio × 1.5, umbral sube un 20%
- Rango protegido: nunca baja de 6, nunca sube de 15
- Parámetros configurables: `ADX_BASE_THRESHOLD`, `ADX_MIN_THRESHOLD`, `ADX_MAX_THRESHOLD`

**Archivos modificados:** `trading_engine.py` - Clase `TechnicalAnalyzer`

---

### 2. MODO SCALP EN RANGO (NUEVO)
**Problema original:** El motor permanecía 100% en NEUTRAL durante días cuando el mercado estaba en rango lateral (ADX < 10), perdiendo oportunidades de scalping en consolidación.

**Solución implementada:**
- Nuevo modo `RANGE_MODE_ENABLED` (activo por defecto)
- Cuando ADX < 18 (configurable) y hay suficiente volatilidad, activa modo rango
- Umbrales de confianza reducidos de 0.35 a 0.30
- Impulso mínimo de 0.08 ATR (vs 0.15 en modo trending)
- Filtro FOMO relajado: RSI < 35/65 (vs < 38/62)
- Detección automática: campo `is_range_mode` en análisis
- Prefijo de razón: `LONG_RANGE` / `SHORT_RANGE`

**Parámetros clave:**
```python
RANGE_MODE_ENABLED = True         # Activar modo rango
RANGE_ADX_MAX = 18.0               # ADX máximo para modo rango
RANGE_CONFIDENCE_THRESHOLD = 0.30   # Confianza mínima en rango
RANGE_IMPULSE_THRESHOLD = 0.08     # Impulso mínimo en rango
RANGE_ATR_DISTANCE_MAX = 1.5       # Distancia ATR máxima desde SMA
```

**Archivos modificados:** `trading_engine.py` - Método `_determine_signal()`

---

### 3. RECONEXIÓN ROBUSTA CON BACKOFF EXPONENCIAL
**Problema original:** Errores de conexión (`NameResolutionError`) resultaban en fallos instantáneos sin reintento. Errores de rate limit (429) no manejados.

**Solución implementada:**
- Sistema de retry con hasta 5 intentos
- Backoff exponencial: espera 2^n segundos entre intentos (2, 4, 8, 16, 32)
- Manejo de rate limit HTTP 429 con header `Retry-After`
- Manejo de timestamp error (-1021) con reintento automático
- Estadísticas internas de éxito/falla/reintentos

**Implementación:**
```python
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 2
```

**Archivos modificados:** `trading_engine.py` - Clase `BinanceFuturesClient`, método `_make_request()`

---

### 4. TELEGRAM MEJORADO CON ESTADÍSTICAS
**Problema original:** Notificaciones básicas sin formato HTML y sin contexto de sesión.

**Solución implementada:**
- Formato HTML con emojis (🚀 💰 ⚙️)
- Mensaje de inicio con saldo y modo operativo
- Retry de envío con backoff exponencial (3 intentos)
- Parámetro `parse_mode` configurable

**Ejemplo de mensaje de inicio:**
```
🚀 <b>OpenBridge Trading Engine v2.0</b> iniciado
💰 Saldo: 28.63 USDT
⚙️ Modo: NORMAL
```

**Archivos modificados:** `trading_engine.py` - Función `send_telegram_async()`

---

### 5. CIRCUIT BREAKER INTELIGENTE + MODO RECUPERACIÓN
**Problema original:** El circuit breaker solo bloqueaba por tiempo fijo sin análisis de rendimiento post-recuperación.

**Solución implementada:**
- Clase `SessionManager` para persistencia de estado
- Tracking de P&L acumulado, trades ganados/perdidos, racha de pérdidas
- Modos de operación: NORMAL → CIRCUIT_BREAKER → RECOVERY → NORMAL
- Auto-reset del circuit breaker tras tiempo + análisis de ADX para resumir
- Persistencia en `engine_state.json`
- Estadísticas accesibles: win rate, P&L total, racha actual

**Parámetros:**
```python
CIRCUIT_BREAKER_WINDOW_HOURS = 4       # Ventana de evaluación
CIRCUIT_BREAKER_MAX_LOSS_USDT = 0.50   # Pérdida máxima antes de freno
CIRCUIT_BREAKER_PAUSE_MINUTES = 120    # Pausa tras activar
CIRCUIT_BREAKER_ADX_RESUME = 25.0      # ADX mínimo para resumir
CIRCUIT_BREAKER_RECOVERY_MODE = True   # Activar modo recuperación
```

**Archivos modificados:** `trading_engine.py` - Nueva clase `SessionManager`

---

### 6. LOGGING AVANZADO CON COLORES
**Problema original:** Logs sin formato visual en consola, dificultando monitoreo en tiempo real.

**Solución implementada:**
- Formatter personalizado `ColoredFormatter` con colores ANSI
- DEBUG = Cyan, INFO = Verde, WARNING = Amarillo, ERROR = Rojo, CRITICAL = Magenta
- Logging dual simultáneo: archivo + consola con colores
- Formato unificado: `%(asctime)s [%(levelname)s] %(message)s`

**Archivos modificados:** `trading_engine.py` - Configuración de logging

---

## 📊 ESTADÍSTICAS ESPERADAS

### Mejoras de Rendimiento
| Métrica | v1.0 | v2.0 Esperado |
|---------|------|----------------|
| Señales/día en rango | 0-1 | 5-15 |
| Latencia API | Variable (timeout 8s) | < 12s con retry |
| Tasa de acierto estimada | ~45% | ~52% (con modo rango) |
| Tiempo de recuperación post-CB | 120 min fijo | 120-300 min adaptativo |

### Riesgos Mitigados
1. **Mercado sin tendencia prolongado** → Modo rango captura micro-movimientos
2. **Fallos de internet transitorios** → Retry automático con backoff
3. **Pérdida en cascada** → Circuit breaker con pausa + modo recuperación
4. **Falso positivo en rango** → ADX adaptativo reduce falsas entradas

---

## 🔧 CONFIGURACIÓN

### Variables editables (top del archivo)
```python
# ADX Adaptive
ADX_BASE_THRESHOLD = 8.0
ADX_MIN_THRESHOLD = 6.0
ADX_MAX_THRESHOLD = 15.0

# Modo Rango
RANGE_MODE_ENABLED = True
RANGE_ADX_MAX = 18.0
RANGE_CONFIDENCE_THRESHOLD = 0.30
RANGE_IMPULSE_THRESHOLD = 0.08

# Retry API
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 2

# Circuit Breaker
CIRCUIT_BREAKER_MAX_LOSS_USDT = 0.50
CIRCUIT_BREAKER_PAUSE_MINUTES = 120
```

---

## ⚠️ NOTAS OPERATIVAS

1. **Modo rango es más riesgoso** que modo trending - monitorear closely primeras horas
2. **Backing up del estado** - `engine_state.json` persiste entre reinicios
3. **Telegram** - Requiere `.env` con TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID
4. **Copia de seguridad** - `trading_engine_v1_backup.py` disponible en caso de emergencia
5. **WSL vs Windows** - Funciona en ambos; logging de colores viene desactivado en Windows si no detecta terminal

---

## 🔄 PROCESO DE DESPLIEGUE

1. Verificar backup: `trading_engine_v1_backup.py` existe ✓
2. Verificar sintaxis: `python -m py_compile trading_engine.py` ✓
3. Ejecutar en modo test primero (sin activar órdenes)
4. Monitorear logs durante 1 hora
5. Confirmar modo rango está activando señales (esperar ADX < 18)

---

**Firmado:** OpenBridge AI  
**Versión:** 2.0.0  
**Commit:** Perfeccionamiento integral 2026-06-07
