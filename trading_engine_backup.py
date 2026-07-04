from collections import deque
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBridge LIVE Trading Engine v1.0
Operativa viva BTC/USDT Futures en Binance
Autor: OpenBridge AI | Fecha: 2025-02-06

PARAMETROS DE OPERACION:
- Capital base: 10 USDT
- Apalancamiento: 30x
- Capital efectivo: 300 USDT notional
- Mercado: BTCUSDT Perpetual (Futures)

ESTRATEGIA:
- Entradas: Analisis tecnico (SMA, RSI, volumen, funding)
- Salidas: SL dinamico con trailing, senal de reversión, o criterio propio
- Ciclo: 10 segundos entre iteraciones

CONTROL: El usuario mantiene monitor externo y puede cerrar manualmente.
"""

import os
import sys
import time
import hmac
import hashlib
import math
import requests
import threading
import logging
import msvcrt
from urllib.parse import urlencode
from datetime import datetime, timedelta

# ─── CONFIGURACIÓN GLOBAL ─────────────────────────────────────────────────────
ENV_PATH = r"C:\OPENBRIDGE\BINANCE\.env"
LOCK_PATH = r"C:\OPENBRIDGE\BINANCE\trading_engine.lock"
BASE_FUTURES = "https://fapi.binance.com"
BASE_REST = "https://api.binance.com"

# Parámetros de operación
CAPITAL_BASE = 10.0           # USDT
LEVERAGE = 30
SYMBOL = "BTCUSDT"
CYCLE_TIME = 7                # segundos entre iteraciones
ENTRY_COOLDOWN_SECONDS = 120  # Evita reentradas inmediatas tras cierre
REQUEST_TIMEOUT = 8           # Timeout API para no quedar colgado en un ciclo
RECV_WINDOW = 5000            # Ventana Binance para requests firmados

# Umbrales de gestión de riesgo dinámicos (basados en ATR)
ATR_SL_MULTIPLIER = 1.8       # SL inicial: 1.8 * ATR del entry_price
ATR_TRAILING_ACTIVACION = 1.0 # Activa trailing cuando el profit supera 1.0 * ATR
ATR_TRAILING_DISTANCE = 0.65  # Buffer de trailing stop: 0.65 * ATR
HARD_SL_PCT = -10.0           # SL absoluto de emergencia sobre margen
MAX_HOLD_TIME = 300           # Máximo 5 min sin movimiento
SIGNAL_DECAY_EXIT_PCT = -3.0  # Corta si la tesis se apaga y la perdida ya pesa
OPPOSITE_IMPULSE_EXIT_ATR = 0.15

# Parámetros avanzados de rentabilidad (Fase 2)
RISK_PER_TRADE_PCT = 1.5      # Riesgo objetivo por operacion
MAX_EFFECTIVE_RISK_PCT = 3.0  # Techo real si el minimo de Binance obliga a redondear
MIN_ADX_TREND = 10.0          # Piso flexible; debajo se evita rango plano
ADX_STRONG_TREND = 20.0       # Desde aqui no se penaliza por fuerza de tendencia
MAX_SPREAD_PCT = 0.05         # Spread máximo permitido del Order Book para evitar slippage

# Parámetros de Impulso (Fase 3 - Fuerza del movimiento)
IMPULSE_LOOKBACK_BARS = 3     # Barras hacia atrás para medir impulso (en timeframe 5m)
IMPULSE_WEIGHT = 0.25         # Peso del impulso en el cálculo de confianza (25%)
MIN_IMPULSE_ATR = 0.15        # Impulso mínimo en unidades ATR para considerar que hay movimiento
STRONG_IMPULSE_ATR = 0.85     # Impulso que permite scalp contra macro si el score acompana
MIN_CONFIDENCE_ENTRY = 0.35   # Umbral base de entrada
COUNTER_TREND_MIN_CONFIDENCE = 0.48
ADX_RANGE_PENALTY = 0.07
MACRO_COUNTER_TREND_PENALTY = 0.08
MIN_QTY_BTC = 0.001           # Precision minima operativa BTCUSDT Futures usada por este motor


# ─── LOGGING ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('trading_engine.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)
_LOCK_HANDLE = None


def acquire_single_instance_lock():
    """Evita que dos motores vivos compitan por las mismas ordenes."""
    global _LOCK_HANDLE
    _LOCK_HANDLE = open(LOCK_PATH, "a+", encoding="utf-8")
    try:
        msvcrt.locking(_LOCK_HANDLE.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        logger.error(f"Otro trading_engine.py ya esta corriendo. Lock activo: {LOCK_PATH}")
        sys.exit(2)

    _LOCK_HANDLE.seek(0)
    _LOCK_HANDLE.truncate()
    _LOCK_HANDLE.write(f"pid={os.getpid()} started_at={datetime.now().isoformat()}\n")
    _LOCK_HANDLE.flush()

# ─── CLIENTE BINANCE FUTURES ────────────────────────────────────────────────
class BinanceApiError(Exception):
    """Error de lectura/escritura contra Binance API."""


class BinanceFuturesClient:
    def __init__(self):
        self.api_key, self.api_secret = self._load_keys()
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'X-MBX-APIKEY': self.api_key
        })
        self.symbol = SYMBOL

    def _load_keys(self):
        """Carga claves API desde archivo .env"""
        api_key, api_secret = "", ""
        try:
            with open(ENV_PATH, "r") as f:
                for line in f:
                    if "=" in line:
                        key, val = line.strip().split("=", 1)
                        if key == "BINANCE_API_KEY":
                            api_key = val
                        elif key == "BINANCE_API_SECRET":
                            api_secret = val
        except FileNotFoundError:
            logger.error(f"Archivo .env no encontrado en {ENV_PATH}")
            sys.exit(1)
        return api_key, api_secret

    def _sign_request(self, params):
        """Firma HMAC-SHA256 para endpoints privados"""
        params = dict(params)
        params['timestamp'] = int(time.time() * 1000)
        params['recvWindow'] = RECV_WINDOW
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return f"{query_string}&signature={signature}"

    def _make_request(self, method, endpoint, params=None, signed=False):
        """Ejecuta request a Binance API"""
        params = dict(params or {})
        method = method.upper()

        url = f"{BASE_FUTURES}{endpoint}"

        try:
            if signed:
                query = self._sign_request(params)
                url = f"{url}?{query}"
                response = self.session.request(method, url, timeout=REQUEST_TIMEOUT)
            else:
                response = self.session.request(method, url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            logger.error(f"Error de conexion Binance {method} {endpoint}: {e}")
            return None, 0

        try:
            data = response.json()
            if isinstance(data, dict) and 'code' in data and 'msg' in data:
                logger.error(f"Error API Binance: {data['msg']} (code: {data['code']})")
                return None, response.status_code
            return data, response.status_code
        except Exception as e:
            logger.error(f"Error parseando respuesta: {e}")
            return None, response.status_code

    # ─── Endpoints de Cuenta ─────────────────────────────────────────────────

    def get_account_balance(self):
        """Obtiene saldo USDT disponible"""
        data, status = self._make_request("GET", "/fapi/v2/balance", signed=True)
        if status == 200 and data:
            for asset in data:
                if asset['asset'] == 'USDT':
                    return {
                        'balance': float(asset['balance']),
                        'available': float(asset.get('availableBalance', 0))
                    }
        return {'balance': 0.0, 'available': 0.0}

    def set_leverage(self, leverage=LEVERAGE):
        """Configura apalancamiento para el símbolo"""
        params = {'symbol': self.symbol, 'leverage': leverage}
        data, status = self._make_request("POST", "/fapi/v1/leverage", params, signed=True)
        if status == 200:
            logger.info(f"Apalancamiento configurado: {leverage}x")
            return True
        logger.error(f"Error configurando apalancamiento: {data}")
        return False

    # ─── Posiciones ───────────────────────────────────────────────────────────

    def get_position(self, fail_on_error=False):
        """Obtiene posición abierta actual en BTCUSDT"""
        data, status = self._make_request("GET", "/fapi/v2/positionRisk", signed=True)
        if status != 200 or data is None:
            message = f"No se pudo leer positionRisk (status {status})"
            if fail_on_error:
                raise BinanceApiError(message)
            logger.warning(message)
            return None

        if data:
            for pos in data:
                if pos['symbol'] == self.symbol:
                    amt = float(pos['positionAmt'])
                    if abs(amt) > 0:
                        entry_price = float(pos['entryPrice'])
                        size = abs(amt)
                        pnl = float(pos['unRealizedProfit'])
                        leverage = int(pos['leverage'])
                        notional = entry_price * size
                        initial_margin = notional / leverage if leverage > 0 else 0
                        pnl_pct_notional = (pnl / notional) * 100 if notional > 0 else 0
                        pnl_pct_margin = (pnl / initial_margin) * 100 if initial_margin > 0 else 0
                        return {
                            'symbol': pos['symbol'],
                            'side': 'LONG' if amt > 0 else 'SHORT',
                            'size': size,
                            'entry_price': entry_price,
                            'mark_price': float(pos['markPrice']),
                            'pnl': pnl,
                            'pnl_pct': pnl_pct_margin,
                            'pnl_pct_margin': pnl_pct_margin,
                            'pnl_pct_notional': pnl_pct_notional,
                            'notional': notional,
                            'initial_margin': initial_margin,
                            'leverage': leverage,
                            'liquidation_price': float(pos['liquidationPrice']),
                            'margin_type': pos.get('marginType', 'CROSSED')
                        }
        return None

    def close_all_positions(self):
        """Cierra todas las posiciones abiertas del símbolo"""
        position = self.get_position(fail_on_error=True)
        if not position:
            logger.info("No hay posiciones abiertas para cerrar")
            return True

        side = 'SELL' if position['side'] == 'LONG' else 'BUY'
        qty = position['size']

        return self.place_order(side=side, quantity=qty, order_type='MARKET', reduce_only=True)

    # ─── Órdenes ────────────────────────────────────────────────────────────

    def place_order(self, side, quantity, order_type='MARKET', price=None, reduce_only=False):
        """Coloca una orden de mercado o límite"""
        quantity = round(quantity, 3)
        if quantity <= 0:
            logger.error(f"Cantidad invalida para orden {side}: {quantity}")
            return None

        params = {
            'symbol': self.symbol,
            'side': side,
            'type': order_type,
            'quantity': f"{quantity:.3f}"  # BTCUSDT Futures: max 3 decimales
        }

        if reduce_only:
            params['reduceOnly'] = 'true'

        if order_type == 'LIMIT' and price:
            params['price'] = f"{round(price, 2):.2f}"
            params['timeInForce'] = 'GTC'

        data, status = self._make_request("POST", "/fapi/v1/order", params, signed=True)

        if status == 200:
            suffix = " reduceOnly" if reduce_only else ""
            logger.info(f"ORDEN EJECUTADA: {side} {quantity} {self.symbol} @ {order_type}{suffix}")
            return data
        else:
            logger.error(f"Error ejecutando orden: {data}")
            return None

    def open_long(self, usdt_amount):
        """Abre posición larga usando el capital en USDT especificado"""
        price_data, _ = self.get_ticker()
        if not price_data:
            return None

        price = float(price_data['price'])
        qty = (usdt_amount * LEVERAGE) / price
        qty = round(qty, 3)  # BTCUSDT Futures: máx 3 decimales

        return self.place_order('BUY', qty, 'MARKET')

    def open_short(self, usdt_amount):
        """Abre posición corta usando el capital en USDT especificado"""
        price_data, _ = self.get_ticker()
        if not price_data:
            return None

        price = float(price_data['price'])
        qty = (usdt_amount * LEVERAGE) / price
        qty = round(qty, 3)  # BTCUSDT Futures: máx 3 decimales

        return self.place_order('SELL', qty, 'MARKET')


    # ─── Datos de Mercado ────────────────────────────────────────────────────

    def get_ticker(self):
        """Obtiene precio actual del símbolo"""
        return self._make_request("GET", "/fapi/v1/ticker/price",
                                  {'symbol': self.symbol})

    def get_klines(self, interval='5m', limit=20):
        """Obtiene velas históricas"""
        params = {'symbol': self.symbol, 'interval': interval, 'limit': limit}
        return self._make_request("GET", "/fapi/v1/klines", params)

    def get_funding_rate(self):
        """Obtiene funding rate actual"""
        data, _ = self._make_request("GET", "/fapi/v1/fundingRate",
                                     {'symbol': self.symbol, 'limit': 1})
        if data and len(data) > 0:
            return float(data[0]['fundingRate'])
        return 0.0

    def get_open_interest(self):
        """Obtiene Open Interest"""
        data, _ = self._make_request("GET", "/fapi/v1/openInterest",
                                     {'symbol': self.symbol})
        if data:
            return float(data['openInterest'])
        return 0.0

    def get_order_book_spread(self):
        """Obtiene el spread porcentual actual del Order Book (bids/asks depth)"""
        data, status = self._make_request("GET", "/fapi/v1/depth", {'symbol': self.symbol, 'limit': 5})
        if status == 200 and data and 'asks' in data and 'bids' in data:
            try:
                best_ask = float(data['asks'][0][0])
                best_bid = float(data['bids'][0][0])
                if best_bid > 0:
                    spread_pct = ((best_ask - best_bid) / best_bid) * 100
                    return spread_pct
            except (IndexError, ValueError, TypeError):
                pass
        return 999.0  # Retornar valor alto si hay error de red/data


    # ─── Gestión de Riesgo ───────────────────────────────────────────────────

    def calculate_position_size(self, usdt_amount, current_price):
        """Calcula tamaño de posición basado en capital y precio"""
        notional = usdt_amount * LEVERAGE
        return notional / current_price

# ─── ANÁLISIS TÉCNICO ───────────────────────────────────────────────────────
class TechnicalAnalyzer:
    def __init__(self, client):
        self.client = client

        # =========================
        # HFT IMPULSE MEMORY
        # =========================
        self.impulse_history = deque(maxlen=5)
        self.last_impulse = 0.0

    def _neutral_result(self, price=0.0):
        return {
            'signal': 'NEUTRAL',
            'confidence': 0,
            'price': price,
            'rsi': 50.0,
            'sma_5_5m': 0,
            'sma_10_5m': 0,
            'funding': 0.0,
            'volume_spike': False,
            'institutional_direction': 0.0,
            'atr': 0.0,
            'ema_50_1h': 0.0,
            'adx': 0.0,
            'impulse_raw': 0.0,
            'long_conditions': 0,
            'short_conditions': 0,
            'long_confidence': 0.0,
            'short_confidence': 0.0,
            'signal_reason': 'DATA_UNAVAILABLE',
            'data_ok': False
        }

    def _calculate_ema(self, prices, period=50):
        """Calcula la media móvil exponencial (EMA)"""
        if len(prices) < period:
            return sum(prices) / len(prices) if prices else 0.0

        # Iniciar con SMA
        ema = sum(prices[:period]) / period
        k = 2 / (period + 1)

        for price in prices[period:]:
            ema = (price * k) + (ema * (1 - k))
        return ema

    def _calculate_atr(self, klines, period=14):
        """Calcula el ATR (Average True Range) a partir de klines de Binance"""
        if len(klines) < period + 1:
            return 0.0

        tr_list = []
        for i in range(1, len(klines)):
            h = float(klines[i][2])
            l = float(klines[i][3])
            pc = float(klines[i-1][4])

            tr = max(h - l, abs(h - pc), abs(l - pc))
            tr_list.append(tr)

        if len(tr_list) < period:
            return sum(tr_list) / len(tr_list) if tr_list else 0.0

        atr = sum(tr_list[:period]) / period
        for tr in tr_list[period:]:
            atr = (atr * (period - 1) + tr) / period
        return atr

    def _calculate_adx(self, klines, period=14):
        """Calcula el ADX (Average Directional Index) nativo a partir de klines"""
        if len(klines) < (period * 2) + 1:
            return 0.0

        # 1. Calcular TR, +DM y -DM
        tr_list = []
        plus_dm_list = []
        minus_dm_list = []

        for i in range(1, len(klines)):
            h = float(klines[i][2])
            l = float(klines[i][3])
            ph = float(klines[i-1][2])
            pl = float(klines[i-1][3])
            pc = float(klines[i-1][4])

            tr = max(h - l, abs(h - pc), abs(l - pc))
            tr_list.append(tr)

            up_move = h - ph
            down_move = pl - l

            if up_move > down_move and up_move > 0:
                plus_dm_list.append(up_move)
            else:
                plus_dm_list.append(0.0)

            if down_move > up_move and down_move > 0:
                minus_dm_list.append(down_move)
            else:
                minus_dm_list.append(0.0)

        # 2. Suavizar usando el método de Welles Wilder
        # TR14, +DM14, -DM14 iniciales (SMA de los primeros 14 períodos)
        tr14 = sum(tr_list[:period])
        pdm14 = sum(plus_dm_list[:period])
        mdm14 = sum(minus_dm_list[:period])

        dx_list = []

        # Primero calcular el primer set
        di_plus = (pdm14 / tr14) * 100 if tr14 > 0 else 0
        di_minus = (mdm14 / tr14) * 100 if tr14 > 0 else 0
        dx = (abs(di_plus - di_minus) / (di_plus + di_minus)) * 100 if (di_plus + di_minus) > 0 else 0
        dx_list.append(dx)

        for i in range(period, len(tr_list)):
            tr14 = tr14 - (tr14 / period) + tr_list[i]
            pdm14 = pdm14 - (pdm14 / period) + plus_dm_list[i]
            mdm14 = mdm14 - (mdm14 / period) + minus_dm_list[i]

            di_plus = (pdm14 / tr14) * 100 if tr14 > 0 else 0
            di_minus = (mdm14 / tr14) * 100 if tr14 > 0 else 0
            dx = (abs(di_plus - di_minus) / (di_plus + di_minus)) * 100 if (di_plus + di_minus) > 0 else 0
            dx_list.append(dx)

        # Calcular el ADX final (SMA de los DX)
        if len(dx_list) < period:
            return sum(dx_list) / len(dx_list) if dx_list else 0.0

        adx = sum(dx_list[:period]) / period
        for dx_val in dx_list[period:]:
            adx = (adx * (period - 1) + dx_val) / period

        return adx

    def analyze_market(self):
        """Análisis completo del mercado con manejo robusto de datos"""
        # Obtener datos - get_klines/get_ticker devuelven (data, status)
        klines_result_5m = self.client.get_klines('5m', 45) # Aumentado para tener suficiente historial para ADX (aprox 2 * period + 10)
        klines_result_15m = self.client.get_klines('15m', 20)
        klines_result_1h = self.client.get_klines('1h', 70) # Para EMA 50
        ticker_result = self.client.get_ticker()


        # Desempaquetar tuplas (data, status)
        if isinstance(klines_result_5m, tuple) and len(klines_result_5m) == 2:
            klines_5m, status_5m = klines_result_5m
        else:
            klines_5m, status_5m = klines_result_5m, 200

        if isinstance(klines_result_15m, tuple) and len(klines_result_15m) == 2:
            klines_15m, status_15m = klines_result_15m
        else:
            klines_15m, status_15m = klines_result_15m, 200

        if isinstance(klines_result_1h, tuple) and len(klines_result_1h) == 2:
            klines_1h, status_1h = klines_result_1h
        else:
            klines_1h, status_1h = klines_result_1h, 200

        if isinstance(ticker_result, tuple) and len(ticker_result) == 2:
            ticker, status_ticker = ticker_result
        else:
            ticker, status_ticker = ticker_result, 200

        if status_5m != 200 or status_15m != 200 or status_1h != 200 or status_ticker != 200:
            return self._neutral_result()

        # Precio actual
        current_price = float(ticker.get('price', 0)) if isinstance(ticker, dict) else 0

        if current_price <= 0:
            return self._neutral_result(current_price)

        # Extraer valores de klines de forma robusta
        def extract_closes_volumes(klines):
            """Extrae closes y volumes de lista de klines API"""
            if not isinstance(klines, list) or len(klines) == 0:
                return [], []
            closes = []
            volumes = []
            for kline in klines:
                if not isinstance(kline, list) or len(kline) < 6:
                    continue
                try:
                    close_val = kline[4]
                    vol_val = kline[5]
                    # Puede ser string o numero segun la API
                    if isinstance(close_val, (int, float)):
                        closes.append(float(close_val))
                    else:
                        closes.append(float(str(close_val).strip('"').strip("'")))
                    if isinstance(vol_val, (int, float)):
                        volumes.append(float(vol_val))
                    else:
                        volumes.append(float(str(vol_val).strip('"').strip("'")))
                except (ValueError, TypeError, IndexError):
                    continue
            return closes, volumes

        closes_5m, volumes_5m = extract_closes_volumes(klines_5m)
        closes_15m, _ = extract_closes_volumes(klines_15m)
        closes_1h, _ = extract_closes_volumes(klines_1h)

        if len(closes_5m) < 15 or len(closes_15m) < 5 or len(closes_1h) < 50:
            return self._neutral_result(current_price)

        sma_5_5m = sum(closes_5m[-5:]) / 5 if len(closes_5m) >= 5 else 0
        sma_10_5m = sum(closes_5m[-10:]) / 10 if len(closes_5m) >= 10 else 0

        sma_5_15m = sum(closes_15m[-5:]) / 5 if len(closes_15m) >= 5 else 0
        sma_10_15m = sum(closes_15m[-10:]) / 10 if len(closes_15m) >= 10 else 0

        # EMA 50 Macro en 1h
        ema_50_1h = self._calculate_ema(closes_1h, 50)


        # ATR 14 en 5m para volatilidad / SL dinámico
        atr = self._calculate_atr(klines_5m, 14)

        # ADX 14 en 5m para fuerza de tendencia
        adx = self._calculate_adx(klines_5m, 14)

        # Impulso: diferencia de precio vs N barras atrás, normalizada por ATR
        price_n_ago = closes_5m[-IMPULSE_LOOKBACK_BARS] if len(closes_5m) >= IMPULSE_LOOKBACK_BARS else current_price

# =========================
# IMPULSO DINÁMICO HFT
# =========================

impulse_raw = (current_price - price_n_ago) / atr if atr > 0 else 0.0

# Historial
self.impulse_history.append(impulse_raw)

# Velocidad
impulse_velocity = impulse_raw - self.last_impulse
self.last_impulse = impulse_raw

# Persistencia
persistence_score = 0.0

if len(self.impulse_history) >= 3:

    recent = list(self.impulse_history)[-3:]

    # aceleración progresiva
    if recent[2] > recent[1] > recent[0]:
        persistence_score = 1.0

    # impulso fuerte sostenido
    elif min(recent) > 0.6:
        persistence_score = 0.7

    # decay
    elif recent[2] < recent[1] < recent[0]:
        persistence_score = -1.0

# score compuesto
impulse_score_dynamic = (
    (impulse_raw * 0.55) +
    (impulse_velocity * 0.30) +
    (persistence_score * 0.15)
)
  # Positivo = alcista, Negativo = bajista

        # Calcular RSI básico
        rsi = self._calculate_rsi(closes_5m)

        # Volumen
        volumes = volumes_5m
        avg_volume = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0
        current_volume = volumes[-1] if volumes else 0
        volume_spike = current_volume > (avg_volume * 1.5) if avg_volume > 0 else False

        # Funding rate
        funding = self.client.get_funding_rate()

        # ─── MATRIX INSTITUCIONAL v3.0 PROXY ─────────────────────────────────
        # Score proxy basado en datos YA disponibles de Binance
        institutional_direction = 0.0  # -1.0 (bearish) a +1.0 (bullish)

        # Capa 1: Funding Rate (35%) — Contrarian per Matrix
        if funding < -0.0002:
            institutional_direction += 0.35  # Largos baratos = MMs acumulan LONG
        elif funding < 0:
            institutional_direction += 0.15
        elif funding > 0.0002:
            institutional_direction -= 0.35  # Cortos baratos = MMs acumulan SHORT
        elif funding > 0:
            institutional_direction -= 0.15

        # Capa 2: Volume Spike (20%) — Interés institucional
        if volume_spike:
            if current_price > sma_10_5m:
                institutional_direction += 0.20  # Vol + precio arriba = MM buying
            elif current_price < sma_10_5m:
                institutional_direction -= 0.20  # Vol + precio abajo = MM selling
            else:
                institutional_direction += 0.10  # Vol sin dirección clara

        # Clamp al rango válido
        institutional_direction = max(-1.0, min(1.0, institutional_direction))
        # ─────────────────────────────────────────────────────────────────────

        # Determinar señal con Matrix Institucional + Filtro MTF + ADX + Impulso
        signal = self._determine_signal(
            current_price, sma_5_5m, sma_10_5m, sma_5_15m, sma_10_15m,
            rsi, volume_spike, funding, institutional_direction, ema_50_1h, adx, impulse_raw
        )

        return {
            'signal': signal['signal'],
            'confidence': signal['confidence'],
            'price': current_price,
            'rsi': rsi,
            'sma_5_5m': sma_5_5m,
            'sma_10_5m': sma_10_5m,
            'funding': funding,
            'volume_spike': volume_spike,
            'institutional_direction': institutional_direction,
            'atr': atr,
            'ema_50_1h': ema_50_1h,
            'adx': adx,
            'impulse_raw': impulse_raw,
            'long_conditions': signal.get('long_conditions', 0),
            'short_conditions': signal.get('short_conditions', 0),
            'long_confidence': signal.get('long_confidence', 0.0),
            'short_confidence': signal.get('short_confidence', 0.0),
            'signal_reason': signal.get('reason', 'NO_REASON'),
            'price_n_ago': price_n_ago,
            'data_ok': True
        }

    def _calculate_rsi(self, closes, period=14):
        """Calcula RSI simple"""
        if len(closes) < period + 1:
            return 50.0

        gains = []
        losses = []

        for i in range(1, period + 1):
            change = closes[-i] - closes[-i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        avg_gain = sum(gains) / period if gains else 0
        avg_loss = sum(losses) / period if losses else 0

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _determine_signal(self, price, sma_5_5m, sma_10_5m, sma_5_15m, sma_10_15m,
                         rsi, volume_spike, funding, institutional_direction=0.0, ema_50_1h=0.0, adx=0.0, impulse_raw=0.0):
        """Determina señal de entrada basada en condiciones técnicas + Matrix Institucional + Filtro MTF + ADX + Impulso

        El impulso es un FACTOR dentro del cálculo de confianza, no un filtro externo.
        Esto asegura que solo se abren posiciones cuando hay fuerza real en la dirección del trade.
        """

        # El ADX se evalua despues como modulador de confianza.

        # Filtro de Tendencia Macro (MTF)
        macro_bullish = price > ema_50_1h if ema_50_1h > 0 else True
        macro_bearish = price < ema_50_1h if ema_50_1h > 0 else True

        # ─── SCORE BASE TÉCNICO (5 condiciones, peso 60%) ─────────────────
        # Señales de compra (LONG)
        long_conditions = 0
        if price > sma_5_5m > sma_10_5m:
            long_conditions += 1
        if sma_5_15m > sma_10_15m:
            long_conditions += 1
        if 50 < rsi < 70:
            long_conditions += 1
        if funding < 0:
            long_conditions += 1
        if volume_spike:
            long_conditions += 1

        # Señales de venta (SHORT)
        short_conditions = 0
        if price < sma_5_5m < sma_10_5m:
            short_conditions += 1
        if sma_5_15m < sma_10_15m:
            short_conditions += 1
        if 30 < rsi < 50:
            short_conditions += 1
        if funding > 0:
            short_conditions += 1
        if volume_spike:
            short_conditions += 1

        # ─── IMPULSO COMO FACTOR DE CONFIANZA (peso 25%) ─────────────────
        # impulse_raw > 0 = alcista, < 0 = bajista
        # Para LONG necesitamos impulso positivo, para SHORT impulso negativo

long_impulse_score = max(
            0.0,
            min(1.0, impulse_score_dynamic)
        )
       # Normalizar: 1.0 ATR de impulso = score máximo

short_impulse_score = max(
            0.0,
            min(1.0, -impulse_score_dynamic)
        )
     # Inverso para SHORT

        # ─── CÁLCULO DE CONFIANZA COMPUESTA ──────────────────────────────
        # Confianza = (técnico * 0.60) + (impulso * 0.25) + (institucional * 0.15)
        TECH_WEIGHT = 0.60
        IMPULSE_WEIGHT_LOCAL = IMPULSE_WEIGHT  # 0.25 de config global
        INST_WEIGHT = 0.15

        # LONG
        long_tech_score = long_conditions / 5.0
        long_inst_score = max(0.0, min(1.0, institutional_direction))  # 0 a 1
        long_confidence = (long_tech_score * TECH_WEIGHT) + (long_impulse_score * IMPULSE_WEIGHT_LOCAL) + (long_inst_score * INST_WEIGHT)

        # SHORT
        short_tech_score = short_conditions / 5.0
        short_inst_score = max(0.0, min(1.0, -institutional_direction))  # 0 a 1
        short_confidence = (short_tech_score * TECH_WEIGHT) + (short_impulse_score * IMPULSE_WEIGHT_LOCAL) + (short_inst_score * INST_WEIGHT)

        def result(signal, confidence, reason):
            return {
                'signal': signal,
                'confidence': confidence,
                'reason': reason,
                'long_conditions': long_conditions,
                'short_conditions': short_conditions,
                'long_confidence': long_confidence,
                'short_confidence': short_confidence,
            }

        if adx < MIN_ADX_TREND:
            return result('NEUTRAL', 0, f'ADX_RANGE:{adx:.2f}<{MIN_ADX_TREND:.2f}')

        if adx < ADX_STRONG_TREND:
            long_confidence = max(0.0, long_confidence - ADX_RANGE_PENALTY)
            short_confidence = max(0.0, short_confidence - ADX_RANGE_PENALTY)

        if not macro_bullish:
            long_confidence = max(0.0, long_confidence - MACRO_COUNTER_TREND_PENALTY)
        if not macro_bearish:
            short_confidence = max(0.0, short_confidence - MACRO_COUNTER_TREND_PENALTY)

        long_countertrend_ok = macro_bullish or (
            impulse_raw >= STRONG_IMPULSE_ATR and long_confidence >= COUNTER_TREND_MIN_CONFIDENCE
        )
        short_countertrend_ok = macro_bearish or (
            -impulse_raw >= STRONG_IMPULSE_ATR and short_confidence >= COUNTER_TREND_MIN_CONFIDENCE
        )

        long_level1 = long_conditions >= 3
        short_level1 = short_conditions >= 3
        long_matrix = long_conditions >= 2 and institutional_direction > 0.20
        short_matrix = short_conditions >= 2 and institutional_direction < -0.20
        long_fast = long_conditions >= 2 and impulse_raw >= STRONG_IMPULSE_ATR and long_confidence >= COUNTER_TREND_MIN_CONFIDENCE
        short_fast = short_conditions >= 2 and -impulse_raw >= STRONG_IMPULSE_ATR and short_confidence >= COUNTER_TREND_MIN_CONFIDENCE

        if long_conditions > short_conditions and (long_level1 or long_matrix or long_fast):

if persistence_score < 0:
                return result(
                    'NEUTRAL',
                    0,
                    f'IMPULSE_DECAY:{impulse_raw:.3f}'
                )

            if impulse_raw < MIN_IMPULSE_ATR:

                return result('NEUTRAL', 0, f'LONG_WEAK_IMPULSE:{impulse_raw:.3f}')
            if not long_countertrend_ok:
                return result('NEUTRAL', 0, f'LONG_MACRO_BLOCK:price<{ema_50_1h:.2f}')
            if long_confidence >= MIN_CONFIDENCE_ENTRY:
                reason = 'LONG_LEVEL1' if long_level1 else ('LONG_FAST' if long_fast else 'LONG_MATRIX')
                logger.info(f"{reason}: conf={long_confidence:.2f} | impulso={impulse_raw:.3f}ATR | tech={long_conditions}/5 | inst={institutional_direction:.2f} | adx={adx:.2f}")
                return result('LONG', long_confidence, reason)
            return result('NEUTRAL', 0, f'LONG_LOW_CONF:{long_confidence:.2f}')

        if short_conditions > long_conditions and (short_level1 or short_matrix or short_fast):
            if -impulse_raw < MIN_IMPULSE_ATR:
                return result('NEUTRAL', 0, f'SHORT_WEAK_IMPULSE:{abs(impulse_raw):.3f}')
            if not short_countertrend_ok:
                return result('NEUTRAL', 0, f'SHORT_MACRO_BLOCK:price>{ema_50_1h:.2f}')
            if short_confidence >= MIN_CONFIDENCE_ENTRY:
                reason = 'SHORT_LEVEL1' if short_level1 else ('SHORT_FAST' if short_fast else 'SHORT_MATRIX')
                logger.info(f"{reason}: conf={short_confidence:.2f} | impulso={impulse_raw:.3f}ATR | tech={short_conditions}/5 | inst={institutional_direction:.2f} | adx={adx:.2f}")
                return result('SHORT', short_confidence, reason)
            return result('NEUTRAL', 0, f'SHORT_LOW_CONF:{short_confidence:.2f}')

        return result('NEUTRAL', 0, f'NO_EDGE:L{long_conditions}/S{short_conditions}')

# ─── SISTEMA DE TRAILING STOP ──────────────────────────────────────────────
class TrailingStopManager:
    def __init__(self):
        self.highest_price = 0.0
        self.trailing_active = False
        self.entry_price = 0.0
        self.side = 'LONG'

    def setup(self, entry_price, side):
        """Inicializa los precios de entrada y lado de la posición"""
        self.entry_price = entry_price
        self.side = side
        self.highest_price = entry_price if side == 'LONG' else entry_price
        self.trailing_active = False

    def update(self, mark_price, atr):
        """Actualiza trailing stop en base al precio actual y ATR dinámico. Retorna True si debe cerrar."""
        if self.entry_price <= 0 or atr <= 0:
            return False

        # Calcular distancia y activación dinámica basada en ATR
        activation_dist = ATR_TRAILING_ACTIVACION * atr
        trail_dist = ATR_TRAILING_DISTANCE * atr

        if self.side == 'LONG':
            # Activar trailing si sube lo suficiente
            if not self.trailing_active and mark_price >= (self.entry_price + activation_dist):
                self.trailing_active = True
                self.highest_price = mark_price
                logger.info(f"TRAILING ACTIVADO para LONG en ${mark_price:.2f} (Target: +${activation_dist:.2f})")
                return False

            if self.trailing_active:
                if mark_price > self.highest_price:
                    self.highest_price = mark_price

                # Cerrar si cae por debajo del trailing stop
                stop_price = self.highest_price - trail_dist
                if mark_price <= stop_price:
                    logger.warning(f"TRAILING TRIGGERED LONG: Price {mark_price:.2f} <= Stop {stop_price:.2f}")
                    return True
        else: # SHORT
            # Activar trailing si baja lo suficiente
            if not self.trailing_active and mark_price <= (self.entry_price - activation_dist):
                self.trailing_active = True
                self.highest_price = mark_price
                logger.info(f"TRAILING ACTIVADO para SHORT en ${mark_price:.2f} (Target: -${activation_dist:.2f})")
                return False

            if self.trailing_active:
                if mark_price < self.highest_price:
                    self.highest_price = mark_price

                # Cerrar si sube por encima del trailing stop
                stop_price = self.highest_price + trail_dist
                if mark_price >= stop_price:
                    logger.warning(f"TRAILING TRIGGERED SHORT: Price {mark_price:.2f} >= Stop {stop_price:.2f}")
                    return True

        return False

    def reset(self):
        """Resetea estado del trailing"""
        self.highest_price = 0.0
        self.trailing_active = False
        self.entry_price = 0.0


# ─── MOTOR DE TRADING EN VIVO ──────────────────────────────────────────────
class LiveTradingEngine:
    def __init__(self):
        self.client = BinanceFuturesClient()
        self.analyzer = TechnicalAnalyzer(self.client)
        self.trailing_manager = TrailingStopManager()
        self.is_running = True
        self.entry_time = None
        self.position = None
        self.last_exit_time = None

    def initialize(self):
        """Inicializa configuraciones del motor"""
        logger.info("=" * 70)
        logger.info("OPENBRIDGE LIVE TRADING ENGINE v1.0")
        logger.info("=" * 70)
        logger.info(f"Símbolo: {SYMBOL}")
        logger.info(f"Capital base: {CAPITAL_BASE} USDT")
        logger.info(f"Apalancamiento: {LEVERAGE}x")
        logger.info(f"Capital efectivo: {CAPITAL_BASE * LEVERAGE} USDT notional")
        logger.info("=" * 70)

        # Configurar apalancamiento
        self.client.set_leverage(LEVERAGE)

        # Verificar saldo
        balance = self.client.get_account_balance()
        logger.info(f"Saldo disponible: {balance['available']:.2f} USDT")

        if balance['available'] < CAPITAL_BASE:
            logger.error(f"Saldo insuficiente. Necesario: {CAPITAL_BASE} USDT, Disponible: {balance['available']:.2f} USDT")
            sys.exit(1)

        logger.info("=" * 70)

    def cooldown_remaining(self):
        """Segundos restantes antes de permitir nueva entrada."""
        if not self.last_exit_time:
            return 0
        elapsed = (datetime.now() - self.last_exit_time).total_seconds()
        return max(0, int(ENTRY_COOLDOWN_SECONDS - elapsed))

    def can_enter_new_position(self):
        remaining = self.cooldown_remaining()
        if remaining > 0:
            logger.info(f"Entrada bloqueada por cooldown: {remaining}s restantes")
            return False
        return True

    def should_enter_long(self, analysis):
        """Determina si entrar largo basado en análisis + Matrix Institucional"""
        if not analysis.get('data_ok', False):
            return False
        if analysis['signal'] == 'LONG':
            return analysis['confidence'] >= MIN_CONFIDENCE_ENTRY
        return False

    def should_enter_short(self, analysis):
        """Determina si entrar corto basado en análisis + Matrix Institucional"""
        if not analysis.get('data_ok', False):
            return False
        if analysis['signal'] == 'SHORT':
            return analysis['confidence'] >= MIN_CONFIDENCE_ENTRY
        return False

    def should_exit(self, position, analysis):
        """Determina si cerrar posición actual"""
        current_pnl_pct = position.get('pnl_pct_margin', position['pnl_pct'])
        mark_price = position['mark_price']

        # Obtener ATR de análisis o usar un fallback si no se tiene
        atr = analysis.get('atr', 0.0)
        if atr <= 0:
            # Fallback en caso de que atr sea nulo
            atr = mark_price * 0.005 # 0.5% del precio

        # 1. Hard SL absoluto
        if current_pnl_pct <= HARD_SL_PCT:
            logger.warning(f"HARD SL ALCANZADO: {current_pnl_pct:.2f}%")
            return True, "HARD_SL"

        # 2. Stop Loss dinámico basado en ATR
        entry_price = position['entry_price']
        sl_distance = ATR_SL_MULTIPLIER * atr
        if position['side'] == 'LONG':
            stop_loss_price = entry_price - sl_distance
            if mark_price <= stop_loss_price:
                logger.warning(f"ATR SL ALCANZADO (LONG): Mark Price {mark_price:.2f} <= SL {stop_loss_price:.2f}")
                return True, "ATR_SL"
        else: # SHORT
            stop_loss_price = entry_price + sl_distance
            if mark_price >= stop_loss_price:
                logger.warning(f"ATR SL ALCANZADO (SHORT): Mark Price {mark_price:.2f} >= SL {stop_loss_price:.2f}")
                return True, "ATR_SL"

        # 3. Trailing Stop dinámico basado en ATR
        if self.trailing_manager.update(mark_price, atr):
            return True, "TRAILING_SL"

        # 4. Reversión de señal
        if (position['side'] == 'LONG' and analysis['signal'] == 'SHORT') or \
           (position['side'] == 'SHORT' and analysis['signal'] == 'LONG'):
            return True, "REVERSAL"

        # 5. Decaimiento de tesis: si se pierde la señal, cortar antes del hard SL.
        if position['side'] == 'LONG':
            opposite_impulse = analysis.get('impulse_raw', 0.0) <= -OPPOSITE_IMPULSE_EXIT_ATR
            same_signal_lost = analysis.get('signal') != 'LONG'
        else:
            opposite_impulse = analysis.get('impulse_raw', 0.0) >= OPPOSITE_IMPULSE_EXIT_ATR
            same_signal_lost = analysis.get('signal') != 'SHORT'

        if current_pnl_pct <= SIGNAL_DECAY_EXIT_PCT and same_signal_lost:
            return True, "SIGNAL_DECAY"

        if current_pnl_pct < 0 and opposite_impulse:
            return True, "OPPOSITE_IMPULSE"

        # 6. Tiempo máximo de hold — solo cerrar si hay GANANCIA neta (no forzar cierre con pérdida)
        if self.entry_time and (datetime.now() - self.entry_time).total_seconds() > MAX_HOLD_TIME:
            if current_pnl_pct > 0.1:  # Solo cerrar por timeout si hay ganancia real (>0.1% margen)
                return True, "TIMEOUT_PROFIT"
            elif current_pnl_pct < -5.0:  # Si lleva mucho tiempo y pérdida considerable, cortar
                return True, "TIMEOUT_STOPLOSS"

        return False, None

    def open_position(self, side, initial_atr=0.0):
        if not self.can_enter_new_position():
            return False

        # 1. Comprobar spread del Order Book para evitar slippage alto
        spread = self.client.get_order_book_spread()
        if spread > MAX_SPREAD_PCT:
            logger.warning(f"Entrada abortada: Spread de Order Book muy alto ({spread:.3f}% > {MAX_SPREAD_PCT}%)")
            return False

        try:
            existing = self.client.get_position(fail_on_error=True)
        except BinanceApiError as e:
            logger.warning(f"No se abre posicion: lectura de posicion fallo ({e})")
            return False

        if existing:
            logger.warning(f"No se abre {side}: ya existe posicion {existing['side']}")
            return False

        # 2. Risk-Based Position Sizing (Cálculo del tamaño por riesgo)
        balance_info = self.client.get_account_balance()
        balance = balance_info['available']

        if balance <= 0:
            logger.error("No se puede calcular tamaño: Balance disponible es 0")
            return False

        # Guardar balance antes del trade para verificación post-cierre
        self.pre_trade_balance = balance

        # Obtener precio de mercado
        price_ticker, _ = self.client.get_ticker()
        if not price_ticker or 'price' not in price_ticker:
            return False
        entry_price = float(price_ticker['price'])

        # Fallback para ATR si es 0
        atr = initial_atr if initial_atr > 0 else entry_price * 0.005
        sl_dist = ATR_SL_MULTIPLIER * atr

        max_loss_usdt = balance * (RISK_PER_TRADE_PCT / 100)
        raw_size_btc = max_loss_usdt / sl_dist if sl_dist > 0 else 0.0
        size_btc = math.floor(raw_size_btc * 1000) / 1000

        if size_btc < MIN_QTY_BTC:
            size_btc = MIN_QTY_BTC

        nominal_value = size_btc * entry_price
        margin_usdt = nominal_value / LEVERAGE
        effective_risk_usdt = sl_dist * size_btc
        effective_risk_pct = (effective_risk_usdt / balance) * 100 if balance > 0 else 999

        if margin_usdt > balance * 0.8:
            logger.warning(f"Entrada abortada: margen requerido {margin_usdt:.2f} USDT > 80% del balance {balance:.2f}")
            return False

        if effective_risk_pct > MAX_EFFECTIVE_RISK_PCT:
            logger.warning(
                f"Entrada abortada: riesgo efectivo {effective_risk_pct:.2f}% "
                f"> techo {MAX_EFFECTIVE_RISK_PCT:.2f}% por redondeo/minimo"
            )
            return False

        order_side = 'BUY' if side == 'LONG' else 'SELL'
        logger.info(
            f"Riesgo: balance={balance:.2f} USDT | ATR={atr:.2f} | SLdist={sl_dist:.2f} | "
            f"qty={size_btc:.3f} BTC | margen={margin_usdt:.2f} USDT | riesgo={effective_risk_pct:.2f}%"
        )
        logger.info(f"ABRIENDO POSICIÓN {side} qty={size_btc:.3f} BTC")

        result = self.client.place_order(order_side, size_btc, 'MARKET')

        if result:
            time.sleep(1)  # Esperar a que la orden se procese
            try:
                self.position = self.client.get_position(fail_on_error=True)
            except BinanceApiError as e:
                logger.error(f"Orden enviada, pero no se pudo confirmar posicion: {e}")
                return False
            if self.position:
                self.entry_time = datetime.now()
                self.trailing_manager.setup(self.position['entry_price'], side)
                logger.info(f"POSICIÓN ABIERTA:")
                logger.info(f"  Lado: {self.position['side']}")
                logger.info(f"  Entry: ${self.position['entry_price']:.2f}")
                logger.info(f"  Size: {self.position['size']:.6f} BTC")
                return True
        return False


    def close_position(self, reason=""):
        """Cierra posición actual"""
        logger.info(f"CERRANDO POSICIÓN - Razón: {reason}")

        try:
            result = self.client.close_all_positions()
        except BinanceApiError as e:
            logger.error(f"No se pudo cerrar posicion: {e}")
            return False
        if result:
            self.trailing_manager.reset()
            self.entry_time = None
            self.position = None
            self.last_exit_time = datetime.now()
            logger.info("POSICIÓN CERRADA EXITOSAMENTE")

            # Verificar balance post-trade y comparar
            time.sleep(2) # Esperar asentamiento de saldo en Binance
            post_balance_info = self.client.get_account_balance()
            post_balance = post_balance_info['available']

            pre_bal = getattr(self, 'pre_trade_balance', 0.0)
            if pre_bal > 0:
                net_profit = post_balance - pre_bal
                if net_profit > 0:
                    logger.info(f"💸 VERIFICACIÓN DE GANANCIA NETA: ¡EXITOSA! Balance inicial: {pre_bal:.4f} USDT | Balance final: {post_balance:.4f} USDT | Neta (con comisiones): +{net_profit:.4f} USDT")
                else:
                    logger.warning(f"⚠️ VERIFICACIÓN DE GANANCIA NETA: ¡SIN GANANCIA NETA! Balance inicial: {pre_bal:.4f} USDT | Balance final: {post_balance:.4f} USDT | Neta (con comisiones): {net_profit:.4f} USDT")
            return True
        return False

    def monitor_position(self, position, analysis):
        """Monitorea posición abierta y decide si cerrar"""
        pnl = position['pnl']
        pnl_pct = position.get('pnl_pct_margin', position['pnl_pct'])
        pnl_notional = position.get('pnl_pct_notional', 0)

        logger.info(
            f"MONITOREO - {position['side']} | P&L: {pnl:.2f} USDT "
            f"(ROI margen: {pnl_pct:.2f}% | notional: {pnl_notional:.2f}%)"
        )

        # Verificar si debe cerrar
        should_close, reason = self.should_exit(position, analysis)

        if should_close:
            self.close_position(reason)
            return False

        return True

    def run_cycle(self):
        """Ejecuta un ciclo completo de trading"""
        # Verificar posición actual
        position = self.client.get_position()

        # Analizar mercado
        analysis = self.analyzer.analyze_market()
        signal = analysis.get('signal', 'NEUTRAL')
        confidence = analysis.get('confidence', 0)
        atr_val = analysis.get('atr', 0.0)

        impulse = analysis.get('impulse_raw', 0.0)
        logger.info(
            f"SEÑAL: {signal} (confianza: {confidence:.0%}) | "
            f"Precio: ${analysis['price']:.2f} | ATR: ${atr_val:.2f} | "
            f"Impulso: {impulse:.3f}ATR | ADX: {analysis.get('adx', 0):.2f} | "
            f"L/S: {analysis.get('long_conditions', 0)}/{analysis.get('short_conditions', 0)} | "
            f"LC/SC: {analysis.get('long_confidence', 0):.2f}/{analysis.get('short_confidence', 0):.2f} | "
            f"EMA50_1h: ${analysis.get('ema_50_1h', 0):.2f} | "
            f"Razón: {analysis.get('signal_reason', 'NA')}"
        )

        if position:
            # Hay posición abierta - monitorear
            self.position = position
            # Si se inicia el bot con posición ya existente y no hay pre_trade_balance, usar balance actual como base
            if not hasattr(self, 'pre_trade_balance'):
                bal_info = self.client.get_account_balance()
                self.pre_trade_balance = bal_info['available']
            # Configurar el trailing stop si no estaba configurado previamente en memoria
            if not self.trailing_manager.entry_price or self.trailing_manager.entry_price == 0:
                self.trailing_manager.setup(position['entry_price'], position['side'])
            if not self.monitor_position(position, analysis):
                self.position = None
        else:
            # No hay posición - buscar entrada
            if self.should_enter_long(analysis):
                self.open_position('LONG', atr_val)
            elif self.should_enter_short(analysis):
                self.open_position('SHORT', atr_val)


    def run(self):
        """Bucle principal del motor de trading"""
        try:
            self.initialize()

            # Verificar si hay posiciones previas
            existing = self.client.get_position()
            if existing:
                logger.warning(f"POSICIÓN EXISTENTE DETECTADA - {existing['side']}")
                self.position = existing

            while self.is_running:
                try:
                    self.run_cycle()
                except Exception as e:
                    logger.error(f"Error en ciclo: {e}")

                time.sleep(CYCLE_TIME)

        except KeyboardInterrupt:
            logger.info("Deteniendo motor de trading...")
            if self.position:
                logger.info("Cerrando posición abierta...")
                self.close_position("MANUAL_SHUTDOWN")
            logger.info("Motor detenido")

# ─── EJECUCIÓN PRINCIPAL ────────────────────────────────────────────────────
if __name__ == "__main__":
    acquire_single_instance_lock()
    engine = LiveTradingEngine()
    engine.run()
