#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# DEEPSEEK QUANT CORE v3.1
import os, sys, time, json, math, logging, requests, hmac, hashlib, re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import urlencode

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
SYMBOL = "BTCUSDT"
LEVERAGE = 30
CAPITAL_BASE = 6.0
BASE_FUTURES = "https://fapi.binance.com"
REQUEST_TIMEOUT = 8
RECV_WINDOW = 60000
CIRCUIT_BREAKER_MAX_LOSS = 0.50
CIRCUIT_BREAKER_WINDOW_HOURS = 4
MAX_SPREAD_PCT = 0.05
PROMPT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "estratega_sistema.md")
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deepseek_memory.json")
LOG_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deepseek_engine.log")
FEAR_GREED_API_URL = "https://api.alternative.me/fng/?limit=1"
MEMPOOL_API_URL = "https://mempool.space/api/v1/fees/recommended"
COINDESK_RSS_URL = "https://www.coindesk.com/arc/outboundfeeds/rss/"
PUELL_MULTIPLE_URL = "https://api.blockchain.info/charts/market-price?timespan=1year&format=json"

_root = logging.getLogger()
if _root.handlers:
    for _h in _root.handlers[:]: _root.removeHandler(_h)
logger = logging.getLogger("DeepSeek")
logger.setLevel(logging.INFO)
logger.propagate = False
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
log_path = LOG_FILE_PATH
fh = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
fh.setFormatter(formatter)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(formatter)
logger.addHandler(fh)
logger.addHandler(sh)

def calcular_rsi(closes, periodo=14):
    if len(closes) < periodo + 1: return 50.0
    g, p = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        g.append(max(d, 0))
        p.append(max(-d, 0))
    ag = sum(g[-periodo:]) / periodo
    ap = sum(p[-periodo:]) / periodo
    if ap == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + ag/ap))

def calcular_ema(datos, periodo):
    if len(datos) < periodo: return sum(datos)/len(datos) if datos else 0
    m = 2/(periodo+1)
    e = sum(datos[:periodo])/periodo
    for p in datos[periodo:]: e = (p-e)*m + e
    return e

def calcular_macd(closes):
    if len(closes) < 26: return None, None, None
    ml = calcular_ema(closes, 12) - calcular_ema(closes, 26)
    sig, hist = 0, 0
    diffs = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    if diffs:
        sig = calcular_ema(diffs, 9)
        hist = ml - sig
    return ml, sig, hist

def calcular_adx(highs, lows, closes, periodo=14):
    if len(highs) < periodo+1: return 20.0
    trs, pdm, mdm = [], [], []
    for i in range(1, len(highs)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
        up = highs[i]-highs[i-1]
        dn = lows[i-1]-lows[i]
        pdm.append(max(up, 0) if up > dn else 0)
        mdm.append(max(dn, 0) if dn > up else 0)
    atr = sum(trs[-periodo:])/periodo
    if atr <= 0: return 20.0
    pdi = 100*(sum(pdm[-periodo:])/periodo)/atr
    mdi = 100*(sum(mdm[-periodo:])/periodo)/atr
    dx = abs(pdi-mdi)/(pdi+mdi)*100 if (pdi+mdi)>0 else 0
    return dx

class ContextAugmenter:
    @staticmethod
    def get_fear_greed_index():
        try:
            r = requests.get(FEAR_GREED_API_URL, timeout=5)
            if r.status_code == 200:
                d = r.json()
                return {"value": int(d["data"][0]["value"]), "sentimiento": d["data"][0]["value_classification"]}
        except: pass
        return {"value": 50, "sentimiento": "Neutral"}

    @staticmethod
    def get_btc_fees():
        try:
            r = requests.get(MEMPOOL_API_URL, timeout=5)
            if r.status_code == 200:
                d = r.json()
                return {"fastest": d.get("fastestFee",0), "hour": d.get("hourFee",0), "minimum": d.get("minimumFee",0)}
        except: pass
        return {"fastest":0,"hour":0,"minimum":0}

    @staticmethod
    def get_long_short_ratio():
        try:
            r = requests.get(BASE_FUTURES + "/futures/data/globalLongShortAccountRatio?symbol=" + SYMBOL + "&period=1h", timeout=5)
            if r.status_code == 200:
                d = r.json()
                if d: return {"ratio": float(d[-1]["longShortRatio"]), "long_pct": float(d[-1]["longAccount"]), "short_pct": float(d[-1]["shortAccount"])}
        except: pass
        return {"ratio":1.0,"long_pct":50,"short_pct":50}

    @staticmethod
    def get_open_interest():
        try:
            r = requests.get(BASE_FUTURES + "/fapi/v1/openInterest?symbol=" + SYMBOL, timeout=5)
            if r.status_code == 200: return {"btc": float(r.json()["openInterest"])}
        except: pass
        return {"btc":0}

    @staticmethod
    def get_funding_rate():
        try:
            r = requests.get(BASE_FUTURES + "/fapi/v1/premiumIndex?symbol=" + SYMBOL, timeout=5)
            if r.status_code == 200:
                d = r.json()
                fr = float(d["lastFundingRate"])
                return {"funding_rate": fr*100, "funding_rate_str": "{:.4f}%".format(fr*100), "next_funding_time": d.get("nextFundingTime",0)}
        except: pass
        return {"funding_rate":0,"funding_rate_str":"0.0000%","next_funding_time":0}

    @staticmethod
    def get_top_liquidations():
        liqs = {"total_24h":0,"long_liq_24h":0,"short_liq_24h":0,"top_liquidations":[],
                "taker_buy_sell_ratio":1.0,"presion_compradora":"NEUTRAL","oi_change_5m_pct":0}
        try:
            r = requests.get(BASE_FUTURES + "/futures/data/takerlongshortRatio?symbol=" + SYMBOL + "&period=5m&limit=1", timeout=5)
            if r.status_code == 200:
                d = r.json()
                if d:
                    bsr = float(d[-1].get("buySellRatio",1.0))
                    liqs["taker_buy_sell_ratio"] = round(bsr,4)
                    liqs["taker_buy_vol"] = round(float(d[-1].get("buyVol",0)),2)
                    liqs["taker_sell_vol"] = round(float(d[-1].get("sellVol",0)),2)
                    liqs["presion_compradora"] = "ALTA" if bsr>1.2 else ("BAJA" if bsr<0.8 else "NEUTRAL")
                    liqs["taker_period"] = "5m"
        except: pass
        try:
            r = requests.get(BASE_FUTURES + "/futures/data/takerlongshortRatio?symbol=" + SYMBOL + "&period=1h&limit=1", timeout=5)
            if r.status_code == 200:
                d = r.json()
                if d:
                    liqs["taker_buy_sell_ratio_1h"] = round(float(d[-1].get("buySellRatio",1.0)),4)
        except: pass
        try:
            r = requests.get("https://fapi.coinglass.com/v2/futures/liquidation/list?symbol=BTC&type=0&limit=5", timeout=5)
            if r.status_code == 200:
                d = r.json()
                if d.get("code")=="00000" and d.get("data"):
                    l = d["data"][:5]
                    liqs["total_24h"] = sum(float(x.get("longLiquidation",0))+float(x.get("shortLiquidation",0)) for x in l)
                    liqs["long_liq_24h"] = sum(float(x.get("longLiquidation",0)) for x in l if x.get("type")==0)
                    liqs["short_liq_24h"] = sum(float(x.get("shortLiquidation",0)) for x in l if x.get("type")==1)
                    liqs["top_liquidations"] = [{"exchange":x.get("exchangeName",""),"amount_btc":float(x.get("longLiquidation",0))+float(x.get("shortLiquidation",0)),"price":float(x.get("price",0)),"type":"LONG" if x.get("type")==0 else "SHORT"} for x in l]
        except: pass
        try:
            r = requests.get(BASE_FUTURES + "/fapi/v1/openInterestHist?symbol=" + SYMBOL + "&period=5m&limit=2", timeout=5)
            if r.status_code == 200:
                d = r.json()
                if d and len(d)>=2:
                    oi_c = float(d[-1].get("sumOpenInterest",0))
                    oi_p = float(d[-2].get("sumOpenInterest",0))
                    if oi_p>0: liqs["oi_change_5m_pct"] = round((oi_c-oi_p)/oi_p*100,2)
        except: pass
        return liqs

    @staticmethod
    def get_crypto_news():
        try:
            r = requests.get(COINDESK_RSS_URL, timeout=8)
            if r.status_code == 200:
                root = ET.fromstring(r.content)
                items = []
                for item in root.findall(".//item")[:5]:
                    items.append({"titulo": item.findtext("title",""), "fuente": item.findtext("link",""), "fecha": item.findtext("pubDate","")})
                if items: return {"noticias_recientes": items, "resumen_noticias": " | ".join([i["titulo"][:80] for i in items])}
        except: pass
        return {"noticias_recientes":[],"resumen_noticias":"No disponibles"}

    @staticmethod
    def get_onchain_metrics():
        m = {}
        try:
            r = requests.get(PUELL_MULTIPLE_URL, timeout=5)
            if r.status_code == 200:
                v = r.json().get("values",[])
                if v:
                    p1y = [x["y"] for x in v[-365:]]
                    avg = sum(p1y)/len(p1y) if p1y else 0
                    cur = v[-1]["y"] if v else 0
                    m["precio_actual_onchain"] = round(cur,2)
                    m["precio_promedio_1y"] = round(avg,2)
                    m["relacion_precio_media_1y"] = round(cur/avg,2) if avg>0 else 1
        except: pass
        return m

    @classmethod
    def augment_context(cls, base_context):
        if base_context is None: return None
        fg = cls.get_fear_greed_index()
        fees = cls.get_btc_fees()
        ls = cls.get_long_short_ratio()
        oi = cls.get_open_interest()
        fr = cls.get_funding_rate()
        liqs = cls.get_top_liquidations()
        news = cls.get_crypto_news()
        onchain = cls.get_onchain_metrics()
        precio = base_context.get("mercado",{}).get("precio_actual",0)
        oi_usd = oi["btc"]*precio if precio>0 and oi["btc"]>0 else 0
        tk = ""
        if liqs.get("taker_buy_sell_ratio",1.0)!=1.0: tk = " | Taker B/S: {:.2f} ({})".format(liqs["taker_buy_sell_ratio"], liqs.get("presion_compradora","?"))
        oc = ""
        if liqs.get("oi_change_5m_pct",0)!=0: oc = " | OI 5m: {:.2f}%".format(liqs["oi_change_5m_pct"])
        analisis_fund = "F&G: {v}/100 ({c}) | L/S: {r:.2f} ({lp:.0f}%L, {sp:.0f}%S) | FR: {fr} | OI: {oi:.0f} BTC (${oiu:,.0f}) | Fees: {f} sat/vB | Liq: ${liq:,.0f}{tk}{oc} | Noticias: {n}".format(
            v=fg["value"],c=fg["sentimiento"],r=ls["ratio"],lp=ls["long_pct"],sp=ls["short_pct"],
            fr=fr["funding_rate_str"],oi=oi["btc"],oiu=oi_usd,f=fees["fastest"],
            liq=liqs["total_24h"],tk=tk,oc=oc,n=news["resumen_noticias"][:120])
        base_context["fundamental"] = {"fear_greed":fg,"bitcoin_fees":fees,"long_short_ratio":ls,"open_interest":oi,"funding_rate":fr,"liquidaciones":liqs,"noticias":news,"onchain":onchain,"analisis":analisis_fund}
        logger.info("Web: F&G=%s L/S=%.2f FR=%s OI=%.0f Taker=%.2f(%s) Liq=%.0f OI5m=%s%% News=%d",
                   fg["value"],ls["ratio"],fr["funding_rate_str"],oi["btc"],liqs.get("taker_buy_sell_ratio",1.0),liqs.get("presion_compradora","?"),liqs["total_24h"],liqs.get("oi_change_5m_pct",0),len(news["noticias_recientes"]))
        return base_context

MOCK_TRADING_MODE = True

class BinanceClient:
    def __init__(self):
        self.env = self._load_env()
        self.api_key = self.env.get("BINANCE_API_KEY","")
        self.api_secret = self.env.get("BINANCE_API_SECRET","")
        self.time_offset = 0
        self.mock_position = None
        self.mock_balance = CAPITAL_BASE
        try: self.sync_time()
        except: pass
    def _load_env(self):
        env = {}
        try:
            with open(ENV_PATH,"r") as f:
                for line in f:
                    l = line.strip()
                    if "=" in l and not l.startswith("#"):
                        k,v = l.split("=",1)
                        env[k.strip()] = v.strip()
        except: pass
        return env
    def sync_time(self):
        try:
            r = requests.get(BASE_FUTURES+"/fapi/v1/time", timeout=5)
            self.time_offset = int(r.json()["serverTime"]) - int(time.time()*1000)
            logger.info("Binance offset: %dms", self.time_offset)
        except Exception as e: logger.warning("Time sync: %s", e)
    def _sign(self, p):
        q = urlencode(p)
        p["signature"] = hmac.new(self.api_secret.encode("utf-8"), q.encode("utf-8"), hashlib.sha256).hexdigest()
        return p
    def _request(self, method, endpoint, params=None, signed=False):
        url = BASE_FUTURES + endpoint
        headers = {"X-MBX-APIKEY": self.api_key} if self.api_key else {}
        if signed:
            params = params or {}
            params["timestamp"] = int(time.time()*1000) + int(self.time_offset)
            params["recvWindow"] = RECV_WINDOW
            self._sign(params)
        try:
            r = requests.request(method, url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            return r.json() if r.text else None
        except Exception as e:
            logger.warning("Binance err: %s", e)
            return None
    def get_ticker(self): return self._request("GET","/fapi/v1/ticker/price",{"symbol":SYMBOL})
    def get_klines(self, interval="5m", limit=50): return self._request("GET","/fapi/v1/klines",{"symbol":SYMBOL,"interval":interval,"limit":limit})
    def get_account_balance(self):
        if MOCK_TRADING_MODE: return self.mock_balance
        r = self._request("GET","/fapi/v2/account", signed=True)
        if r and isinstance(r,dict):
            if "code" in r: return 0.0
            bal = float(r.get("totalWalletBalance",0))
            if bal>0: return bal
            for a in r.get("assets",[]):
                if a.get("asset","") in ("USDT","U"): return float(a.get("walletBalance",0))
        return 0.0
    def get_position(self):
        if MOCK_TRADING_MODE:
            if not self.mock_position: return None
            t = self.get_ticker()
            if t:
                pr = float(t.get("price", 0))
                if pr > 0:
                    self.mock_position["mark_price"] = pr
                    if self.mock_position["side"] == "LONG":
                        self.mock_position["pnl"] = (pr - self.mock_position["entry_price"]) * self.mock_position["size"]
                    else:
                        self.mock_position["pnl"] = (self.mock_position["entry_price"] - pr) * self.mock_position["size"]
            return self.mock_position
        r = self._request("GET","/fapi/v2/positionRisk",{"symbol":SYMBOL}, signed=True)
        if r and isinstance(r,list):
            for p in r:
                try:
                    amt = float(p.get("positionAmt",0))
                    if amt!=0: return {"side":"LONG" if amt>0 else "SHORT","entry_price":float(p.get("entryPrice",0)),"size":abs(amt),"pnl":float(p.get("unRealizedProfit",0)),"mark_price":float(p.get("markPrice",0))}
                except: continue
        elif r and isinstance(r,dict) and "code" in r: logger.warning("Pos err: %s", r.get("msg"))
        return None
    def set_leverage(self): return self._request("POST","/fapi/v1/leverage",{"symbol":SYMBOL,"leverage":LEVERAGE}, signed=True)
    def place_market_order(self, side, qty):
        if MOCK_TRADING_MODE:
            t = self.get_ticker()
            pr = float(t.get("price", 0)) if t else 0.0
            self.mock_position = {
                "side": "LONG" if side == "BUY" else "SHORT",
                "entry_price": pr,
                "size": qty,
                "pnl": 0.0,
                "mark_price": pr
            }
            logger.info("MOCK_ORDER_PLACED: %s %f @ %f", side, qty, pr)
            return {"orderId": "MOCK_" + str(int(time.time())), "status": "FILLED"}
        return self._request("POST","/fapi/v1/order",{"symbol":SYMBOL,"side":side,"type":"MARKET","quantity":qty}, signed=True)
    def close_position(self):
        if MOCK_TRADING_MODE:
            if self.mock_position:
                pnl = self.mock_position.get("pnl", 0)
                self.mock_balance += pnl
                self.mock_position = None
                logger.info("MOCK_POSITION_CLOSED. New mock balance: %f", self.mock_balance)
                return {"orderId": "MOCK_CLOSE_" + str(int(time.time())), "status": "FILLED"}
            return None
        pos = self.get_position()
        if pos: return self.place_market_order("SELL" if pos["side"]=="LONG" else "BUY", round(pos["size"],3))
        return None
    def get_order_book_spread(self):
        d = self._request("GET","/fapi/v1/depth",{"symbol":SYMBOL,"limit":5})
        if d and isinstance(d,dict):
            b, a = d.get("bids",[]), d.get("asks",[])
            if b and a:
                bb, ba = float(b[0][0]), float(a[0][0])
                if bb>0: return (ba-bb)/bb*100
        return 0.0

class MarketContextCollector:
    def __init__(self): self.binance = BinanceClient()
    def collect(self):
        ctx = {}
        try:
            t = self.binance.get_ticker()
            if t: ctx["precio"] = float(t["price"])
            k = self.binance.get_klines()
            if k and len(k)>30:
                c = [float(x[4]) for x in k]
                h = [float(x[2]) for x in k]
                l = [float(x[3]) for x in k]
                v = [float(x[5]) for x in k]
                precio = c[-1]
                rsi = calcular_rsi(c)
                ml, ms, mh = calcular_macd(c)
                adx = calcular_adx(h,l,c)
                e5 = calcular_ema(c,5)
                e20 = calcular_ema(c,20)
                mx20 = max(h[-20:])
                mn20 = min(l[-20:])
                vp20 = sum(v[-20:])/20
                va5 = sum(v[-5:])
                rv = va5/(vp20*5) if vp20>0 else 1.0
                trs = [max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(h))]
                atr = sum(trs[-14:])/14 if len(trs)>=14 else (sum(trs)/len(trs) if trs else 0)
                i3 = (c[-1]-c[-4])/c[-4]*100 if len(c)>=4 else 0
                i5 = (c[-1]-c[-6])/c[-6]*100 if len(c)>=6 else 0
                sp = self.binance.get_order_book_spread()
                td = "ALCISTA" if rsi>60 and e5>e20 and precio>e5 else ("BAJISTA" if rsi<40 and e5<e20 and precio<e5 else "LATERAL")
                rg = "TENDENCIA" if adx>=25 else ("TENDENCIA_DEBIL" if adx>=20 else "RANGO")
                sen = []
                if rsi<30: sen.append("SOBREVENDIDO")
                elif rsi>70: sen.append("SOBRECOMPRADO")
                if mh is not None and mh>0: sen.append("MACD_POSITIVO")
                elif mh is not None and mh<0: sen.append("MACD_NEGATIVO")
                if rv>1.5: sen.append("VOLUMEN_ALTO")
                if precio<=mn20*1.002: sen.append("CERCA_SOPORTE_20")
                if precio>=mx20*0.998: sen.append("CERCA_RESISTENCIA_20")
                ctx.update({"precio":precio,"rsi":round(rsi,1),"macd_line":round(ml,2) if ml else 0,"macd_signal":round(ms,2) if ms else 0,"macd_hist":round(mh,2) if mh else 0,"ema5":round(e5,2),"ema20":round(e20,2),"atr":round(atr,2),"adx":round(adx,1),"impulso_3":round(i3,2),"impulso_5":round(i5,2),"max_20":mx20,"min_20":mn20,"vol_5":round(va5,2),"vol_prom_20":round(vp20,2),"ratio_vol":round(rv,2),"spread":sp,"tendencia":td,"regimen":rg,"senales":sen,"dist_ema5":round((precio-e5)/e5*100,2) if e5>0 else 0,"dist_ema20":round((precio-e20)/e20*100,2) if e20>0 else 0})
        except Exception as e: logger.warning("Collect: %s", e)
        return ctx

class EngineState:
    def __init__(self, state_file):
        self.state_file = state_file
        self.data = self._load()
    def _load(self):
        try:
            with open(self.state_file,"r") as f: return json.load(f)
        except: return {"trades":[],"total_trades":0,"wins":0,"losses":0,"pnl":0.0,"consecutive_losses":0,"max_consecutive_losses":0}
    def save(self):
        try:
            with open(self.state_file,"w") as f: json.dump(self.data,f,indent=2)
        except: pass
    def add_trade(self, side, entry, exit_p, pnl, meta=None):
        t = {"side":side,"entry":entry,"exit":exit_p,"pnl":pnl,"time":datetime.now().isoformat(),"meta":meta or {}}
        self.data["trades"].append(t)
        self.data["total_trades"]+=1
        self.data["pnl"]=self.data.get("pnl",0)+pnl
        if pnl>0:
            self.data["wins"]=self.data.get("wins",0)+1
            self.data["consecutive_losses"]=0
        else:
            self.data["losses"]=self.data.get("losses",0)+1
            self.data["consecutive_losses"]=self.data.get("consecutive_losses",0)+1
            self.data["max_consecutive_losses"]=max(self.data.get("max_consecutive_losses",0),self.data["consecutive_losses"])
        self.save()
    def get_win_rate(self): t=self.data.get("total_trades",0); return self.data.get("wins",0)/t*100 if t>0 else 0
    def circuit_breaker_active(self):
        ct = datetime.now()-timedelta(hours=CIRCUIT_BREAKER_WINDOW_HOURS)
        r = [x for x in self.data.get("trades",[]) if datetime.fromisoformat(x["time"])>ct]
        return abs(sum(x["pnl"] for x in r if x["pnl"]<0))>=CIRCUIT_BREAKER_MAX_LOSS
    def cooldown_remaining(self):
        l = self.data.get("consecutive_losses",0)
        if l==0: return 0
        t = self.data.get("trades",[])
        if not t: return 0
        lt = datetime.fromisoformat(t[-1]["time"])
        cd = 2**(l-1)*60
        return max(0,int(cd-(datetime.now()-lt).total_seconds()))
    def get_summary(self):
        return {"total_trades":self.data.get("total_trades",0),"win_rate":round(self.get_win_rate(),1),"consecutive_losses":self.data.get("consecutive_losses",0),"pnl":round(self.data.get("pnl",0),2),"cb_active":self.circuit_breaker_active(),"cooldown_remaining":self.cooldown_remaining()}

class EngineMemory:
    def __init__(self, memory_file):
        self.memory_file = memory_file
        self.data = self._load()
    def _load(self):
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {"events": []}
    def save(self):
        try:
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except:
            pass
    def add_event(self, event_type, message, metadata=None):
        e = {"time": datetime.now().isoformat(), "type": event_type, "message": message, "metadata": metadata or {}}
        self.data.setdefault("events", []).append(e)
        self.save()
    def get_recent_events(self, limit=50):
        events = self.data.get("events", [])
        return events[-limit:]
    def get_memory_snapshot(self, limit=50):
        return {"events": self.get_recent_events(limit), "total_events": len(self.data.get("events", []))}
