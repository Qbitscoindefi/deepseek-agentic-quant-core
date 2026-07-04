#!/usr/bin/env python3
# -*- coding: utf-8 -*-

######################################################################
# OPENBRIDGE DEEPSEEK TRADING ENGINE v1.0
# "EL FRANCOTIRADOR"
# Motor 100% DeepSeek API - Decisiones en vivo
######################################################################

import os, sys, time, json, math, logging, threading
import requests, hmac, hashlib, base64, re
from datetime import datetime, timedelta
from collections import deque
from urllib.parse import urlencode

# === CONFIG ===
ENV_PATH = "C:/OPENBRIDGE/BINANCE/.env"
SYMBOL = "BTCUSDT"
LEVERAGE = 30
CAPITAL_BASE = 6.0
BASE_FUTURES = "https://fapi.binance.com"
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_TEMPERATURE = 0.3
DEEPSEEK_TIMEOUT = 15
REQUEST_TIMEOUT = 8
RECV_WINDOW = 5000
MAX_SPREAD_PCT = 0.05
MAX_SLIPPAGE_PCT = 0.04
CIRCUIT_BREAKER_MAX_LOSS = 0.50
CIRCUIT_BREAKER_WINDOW_HOURS = 4
PROMPT_FILE = "C:/OPENBRIDGE/BINANCE/prompts/estratega_sistema.md"
STATE_FILE = "C:/OPENBRIDGE/BINANCE/engine_state.json"

# === LOGGING ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
for h in [logging.FileHandler("deepseek_engine.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)]:
    logging.getLogger().addHandler(h)
logger = logging.getLogger("DeepSeek")

# === DEEPSEEK CLIENT ===

class DeepSeekClient:
    def __init__(self):
        self.api_key = self._load_api_key()
        self.prompt_system = self._load_prompt()

    def _load_api_key(self):
        try:
            with open(ENV_PATH, "r") as f:
                for line in f:
                    if line.startswith("DEEPSEEK_API_KEY="):
                        return line.strip().split("=", 1)[1]
        except: pass
        logger.warning("No DEEPSEEK_API_KEY en .env")
        return ""

    def _load_prompt(self):
        try:
            with open(PROMPT_FILE, "r", encoding="utf-8") as f:
                return f.read()
        except:
            return "Eres un trader profesional."

    def consultar(self, contexto):
        try:
            if not self.api_key:
                return self._mock_decision(contexto)
            headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
            payload = {
                "model": DEEPSEEK_MODEL,
                "temperature": DEEPSEEK_TEMPERATURE,
                "messages": [
                    {"role": "system", "content": self.prompt_system},
                    {"role": "user", "content": json.dumps(contexto, indent=2)}
                ]
            }
            r = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=DEEPSEEK_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                text = data["choices"][0]["message"]["content"]
                return self._parse_response(text)
        except Exception as e:
            logger.warning(f"DeepSeek error: {e}")
        return self._mock_decision(contexto)

    def _parse_response(self, text):
        try:
            return json.loads(text)
        except:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try: return json.loads(match.group())
                except: pass
        return {"accion": "NEUTRAL", "razon": "PARSE_ERROR", "confianza": 0.0, "ajustes": {}}

    def _mock_decision(self, ctx):
        signal = ctx.get("senal", {}).get("tipo", "NEUTRAL")
        if signal in ("LONG", "SHORT"):
            return {"accion": "ABRIR_" + signal, "confianza": 0.3, "razon": "MOCK", "explicacion": "Modo simulado", "ajustes": {}}
        return {"accion": "NEUTRAL", "confianza": 0.0, "razon": "NO_SIGNAL", "explicacion": "Esperando", "ajustes": {}}

# === MARKET CONTEXT COLLECTOR ===

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
                ctx["ema_rapida"] = sum(closes[-5:])/5
                ctx["ema_lenta"] = sum(closes)/len(closes)
                ctx["volumen"] = sum(float(k[5]) for k in klines[-5:])
                ctx["cuerpo_medio"] = sum(abs(float(k[4])-float(k[1])) for k in klines[-5:])/5
            spread = self.binance.get_order_book_spread()
            ctx["spread_pct"] = spread
            self.last_ticker = ctx
            self.last_update = time.time()
        except Exception as e:
            logger.warning(f"Collect error: {e}")
        return ctx

    def analyze_technicals(self, klines):
        if not klines or len(klines) < 20: return {}
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        analysis = {}
        # RSI
        gains = [max(closes[i]-closes[i-1],0) for i in range(1,len(closes))]
        losses = [max(closes[i-1]-closes[i],0) for i in range(1,len(closes))]
        avg_gain = sum(gains[-14:])/14 if len(gains)>=14 else 0.5
        avg_loss = sum(losses[-14:])/14 if len(losses)>=14 else 0.5
        rs = avg_gain/avg_loss if avg_loss>0 else 50
        analysis["rsi"] = 100 - (100/(1+rs))
        # ATR
        trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1,len(closes))]
        analysis["atr"] = sum(trs[-14:])/14 if len(trs)>=14 else 0
        # Impulso (rate of change)
        analysis["impulso"] = (closes[-1]-closes[-5])/closes[-5]*100 if len(closes)>=5 else 0
        # Tendencia basica
        if analysis["rsi"] > 60: analysis["tendencia"] = "ALCISTA"
        elif analysis["rsi"] < 40: analysis["tendencia"] = "BAJISTA"
        else: analysis["tendencia"] = "LATERAL"
        return analysis

# === BINANCE CLIENT ===

class BinanceClient:
    def __init__(self):
        self.env = self._load_env()
        self.api_key = self.env.get("BINANCE_API_KEY", "")
        self.api_secret = self.env.get("BINANCE_API_SECRET", "")

    def _load_env(self):
        env = {};
        try:
            with open(ENV_PATH, "r") as f:
                for line in f:
                    line = line.strip();
                    if "=" in line:
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip()
        except: pass
        return env

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
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = RECV_WINDOW
            self._sign_request(params)
        try:
            r = requests.request(method, url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            return r.json()
        except Exception as e:
            logger.warning(f"Binance req error: {e}")
            return None

    def get_ticker(self):
        return self._request("GET", "/fapi/v1/ticker/price", {'symbol': SYMBOL})

    def get_klines(self, interval="5m", limit=50):
        return self._request("GET", "/fapi/v1/klines", {'symbol': SYMBOL, 'interval': interval, 'limit': limit})

    def get_account_balance(self):
        r = self._request("GET", "/fapi/v2/account", signed=True)
        if r:
            for a in r.get("assets", []):
                if a.get("asset") == "USDT":
                    return float(a.get("walletBalance", 0))
        return 0

    def get_position(self):
        r = self._request("GET", "/fapi/v2/positionRisk", {'symbol': SYMBOL}, signed=True)
        if r:
            for p in r:
                if float(p.get("positionAmt", 0)) != 0:
                    return {
                        "side": ("LONG" if float(p["positionAmt"]) > 0 else "SHORT"),
                        "entry_price": float(p.get("entryPrice", 0)),
                        "size": abs(float(p.get("positionAmt", 0))),
                        "pnl": float(p.get("unRealizedProfit", 0)),
                        "mark_price": float(p.get("markPrice", 0))
                    }
        return None

    def set_leverage(self):
        return self._request("POST", "/fapi/v1/leverage", {'symbol': SYMBOL, 'leverage': LEVERAGE}, signed=True)

    def place_market_order(self, side, quantity):
        params = {'symbol': SYMBOL, 'side': side, 'type': "MARKET", 'quantity': quantity}
        return self._request("POST", "/fapi/v1/order", params, signed=True)

    def close_position(self):
        pos = self.get_position()
        if pos:
            side = "SELL" if pos["side"] == "LONG" else "BUY"
            return self.place_market_order(side, round(pos["size"], 3))
        return None

    def get_order_book_spread(self):
        data = self._request("GET", "/fapi/v1/depth", {'symbol': SYMBOL, 'limit': 5})
        if data:
            bids = data.get("bids", []);
            asks = data.get("asks", []);
            if bids and asks:
                best_bid = float(bids[0][0]);
                best_ask = float(asks[0][0]);
                if best_bid > 0 and best_ask > 0:
                    return (best_ask - best_bid) / best_bid * 100
        return 0.0

# === ENGINE STATE MANAGER ===

class EngineState:
    def __init__(self):
        self.data = self._load()

    def _load(self):
        try:
            with open(STATE_FILE, "r") as f: return json.load(f)
        except: return {'trades': [], 'total_trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0, 'consecutive_losses': 0, 'max_consecutive_losses': 0}

    def save(self):
        try:
            with open(STATE_FILE, "w") as f: json.dump(self.data, f, indent=2)
        except: pass

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
            self.data['max_consecutive_losses'] = max(self.data.get('max_consecutive_losses', 0), self.data['consecutive_losses'])
        self.save()

    def get_win_rate(self):
        total = self.data.get('total_trades', 0)
        if total == 0: return 0
        return self.data.get('wins', 0) / total * 100

    def circuit_breaker_active(self):
        recent = [t for t in self.data.get('trades', []) if datetime.fromisoformat(t['time']) > datetime.now() - timedelta(hours=CIRCUIT_BREAKER_WINDOW_HOURS)]
        loss = sum(t['pnl'] for t in recent if t['pnl'] < 0)
        return abs(loss) >= CIRCUIT_BREAKER_MAX_LOSS

    def cooldown_active(self):
        losses = self.data.get('consecutive_losses', 0)
        if losses == 0: return False, 0
        last_trade = self.data.get('trades', [])
        if not last_trade: return False, 0
        last_time = datetime.fromisoformat(last_trade[-1]['time'])
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


# !!!APPEND_MARKER!!!
