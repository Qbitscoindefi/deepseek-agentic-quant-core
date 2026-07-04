#!/usr/bin/env python3
# -*- coding: utf-8 -*-
######################################################################
# OPENBRIDGE DEEPSEEK TRADING ENGINE v1.0
# "EL FRANCOTIRADOR"
# Motor 100% DeepSeek API - Decisiones en vivo sin reglas fijas
# Incluye: analisis tecnico, on-chain, sentimiento, fundamental
######################################################################

import os
import sys
import time
import json
import math
import logging
import threading
import requests
import hmac
import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlencode

# ============================================================
# CONFIGURACION GLOBAL
# ============================================================
ENV_PATH = "C:/OPENBRIDGE/BINANCE/.env"
SYMBOL = "BTCUSDT"
LEVERAGE = 30
CAPITAL_BASE = 6.0
BASE_FUTURES = "https://fapi.binance.com"
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_TEMPERATURE = 0.3
DEEPSEEK_TIMEOUT = 30
REQUEST_TIMEOUT = 8
RECV_WINDOW = 60000
MAX_SPREAD_PCT = 0.05
MAX_SLIPPAGE_PCT = 0.04
CIRCUIT_BREAKER_MAX_LOSS = 0.50
CIRCUIT_BREAKER_WINDOW_HOURS = 4
PROMPT_FILE = "C:/OPENBRIDGE/BINANCE/prompts/estratega_sistema.md"
STATE_FILE = "C:/OPENBRIDGE/BINANCE/engine_state.json"

# Cooldown para evitar ejecutar la misma accion muchas veces
ACTION_DUPLICATE_COOLDOWN = 30  # segundos

# Fuentes de datos externas para contexto aumentado
FEAR_GREED_API_URL = "https://api.alternative.me/fng/?limit=1"
MEMPOOL_API_URL = "https://mempool.space/api/v1/fees/recommended"

# ============================================================
# LOGGING (configurado para evitar handlers duplicados)
# ============================================================
_root = logging.getLogger()
if _root.handlers:
    for _h in _root.handlers[:]:
        _root.removeHandler(_h)

logger = logging.getLogger("DeepSeek")
logger.setLevel(logging.INFO)
logger.propagate = False

formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

fh = logging.FileHandler("deepseek_engine.log", encoding="utf-8")
fh.setFormatter(formatter)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(formatter)

logger.addHandler(fh)
logger.addHandler(sh)

# ============================================================
# COLECTOR DE CONTEXTO EXTERNO (on-chain, sentimiento, fees)
# ============================================================

class ContextAugmenter:
    @staticmethod
    def get_fear_greed_index() -> dict:
        try:
            r = requests.get(FEAR_GREED_API_URL, timeout=5)
            if r.status_code == 200:
                data = r.json()
                value = data["data"][0]["value"]
                classification = data["data"][0]["value_classification"]
                return {"fear_greed_value": int(value), "fear_greed_sentimiento": classification}
        except Exception as e:
            logger.debug("No se pudo obtener Fear & Greed: %s", e)
        return {"fear_greed_value": 50, "fear_greed_sentimiento": "Neutral"}

    @staticmethod
    def get_btc_fees() -> dict:
        try:
            r = requests.get(MEMPOOL_API_URL, timeout=5)
            if r.status_code == 200:
                data = r.json()
                return {
                    "fee_fastest": data.get("fastestFee", 0),
                    "fee_hour": data.get("hourFee", 0),
                    "fee_minimum": data.get("minimumFee", 0)
                }
        except Exception as e:
            logger.debug("No se pudo obtener fees: %s", e)
        return {"fee_fastest": 0, "fee_hour": 0, "fee_minimum": 0}

    @staticmethod
    def get_long_short_ratio() -> dict:
        try:
            r = requests.get(f"{BASE_FUTURES}/futures/data/globalLongShortAccountRatio?symbol={SYMBOL}&period=1h", timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data:
                    latest = data[-1]
                    return {
                        "long_short_ratio": float(latest.get("longShortRatio", 1.0)),
                        "long_account_pct": float(latest.get("longAccount", 50)),
                        "short_account_pct": float(latest.get("shortAccount", 50))
                    }
        except Exception as e:
            logger.debug("No se pudo obtener long/short ratio: %s", e)
        return {"long_short_ratio": 1.0, "long_account_pct": 50, "short_account_pct": 50}

    @staticmethod
    def get_open_interest() -> dict:
        try:
            r = requests.get(f"{BASE_FUTURES}/fapi/v1/openInterest?symbol={SYMBOL}", timeout=5)
            if r.status_code == 200:
                data = r.json()
                oi = float(data.get("openInterest", 0))
                return {"open_interest_btc": oi, "open_interest_usd": 0}
        except Exception as e:
            logger.debug("No se pudo obtener open interest: %s", e)
        return {"open_interest_btc": 0, "open_interest_usd": 0}

    @classmethod
    def augment_context(cls, base_context: dict) -> dict:
        if base_context is None:
            return None

        fg = cls.get_fear_greed_index()
        fees = cls.get_btc_fees()
        ls = cls.get_long_short_ratio()
        oi = cls.get_open_interest()

        precio = base_context.get("mercado", {}).get("precio_actual", 0)
        if precio > 0 and oi["open_interest_btc"] > 0:
            oi["open_interest_usd"] = oi["open_interest_btc"] * precio

        base_context["fundamental"] = {
            "fear_greed": fg,
            "bitcoin_fees": fees,
            "long_short_ratio": ls,
            "open_interest": oi,
            "analisis_caliente": (
                "Fear & Greed: {}/100 ({}). Long/Short Ratio: {:.2f} (Long: {:.1f}% / Short: {:.1f}%). "
                "Fees BTC: rapido={} sat/vB, economico={}. Open Interest: {:.1f} BTC (${:,}.)."
            ).format(
                fg["fear_greed_value"],
                fg["fear_greed_sentimiento"],
                ls["long_short_ratio"],
                ls["long_account_pct"],
                ls["short_account_pct"],
                fees["fee_fastest"],
                fees["fee_minimum"],
                oi["open_interest_btc"],
                int(oi["open_interest_usd"])
            )
        }

        return base_context

# ============================================================
# BINANCE CLIENT - API REST (con sincronizacion de tiempo)
# ============================================================

class BinanceClient:
    def __init__(self):
        self.env = self._load_env()
        self.api_key = self.env.get("BINANCE_API_KEY", "")
        self.api_secret = self.env.get("BINANCE_API_SECRET", "")
        self.time_offset = 0
        try:
            self.sync_time()
        except Exception as e:
            logger.debug("No se pudo sincronizar tiempo Binance: %s", e)

    def _load_env(self):
        env = {}
        try:
            with open(ENV_PATH, "r") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip()
        except Exception:
            pass
        return env

    def sync_time(self):
        try:
            r = requests.get(f"{BASE_FUTURES}/fapi/v1/time", timeout=5)
            r.raise_for_status()
            data = r.json()
            server_time = int(data.get("serverTime", 0))
            local_time = int(time.time() * 1000)
            self.time_offset = server_time - local_time
            logger.info("Binance time sync: server=%s local=%s offset=%dms", server_time, local_time, self.time_offset)
            return True
        except Exception as e:
            logger.warning("Error sincronizando hora con Binance: %s", e)
            return False

    def _sign_request(self, params):
        query = urlencode(params)
        signature = hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        params["signature"] = signature
        return params

    def _request(self, method, endpoint, params=None, signed=False):
        url = BASE_FUTURES + endpoint
        headers = {}
        if self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key
        if signed:
            params = params or {}
            params["timestamp"] = int(time.time() * 1000) + int(self.time_offset)
            params["recvWindow"] = RECV_WINDOW
            self._sign_request(params)
        try:
            r = requests.request(method, url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            try:
                return r.json()
            except Exception:
                return None
        except Exception as e:
            logger.warning("Binance req error temporal: %s", e)
            return None

    def get_ticker(self):
        return self._request("GET", "/fapi/v1/ticker/price", {"symbol": SYMBOL})

    def get_klines(self, interval="5m", limit=50):
        return self._request("GET", "/fapi/v1/klines", {"symbol": SYMBOL, "interval": interval, "limit": limit})

    def get_account_balance(self):
        r = self._request("GET", "/fapi/v2/account", signed=True)
        if r and isinstance(r, dict):
            for a in r.get("assets", []):
                if a.get("asset") == "USDT":
                    return float(a.get("walletBalance", 0))
        elif isinstance(r, dict) and "msg" in r:
            logger.warning("Error de API al consultar balance: %s", r.get("msg"))
        return 0.0

    def get_position(self):
        r = self._request("GET", "/fapi/v2/positionRisk", {"symbol": SYMBOL}, signed=True)
        if r and isinstance(r, list):
            for p in r:
                try:
                    if isinstance(p, dict) and float(p.get("positionAmt", 0)) != 0:
                        return {
                            "side": "LONG" if float(p["positionAmt"]) > 0 else "SHORT",
                            "entry_price": float(p.get("entryPrice", 0)),
                            "size": abs(float(p.get("positionAmt", 0))),
                            "pnl": float(p.get("unRealizedProfit", 0)),
                            "mark_price": float(p.get("markPrice", 0))
                        }
                except Exception:
                    continue
        elif isinstance(r, dict) and "msg" in r:
            logger.warning("Error de API al consultar posición: %s", r.get("msg"))
        return None

    def set_leverage(self):
        return self._request("POST", "/fapi/v1/leverage", {"symbol": SYMBOL, "leverage": LEVERAGE}, signed=True)

    def place_market_order(self, side, quantity):
        params = {"symbol": SYMBOL, "side": side, "type": "MARKET", "quantity": quantity}
        return self._request("POST", "/fapi/v1/order", params, signed=True)

    def close_position(self):
        pos = self.get_position()
        if pos:
            side = "SELL" if pos["side"] == "LONG" else "BUY"
            return self.place_market_order(side, round(pos["size"], 3))
        return None

    def get_order_book_spread(self):
        data = self._request("GET", "/fapi/v1/depth", {"symbol": SYMBOL, "limit": 5})
        if data and isinstance(data, dict):
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            if bids and asks:
                best_bid = float(bids[0][0])
                best_ask = float(asks[0][0])
                if best_bid > 0 and best_ask > 0:
                    return (best_ask - best_bid) / best_bid * 100
        return 0.0

# ============================================================
# DEEPSEEK CLIENT - API CHAT
# ============================================================

class DeepSeekClient:
    def __init__(self):
        self.api_key = self._load_api_key()
        self.prompt_system = self._load_prompt()
        if self.api_key:
            logger.info("DeepSeek API key cargada correctamente")
        else:
            logger.warning("DeepSeek sin API key - modo MOCK simulado")

    def _load_api_key(self):
        try:
            with open(ENV_PATH, "r") as f:
                for line in f:
                    if line.startswith("DEEPSEEK_API_KEY="):
                        return line.strip().split("=", 1)[1]
        except Exception:
            pass
        return ""

    def _load_prompt(self):
        try:
            with open(PROMPT_FILE, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return (
                "Eres un Quantitative Trader de nivel institucional especializado en futuros de Bitcoin.\n\n"
                "TUS CAPACIDADES:\n"
                "- Analizas datos técnicos (RSI, ATR, EMAs, volumen, impulso, spread)\n"
                "- Interpretas datos fundamentales (Fear & Greed, Long/Short Ratio, Open Interest, fees on-chain)\n"
                "- Tomas decisiones de trading en vivo SIN reglas fijas — usas tu criterio de agente\n\n"
                "FORMATO DE RESPUESTA OBLIGATORIO:\n"
                "Debes responder ÚNICAMENTE con un objeto JSON válido. NO incluyas markdown, ni bloques de código (```json), ni texto fuera del JSON.\n"
                "Asegúrate de que TODAS las claves y TODOS los valores de tipo string estén encerrados entre comillas dobles.\n\n"
                "CAMPOS DEL JSON:\n"
                "- \"accion\": \"LONG\", \"SHORT\", \"CERRAR\", \"NEUTRAL\" o \"AJUSTAR\"\n"
                "- \"confianza\": número decimal entre 0.0 y 1.0\n"
                "- \"razon\": breve string resumiendo la causa\n"
                "- \"explicacion\": string detallando el análisis\n"
                "- \"ajustes\": diccionario vacío {}\n\n"
                "EJEMPLO DE RESPUESTA PERFECTA:\n"
                "{\n"
                "  \"accion\": \"NEUTRAL\",\n"
                "  \"confianza\": 0.0,\n"
                "  \"razon\": \"MERCADO_LATERAL\",\n"
                "  \"explicacion\": \"Tendencia lateral, spread aceptable, esperando confirmación.\",\n"
                "  \"ajustes\": {}\n"
                "}"
            )

    def consultar(self, contexto):
        try:
            if not self.api_key:
                return self._mock_decision(contexto)

            headers = {
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json"
            }

            payload = {
                "model": DEEPSEEK_MODEL,
                "temperature": DEEPSEEK_TEMPERATURE,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": self.prompt_system},
                    {"role": "user", "content": json.dumps(contexto, indent=2)}
                ]
            }

            r = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=DEEPSEEK_TIMEOUT)

            if r.status_code == 200:
                data = r.json()
                text = data["choices"][0]["message"]["content"]
                text = text.replace("```json", "").replace("```", "").strip()
                logger.info("DeepSeek respuesta raw: %s%s", text[:250], "..." if len(text) > 250 else "")
                return self._parse_response(text)
            else:
                logger.warning("DeepSeek API error: %s - %s", r.status_code, r.text[:200])

        except Exception as e:
            logger.warning("DeepSeek request failed: %s", e)

        return self._mock_decision(contexto)

    def _parse_response(self, text):
        try:
            return json.loads(text)
        except Exception as e:
            logger.warning("Fallo extremo al parsear: %s | Contenido: %s", e, text[:200])
            return {"accion": "NEUTRAL", "razon": "PARSE_ERROR", "confianza": 0.0, "ajustes": {}}

    def _mock_decision(self, ctx):
        signal = ctx.get("senal", {}).get("tipo", "NEUTRAL")
        if signal in ("LONG", "SHORT"):
            return {
                "accion": "ABRIR_" + signal,
                "confianza": 0.3,
                "razon": "MOCK_LIVE",
                "explicacion": "Modo simulado - operando con reglas base",
                "ajustes": {}
            }
        return {
            "accion": "NEUTRAL",
            "confianza": 0.0,
            "razon": "NO_SIGNAL",
            "explicacion": "Esperando senal valida",
            "ajustes": {}
        }

# ============================================================
# MARKET CONTEXT COLLECTOR
# ============================================================

class MarketContextCollector:
    def __init__(self):
        self.binance = BinanceClient()
        self.last_ticker = {}
        self.last_klines = []
        self.last_update = 0

    def collect(self):
        ctx = {}
        try:
            ticker = self.binance.get_ticker()
            if ticker:
                ctx["precio"] = float(ticker.get("price", 0))

            klines = self.binance.get_klines()
            if klines and len(klines) > 20:
                ctx["velas"] = klines[-50:]
                closes = [float(k[4]) for k in klines[-20:]]
                highs = [float(k[2]) for k in klines[-20:]]
                lows = [float(k[3]) for k in klines[-20:]]
                ctx["max_20"] = max(highs)
                ctx["min_20"] = min(lows)
                ctx["ema_rapida"] = sum(closes[-5:]) / 5
                ctx["ema_lenta"] = sum(closes) / len(closes)
                ctx["volumen"] = sum(float(k[5]) for k in klines[-5:])
                ctx["cuerpo_medio"] = sum(abs(float(k[4]) - float(k[1])) for k in klines[-5:]) / 5

            spread = self.binance.get_order_book_spread()
            ctx["spread_pct"] = spread

            self.last_ticker = ctx
            self.last_update = time.time()

        except Exception as e:
            logger.warning("Collect error: %s", e)

        return ctx

    def analyze_technicals(self, klines):
        if not klines or len(klines) < 20:
            return {}

        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        analysis = {}

        gains = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
        losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
        avg_gain = sum(gains[-14:]) / 14 if len(gains) >= 14 else 0.5
        avg_loss = sum(losses[-14:]) / 14 if len(losses) >= 14 else 0.5
        rs = avg_gain / avg_loss if avg_loss > 0 else 50
        analysis["rsi"] = 100 - (100 / (1 + rs))

        trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
               for i in range(1, len(closes))]
        analysis["atr"] = sum(trs[-14:]) / 14 if len(trs) >= 14 else 0

        analysis["impulso"] = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0

        if analysis["rsi"] > 60:
            analysis["tendencia"] = "ALCISTA"
        elif analysis["rsi"] < 40:
            analysis["tendencia"] = "BAJISTA"
        else:
            analysis["tendencia"] = "LATERAL"

        return analysis

# ============================================================
# ENGINE STATE MANAGER
# ============================================================

class EngineState:
    def __init__(self):
        self.data = self._load()

    def _load(self):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {'trades': [], 'total_trades': 0, 'wins': 0, 'losses': 0,
                    'pnl': 0.0, 'consecutive_losses': 0, 'max_consecutive_losses': 0}

    def save(self):
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(self.data, f, indent=2)
        except Exception:
            pass

    def add_trade(self, side, entry, exit_p, pnl):
        trade = {'side': side, 'entry': entry, 'exit': exit_p, 'pnl': pnl, 'time': datetime.now().isoformat()}
        self.data['trades'].append(trade)
        self.data['total_trades'] += 1
        self.data['pnl'] = self.data.get('pnl', 0) + pnl
        if pnl > 0:
            self.data['wins'] = self.data.get('wins', 0) + 1
            self.data['consecutive_losses'] = 0
        else:
            self.data['losses'] = self.data.get('losses', 0) + 1
            self.data['consecutive_losses'] = self.data.get('consecutive_losses', 0) + 1
            self.data['max_consecutive_losses'] = max(
                self.data.get('max_consecutive_losses', 0),
                self.data['consecutive_losses'])
        self.save()

    def get_win_rate(self):
        total = self.data.get('total_trades', 0)
        if total == 0:
            return 0
        return self.data.get('wins', 0) / total * 100

    def circuit_breaker_active(self):
        recent = [t for t in self.data.get('trades', [])
                  if datetime.fromisoformat(t['time']) > datetime.now() - timedelta(hours=CIRCUIT_BREAKER_WINDOW_HOURS)]
        loss = sum(t['pnl'] for t in recent if t['pnl'] < 0)
        return abs(loss) >= CIRCUIT_BREAKER_MAX_LOSS

    def cooldown_active(self):
        losses = self.data.get('consecutive_losses', 0)
        if losses == 0:
            return False, 0
        trades = self.data.get('trades', [])
        if not trades:
            return False, 0
        last_time = datetime.fromisoformat(trades[-1]['time'])
        cooldown = 2 ** (losses - 1) * 60
        elapsed = (datetime.now() - last_time).total_seconds()
        return elapsed < cooldown, max(0, cooldown - elapsed)

    def get_summary(self):
        return {
            'total_trades': self.data.get('total_trades', 0),
            'win_rate': round(self.get_win_rate(), 1),
            'consecutive_losses': self.data.get('consecutive_losses', 0),
            'pnl': round(self.data.get('pnl', 0), 2),
            'cb_active': self.circuit_breaker_active()
        }

# ============================================================
# DEEPSEEK TRADING ENGINE - NUCLEO
# ============================================================

class DeepSeekTradingEngine:
    def __init__(self):
        self.running = False
        self.binance = BinanceClient()
        self.collector = MarketContextCollector()
        self.augmenter = ContextAugmenter()
        self.deepseek = DeepSeekClient()
        self.state = EngineState()
        self.position = None
        self.lock = threading.Lock()
        self.cycle_count = 0
        self.last_context = {}
        self.last_decision = {'accion': None, 'timestamp': 0.0}

    def build_context(self):
        ctx = self.collector.collect()
        if not ctx or not ctx.get('velas'):
            return None

        tech = self.collector.analyze_technicals(ctx.get('velas', []))
        pos = self.binance.get_position()
        bal = self.binance.get_account_balance()
        summary = self.state.get_summary()

        contexto = {
            'timestamp': datetime.now().isoformat(),
            'symbol': SYMBOL,
            'senal_tecnica': {
                'tendencia': tech.get('tendencia', 'LATERAL'),
                'rsi': round(tech.get('rsi', 50), 1),
                'atr': round(tech.get('atr', 0), 2),
                'impulso_pct': round(tech.get('impulso', 0), 2)
            },
            'mercado': {
                'precio_actual': ctx.get('precio', 0),
                'max_20_periodos': ctx.get('max_20', 0),
                'min_20_periodos': ctx.get('min_20', 0),
                'ema_rapida_5': round(ctx.get('ema_rapida', 0), 2),
                'ema_lenta_20': round(ctx.get('ema_lenta', 0), 2),
                'volumen_5velas': ctx.get('volumen', 0),
                'spread_pct': ctx.get('spread_pct', 0)
            },
            'posicion_actual': pos if pos else None,
            'sesion': {
                'balance_usdt': bal,
                'capital_efectivo': bal * LEVERAGE,
                'trades_totales': summary['total_trades'],
                'win_rate_pct': summary['win_rate'],
                'racha_perdidas': summary['consecutive_losses'],
                'pnl_acumulado': summary['pnl'],
                'circuit_breaker_activo': summary['cb_active']
            }
        }

        contexto = self.augmenter.augment_context(contexto)
        return contexto

    def _is_duplicate_action(self, accion):
        now = time.time()
        last_action = self.last_decision.get('accion')
        last_ts = self.last_decision.get('timestamp', 0)
        if last_action == accion and (now - last_ts) < ACTION_DUPLICATE_COOLDOWN:
            return True
        return False

    def process_decision(self, decision, context):
        accion = decision.get('accion', 'NEUTRAL')
        confianza = decision.get('confianza', 0.0)

        if self._is_duplicate_action(accion):
            logger.info("Ignorando accion duplicada por cooldown: %s", accion)
            self.last_decision['timestamp'] = time.time()
            return

        self.last_decision = {'accion': accion, 'timestamp': time.time()}

        if not context or 'mercado' not in context:
            return

        precio = context['mercado'].get('precio_actual', 0)
        if precio <= 0:
            return

        if accion in ('LONG', 'ABRIR_LONG'):
            if self.position:
                logger.info("DeepSeek pide LONG pero ya hay posicion %s", self.position['side'])
                return
            if self.state.circuit_breaker_active():
                logger.warning("CB activo - no puede abrir LONG")
                return
            cd, rem = self.state.cooldown_active()
            if cd:
                logger.info("Cooldown activo %.0fs - LONG bloqueado", rem)
                return
            if context['mercado'].get('spread_pct', 0) > MAX_SPREAD_PCT:
                logger.warning("Spread demasiado alto para LONG")
                return

            size = round(CAPITAL_BASE * LEVERAGE / precio, 3)
            if size < 0.001:
                size = 0.001

            logger.info("Abrir LONG | confianza: %.0f%% | razon: %s | size: %s BTC", confianza * 100, decision.get('razon', ''), size)
            result = self.binance.place_market_order('BUY', size)
            if result and isinstance(result, dict) and result.get('orderId'):
                self.position = {'side': 'LONG', 'size': size, 'entry_time': time.time(), 'entry_price': precio, 'decision': decision}
                logger.info("Posicion LONG abierta @ %s", precio)

        elif accion in ('SHORT', 'ABRIR_SHORT'):
            if self.position:
                logger.info("DeepSeek pide SHORT pero ya hay posicion %s", self.position['side'])
                return
            if self.state.circuit_breaker_active():
                logger.warning("CB activo - no puede abrir SHORT")
                return
            cd, rem = self.state.cooldown_active()
            if cd:
                logger.info("Cooldown activo %.0fs - SHORT bloqueado", rem)
                return
            if context['mercado'].get('spread_pct', 0) > MAX_SPREAD_PCT:
                logger.warning("Spread demasiado alto para SHORT")
                return

            size = round(CAPITAL_BASE * LEVERAGE / precio, 3)
            if size < 0.001:
                size = 0.001

            logger.info("Abrir SHORT | confianza: %.0f%% | razon: %s | size: %s BTC", confianza * 100, decision.get('razon', ''), size)
            result = self.binance.place_market_order('SELL', size)
            if result and isinstance(result, dict) and result.get('orderId'):
                self.position = {'side': 'SHORT', 'size': size, 'entry_time': time.time(), 'entry_price': precio, 'decision': decision}
                logger.info("Posicion SHORT abierta @ %s", precio)

        elif accion == 'CERRAR':
            if not self.position:
                logger.info("DeepSeek pide CERRAR pero no hay posicion")
                return
            logger.info("Cerrar %s | razon: %s", self.position['side'], decision.get('razon', ''))
            result = self.binance.close_position()
            if result:
                pnl = (precio - self.position['entry_price']) * self.position['size'] if self.position['side'] == 'LONG' else (self.position['entry_price'] - precio) * self.position['size']
                self.state.add_trade(self.position['side'], self.position['entry_price'], precio, pnl)
                logger.info("Posicion cerrada | PnL: %.2f USDT", pnl)
                self.position = None

        elif accion == 'AJUSTAR':
            ajustes = decision.get('ajustes', {})
            for k, v in ajustes.items():
                if k in globals():
                    globals()[k] = v
                    logger.info("Ajuste: %s = %s", k, v)

        elif accion == 'NEUTRAL':
            logger.info("DeepSeek: NEUTRAL | %s | conf: %.0f%%", decision.get('razon', 'esperando'), confianza * 100)

    def run_cycle(self):
        context = self.build_context()
        if not context:
            logger.debug("Contexto no disponible aun")
            return

        self.last_context = context
        self.cycle_count += 1

        if self.position:
            pos = self.binance.get_position()
            if not pos:
                logger.warning("Posicion perdida en exchange - sincronizando")
                self.position = None
                return

            pnl = pos.get('pnl', 0)
            entry = self.position['entry_price']
            mark = pos.get('mark_price', 0)
            atr = context.get('senal_tecnica', {}).get('atr', 0)

            if entry > 0 and atr > 0:
                if self.position['side'] == 'LONG':
                    stop_price = entry - atr * 1.8
                    if mark < stop_price:
                        logger.info("Stop LONG: %.2f < %.2f", mark, stop_price)
                        self.binance.close_position()
                        self.state.add_trade('LONG', entry, mark, pnl)
                        self.position = None
                        return
                else:
                    stop_price = entry + atr * 1.8
                    if mark > stop_price:
                        logger.info("Stop SHORT: %.2f > %.2f", mark, stop_price)
                        self.binance.close_position()
                        self.state.add_trade('SHORT', entry, mark, pnl)
                        self.position = None
                        return

            decision = self.deepseek.consultar(context)
            self.last_decision = {'accion': decision.get('accion'), 'timestamp': time.time()}
            if decision.get('accion') == 'CERRAR':
                self.process_decision(decision, context)
            else:
                logger.info("Monitoreando %s | PnL: %.2f | DeepSeek: %s", pos['side'], pnl, decision.get('accion', '?'))

        else:
            logger.info("Ciclo #%d - Consultando a DeepSeek...", self.cycle_count)
            decision = self.deepseek.consultar(context)
            self.last_decision = {'accion': decision.get('accion'), 'timestamp': time.time()}
            logger.info("DeepSeek decide: %s | conf: %.0f%% | razon: %s", decision.get('accion', '?'), decision.get('confianza', 0) * 100, decision.get('razon', ''))
            self.process_decision(decision, context)

    def run(self):
        self.running = True

        logger.info("=" * 60)
        logger.info("OPENBRIDGE DEEPSEEK TRADING ENGINE v1.0")
        logger.info("EL FRANCOTIRADOR")
        logger.info("=" * 60)
        logger.info("Simbolo: %s | Apalancamiento: %sx | Capital: %s USDT", SYMBOL, LEVERAGE, CAPITAL_BASE)
        logger.info("DeepSeek Model: %s | Temp: %s", DEEPSEEK_MODEL, DEEPSEEK_TEMPERATURE)
        logger.info("=" * 60)

        try:
            self.binance.set_leverage()
            logger.info("Apalancamiento configurado: %sx", LEVERAGE)
        except Exception as e:
            logger.debug("No se pudo configurar apalancamiento: %s", e)

        bal = self.binance.get_account_balance()
        logger.info("Balance disponible: %s USDT", bal)

        pos = self.binance.get_position()
        if pos:
            logger.warning("Posicion existente detectada: %s @ %s", pos['side'], pos['entry_price'])
            self.position = pos

        logger.info("Motor agentico DeepSeek ACTIVO")
        logger.info("=" * 60)

        while self.running:
            cycle_start = time.time()
            try:
                self.run_cycle()
            except Exception as e:
                logger.error("Error en ciclo: %s", e)
                import traceback
                traceback.print_exc()

            elapsed = time.time() - cycle_start
            sleep_time = max(0.5, 5.0 - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        logger.info("Motor DeepSeek detenido")
        self.state.save()

    def stop(self):
        self.running = False

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == '__main__':
    engine = DeepSeekTradingEngine()
    try:
        engine.run()
    except KeyboardInterrupt:
        logger.info("Interrupcion de usuario - deteniendo motor...")
        engine.stop()
        if engine.position:
            logger.info("Cerrando posicion por shutdown...")
            engine.binance.close_position()
        engine.state.save()
        logger.info("Motor DeepSeek detenido")
    except Exception as e:
        logger.critical("Error fatal: %s", e)
        engine.stop()
