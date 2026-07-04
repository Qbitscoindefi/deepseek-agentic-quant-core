#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBridge LIVE Trading Engine v2.0 - OPTIMIZED
Operativa viva BTC/USDT Futures en Binance
Autor: OpenBridge AI | Fecha: 2025-02-07

MEJORAS v2.0:
- Adaptive ADX: Umbral dinámico según volatilidad histórica
- Modo Scalp en Rango: Estrategia para mercados sin tendencia
- Reconexión Robusta: Backoff exponencial con retry automático
- Telegram Mejorado: Estadísticas de sesión y alertas enriquecidas
- Circuit Breaker Inteligente: Modo recuperación con análisis post-pérdida

PARAMETROS DE OPERACION:
- Capital base: 6 USDT
- Apalancamiento: 30x
- Capital efectivo: 300 USDT notional
- Mercado: BTCUSDT Perpetual (Futures)

ESTRATEGIA:
- Entradas: Análisis técnico (SMA, RSI, volumen, funding) + Adaptive ADX + Impulso
- Salidas: SL dinámico con trailing, señal de reversión, o criterio propio
- Ciclo: 7 segundos entre iteraciones
"""

from collections import deque
import os
import sys
import time
import hmac
import hashlib
import json
import math
import requests
import threading
import logging
import msvcrt
import winsound
from urllib.parse import urlencode
from datetime import datetime, timedelta

# ───────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN DE LOGGING AVANZADO
# ───────────────────────────────────────────────────────────────────────────────
class ColoredFormatter(logging.Formatter):
    """Formatter con colores para consola"""
    COLORS = {
        'DEBUG': '\033[36m',    # Cyan
        'INFO': '\033[32m',     # Verde
        'WARNING': '\033[33m',  # Amarillo
        'ERROR': '\033[31m',   # Rojo
        'CRITICAL': '\033[35m' # Magenta
    }
    RESET = '\033[0m'

    def format(self, record):
        log_color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{log_color}{record.levelname}{self.RESET}"
        return super().format(record)

# Configurar logging dual: archivo + consola con colores
log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
console_formatter = ColoredFormatter('%(asctime)s [%(levelname)s] %(message)s')

file_handler = logging.FileHandler('trading_engine.log', encoding='utf-8')
file_handler.setFormatter(log_formatter)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(console_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger(__name__)

# ─── UTILIDADES ───────────────────────────────────────────────────────────────

def play_alarm_async():
    """Reproduce alarma asíncrona de 3 beeps"""
    def alarm_worker():
        try:
            for _ in range(3):
                winsound.Beep(1000, 500)
                time.sleep(0.1)
        except Exception as e:
            pass
    threading.Thread(target=alarm_worker, daemon=True).start()

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = None
TELEGRAM_CHAT_ID = None

def _load_telegram_credentials():
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        return
    try:
        env_path = r"C:\OPENBRIDGE\BINANCE\.env"
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('TELEGRAM_BOT_TOKEN='):
                    TELEGRAM_BOT_TOKEN = line.split('=', 1)[1]
                elif line.startswith('TELEGRAM_CHAT_ID='):
                    TELEGRAM_CHAT_ID = line.split('=', 1)[1]
    except Exception:
        pass

def send_telegram_async(message, parse_mode='HTML'):
    """Envía mensaje a Telegram de forma asíncrona con retry"""
    def telegram_worker():
        for attempt in range(3):
            try:
                _load_telegram_credentials()
                if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
                    return
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                data = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message,
                    "parse_mode": parse_mode
                }
                response = requests.post(url, data=data, timeout=10)
                if response.status_code == 200:
                    return
            except Exception:
                time.sleep(2 ** attempt)  # Backoff exponencial
    threading.Thread(target=telegram_worker, daemon=True).start()

# ─── CONFIGURACIÓN GLOBAL ─────────────────────────────────────────────────────
ENV_PATH = r"C:\OPENBRIDGE\BINANCE\.env"
LOCK_PATH = r"C:\OPENBRIDGE\BINANCE\trading_engine.lock"
STATE_FILE = r"C:\OPENBRIDGE\BINANCE\engine_state.json"
BASE_FUTURES = "https://fapi.binance.com"
BASE_REST = "https://api.binance.com"

# ─── PARÁMETROS DE OPERACIÓN ─────────────────────────────────────────────
CAPITAL_BASE = 6.0
LEVERAGE = 30
SYMBOL = "BTCUSDT"
CYCLE_TIME = 7
ENTRY_COOLDOWN_SECONDS = 120
REQUEST_TIMEOUT = 8
RECV_WINDOW = 5000

# Umbrales de gestión de riesgo dinámicos
ATR_SL_MULTIPLIER = 1.8
ATR_TRAILING_ACTIVACION = 1.0
ATR_TRAILING_DISTANCE = 0.65
HARD_SL_PCT = -10.0
MAX_HOLD_TIME = 300
SIGNAL_DECAY_EXIT_PCT = -3.0
OPPOSITE_IMPULSE_EXIT_ATR = 0.30
OPPOSITE_IMPULSE_MIN_LOSS_PCT = -1.0   # Solo cerrar por impulso opuesto si ya hay pérdida >1% margen
TIMEOUT_PROFIT_MIN_PCT = 3.5      # ROI margen mínimo para TIMEOUT_PROFIT (cubrir fees taker 30x)
MAX_SPREAD_PCT = 0.05           # Spread máximo permitido del Order Book
MAX_SLIPPAGE_PCT = 0.040        # Slippage máximo post-fill (0.040% — audit v3.1 HFT)

# Parámetros avanzados de rentabilidad
RISK_PER_TRADE_PCT = 1.5
MAX_EFFECTIVE_RISK_PCT = 3.0
TAKER_FEE_PCT = 0.05

# ─── PARÁMETROS ADAPTIVOS V2.0 ───────────────────────────────────────────────

# ADX Adaptive Configuration
ADX_BASE_THRESHOLD = 8.0           # Umbral base (bajado de 10)
ADX_VOLATILITY_WINDOW = 24         # Ventana de horas para calcular volatilidad adaptativa
ADX_MIN_THRESHOLD = 6.0            # Nunca bajar de este umbral
ADX_MAX_THRESHOLD = 15.0           # Nunca subir de este umbral

# Modo Scalp en Rango (nuevo)
RANGE_MODE_ENABLED = True            # Activar estrategia de rango
RANGE_ADX_MAX = 22.0               # ADX máximo para modo rango
RANGE_MIN_VOLATILITY_PCT = 0.3     # Volatilidad mínima para operar en rango (%)
RANGE_CONFIDENCE_THRESHOLD = 0.42    # Umbral de confianza en modo rango (subido: menos entradas falsas)
RANGE_IMPULSE_THRESHOLD = 0.22     # Impulso mínimo en modo rango (subido: exigir movimiento real)
RANGE_ATR_DISTANCE_MAX = 0.8       # Distancia ATR máxima desde SMA para entrada
RANGE_ATR_PENALTY = 0.08    # Penalizacion por distancia ATR (gradual, no muro)

# Parámetros de Impulso
IMPULSE_LOOKBACK_BARS = 3
IMPULSE_WEIGHT = 0.25
MIN_IMPULSE_ATR = 0.15             # Bajado de 0.15 para modo rango
STRONG_IMPULSE_ATR = 0.70
MIN_CONFIDENCE_ENTRY = 0.35
MIN_CONFIDENCE_AFTER_LOSES = 0.40
MIN_CONFIDENCE_PULLBACK = 0.28
MIN_CONFIDENCE_PULLBACK_DEFENSIVE = 0.38
COUNTER_TREND_MIN_CONFIDENCE = 0.48
ADX_RANGE_PENALTY = 0.07
MACRO_COUNTER_TREND_PENALTY = 0.08
MIN_QTY_BTC = 0.001
IMPULSE_VELOCITY_MIN_FIRE = 0.05

# Circuit Breaker Avanzado
CIRCUIT_BREAKER_WINDOW_HOURS = 4
CIRCUIT_BREAKER_MAX_LOSS_USDT = 0.50
CIRCUIT_BREAKER_PAUSE_MINUTES = 60
CIRCUIT_BREAKER_ADX_RESUME = 25.0
CIRCUIT_BREAKER_RECOVERY_MODE = True  # Nuevo: modo recuperación

# Reconexión robusta
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 2

# ─── LOCK DE INSTANCIA ÚNICA ─────────────────────────────────────────────────
_LOCK_HANDLE = None

def acquire_single_instance_lock():
    """Evita que dos motores vivos compitan por las mismas órdenes."""
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

# ─── CLIENTE BINANCE FUTURES CON RECONEXIÓN ROBUSTA ─────────────────────────
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
        self._request_stats = {'success': 0, 'failure': 0, 'retries': 0}

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
        """Ejecuta request a Binance API con retry automático"""
        params = dict(params or {})
        method = method.upper()
        url = f"{BASE_FUTURES}{endpoint}"

        for attempt in range(MAX_RETRIES):
            try:
                if signed:
                    query = self._sign_request(params)
                    url_full = f"{url}?{query}"
                    response = self.session.request(method, url_full, timeout=REQUEST_TIMEOUT)
                else:
                    response = self.session.request(method, url, params=params, timeout=REQUEST_TIMEOUT)

                if response.status_code == 429:
                    # Rate limit - esperar y reintentar
                    retry_after = int(response.headers.get('Retry-After', 1))
                    logger.warning(f"Rate limit hit. Esperando {retry_after}s...")
                    time.sleep(retry_after)
                    continue

                response.raise_for_status()

                data = response.json()
                if isinstance(data, dict) and 'code' in data and 'msg' in data:
                    if data['code'] == -1021:  # Timestamp error
                        logger.warning("Timestamp error, reintentando...")
                        time.sleep(0.5)
                        continue
                    logger.error(f"Error API Binance: {data['msg']} (code: {data['code']})")
                    return None, data.get('code', 0)

                self._request_stats['success'] += 1
                return data, response.status_code

            except requests.exceptions.Timeout:
                logger.warning(f"Timeout en {method} {endpoint} (intento {attempt + 1}/{MAX_RETRIES})")
                self._request_stats['retries'] += 1
                time.sleep(RETRY_BACKOFF_BASE ** attempt)
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Error de conexión: {e} (intento {attempt + 1}/{MAX_RETRIES})")
                self._request_stats['retries'] += 1
                time.sleep(RETRY_BACKOFF_BASE ** attempt)
            except requests.exceptions.RequestException as e:
                logger.error(f"Error de request: {e}")
                self._request_stats['failure'] += 1
                return None, 0

        logger.error(f"Max retries excedido para {method} {endpoint}")
        self._request_stats['failure'] += 1
        return None, 0

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
            logger.error(f"Cantidad inválida para orden {side}: {quantity}")
            return None

        params = {
            'symbol': self.symbol,
            'side': side,
            'type': order_type,
            'quantity': f"{quantity:.3f}"
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
        qty = round(qty, 3)

        return self.place_order('BUY', qty, 'MARKET')

    def open_short(self, usdt_amount):
        """Abre posición corta usando el capital en USDT especificado"""
        price_data, _ = self.get_ticker()
        if not price_data:
            return None

        price = float(price_data['price'])
        qty = (usdt_amount * LEVERAGE) / price
        qty = round(qty, 3)

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
        """Obtiene el spread porcentual actual del Order Book"""
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
        return 999.0

    def get_recent_trades(self, limit=100):
        """Obtiene trades recientes para análisis de volumen"""
        data, _ = self._make_request("GET", "/fapi/v1/trades",
                                     {'symbol': self.symbol, 'limit': limit})
        return data if data else []


# ─── ANÁLISIS TÉCNICO V2.0 ─────────────────────────────────────────────────
class TechnicalAnalyzer:
    def __init__(self, client):
        self.client = client
        self.impulse_history = deque(maxlen=10)  # Aumentado para mejor análisis
        self.last_impulse = 0.0
        self.adx_history = deque(maxlen=48)  # 4 horas de historial ADX
        self.volatility_history = deque(maxlen=288)  # 24 horas de volatilidad
        self.last_adaptive_threshold = ADX_BASE_THRESHOLD

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
            'ema_50_4h': 0.0,
            'ema_50_1d': 0.0,
            'adx': 0.0,
            'impulse_raw': 0.0,
            'impulse_velocity': 0.0,
            'impulse_acceleration': 0.0,
            'long_conditions': 0,
            'short_conditions': 0,
            'long_confidence': 0.0,
            'short_confidence': 0.0,
            'signal_reason': 'DATA_UNAVAILABLE',
            'data_ok': False,
            'regime': 'UNKNOWN',
            'adaptive_adx_threshold': self.last_adaptive_threshold,
            'is_range_mode': False
        }

    def _calculate_ema(self, prices, period=50):
        """Calcula la media móvil exponencial (EMA)"""
        if len(prices) < period:
            return sum(prices) / len(prices) if prices else 0.0

        ema = sum(prices[:period]) / period
        k = 2 / (period + 1)

        for price in prices[period:]:
            ema = (price * k) + (ema * (1 - k))
        return ema

    def _calculate_atr(self, klines, period=14):
        """Calcula el ATR (Average True Range)"""
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
        """Calcula el ADX (Average Directional Index)"""
        if len(klines) < (period * 2) + 1:
            return 0.0

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

        tr14 = sum(tr_list[:period])
        pdm14 = sum(plus_dm_list[:period])
        mdm14 = sum(minus_dm_list[:period])

        dx_list = []

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

        if len(dx_list) < period:
            return sum(dx_list) / len(dx_list) if dx_list else 0.0

        adx = sum(dx_list[:period]) / period
        for dx_val in dx_list[period:]:
            adx = (adx * (period - 1) + dx_val) / period

        return adx

    def _detect_market_regime(self, adx, price, ema_50_4h, ema_50_1d):
        """Clasifica el clima del mercado"""
        if adx < 25.0:
            return 'RANGING'
        else:
            if ema_50_4h > 0 and ema_50_1d > 0:
                if price > ema_50_4h and price > ema_50_1d:
                    return 'TRENDING_BULL'
                elif price < ema_50_4h and price < ema_50_1d:
                    return 'TRENDING_BEAR'
        return 'RANGING'

    def _calculate_adaptive_adx_threshold(self, current_adx, atr, current_price):
        """
        Calcula umbral ADX adaptativo basado en volatilidad reciente.
        En mercados de baja volatilidad, permite umbral más bajo.
        En mercados volátiles, sube el umbral para filtrar ruido.
        """
        if not self.volatility_history:
            return ADX_BASE_THRESHOLD

        # Calcular volatilidad promedio del período
        avg_volatility = sum(self.volatility_history) / len(self.volatility_history)
        current_volatility = (atr / current_price) * 100 if current_price > 0 else 0

        # Añadir al historial
        self.volatility_history.append(current_volatility)

        if avg_volatility <= 0:
            return ADX_BASE_THRESHOLD

        # Ratio de volatilidad actual vs promedio
        volatility_ratio = current_volatility / avg_volatility if avg_volatility > 0 else 1.0

        # Ajustar umbral: más volatilidad = umbral más alto
        if volatility_ratio > 1.5:
            threshold = ADX_BASE_THRESHOLD * 1.2
        elif volatility_ratio < 0.7:
            threshold = ADX_BASE_THRESHOLD * 0.8
        else:
            threshold = ADX_BASE_THRESHOLD

        # Limitar a rangos válidos
        threshold = max(ADX_MIN_THRESHOLD, min(ADX_MAX_THRESHOLD, threshold))
        self.last_adaptive_threshold = threshold

        return threshold

    def analyze_market(self, consecutive_losses=0):
        """Análisis completo del mercado con mejoras v2.0"""
        # Obtener datos
        klines_result_5m = self.client.get_klines('5m', 45)
        klines_result_15m = self.client.get_klines('15m', 20)
        klines_result_1h = self.client.get_klines('1h', 70)
        klines_result_4h = self.client.get_klines('4h', 60)
        klines_result_1d = self.client.get_klines('1d', 60)
        ticker_result = self.client.get_ticker()

        # Desempaquetar
        def unpack(result):
            if isinstance(result, tuple) and len(result) == 2:
                return result
            return result, 200

        klines_5m, s_5m = unpack(klines_result_5m)
        klines_15m, s_15m = unpack(klines_result_15m)
        klines_1h, s_1h = unpack(klines_result_1h)
        klines_4h, s_4h = unpack(klines_result_4h)
        klines_1d, s_1d = unpack(klines_result_1d)
        ticker, s_ticker = unpack(ticker_result)

        if any(s != 200 for s in [s_5m, s_15m, s_1h, s_4h, s_1d, s_ticker]):
            return self._neutral_result()

        current_price = float(ticker.get('price', 0)) if isinstance(ticker, dict) else 0
        if current_price <= 0:
            return self._neutral_result(current_price)

        # Extraer datos
        def extract_closes_volumes(klines):
            if not isinstance(klines, list) or len(klines) == 0:
                return [], []
            closes, volumes = [], []
            for kline in klines:
                if not isinstance(kline, list) or len(kline) < 6:
                    continue
                try:
                    closes.append(float(kline[4]))
                    volumes.append(float(kline[5]))
                except (ValueError, TypeError, IndexError):
                    continue
            return closes, volumes

        def extract_highs_lows(klines):
            if not isinstance(klines, list) or len(klines) == 0:
                return [], []
            highs, lows = [], []
            for kline in klines:
                if not isinstance(kline, list) or len(kline) < 6:
                    continue
                try:
                    highs.append(float(kline[2]))
                    lows.append(float(kline[3]))
                except (ValueError, TypeError, IndexError):
                    continue
            return highs, lows

        closes_5m, volumes_5m = extract_closes_volumes(klines_5m)
        highs_5m, lows_5m = extract_highs_lows(klines_5m)
        closes_15m, _ = extract_closes_volumes(klines_15m)
        closes_1h, _ = extract_closes_volumes(klines_1h)
        closes_4h, _ = extract_closes_volumes(klines_4h)
        closes_1d, _ = extract_closes_volumes(klines_1d)

        if len(closes_5m) < 15 or len(closes_15m) < 5 or len(closes_1h) < 50:
            return self._neutral_result(current_price)

        # Cálculos técnicos
        sma_5_5m = sum(closes_5m[-5:]) / 5 if len(closes_5m) >= 5 else 0
        sma_10_5m = sum(closes_5m[-10:]) / 10 if len(closes_5m) >= 10 else 0
        sma_5_15m = sum(closes_15m[-5:]) / 5 if len(closes_15m) >= 5 else 0
        sma_10_15m = sum(closes_15m[-10:]) / 10 if len(closes_15m) >= 10 else 0

        ema_50_1h = self._calculate_ema(closes_1h, 50)
        ema_50_4h = self._calculate_ema(closes_4h, 50)
        ema_50_1d = self._calculate_ema(closes_1d, 50)

        atr = self._calculate_atr(klines_5m, 14)
        adx_5m = self._calculate_adx(klines_5m, 14)
        adx_1h = self._calculate_adx(klines_1h, 14) if len(klines_1h) >= 29 else adx_5m

        adx = adx_5m
        adx_regime = max(adx_5m, adx_1h)

        # NUEVO: Calcular umbral ADX adaptativo
        adaptive_adx_threshold = self._calculate_adaptive_adx_threshold(adx, atr, current_price)

        # Impulso
        price_n_ago = closes_5m[-IMPULSE_LOOKBACK_BARS] if len(closes_5m) >= IMPULSE_LOOKBACK_BARS else current_price
        impulse_raw = ((current_price - price_n_ago) / atr) if atr > 0 else 0.0
        self.impulse_history.append(impulse_raw)

        if len(self.impulse_history) < 4:
            self.last_impulse = impulse_raw
            logger.info(f"WARMUP: ciclo {len(self.impulse_history)}/4 | Precio: ${current_price:.2f} | impulso: {impulse_raw:.3f}ATR")
            result = self._neutral_result(current_price)
            result['atr'] = atr
            result['adx'] = adx
            result['regime'] = 'WARMUP'
            result['ema_50_1h'] = ema_50_1h
            result['adaptive_adx_threshold'] = adaptive_adx_threshold
            return result

        impulse_velocity = impulse_raw - self.last_impulse
        recent_for_accel = list(self.impulse_history)[-3:]
        if len(recent_for_accel) >= 3:
            prev_velocity = recent_for_accel[1] - recent_for_accel[0]
            impulse_acceleration = impulse_velocity - prev_velocity
        else:
            impulse_acceleration = 0.0

        self.last_impulse = impulse_raw

        # Persistencia
        persistence_score = 0.0
        if len(self.impulse_history) >= 3:
            recent = list(self.impulse_history)[-3:]
            if recent[2] > recent[1] > recent[0]:
                persistence_score = 1.0
            elif min(recent) > 0.6:
                persistence_score = 0.7
            elif recent[2] < recent[1] < recent[0]:
                persistence_score = -1.0

        impulse_score_dynamic = (impulse_raw * 0.55) + (impulse_velocity * 0.30) + (persistence_score * 0.15)
        rsi = self._calculate_rsi(closes_5m)

        # Volumen
        avg_volume = sum(volumes_5m[-5:]) / 5 if len(volumes_5m) >= 5 else 0
        current_volume = volumes_5m[-1] if volumes_5m else 0
        volume_spike = current_volume > (avg_volume * 1.5) if avg_volume > 0 else False

        funding = self.client.get_funding_rate()

        # Matrix Institucional
        institutional_direction = 0.0
        if funding < -0.0002:
            institutional_direction += 0.35
        elif funding < 0:
            institutional_direction += 0.15
        elif funding > 0.0002:
            institutional_direction -= 0.35
        elif funding > 0:
            institutional_direction -= 0.15

        if volume_spike:
            if current_price > sma_10_5m:
                institutional_direction += 0.20
            elif current_price < sma_10_5m:
                institutional_direction -= 0.20
            else:
                institutional_direction += 0.10

        institutional_direction = max(-1.0, min(1.0, institutional_direction))

        # Régimen de mercado
        regime = self._detect_market_regime(adx_regime, current_price, ema_50_4h, ema_50_1d)

        # NUEVO: Determinar modo (trending vs range)
        is_range_mode = (adx < RANGE_ADX_MAX) and RANGE_MODE_ENABLED

        # Señal
        signal = self._determine_signal(
            current_price, sma_5_5m, sma_10_5m, sma_5_15m, sma_10_15m,
            rsi, volume_spike, funding, institutional_direction, ema_50_1h, ema_50_4h, ema_50_1d,
            adx, impulse_raw, impulse_score_dynamic, persistence_score, impulse_velocity,
            impulse_acceleration, regime, atr, highs_5m=highs_5m, lows_5m=lows_5m,
            consecutive_losses=consecutive_losses, adaptive_adx_threshold=adaptive_adx_threshold,
            is_range_mode=is_range_mode
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
            'ema_50_4h': ema_50_4h,
            'ema_50_1d': ema_50_1d,
            'adx': adx,
            'impulse_raw': impulse_raw,
            'impulse_velocity': impulse_velocity,
            'impulse_acceleration': impulse_acceleration,
            'long_conditions': signal.get('long_conditions', 0),
            'short_conditions': signal.get('short_conditions', 0),
            'long_confidence': signal.get('long_confidence', 0.0),
            'short_confidence': signal.get('short_confidence', 0.0),
            'signal_reason': signal.get('reason', 'NO_REASON'),
            'data_ok': True,
            'regime': regime,
            'adaptive_adx_threshold': adaptive_adx_threshold,
            'is_range_mode': is_range_mode
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
                         rsi, volume_spike, funding, institutional_direction=0.0,
                         ema_50_1h=0.0, ema_50_4h=0.0, ema_50_1d=0.0, adx=0.0,
                         impulse_raw=0.0, impulse_score_dynamic=0.0, persistence_score=0.0,
                         impulse_velocity=0.0, impulse_acceleration=0.0, regime='UNKNOWN', atr=0.0,
                         highs_5m=None, lows_5m=None, consecutive_losses=0,
                         adaptive_adx_threshold=ADX_BASE_THRESHOLD, is_range_mode=False):
        """
        Determina señal de entrada con lógica mejorada v2.0:
        - ADX adaptativo
        - Modo rango habilitado
        - Umbrales dinámicos
        """

        def result(signal, confidence, reason):
            return {
                'signal': signal,
                'confidence': confidence,
                'reason': reason,
                'long_conditions': 0,
                'short_conditions': 0,
                'long_confidence': 0.0,
                'short_confidence': 0.0,
            }

        # Macro MTF
        macro_bullish = True
        macro_bearish = True
        if ema_50_4h > 0 and ema_50_1d > 0:
            macro_bullish = (price > ema_50_4h) and (price > ema_50_1d)
            macro_bearish = (price < ema_50_4h) and (price < ema_50_1d)
        elif ema_50_1h > 0:
            macro_bullish = price > ema_50_1h
            macro_bearish = price < ema_50_1h

        atr_distance = abs(price - sma_5_5m) / max(atr, 1.0)

        # Detector de caza de stops
        is_liquidity_sweep_long = False
        is_liquidity_sweep_short = False

        if highs_5m and lows_5m and len(highs_5m) >= 21 and len(lows_5m) >= 21:
            range_high_20 = max(highs_5m[-21:-1])
            range_low_20 = min(lows_5m[-21:-1])

            is_liquidity_sweep_short = (
                (highs_5m[-1] > range_high_20) and
                (price < range_high_20) and
                (impulse_velocity < -0.05) and
                volume_spike
            )

            is_liquidity_sweep_long = (
                (lows_5m[-1] < range_low_20) and
                (price > range_low_20) and
                (impulse_velocity > 0.05) and
                volume_spike
            )

        # Setup Pullback
        long_pullback_setup = (
            macro_bullish and (40 <= rsi <= 60) and (atr_distance <= 0.35) and
            (impulse_raw > 0) and (impulse_velocity > 0)
        )

        short_pullback_setup = (
            macro_bearish and (40 <= rsi <= 60) and (atr_distance <= 0.35) and
            (impulse_raw < 0) and (impulse_velocity < 0)
        )

        # Score técnico
        long_conditions = 0
        if price > sma_5_5m > sma_10_5m: long_conditions += 1
        if sma_5_15m > sma_10_15m: long_conditions += 1
        if 50 < rsi < 70: long_conditions += 1
        if funding < 0: long_conditions += 1
        if volume_spike: long_conditions += 1

        short_conditions = 0
        if price < sma_5_5m < sma_10_5m: short_conditions += 1
        if sma_5_15m < sma_10_15m: short_conditions += 1
        if 30 < rsi < 50: short_conditions += 1
        if funding > 0: short_conditions += 1
        if volume_spike: short_conditions += 1

        # Impulso como factor de confianza
        long_impulse_score = max(0.0, min(1.0, impulse_score_dynamic))
        short_impulse_score = max(0.0, min(1.0, -impulse_score_dynamic))

        # Cálculo de confianza compuesta
        TECH_WEIGHT = 0.60
        IMPULSE_WEIGHT_LOCAL = IMPULSE_WEIGHT
        INST_WEIGHT = 0.15

        long_tech_score = long_conditions / 5.0
        long_inst_score = max(0.0, min(1.0, institutional_direction))
        long_confidence = (long_tech_score * TECH_WEIGHT) + (long_impulse_score * IMPULSE_WEIGHT_LOCAL) + (long_inst_score * INST_WEIGHT)

        short_tech_score = short_conditions / 5.0
        short_inst_score = max(0.0, min(1.0, -institutional_direction))
        short_confidence = (short_tech_score * TECH_WEIGHT) + (short_impulse_score * IMPULSE_WEIGHT_LOCAL) + (short_inst_score * INST_WEIGHT)

        # NUEVO: Modo Rango - filtro ADX más permisivo
        if is_range_mode:
            # En modo rango, reducir requisitos de impulso y confianza
            dynamic_min_impulse_atr = RANGE_IMPULSE_THRESHOLD
            effective_min_conf_range = RANGE_CONFIDENCE_THRESHOLD

            # Aplicar filtro de distancia ATR desde la media (gradual)
            if atr_distance > RANGE_ATR_DISTANCE_MAX:
                # Penalizar confianza gradualmente en vez de bloquear
                if is_range_mode:
                    # En modo rango, la penalizacion reduce confianza pero no bloquea
                    distance_penalty = min(0.15, (atr_distance - RANGE_ATR_DISTANCE_MAX) * 0.5)
                    long_confidence = max(0.0, long_confidence - distance_penalty)
                    short_confidence = max(0.0, short_confidence - distance_penalty)
                    # Solo bloquear si la penalizacion deja la confianza muy baja
                    if long_confidence < RANGE_CONFIDENCE_THRESHOLD - 0.10 and short_confidence < RANGE_CONFIDENCE_THRESHOLD - 0.10:
                        return result('NEUTRAL', 0, f'RANGE_ATR_DIST:{atr_distance:.2f}>{RANGE_ATR_DISTANCE_MAX}')

            # Penalizar menos por falta de tendencia en modo rango
            if adx >= RANGE_ADX_MAX:
                return result('NEUTRAL', 0, f'ADX_TOO_HIGH_FOR_RANGE:{adx:.2f}>={RANGE_ADX_MAX}')
        else:
            # Modo normal: usar ADX adaptativo
            if adx < adaptive_adx_threshold:
                return result('NEUTRAL', 0, f'ADX_RANGE:{adx:.2f}<{adaptive_adx_threshold:.2f}')

            dynamic_min_impulse_atr = MIN_IMPULSE_ATR

        # Penalización ADX rango (solo en modo normal)
        if not is_range_mode and adx < 20.0:
            long_confidence = max(0.0, long_confidence - ADX_RANGE_PENALTY)
            short_confidence = max(0.0, short_confidence - ADX_RANGE_PENALTY)

        # Penalización macro contratendencia
        if not macro_bullish:
            long_confidence = max(0.0, long_confidence - MACRO_COUNTER_TREND_PENALTY)
        if not macro_bearish:
            short_confidence = max(0.0, short_confidence - MACRO_COUNTER_TREND_PENALTY)

        long_countertrend_ok = macro_bullish or (impulse_raw >= STRONG_IMPULSE_ATR and long_confidence >= COUNTER_TREND_MIN_CONFIDENCE)
        short_countertrend_ok = macro_bearish or (-impulse_raw >= STRONG_IMPULSE_ATR and short_confidence >= COUNTER_TREND_MIN_CONFIDENCE)

        # Detección de traps
        is_bear_trap_sweep = (
            (volume_spike and impulse_raw < -1.2 and impulse_velocity > 0.0 and impulse_acceleration > 0.05 and institutional_direction > 0.15) or
            is_liquidity_sweep_long
        )
        is_bull_trap_sweep = (
            (volume_spike and impulse_raw > 1.2 and impulse_velocity < 0.0 and impulse_acceleration < -0.05 and institutional_direction < -0.15) or
            is_liquidity_sweep_short
        )

        if is_bear_trap_sweep:
            long_confidence += 0.20
            long_countertrend_ok = True
        if is_bull_trap_sweep:
            short_confidence += 0.20
            short_countertrend_ok = True

        # Early impulse detection
        long_early = (long_conditions >= 2 and impulse_velocity > 0.05 and impulse_acceleration > 0 and persistence_score > 0 and 0.08 < impulse_raw < 0.7)
        short_early = (short_conditions >= 2 and impulse_velocity < -0.05 and impulse_acceleration < 0 and persistence_score > 0 and -0.7 < impulse_raw < -0.08)

        # Filtro de rango adicional
        if is_range_mode:
            # En modo rango, solo permitir early si hay impulso fuerte
            if not (abs(impulse_raw) >= STRONG_IMPULSE_ATR):
                long_early = False
                short_early = False

        # Impulse exhaustion
        magnitude_ceiling = abs(impulse_raw) > 2.5
        long_exhausted = magnitude_ceiling or (impulse_raw > 1.5 and impulse_velocity < 0 and persistence_score <= 0)
        short_exhausted = magnitude_ceiling or (impulse_raw < -1.5 and impulse_velocity > 0 and persistence_score <= 0)

        long_level1 = long_conditions >= 3
        short_level1 = short_conditions >= 3
        long_matrix = long_conditions >= 2 and institutional_direction > 0.20
        short_matrix = short_conditions >= 2 and institutional_direction < -0.20
        long_fast = long_conditions >= 2 and impulse_raw >= STRONG_IMPULSE_ATR and long_confidence >= COUNTER_TREND_MIN_CONFIDENCE
        short_fast = short_conditions >= 2 and -impulse_raw >= STRONG_IMPULSE_ATR and short_confidence >= COUNTER_TREND_MIN_CONFIDENCE

        # Filtro Anti-FOMO
        long_fomo_block = (rsi > 62) or (atr_distance > 0.6)
        short_fomo_block = (rsi < 38) or (atr_distance > 0.6)

        # NUEVO: En modo rango, relajar FOMO block
        if is_range_mode:
            long_fomo_block = (rsi > 65) or (atr_distance > 1.2)
            short_fomo_block = (rsi < 35) or (atr_distance > 1.2)

        long_candidate = (
            is_bear_trap_sweep or long_pullback_setup or
            (long_conditions > short_conditions and (long_level1 or long_matrix or long_fast or long_early))
        )
        short_candidate = (
            is_bull_trap_sweep or short_pullback_setup or
            (short_conditions > long_conditions and (short_level1 or short_matrix or short_fast or short_early))
        )

        if long_candidate:
            if long_fomo_block and not is_bear_trap_sweep:
                return result('NEUTRAL', 0, f'LONG_FOMO_BLOCK:rsi={rsi:.1f},dist={atr_distance:.2f}ATR')
            if long_exhausted:
                return result('NEUTRAL', 0, f'IMPULSE_EXHAUSTION:{impulse_raw:.3f}')
            if persistence_score < 0 and not is_bear_trap_sweep:
                return result('NEUTRAL', 0, f'IMPULSE_DECAY:{impulse_raw:.3f}')
            if impulse_raw < dynamic_min_impulse_atr and not long_early and not is_bear_trap_sweep and not long_pullback_setup:
                return result('NEUTRAL', 0, f'LONG_WEAK_IMPULSE:{impulse_raw:.3f}')
            if not long_countertrend_ok:
                return result('NEUTRAL', 0, f'LONG_MACRO_BLOCK:price<ema50(4H/1D)')

            # NUEVO: Aplicar umbral adaptativo o de rango
            if is_range_mode:
                effective_min_conf = RANGE_CONFIDENCE_THRESHOLD
            else:
                effective_min_conf = (MIN_CONFIDENCE_PULLBACK_DEFENSIVE if consecutive_losses >= 2 else MIN_CONFIDENCE_PULLBACK) if long_pullback_setup else (MIN_CONFIDENCE_AFTER_LOSES if consecutive_losses >= 2 else MIN_CONFIDENCE_ENTRY)

            if long_confidence >= effective_min_conf:
                reason = 'LONG_RANGE' if is_range_mode else ('LONG_PULLBACK' if long_pullback_setup else ('LIQUIDITY_SWEEP_LONG' if is_liquidity_sweep_long else ('BEAR_TRAP_SWEEP' if is_bear_trap_sweep else ('LONG_EARLY' if long_early else ('LONG_LEVEL1' if long_level1 else ('LONG_FAST' if long_fast else 'LONG_MATRIX'))))))
                logger.info(f"{reason} ({'RANGE' if is_range_mode else 'TREND'}, REGIME:{regime}): conf={long_confidence:.2f} | impulso={impulse_raw:.3f}ATR | tech={long_conditions}/5 | adx={adx:.2f}")
                return result('LONG', long_confidence, reason)
            return result('NEUTRAL', 0, f'LONG_LOW_CONF:{long_confidence:.2f}')

        if short_candidate:
            if short_fomo_block and not is_bull_trap_sweep:
                return result('NEUTRAL', 0, f'SHORT_FOMO_BLOCK:rsi={rsi:.1f},dist={atr_distance:.2f}ATR')
            if short_exhausted:
                return result('NEUTRAL', 0, f'IMPULSE_EXHAUSTION:{impulse_raw:.3f}')
            if persistence_score < 0 and not is_bull_trap_sweep:
                return result('NEUTRAL', 0, f'IMPULSE_DECAY:{impulse_raw:.3f}')
            if -impulse_raw < dynamic_min_impulse_atr and not short_early and not is_bull_trap_sweep and not short_pullback_setup:
                return result('NEUTRAL', 0, f'SHORT_WEAK_IMPULSE:{impulse_raw:.3f}')
            if not short_countertrend_ok:
                return result('NEUTRAL', 0, f'SHORT_MACRO_BLOCK:price>ema50(4H/1D)')

            if is_range_mode:
                effective_min_conf = RANGE_CONFIDENCE_THRESHOLD
            else:
                effective_min_conf = (MIN_CONFIDENCE_PULLBACK_DEFENSIVE if consecutive_losses >= 2 else MIN_CONFIDENCE_PULLBACK) if short_pullback_setup else (MIN_CONFIDENCE_AFTER_LOSES if consecutive_losses >= 2 else MIN_CONFIDENCE_ENTRY)

            if short_confidence >= effective_min_conf:
                reason = 'SHORT_RANGE' if is_range_mode else ('SHORT_PULLBACK' if short_pullback_setup else ('LIQUIDITY_SWEEP_SHORT' if is_liquidity_sweep_short else ('BULL_TRAP_SWEEP' if is_bull_trap_sweep else ('SHORT_EARLY' if short_early else ('SHORT_LEVEL1' if short_level1 else ('SHORT_FAST' if short_fast else 'SHORT_MATRIX'))))))
                logger.info(f"{reason} ({'RANGE' if is_range_mode else 'TREND'}, REGIME:{regime}): conf={short_confidence:.2f} | impulso={impulse_raw:.3f}ATR | tech={short_conditions}/5 | adx={adx:.2f}")
                return result('SHORT', short_confidence, reason)
            return result('NEUTRAL', 0, f'SHORT_LOW_CONF:{short_confidence:.2f}')

        # No hay candidato válido
        return result('NEUTRAL', 0, f'NO_EDGE:L{long_conditions}/S{short_conditions}')


# ─── TRAILING STOP DINÁMICO ──────────────────────────────────────────────────
class TrailingStopManager:
    """Gestiona trailing stop dinámico basado en ATR"""

    def __init__(self):
        self.highest_price = 0.0
        self.trailing_active = False
        self.entry_price = 0.0
        self.side = 'LONG'

    def setup(self, entry_price, side):
        """Inicializa los precios de entrada y lado de la posición"""
        self.entry_price = entry_price
        self.side = side
        self.highest_price = entry_price
        self.trailing_active = False

    def update(self, mark_price, atr):
        """Actualiza trailing stop. Retorna True si debe cerrar."""
        if self.entry_price <= 0 or atr <= 0:
            return False

        activation_dist = ATR_TRAILING_ACTIVACION * atr
        trail_dist = ATR_TRAILING_DISTANCE * atr

        if self.side == 'LONG':
            if not self.trailing_active and mark_price >= (self.entry_price + activation_dist):
                self.trailing_active = True
                self.highest_price = mark_price
                logger.info(f"TRAILING ACTIVADO para LONG en ${mark_price:.2f} (Target: +${activation_dist:.2f})")
                return False
            if self.trailing_active:
                if mark_price > self.highest_price:
                    self.highest_price = mark_price
                stop_price = self.highest_price - trail_dist
                if mark_price <= stop_price:
                    logger.warning(f"TRAILING TRIGGERED LONG: Price {mark_price:.2f} <= Stop {stop_price:.2f}")
                    return True
        else:  # SHORT
            if not self.trailing_active and mark_price <= (self.entry_price - activation_dist):
                self.trailing_active = True
                self.highest_price = mark_price
                logger.info(f"TRAILING ACTIVADO para SHORT en ${mark_price:.2f} (Target: -${activation_dist:.2f})")
                return False
            if self.trailing_active:
                if mark_price < self.highest_price:
                    self.highest_price = mark_price
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


# ─── GESTIÓN DE ESTADO Y SESIÓN ──────────────────────────────────────────────
class SessionManager:
    """Gestiona el estado de la sesión de trading con persistencia"""

    def __init__(self, state_file=STATE_FILE):
        self.state_file = state_file
        self.session_data = {
            'started_at': datetime.now().isoformat(),
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_pnl': 0.0,
            'largest_win': 0.0,
            'largest_loss': 0.0,
            'consecutive_losses': 0,
            'circuit_breaker_triggered': False,
            'circuit_breaker_until': None,
            'mode': 'NORMAL',  # NORMAL, CIRCUIT_BRAKER, RECOVERY
            'last_trades': []
        }
        self.load_state()

    def load_state(self):
        """Carga estado previo si existe — CORREGIDO v2.1"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    saved_state = json.load(f)
                    # Restaurar TODOS los campos relevantes, incluyendo circuit breaker
                    full_restore = [
                        'total_trades', 'winning_trades', 'losing_trades',
                        'total_pnl', 'largest_win', 'largest_loss',
                        'consecutive_losses', 'circuit_breaker_triggered',
                        'circuit_breaker_until', 'mode', 'last_trades',
                        'loss_history'
                    ]
                    for key in full_restore:
                        if key in saved_state:
                            self.session_data[key] = saved_state[key]

                    # Si el CB estaba activo, restaurar modo correctamente
                    if saved_state.get('circuit_breaker_triggered', False):
                        self.session_data['mode'] = 'CIRCUIT_BREAKER'

                    # Verificar pérdida rolling contra límite del CB
                    loss_history = saved_state.get('loss_history', [])
                    if loss_history:
                        from datetime import datetime, timedelta
                        now = datetime.now()
                        cutoff = now - timedelta(hours=CIRCUIT_BREAKER_WINDOW_HOURS)
                        rolling_loss = sum(
                            entry['amount'] for entry in loss_history
                            if datetime.fromisoformat(entry['ts']) > cutoff
                        )
                        if rolling_loss >= CIRCUIT_BREAKER_MAX_LOSS_USDT:
                            if not self.session_data.get('circuit_breaker_triggered', False):
                                logger.warning(
                                    f"⚠ PÉRDIDA ROLLING ${rolling_loss:.4f} SUPERA LÍMITE ${CIRCUIT_BREAKER_MAX_LOSS_USDT:.2f}. "
                                    f"Activando circuit breaker automáticamente."
                                )
                                self.session_data['circuit_breaker_triggered'] = True
                                until = now + timedelta(minutes=CIRCUIT_BREAKER_PAUSE_MINUTES)
                                self.session_data['circuit_breaker_until'] = until.isoformat()
                                self.session_data['mode'] = 'CIRCUIT_BREAKER'
                                logger.info(f"CB activado hasta {until.strftime('%H:%M:%S')}")
            except Exception as e:
                logger.warning(f"No se pudo cargar estado previo: {e}")

    def save_state(self):
        """Guarda estado actual con datos completos para persistencia"""
        try:
            # Asegurar que se guardan datos críticos del CB
            save_data = {
                'total_trades': self.session_data['total_trades'],
                'winning_trades': self.session_data['winning_trades'],
                'losing_trades': self.session_data['losing_trades'],
                'total_pnl': self.session_data['total_pnl'],
                'largest_win': self.session_data['largest_win'],
                'largest_loss': self.session_data['largest_loss'],
                'consecutive_losses': self.session_data['consecutive_losses'],
                'circuit_breaker_triggered': self.session_data['circuit_breaker_triggered'],
                'circuit_breaker_until': self.session_data.get('circuit_breaker_until'),
                'mode': self.session_data.get('mode', 'NORMAL'),
                'last_trades': self.session_data.get('last_trades', []),
                'last_exit_time': datetime.now().isoformat(),
                'saved_at': datetime.now().isoformat()
            }

            # Mantener historial de pérdidas si existe
            if hasattr(self, '_loss_history'):
                save_data['loss_history'] = self._loss_history

            with open(self.state_file, 'w') as f:
                json.dump(save_data, f, indent=2)
        except Exception as e:
            logger.warning(f"No se pudo guardar estado: {e}")

    def record_trade(self, pnl, side, confidence, reason):
        """Registra un trade completado con historial de perdidas persistente (Ginger v2.1)"""
        self.session_data['total_trades'] += 1
        self.session_data['total_pnl'] += pnl

        # Inicializar historial de perdidas si no existe
        if not hasattr(self, '_loss_history'):
            self._loss_history = []

        if pnl > 0:
            self.session_data['winning_trades'] += 1
            self.session_data['consecutive_losses'] = 0
            if pnl > self.session_data['largest_win']:
                self.session_data['largest_win'] = pnl
            # Resetear historial de perdidas en ganancia
            self._loss_history = []
        else:
            self.session_data['losing_trades'] += 1
            self.session_data['consecutive_losses'] += 1
            if pnl < self.session_data['largest_loss']:
                self.session_data['largest_loss'] = pnl
            # Registrar perdida en el historial rolling
            self._loss_history.append({
                'ts': datetime.now().isoformat(),
                'amount': abs(pnl)
            })
            # Mantener solo ultimas 20 perdidas
            if len(self._loss_history) > 20:
                self._loss_history = self._loss_history[-20:]

        # Mantener ultimos 10 trades
        self.session_data['last_trades'].append({
            'time': datetime.now().isoformat(),
            'pnl': pnl,
            'side': side,
            'confidence': confidence,
            'reason': reason
        })
        self.session_data['last_trades'] = self.session_data['last_trades'][-10:]

        self.save_state()

    def check_circuit_breaker(self):
        """Verifica si el circuit breaker está activo"""
        if self.session_data['circuit_breaker_triggered']:
            if self.session_data['circuit_breaker_until']:
                until = datetime.fromisoformat(self.session_data['circuit_breaker_until'])
                if datetime.now() < until:
                    return True
                else:
                    self.session_data['circuit_breaker_triggered'] = False
                    self.session_data['circuit_breaker_until'] = None
                    self.session_data['mode'] = 'RECOVERY'
                    self.save_state()
                    return False
        return False

    def trigger_circuit_breaker(self):
        """Activa el circuit breaker"""
        until = datetime.now() + timedelta(minutes=CIRCUIT_BREAKER_PAUSE_MINUTES)
        self.session_data['circuit_breaker_triggered'] = True
        self.session_data['circuit_breaker_until'] = until.isoformat()
        self.session_data['mode'] = 'CIRCUIT_BREAKER'
        self.save_state()
        return until

    def get_stats(self):
        """Devuelve estadísticas de la sesión"""
        total = self.session_data['total_trades']
        wins = self.session_data['winning_trades']
        win_rate = (wins / total * 100) if total > 0 else 0
        return {
            'total_trades': total,
            'win_rate': win_rate,
            'total_pnl': self.session_data['total_pnl'],
            'consecutive_losses': self.session_data['consecutive_losses'],
            'mode': self.session_data['mode']
        }


# ─── MOTOR DE TRADING EN VIVO v2.0 ──────────────────────────────────────────
class LiveTradingEngine:
    """Motor completo de ejecución con todas las capas defensivas v2.0"""

    def __init__(self):
        self.client = BinanceFuturesClient()
        self.analyzer = TechnicalAnalyzer(self.client)
        self.trailing_manager = TrailingStopManager()
        self.session = SessionManager()
        self.is_running = True
        self.entry_time = None
        self.position = None
        self.last_exit_time = None
        self.pre_trade_balance = 0.0
        self.entry_signal_reason = ''
        self.entry_confidence = 0.0
        self._pending_entry_reason = ''
        self._pending_entry_confidence = 0.0

    def initialize(self):
        """Inicializa configuraciones del motor"""
        logger.info("=" * 70)
        logger.info("OPENBRIDGE LIVE TRADING ENGINE v2.0")
        logger.info("=" * 70)
        logger.info(f"Símbolo: {SYMBOL}")
        logger.info(f"Capital base: {CAPITAL_BASE} USDT")
        logger.info(f"Apalancamiento: {LEVERAGE}x")
        logger.info(f"Capital efectivo: {CAPITAL_BASE * LEVERAGE} USDT notional")
        logger.info(f"Modo Rango: {'ON' if RANGE_MODE_ENABLED else 'OFF'} (ADX < {RANGE_ADX_MAX})")
        logger.info(f"Circuit Breaker: ${CIRCUIT_BREAKER_MAX_LOSS_USDT} / {CIRCUIT_BREAKER_WINDOW_HOURS}h")
        logger.info("=" * 70)

        # Configurar apalancamiento
        self.client.set_leverage(LEVERAGE)

        # Verificar saldo
        balance = self.client.get_account_balance()
        logger.info(f"Saldo disponible: {balance['available']:.2f} USDT")

        if balance['available'] < CAPITAL_BASE:
            logger.warning(f"Saldo bajo. Necesario: {CAPITAL_BASE} USDT, Disponible: {balance['available']:.2f} USDT")

        # Enviar notificación de inicio
        send_telegram_async(
            f"🚀 <b>OpenBridge Trading Engine v2.0</b> iniciado\n"
            f"💰 Saldo: {balance['available']:.2f} USDT\n"
            f"⚙️ Modo: {self.session.session_data['mode']}\n"
            f"📊 Pérdidas consecutivas: {self.session.session_data['consecutive_losses']}"
        )

        logger.info("=" * 70)
        return balance

    # ─── Cooldown con escalado exponencial ────────────────────────────────

    def _get_effective_cooldown(self):
        """Calcula cooldown exponencial basado en pérdidas consecutivas.

        v3.1: exponente desplazado (losses-2) — 3 pérdidas = 240s (antes 480s).
        """
        losses = self.session.session_data['consecutive_losses']
        if losses < 2:
            return ENTRY_COOLDOWN_SECONDS
        exponent = min(max(0, losses - 2), 2)  # Cap 2^2 = 4x base (480s)
        return ENTRY_COOLDOWN_SECONDS * (2 ** exponent)

    def cooldown_remaining(self):
        """Segundos restantes antes de permitir nueva entrada."""
        if not self.last_exit_time:
            return 0
        effective_cd = self._get_effective_cooldown()
        elapsed = (datetime.now() - self.last_exit_time).total_seconds()
        return max(0, int(effective_cd - elapsed))

    def can_enter_new_position(self):
        """Verifica si se puede abrir posición nueva"""
        remaining = self.cooldown_remaining()
        if remaining > 0:
            logger.info(f"Entrada bloqueada por cooldown: {remaining}s restantes (exp: {self._get_effective_cooldown()}s)")
            return False
        return True

    # ─── Decisiones de entrada ────────────────────────────────────────────

    def should_enter_long(self, analysis):
        """Determina si entrar largo"""
        if not analysis.get('data_ok', False):
            return False
        if analysis['signal'] != 'LONG':
            return False
        # La confianza ya fue evaluada en _determine_signal,
        # si llegó como LONG ya pasó el umbral
        return True

    def should_enter_short(self, analysis):
        """Determina si entrar corto"""
        if not analysis.get('data_ok', False):
            return False
        if analysis['signal'] != 'SHORT':
            return False
        return True

    # ─── Decisiones de salida ─────────────────────────────────────────────

    def should_exit(self, position, analysis):
        """Determina si cerrar posición actual con todas las capas defensivas"""
        current_pnl_pct = position.get('pnl_pct_margin', position['pnl_pct'])
        mark_price = position['mark_price']

        atr = analysis.get('atr', 0.0)
        if atr <= 0:
            atr = mark_price * 0.005  # Fallback 0.5% del precio

        impulse_vel = analysis.get('impulse_velocity', 0.0)

        # 1. Hard SL absoluto
        if current_pnl_pct <= HARD_SL_PCT:
            logger.warning(f"HARD SL ALCANZADO: {current_pnl_pct:.2f}%")
            play_alarm_async()
            return True, "HARD_SL"

        # 2. Stop Loss dinámico basado en ATR
        entry_price = position['entry_price']
        sl_distance = ATR_SL_MULTIPLIER * atr
        if position['side'] == 'LONG':
            stop_loss_price = entry_price - sl_distance
            if mark_price <= stop_loss_price:
                logger.warning(f"ATR SL ALCANZADO (LONG): Mark {mark_price:.2f} <= SL {stop_loss_price:.2f}")
                return True, "ATR_SL"
        else:  # SHORT
            stop_loss_price = entry_price + sl_distance
            if mark_price >= stop_loss_price:
                logger.warning(f"ATR SL ALCANZADO (SHORT): Mark {mark_price:.2f} >= SL {stop_loss_price:.2f}")
                return True, "ATR_SL"

        # 3. Trailing Stop dinámico
        if self.trailing_manager.update(mark_price, atr):
            return True, "TRAILING_SL"

        # 4. MICRO_TP Fee-Aware
        ROUND_TRIP_FEE_ON_MARGIN_PCT = TAKER_FEE_PCT * 2 * LEVERAGE
        MICRO_TP_ROI_BASE_PCT = 2.0
        NET_MICRO_TP_ROI_PCT = MICRO_TP_ROI_BASE_PCT + ROUND_TRIP_FEE_ON_MARGIN_PCT
        MICRO_TP_VELOCITY_THRESHOLD = -0.05
        if current_pnl_pct >= NET_MICRO_TP_ROI_PCT:
            if (position['side'] == 'LONG' and impulse_vel < MICRO_TP_VELOCITY_THRESHOLD) or \
               (position['side'] == 'SHORT' and impulse_vel > -MICRO_TP_VELOCITY_THRESHOLD):
                logger.info(f"MICRO_TP NET: ROI {current_pnl_pct:.2f}% >= {NET_MICRO_TP_ROI_PCT:.2f}%")
                return True, "MICRO_TP"

        # 5. Reversión de señal
        if (position['side'] == 'LONG' and analysis['signal'] == 'SHORT') or \
           (position['side'] == 'SHORT' and analysis['signal'] == 'LONG'):
            return True, "REVERSAL"

        # 6. Decaimiento de tesis
        if position['side'] == 'LONG':
            opposite_impulse = analysis.get('impulse_raw', 0.0) <= -OPPOSITE_IMPULSE_EXIT_ATR
            same_signal_lost = analysis.get('signal') != 'LONG'
        else:
            opposite_impulse = analysis.get('impulse_raw', 0.0) >= OPPOSITE_IMPULSE_EXIT_ATR
            same_signal_lost = analysis.get('signal') != 'SHORT'

        entry_reason = getattr(self, 'entry_signal_reason', '') or ''
        is_agg_spike_entry = entry_reason.startswith('AGG_SPIKE')

        if (
            not is_agg_spike_entry
            and current_pnl_pct <= SIGNAL_DECAY_EXIT_PCT
            and same_signal_lost
        ):
            return True, "SIGNAL_DECAY"

        if current_pnl_pct < OPPOSITE_IMPULSE_MIN_LOSS_PCT and opposite_impulse:
            return True, "OPPOSITE_IMPULSE"

        # 7. Timeout
        if self.entry_time and (datetime.now() - self.entry_time).total_seconds() > MAX_HOLD_TIME:
            if current_pnl_pct >= TIMEOUT_PROFIT_MIN_PCT:
                return True, "TIMEOUT_PROFIT"
            elif current_pnl_pct < -5.0:
                return True, "TIMEOUT_STOPLOSS"

        return False, None

    # ─── Apertura de posición ─────────────────────────────────────────────

    def open_position(self, side, analysis):
        """Abre posición con todas las validaciones pre-disparo"""
        if not self.can_enter_new_position():
            return False

        # Verificar circuit breaker
        if self.session.check_circuit_breaker():
            logger.info("Entrada bloqueada por circuit breaker")
            return False

        initial_atr = analysis.get('atr', 0.0)
        confidence = analysis.get('confidence', 0.0)
        reason = analysis.get('signal_reason', 'UNKNOWN')
        self._pending_entry_reason = reason
        self._pending_entry_confidence = confidence

        # 1. Comprobar spread del Order Book
        spread = self.client.get_order_book_spread()
        if spread > MAX_SPREAD_PCT:
            logger.warning(f"Entrada abortada: Spread muy alto ({spread:.3f}% > {MAX_SPREAD_PCT}%)")
            return False

        # 2. Verificar que no hay posición existente
        try:
            existing = self.client.get_position(fail_on_error=True)
        except BinanceApiError as e:
            logger.warning(f"No se abre posición: lectura de posición falló ({e})")
            return False

        if existing:
            logger.warning(f"No se abre {side}: ya existe posición {existing['side']}")
            return False

        # 3. FIRE CHECK — Re-verificar impulso al momento del disparo
        # Entradas AGG_SPIKE usan el burst como trigger; no aplicar decay de velas.
        skip_fire_check = reason.startswith('AGG_SPIKE')
        fire_analysis = self.analyzer.analyze_market(
            consecutive_losses=self.session.session_data['consecutive_losses']
        )
        if not skip_fire_check and fire_analysis.get('data_ok', False):
            fire_vel = fire_analysis.get('impulse_velocity', 0.0)
            if side == 'LONG' and fire_vel < -IMPULSE_VELOCITY_MIN_FIRE:
                logger.warning(f"ENTRY_ABORT: impulso decayendo al disparo LONG (vel={fire_vel:.3f})")
                return False
            if side == 'SHORT' and fire_vel > IMPULSE_VELOCITY_MIN_FIRE:
                logger.warning(f"ENTRY_ABORT: impulso decayendo al disparo SHORT (vel={fire_vel:.3f})")
                return False

        # 4. Risk-Based Position Sizing
        balance_info = self.client.get_account_balance()
        balance = balance_info['available']

        if balance <= 0:
            logger.error("No se puede calcular tamaño: Balance disponible es 0")
            return False

        self.pre_trade_balance = balance

        price_ticker, _ = self.client.get_ticker()
        if not price_ticker or 'price' not in price_ticker:
            return False
        entry_price = float(price_ticker['price'])

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
            logger.warning(f"Entrada abortada: margen {margin_usdt:.2f} > 80% del balance {balance:.2f}")
            return False

        if effective_risk_pct > MAX_EFFECTIVE_RISK_PCT:
            logger.warning(f"Entrada abortada: riesgo {effective_risk_pct:.2f}% > techo {MAX_EFFECTIVE_RISK_PCT:.2f}%")
            return False

        # 5. DISPARO
        order_side = 'BUY' if side == 'LONG' else 'SELL'
        logger.info(
            f"🎯 DISPARANDO {side} | Razón: {reason} | Conf: {confidence:.2f} | "
            f"qty={size_btc:.3f} BTC | margen={margin_usdt:.2f} USDT | riesgo={effective_risk_pct:.2f}%"
        )
        play_alarm_async()

        result = self.client.place_order(order_side, size_btc, 'MARKET')

        if result:
            time.sleep(1)  # Esperar procesamiento

            # 6. SLIPPAGE GUARD — Verificar fill price
            try:
                self.position = self.client.get_position(fail_on_error=True)
            except BinanceApiError as e:
                logger.error(f"Orden enviada, pero no se pudo confirmar posición: {e}")
                return False

            if self.position:
                actual_entry = self.position['entry_price']
                if entry_price > 0:
                    adverse_slippage_pct = abs(actual_entry - entry_price) / entry_price * 100
                    if side == 'LONG':
                        adverse_slippage_pct = max(0, (actual_entry - entry_price) / entry_price * 100)
                    else:
                        adverse_slippage_pct = max(0, (entry_price - actual_entry) / entry_price * 100)

                    if adverse_slippage_pct > MAX_SLIPPAGE_PCT:
                        logger.warning(
                            f"⚠️ SLIPPAGE GUARD: slippage_adverso={adverse_slippage_pct:.4f}% > "
                            f"max={MAX_SLIPPAGE_PCT}% — CERRANDO INMEDIATAMENTE"
                        )
                        self.entry_signal_reason = reason
                        self.entry_confidence = confidence
                        self.close_position("SLIPPAGE_ABORT")
                        return False
                    else:
                        logger.info(f"Slippage OK: adverso={adverse_slippage_pct:.4f}% <= {MAX_SLIPPAGE_PCT}%")

                self.entry_time = datetime.now()
                self.entry_signal_reason = reason
                self.entry_confidence = confidence
                self.trailing_manager.setup(self.position['entry_price'], side)

                logger.info(f"✅ POSICIÓN ABIERTA:")
                logger.info(f"  Lado: {self.position['side']}")
                logger.info(f"  Entry: ${self.position['entry_price']:.2f}")
                logger.info(f"  Size: {self.position['size']:.6f} BTC")

                send_telegram_async(
                    f"🎯 <b>POSICIÓN ABIERTA</b>\n"
                    f"Lado: {self.position['side']}\n"
                    f"Entry: ${self.position['entry_price']:.2f}\n"
                    f"Size: {self.position['size']:.6f} BTC\n"
                    f"Razón: {reason}\n"
                    f"Confianza: {confidence:.0%}"
                )
                return True
        return False

    # ─── Cierre de posición ───────────────────────────────────────────────

    def close_position(self, reason=""):
        """Cierra posición actual y registra en sesión"""
        logger.info(f"CERRANDO POSICIÓN - Razón: {reason}")

        side = self.position['side'] if self.position else 'UNKNOWN'

        try:
            result = self.client.close_all_positions()
        except BinanceApiError as e:
            logger.error(f"No se pudo cerrar posición: {e}")
            return False

        if result:
            self.trailing_manager.reset()
            self.entry_time = None
            self.position = None
            self.last_exit_time = datetime.now()

            # Verificar balance post-trade
            time.sleep(2)  # Esperar asentamiento
            post_balance_info = self.client.get_account_balance()
            post_balance = post_balance_info['available']

            net_profit = 0.0
            if self.pre_trade_balance > 0:
                net_profit = post_balance - self.pre_trade_balance
                if net_profit > 0:
                    logger.info(
                        f"💸 GANANCIA NETA: +{net_profit:.4f} USDT | "
                        f"Balance: {self.pre_trade_balance:.4f} → {post_balance:.4f}"
                    )
                else:
                    logger.warning(
                        f"⚠️ PÉRDIDA NETA: {net_profit:.4f} USDT | "
                        f"Balance: {self.pre_trade_balance:.4f} → {post_balance:.4f}"
                    )

            trade_reason = self.entry_signal_reason or getattr(self, '_pending_entry_reason', '')
            trade_confidence = self.entry_confidence if self.entry_confidence else getattr(
                self, '_pending_entry_confidence', 0.0
            )
            self.session.record_trade(
                pnl=net_profit,
                side=side,
                confidence=trade_confidence,
                reason=trade_reason
            )
            self.entry_signal_reason = ''
            self.entry_confidence = 0.0
            self._pending_entry_reason = ''
            self._pending_entry_confidence = 0.0

            # Verificar si debe activar circuit breaker
            if net_profit < 0:
                # Evaluar pérdida rolling
                loss_history = getattr(self.session, '_loss_history', [])
                now = datetime.now()
                cutoff = now - timedelta(hours=CIRCUIT_BREAKER_WINDOW_HOURS)
                rolling_loss = sum(
                    entry['amount'] for entry in loss_history
                    if datetime.fromisoformat(entry['ts']) > cutoff
                )
                if rolling_loss >= CIRCUIT_BREAKER_MAX_LOSS_USDT:
                    until = self.session.trigger_circuit_breaker()
                    logger.warning(
                        f"🛑 CIRCUIT BREAKER ACTIVADO: Pérdida rolling ${rolling_loss:.4f} >= "
                        f"${CIRCUIT_BREAKER_MAX_LOSS_USDT:.2f}. Pausa hasta {until.strftime('%H:%M:%S')}"
                    )
                    play_alarm_async()
                    send_telegram_async(
                        f"🛑 <b>CIRCUIT BREAKER ACTIVADO</b>\n"
                        f"Pérdida rolling: ${rolling_loss:.4f}\n"
                        f"Pausa hasta: {until.strftime('%H:%M:%S')}"
                    )

            # Notificación Telegram
            stats = self.session.get_stats()
            emoji = "💰" if net_profit > 0 else "📉"
            send_telegram_async(
                f"{emoji} <b>POSICIÓN CERRADA</b>\n"
                f"Razón: {reason}\n"
                f"P&L Neto: {net_profit:+.4f} USDT\n"
                f"Trades: {stats['total_trades']} | Win Rate: {stats['win_rate']:.1f}%\n"
                f"P&L Sesión: {stats['total_pnl']:+.4f} USDT"
            )

            logger.info(
                f"POSICIÓN CERRADA | P&L: {net_profit:+.4f} USDT | "
                f"Trades: {stats['total_trades']} | WR: {stats['win_rate']:.1f}% | "
                f"Racha pérdidas: {stats['consecutive_losses']}"
            )
            return True
        return False

    # ─── Monitor de posición ──────────────────────────────────────────────

    def monitor_position(self, position, analysis):
        """Monitorea posición abierta y decide si cerrar"""
        pnl = position['pnl']
        pnl_pct = position.get('pnl_pct_margin', position['pnl_pct'])
        pnl_notional = position.get('pnl_pct_notional', 0)

        logger.info(
            f"MONITOREO - {position['side']} | P&L: {pnl:.4f} USDT "
            f"(ROI margen: {pnl_pct:.2f}% | notional: {pnl_notional:.2f}%)"
        )

        should_close, reason = self.should_exit(position, analysis)

        if should_close:
            self.close_position(reason)
            return False

        return True

    # ─── Ciclo de trading ─────────────────────────────────────────────────

    def run_cycle(self):
        """Ejecuta un ciclo completo de trading"""
        # Verificar posición actual en Binance
        position = self.client.get_position()

        # Analizar mercado
        analysis = self.analyzer.analyze_market(
            consecutive_losses=self.session.session_data['consecutive_losses']
        )

        if not analysis['data_ok']:
            logger.warning("Datos de mercado no disponibles, esperando...")
            return

        # Log de señal con información de modo
        mode_str = "🔄 RANGE" if analysis.get('is_range_mode') else "📈 TREND"
        logger.info(
            f"SEÑAL: {analysis['signal']} (conf: {analysis['confidence']:.0%}) | "
            f"Precio: ${analysis['price']:.2f} | ATR: ${analysis['atr']:.2f} | "
            f"Impulso: {analysis['impulse_raw']:.3f}ATR | ADX: {analysis['adx']:.2f} | "
            f"L/S: {analysis['long_conditions']}/{analysis['short_conditions']} | "
            f"LC/SC: {analysis['long_confidence']:.2f}/{analysis['short_confidence']:.2f} | "
            f"EMA50_1h: ${analysis['ema_50_1h']:.2f} | "
            f"Umbral ADX: {analysis.get('adaptive_adx_threshold', 'N/A'):.2f} | "
            f"Modo: {mode_str} | Razón: {analysis['signal_reason']}"
        )

        if position:
            # Hay posición abierta - monitorear
            self.position = position

            if not hasattr(self, 'pre_trade_balance') or self.pre_trade_balance == 0:
                bal_info = self.client.get_account_balance()
                self.pre_trade_balance = bal_info['available']
            # Configurar trailing si no estaba activo
            if not self.trailing_manager.entry_price or self.trailing_manager.entry_price == 0:
                self.trailing_manager.setup(position['entry_price'], position['side'])
            if not self.entry_time:
                self.entry_time = datetime.now()
            if not self.monitor_position(position, analysis):
                self.position = None
        else:
            # No hay posicion - buscar entrada
            # Verificar circuit breaker
            if self.session.check_circuit_breaker():
                return

            if self.should_enter_long(analysis):
                self.open_position('LONG', analysis)
            elif self.should_enter_short(analysis):
                self.open_position('SHORT', analysis)

    # --- Bucle principal ---

    def run(self):
        """Bucle principal del motor de trading"""
        try:
            self.initialize()

            # Verificar si hay posiciones previas
            existing = self.client.get_position()
            if existing:
                logger.warning(f"POSICION EXISTENTE DETECTADA - {existing['side']}")
                self.position = existing
                self.trailing_manager.setup(existing['entry_price'], existing['side'])
                self.entry_time = datetime.now()
                bal_info = self.client.get_account_balance()
                self.pre_trade_balance = bal_info['available']

            while self.is_running:
                try:
                    start_time = time.time()
                    self.run_cycle()
                    elapsed = time.time() - start_time
                    sleep_time = max(0, CYCLE_TIME - elapsed)
                    time.sleep(sleep_time)
                except Exception as e:
                    logger.error(f"Error en ciclo: {e}")
                    time.sleep(CYCLE_TIME)

        except KeyboardInterrupt:
            logger.info("Deteniendo motor de trading...")
            if self.position:
                logger.info("Cerrando posicion abierta...")
                self.close_position("MANUAL_SHUTDOWN")
            self.session.save_state()
            logger.info("Motor detenido")


# --- EJECUCION PRINCIPAL ---
if __name__ == "__main__":
    acquire_single_instance_lock()
    engine = LiveTradingEngine()
    engine.run()
