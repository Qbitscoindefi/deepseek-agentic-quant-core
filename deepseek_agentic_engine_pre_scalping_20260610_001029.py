#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# OPENBRIDGE DEEPSEEK TRADING ENGINE v3.1

import os, sys, time, json, logging, requests, re
from datetime import datetime
from deepseek_quant_core import (
    BinanceClient, MarketContextCollector, ContextAugmenter,
    EngineState, EngineMemory, logger, ENV_PATH, SYMBOL, LEVERAGE, CAPITAL_BASE,
    MAX_SPREAD_PCT, PROMPT_FILE, MEMORY_FILE, LOG_FILE_PATH
)

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_TEMPERATURE = 0.45
DEEPSEEK_TIMEOUT = 5
ACTION_DUPLICATE_COOLDOWN = 5
MIN_CONFIDENCE = 0.0
SPREAD_THRESHOLD_PCT = MAX_SPREAD_PCT
TRADE_MODE = "SCALPING"
MAX_POSITION_DURATION_SEC = 90
SCALPING_MAX_POSITION_DURATION_SEC = 90
ATR_VOLATILITY_DIVISOR = 80.0
MAX_TRADE_NOTIONAL_RATIO = 0.08
SCALPING_MAX_TRADE_NOTIONAL_RATIO = 0.08
RISK_PER_TRADE_PCT = 0.012
SCALPING_RISK_PER_TRADE_PCT = 0.012
TAKER_FEE_PCT = 0.05
SCALPING_EXIT_ATR_MULTIPLIER = 1.2
MIN_SCALP_ADX = 14.0
MIN_SCALP_VOLUME_RATIO = 1.08
MIN_SCALP_IMPULSO_PCT = 0.25
MIN_SCALP_MACD_HIST = 0.08
MIN_SCALP_CONFIDENCE = 0.45
BLACK_SWAN_FG_EXTREME = 12
BLACK_SWAN_OI_CHANGE_PCT = 3.0
BLACK_SWAN_TAKER_RATIO = 1.4
BLACK_SWAN_FUNDING_RATE = 0.12
BLACK_SWAN_ONCHAIN_EXTREME = 1.6
BLACK_SWAN_ALLOW_CONFIDENCE = 0.65
BLACK_SWAN_ALLOW_ADX = 10.0
BLACK_SWAN_ALLOW_VOLUME_RATIO = 1.1
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine_state.json")


class DeepSeekClient:
    def __init__(self):
        self.api_key = self._load_api_key()
        self.prompt_system = self._load_prompt()
        if self.api_key:
            logger.info("DeepSeek OK | prompt: %d chars", len(self.prompt_system))

    def _load_api_key(self):
        try:
            with open(ENV_PATH, "r") as f:
                for line in f:
                    if line.startswith("DEEPSEEK_API_KEY="):
                        return line.strip().split("=", 1)[1]
        except:
            return ""

    def _load_prompt(self):
        try:
            if os.path.exists(PROMPT_FILE):
                with open(PROMPT_FILE, "r", encoding="utf-8") as f:
                    return f.read()
        except:
            pass
        return "Eres un trader. Responde JSON."

    def consultar(self, contexto):
        try:
            if not self.api_key:
                return {"accion": "NEUTRAL", "confianza": 0.0, "razon": "MOCK", "explicacion": "", "senal_tecnica": "MOCK", "riesgo": "BAJO", "ajustes": {}}
            headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
            payload = {
                "model": DEEPSEEK_MODEL,
                "temperature": DEEPSEEK_TEMPERATURE,
                "max_tokens": 500,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": self.prompt_system},
                    {"role": "user", "content": json.dumps(contexto, indent=2)}
                ]
            }
            r = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=DEEPSEEK_TIMEOUT)
            if r.status_code == 200:
                body = r.json()
                choice = (body.get("choices") or [{}])[0]
                text = choice.get("message", {}).get("content", "") if isinstance(choice, dict) else ""
                text = text.replace("```json", "").replace("```", "").strip()
                if not text:
                    logger.warning("DeepSeek empty response: %s", body)
                    raise ValueError("Empty DeepSeek response")
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    import re
                    match = re.search(r"\{.*\}", text, re.S)
                    if match:
                        return json.loads(match.group(0))
                    logger.warning("DeepSeek parse fail: %s", text[:200])
                    raise
            else:
                logger.warning("DeepSeek HTTP %s %s", r.status_code, getattr(r, 'text', ''))
        except requests.Timeout:
            logger.warning("DeepSeek TIMEOUT (%ds)", DEEPSEEK_TIMEOUT)
        except Exception as e:
            logger.warning("DeepSeek: %s", e)
        return {"accion": "NEUTRAL", "confianza": 0.0, "razon": "ERROR", "explicacion": "", "senal_tecnica": "NO_DISPONIBLE", "riesgo": "MEDIO", "ajustes": {}}


class DeepSeekTradingEngine:
    def __init__(self):
        self.running = False
        self.binance = BinanceClient()
        self.collector = MarketContextCollector()
        self.augmenter = ContextAugmenter()
        self.deepseek = DeepSeekClient()
        self.state = EngineState(STATE_FILE)
        self.memory = EngineMemory(MEMORY_FILE)
        self.position = None
        self.cycle_count = 0
        self.last_decision = {"accion": None, "timestamp": 0.0}
        self.last_execution_metrics = None

    def _normalize_decision(self, decision):
        if not isinstance(decision, dict):
            return {"accion": "NEUTRAL", "confianza": 0.0, "razon": "INVALID_DECISION", "explicacion": "", "senal_tecnica": "NO_DISPONIBLE", "riesgo": "MEDIO", "ajustes": {}}
        accion = str(decision.get("accion", "NEUTRAL")).upper() if decision.get("accion") else "NEUTRAL"
        if accion == "CLOSE":
            accion = "CERRAR"
        if accion not in ("LONG", "SHORT", "CERRAR", "NEUTRAL", "ABRIR_LONG", "ABRIR_SHORT"):
            accion = "NEUTRAL"
        try:
            confianza = float(decision.get("confianza", 0.0))
        except (TypeError, ValueError):
            confianza = 0.0
        confianza = max(0.0, min(1.0, confianza))
        riesgo = str(decision.get("riesgo", "MEDIO")).upper() if decision.get("riesgo") else "MEDIO"
        if riesgo not in ("BAJO", "MEDIO", "ALTO"):
            riesgo = "MEDIO"
        senal_tecnica = str(decision.get("senal_tecnica", "NO_DISPONIBLE")).strip() if decision.get("senal_tecnica") else "NO_DISPONIBLE"
        razon = str(decision.get("razon", "EVALUANDO")).upper().replace(" ", "_")[:60] or "EVALUANDO"
        explicacion = str(decision.get("explicacion", ""))[:400]
        ajustes = decision.get("ajustes", {})
        if not isinstance(ajustes, dict):
            ajustes = {}
        return {
            "accion": accion,
            "confianza": confianza,
            "razon": razon,
            "explicacion": explicacion,
            "senal_tecnica": senal_tecnica,
            "riesgo": riesgo,
            "ajustes": ajustes
        }

    def _position_trend_supported(self, side, context):
        tendencia = str(context.get("senal_tecnica", {}).get("tendencia", "")).upper()
        regimen = str(context.get("senal_tecnica", {}).get("regimen", "")).upper()
        rsi = context.get("senal_tecnica", {}).get("rsi", 50)
        if side == "LONG":
            if tendencia == "ALCISTA" and regimen in ("TENDENCIA", "TENDENCIA_DEBIL"):
                return True
            return rsi >= 55
        if side == "SHORT":
            if tendencia == "BAJISTA" and regimen in ("TENDENCIA", "TENDENCIA_DEBIL"):
                return True
            return rsi <= 45
        return False

    def _market_ready_for_scalp(self, context, decision):
        senal = context.get("senal_tecnica", {})
        adx = float(senal.get("adx", 0) or 0)
        vol_ratio = float(senal.get("ratio_volumen", 1.0) or 1.0)
        impulso_3 = abs(float(senal.get("impulso_3velas_pct", 0) or 0))
        impulso_5 = abs(float(senal.get("impulso_5velas_pct", 0) or 0))
        macd_hist = abs(float(senal.get("macd_histograma", 0) or 0))
        precio = float(context.get("mercado", {}).get("precio_actual", 0) or 0)
        atr = float(senal.get("atr", 0) or 0)

        has_impulse = impulso_3 >= MIN_SCALP_IMPULSO_PCT or impulso_5 >= MIN_SCALP_IMPULSO_PCT or macd_hist >= MIN_SCALP_MACD_HIST or (precio > 0 and atr >= precio * 0.0015)
        has_volatility = adx >= MIN_SCALP_ADX or vol_ratio >= MIN_SCALP_VOLUME_RATIO
        strong_decision = float(decision.get("confianza", 0) or 0) >= 0.7
        black_swan = bool(context.get("fundamental", {}).get("black_swan", {}).get("alertas"))

        if has_impulse and has_volatility:
            return True, None
        if strong_decision and (has_impulse or has_volatility):
            return True, None
        if black_swan and strong_decision and (has_impulse or adx >= BLACK_SWAN_ALLOW_ADX or vol_ratio >= BLACK_SWAN_ALLOW_VOLUME_RATIO):
            return True, "Escenario cisne negro con impulso o volumen"
        if black_swan and has_impulse and (adx >= 10 or vol_ratio >= 1.1):
            return True, "Cisne negro con impulso y volumen"
        if not has_impulse:
            return False, "Impulso insuficiente"
        return False, "Volatilidad/volumen insuficiente"

    def _black_swan_entry_allowed(self, accion, context, decision):
        if accion not in ("LONG", "ABRIR_LONG", "SHORT", "ABRIR_SHORT"):
            return False, "Acción no operativa"
        if float(decision.get("confianza", 0.0) or 0) < BLACK_SWAN_ALLOW_CONFIDENCE:
            return False, "Confianza insuficiente para cisne negro"
        senal = context.get("senal_tecnica", {})
        adx = float(senal.get("adx", 0) or 0)
        vol_ratio = float(senal.get("ratio_volumen", 1.0) or 1.0)
        if adx >= BLACK_SWAN_ALLOW_ADX or vol_ratio >= BLACK_SWAN_ALLOW_VOLUME_RATIO:
            return True, None
        return False, "ADX/volumen insuficiente para ingresar en cisne negro"

    def _match_black_swan_news(self, text):
        if not text:
            return False
        return bool(re.search(r"\b(black swan|cisne negro|crash|colapso|hack|hackeo|regulaci[oó]n|ban|prohibici[oó]n|quiebra|bankrupt|default|deuda|emergencia|crisis|p[aá]nico|panico|caida|caída|miedo|accidente|cierre)\b", text, re.I))

    def _detect_black_swan(self, context):
        fund = context.get("fundamental", {})
        noticias = fund.get("noticias", {}).get("resumen_noticias", "")
        fg = int(fund.get("fear_greed", {}).get("value", 50))
        oi_change = float(fund.get("liquidaciones", {}).get("oi_change_5m_pct", 0) or 0)
        bsr = float(fund.get("liquidaciones", {}).get("taker_buy_sell_ratio", 1.0) or 1.0)
        fr = float(fund.get("funding_rate", {}).get("funding_rate", 0) or 0)
        onchain_ratio = float(fund.get("onchain", {}).get("relacion_precio_media_1y", 1) or 1)
        alertas = []
        if fg <= BLACK_SWAN_FG_EXTREME or fg >= 100 - BLACK_SWAN_FG_EXTREME:
            alertas.append("F&G_EXTREMO")
        if abs(oi_change) >= BLACK_SWAN_OI_CHANGE_PCT:
            alertas.append("OI_SPIKE")
        if bsr <= 1.0 / BLACK_SWAN_TAKER_RATIO or bsr >= BLACK_SWAN_TAKER_RATIO:
            alertas.append("TAKER_EXTREMO")
        if abs(fr) >= BLACK_SWAN_FUNDING_RATE:
            alertas.append("FUNDING_EXTREMO")
        if onchain_ratio >= BLACK_SWAN_ONCHAIN_EXTREME or onchain_ratio <= 1.0 / BLACK_SWAN_ONCHAIN_EXTREME:
            alertas.append("ONCHAIN_EXTREMO")
        if self._match_black_swan_news(noticias):
            alertas.append("NOTICIAS_CRITICAS")
        return {
            "alertas": alertas,
            "fear_greed": fg,
            "oi_change_5m_pct": oi_change,
            "taker_bs": bsr,
            "funding_rate": fr,
            "onchain_ratio": onchain_ratio,
            "noticias": noticias
        }

    def _calculate_trade_metrics(self, precio, atr, spread_pct, confianza, risk_factor, volatility_factor):
        risk_amount = CAPITAL_BASE * SCALPING_RISK_PER_TRADE_PCT
        stop_distance = max(atr * SCALPING_EXIT_ATR_MULTIPLIER, precio * 0.0006, 1e-6)
        size_by_risk = risk_amount / stop_distance
        target_notional = CAPITAL_BASE * LEVERAGE * risk_factor * volatility_factor
        max_notional = CAPITAL_BASE * LEVERAGE * SCALPING_MAX_TRADE_NOTIONAL_RATIO
        notional = min(target_notional, max_notional)
        size = round(min(size_by_risk, notional / max(precio, 1e-8)), 3)
        if size < 0.001:
            size = 0.001
        fee_cost = precio * size * (TAKER_FEE_PCT / 100) * 2
        slippage_cost = precio * size * min(spread_pct, 1.0) / 100
        total_cost = fee_cost + slippage_cost
        expected_return_pct = min(0.12, max(0.03, confianza * 0.09 + 0.03))
        expected_profit = precio * size * expected_return_pct
        expected_net_gain = expected_profit - total_cost
        return {
            "risk_amount": round(risk_amount, 6),
            "stop_distance": round(stop_distance, 6),
            "size": size,
            "notional": round(precio * size, 6),
            "fee_cost": round(fee_cost, 6),
            "slippage_cost": round(slippage_cost, 6),
            "total_cost": round(total_cost, 6),
            "expected_return_pct": round(expected_return_pct * 100, 2),
            "expected_profit": round(expected_profit, 6),
            "expected_net_gain": round(expected_net_gain, 6),
            "profit_to_cost": round(expected_profit / max(total_cost, 1e-8), 2)
        }

    def _read_log_tail(self, max_lines=80):
        try:
            with open(LOG_FILE_PATH, "rb") as f:
                f.seek(0, 2)
                filesize = f.tell()
                block_size = 1024
                data = b""
                while filesize > 0 and data.count(b"\n") <= max_lines:
                    read_size = min(block_size, filesize)
                    f.seek(filesize - read_size)
                    chunk = f.read(read_size)
                    data = chunk + data
                    filesize -= read_size
                text = data.decode("utf-8", errors="replace")
                lines = text.splitlines()
                return "\n".join(lines[-max_lines:])
        except Exception:
            return ""

    def build_context(self):
        ctx = self.collector.collect()
        if not ctx or not ctx.get("precio"):
            return None
        pos = self.binance.get_position()
        bal = self.binance.get_account_balance()
        summary = self.state.get_summary()
        contexto = {
            "timestamp": datetime.now().isoformat(),
            "symbol": SYMBOL,
            "senal_tecnica": {
                "tendencia": ctx.get("tendencia", "?"),
                "regimen": ctx.get("regimen", "?"),
                "rsi": ctx.get("rsi", 50),
                "atr": ctx.get("atr", 0),
                "adx": ctx.get("adx", 0),
                "macd_histograma": ctx.get("macd_hist", 0),
                "impulso_3velas_pct": ctx.get("impulso_3", 0),
                "impulso_5velas_pct": ctx.get("impulso_5", 0),
                "ratio_volumen": ctx.get("ratio_vol", 1.0),
                "senales": ctx.get("senales", [])
            },
            "mercado": {
                "precio_actual": ctx.get("precio", 0),
                "max_20_periodos": ctx.get("max_20", 0),
                "min_20_periodos": ctx.get("min_20", 0),
                "ema_rapida_5": ctx.get("ema5", 0),
                "ema_lenta_20": ctx.get("ema20", 0),
                "distancia_ema5_pct": ctx.get("dist_ema5", 0),
                "distancia_ema20_pct": ctx.get("dist_ema20", 0),
                "spread_pct": ctx.get("spread", 0)
            },
            "posicion_actual": pos if pos else None,
            "sesion": {
                "balance_usdt": bal,
                "capital_efectivo": bal * LEVERAGE,
                "trades_totales": summary["total_trades"],
                "win_rate_pct": summary["win_rate"],
                "racha_perdidas": summary["consecutive_losses"],
                "pnl_acumulado": summary["pnl"],
                "circuit_breaker_activo": summary["cb_active"],
                "cooldown_restante_seg": summary["cooldown_remaining"]
            }
        }
        contexto = self.augmenter.augment_context(contexto)
        if contexto and "fundamental" in contexto:
            contexto["fundamental"]["black_swan"] = self._detect_black_swan(contexto)
        if contexto is not None:
            contexto["memoria"] = self.memory.get_memory_snapshot(limit=40)
            contexto["logs"] = {"tail": self._read_log_tail(80)}
        return contexto

    def process_decision(self, decision, context):
        decision = self._normalize_decision(decision)
        accion = decision.get("accion", "NEUTRAL")
        confianza = decision.get("confianza", 0.0)
        now = time.time()
        if self.last_decision.get("accion") == accion and (now - self.last_decision.get("timestamp", 0)) < ACTION_DUPLICATE_COOLDOWN:
            self.last_decision["timestamp"] = now
            return
        self.last_decision = {"accion": accion, "timestamp": now}
        if not context or "mercado" not in context:
            return
        precio = context["mercado"].get("precio_actual", 0)
        spread_pct = context["mercado"].get("spread_pct", 0)
        if precio <= 0:
            return
        black_swan = context.get("fundamental", {}).get("black_swan", {})
        if black_swan.get("alertas"):
            logger.warning("ALERTA CISNE NEGRO detectada: %s", ", ".join(black_swan["alertas"]))
            if self.position:
                if not self._position_trend_supported(self.position["side"], context):
                    logger.info(">>> GUARDIA DE RIESGO: cierra posicion activa por alerta cisne negro")
                    r = self.binance.close_position()
                else:
                    logger.info(">>> Mantiene posicion durante evento cisne negro, tendencia aun soporta")
            else:
                allow, reason = self._black_swan_entry_allowed(accion, context, decision)
                if not allow:
                    logger.info(">>> No se abre posicion en cisne negro: %s", reason)
                    return
                logger.info(">>> Cisne negro detectado pero la dirección es válida, permite apertura con cuidado")

        if accion in ("LONG", "ABRIR_LONG", "SHORT", "ABRIR_SHORT"):
            market_ready, reason = self._market_ready_for_scalp(context, decision)
            if not market_ready:
                logger.info(">>> No abre scalping: %s | ADX=%.1f vol=%.2f imp3=%.2f imp5=%.2f macd=%.2f conf=%.2f", reason, context.get("senal_tecnica", {}).get("adx", 0), context.get("senal_tecnica", {}).get("ratio_volumen", 0), context.get("senal_tecnica", {}).get("impulso_3velas_pct", 0), context.get("senal_tecnica", {}).get("impulso_5velas_pct", 0), abs(context.get("senal_tecnica", {}).get("macd_histograma", 0)), confianza)
                return
            if confianza < MIN_CONFIDENCE:
                logger.info("Advertencia: confianza baja %.2f, pero permitiendo ejecución a pedido del agente", confianza)
            if spread_pct > SPREAD_THRESHOLD_PCT:
                logger.info("Advertencia: spread alto %.3f%%, se mantiene apertura según el agente", spread_pct)
            if self.state.circuit_breaker_active():
                logger.info("Circuit breaker activo, no se abre nueva posicion")
                return
            cd = self.state.cooldown_remaining()
            if cd > 0:
                logger.info("Cooldown activo %ds, no se abre nueva posicion", cd)
                return

        atr = context.get("senal_tecnica", {}).get("atr", 0)
        volatility_factor = 1.0
        if atr > 0:
            volatility_factor = max(0.35, min(1.0, ATR_VOLATILITY_DIVISOR / atr))

        risk_factor = max(0.35, min(1.0, 0.5 + 0.5 * confianza))

        if accion in ("LONG", "ABRIR_LONG"):
            if self.position: return
            if self.state.circuit_breaker_active(): return
            if self.state.cooldown_remaining() > 0: return
            atr = context.get("senal_tecnica", {}).get("atr", 0)
            metrics = self._calculate_trade_metrics(precio, atr, spread_pct, confianza, risk_factor, volatility_factor)
            logger.info(">>> EJECUTANDO LONG | conf: %.0f%% | factor: %.2f | vol: %.2f | size: %.3f | cost≈%.4f | exp_net≈%.4f | profit/cost=%.2f", confianza * 100, risk_factor, volatility_factor, metrics["size"], metrics["total_cost"], metrics["expected_net_gain"], metrics["profit_to_cost"])
            r = self.binance.place_market_order("BUY", metrics["size"])
            if r and isinstance(r, dict) and r.get("orderId"):
                self.position = {"side": "LONG", "size": metrics["size"], "entry_time": now, "entry_price": precio, "entry_mark_price": precio}
                self.last_execution_metrics = {"side": "LONG", "risk_factor": risk_factor, "volatility_factor": volatility_factor, "confidence": confianza, "spread_pct": spread_pct, **metrics}
                self.memory.add_event("trade_opened", "LONG abierta", {"price": precio, "size": metrics["size"], "cost": metrics["total_cost"], "expected_net_gain": metrics["expected_net_gain"]})
                logger.info(">>> LONG @ %.2f", precio)
            else:
                logger.warning("Orden LONG fallida: %s", r)

        elif accion in ("SHORT", "ABRIR_SHORT"):
            if self.position: return
            if self.state.circuit_breaker_active(): return
            if self.state.cooldown_remaining() > 0: return
            atr = context.get("senal_tecnica", {}).get("atr", 0)
            metrics = self._calculate_trade_metrics(precio, atr, spread_pct, confianza, risk_factor, volatility_factor)
            logger.info(">>> EJECUTANDO SHORT | conf: %.0f%% | factor: %.2f | vol: %.2f | size: %.3f | cost≈%.4f | exp_net≈%.4f | profit/cost=%.2f", confianza * 100, risk_factor, volatility_factor, metrics["size"], metrics["total_cost"], metrics["expected_net_gain"], metrics["profit_to_cost"])
            r = self.binance.place_market_order("SELL", metrics["size"])
            if r and isinstance(r, dict) and r.get("orderId"):
                self.position = {"side": "SHORT", "size": metrics["size"], "entry_time": now, "entry_price": precio, "entry_mark_price": precio}
                self.last_execution_metrics = {"side": "SHORT", "risk_factor": risk_factor, "volatility_factor": volatility_factor, "confidence": confianza, "spread_pct": spread_pct, **metrics}
                self.memory.add_event("trade_opened", "SHORT abierta", {"price": precio, "size": metrics["size"], "cost": metrics["total_cost"], "expected_net_gain": metrics["expected_net_gain"]})
                logger.info(">>> SHORT @ %.2f", precio)
            else:
                logger.warning("Orden SHORT fallida: %s", r)

        elif accion == "CERRAR":
            if not self.position: return
            logger.info(">>> CERRANDO %s", self.position["side"])
            r = self.binance.close_position()
            if r:
                pnl = (precio - self.position["entry_price"]) * self.position["size"]
                if self.position["side"] == "SHORT":
                    pnl = (self.position["entry_price"] - precio) * self.position["size"]
                meta = dict(self.last_execution_metrics or {}, duration_sec=int(now-self.position.get("entry_time", now)), trend_supported=self._position_trend_supported(self.position["side"], context), exit_reason="CERRAR")
                self.state.add_trade(self.position["side"], self.position["entry_price"], precio, pnl, meta)
                self.memory.add_event("trade_closed", "Cierre por CERRAR", {"side": self.position["side"], "entry": self.position["entry_price"], "exit": precio, "pnl": pnl, "duration_sec": int(now-self.position.get("entry_time", now))})
                logger.info(">>> CERRADO | entry=%.2f mark=%.2f | PnL=%.2f USDT", self.position["entry_price"], precio, pnl)
                self.position = None

        elif accion == "NEUTRAL" and self.position:
            entry_time = self.position.get("entry_time", now)
            if now - entry_time > MAX_POSITION_DURATION_SEC:
                if not self._position_trend_supported(self.position["side"], context):
                    logger.info(">>> CERRANDO por NEUTRAL tras %ds sin tendencia soportante", MAX_POSITION_DURATION_SEC)
                    r = self.binance.close_position()
                    if r:
                        pnl = (precio - self.position["entry_price"]) * self.position["size"]
                        if self.position["side"] == "SHORT":
                            pnl = (self.position["entry_price"] - precio) * self.position["size"]
                        meta = dict(self.last_execution_metrics or {}, duration_sec=int(now-self.position.get("entry_time", now)), trend_supported=False, exit_reason="NEUTRAL")
                        self.state.add_trade(self.position["side"], self.position["entry_price"], precio, pnl, meta)
                        self.memory.add_event("trade_closed", "Cierre por NEUTRAL", {"side": self.position["side"], "entry": self.position["entry_price"], "exit": precio, "pnl": pnl, "duration_sec": int(now-self.position.get("entry_time", now))})
                        logger.info(">>> CERRADO | entry=%.2f mark=%.2f | PnL=%.2f USDT", self.position["entry_price"], precio, pnl)
                        self.position = None
                        return
                else:
                    logger.info(">>> Duración > %ds y NEUTRAL, pero tendencia sigue soportando", MAX_POSITION_DURATION_SEC)

    def run_cycle(self):
        context = self.build_context()
        if not context:
            return
        self.cycle_count += 1
        self.memory.add_event("cycle_start", "Ciclo iniciado", {"cycle": self.cycle_count, "price": context.get("mercado",{}).get("precio_actual",0), "position": self.position is not None})

        if self.position:
            now = time.time()
            pos = self.binance.get_position()
            if not pos:
                self.position = None
                return
            pnl = pos.get("pnl", 0)
            entry = self.position["entry_price"]
            mark = pos.get("mark_price", 0)
            atr = context.get("senal_tecnica", {}).get("atr", 0)
            if now - self.position["entry_time"] > MAX_POSITION_DURATION_SEC:
                if not self._position_trend_supported(self.position["side"], context):
                    logger.info(">>> CERRANDO por duración %ds y tendencia no soporta: %s %s", MAX_POSITION_DURATION_SEC, context.get('senal_tecnica', {}).get('tendencia'), context.get('senal_tecnica', {}).get('regimen'))
                    self.binance.close_position()
                    meta = dict(self.last_execution_metrics or {}, duration_sec=int(now-self.position.get("entry_time", now)), trend_supported=self._position_trend_supported(self.position["side"], context), exit_reason="ATR_STOP")
                    self.state.add_trade(self.position["side"], entry, mark, pnl, meta)
                    self.memory.add_event("trade_closed", "Cierre por ATR_STOP", {"side": self.position["side"], "entry": entry, "exit": mark, "pnl": pnl, "duration_sec": int(now-self.position.get("entry_time", now))})
                    self.position = None
                    return
                logger.info(">>> Duración > %ds pero tendencia aún soporta la operación", MAX_POSITION_DURATION_SEC)
            if entry > 0 and atr > 0:
                if self.position["side"] == "LONG" and mark < entry - atr * SCALPING_EXIT_ATR_MULTIPLIER:
                    self.binance.close_position()
                    meta = dict(self.last_execution_metrics or {}, duration_sec=int(now-self.position.get("entry_time", now)), trend_supported=self._position_trend_supported("LONG", context), exit_reason="ATR_STOP")
                    self.state.add_trade("LONG", entry, mark, pnl, meta)
                    self.memory.add_event("trade_closed", "Cierre por ATR_STOP", {"side": "LONG", "entry": entry, "exit": mark, "pnl": pnl, "duration_sec": int(now-self.position.get("entry_time", now))})
                    self.position = None
                    return
                elif self.position["side"] == "SHORT" and mark > entry + atr * SCALPING_EXIT_ATR_MULTIPLIER:
                    self.binance.close_position()
                    meta = dict(self.last_execution_metrics or {}, duration_sec=int(now-self.position.get("entry_time", now)), trend_supported=self._position_trend_supported("SHORT", context), exit_reason="ATR_STOP")
                    self.state.add_trade("SHORT", entry, mark, pnl, meta)
                    self.memory.add_event("trade_closed", "Cierre por ATR_STOP", {"side": "SHORT", "entry": entry, "exit": mark, "pnl": pnl, "duration_sec": int(now-self.position.get("entry_time", now))})
                    self.position = None
                    return

        print("\n>>> CICLO #%d | %s" % (self.cycle_count, datetime.now().strftime("%H:%M:%S")))
        decision = self.deepseek.consultar(context)
        decision = self._normalize_decision(decision)
        self.last_decision = {"accion": decision.get("accion"), "timestamp": time.time()}
        self.memory.add_event("decision", "DeepSeek decision", {"accion": decision.get("accion"), "confianza": decision.get("confianza"), "razon": decision.get("razon"), "riesgo": decision.get("riesgo")})

        d = decision
        c = context
        if self.position:
            age = int(time.time() - self.position.get("entry_time", time.time()))
            trend_ok = self._position_trend_supported(self.position["side"], c)
            print("POS: %s | age: %ds | trend_support: %s | entry: %.2f | size: %.3f" % (
                self.position["side"], age, "YES" if trend_ok else "NO",
                self.position["entry_price"], self.position["size"]
            ))
            if age > MAX_POSITION_DURATION_SEC:
                print("    DURACION > %ds" % MAX_POSITION_DURATION_SEC)
            if self.last_execution_metrics:
                m = self.last_execution_metrics
                print("    last_exec: conf=%.0f%% | risk=%.2f | vol=%.2f | spread=%.3f%% | size=%.3f" % (
                    m.get("confidence", 0.0)*100,
                    m.get("risk_factor", 0.0),
                    m.get("volatility_factor", 0.0),
                    m.get("spread_pct", 0.0),
                    m.get("size", 0.0)
                ))
        print("PRECIO: %.2f | RSI: %.1f | ADX: %.1f | VOL: %.2fx | SPREAD: %.3f%%" % (
            c.get("mercado",{}).get("precio_actual",0),
            c.get("senal_tecnica",{}).get("rsi",0),
            c.get("senal_tecnica",{}).get("adx",0),
            c.get("senal_tecnica",{}).get("ratio_volumen",0),
            c.get("mercado",{}).get("spread_pct",0)
        ))
        print("EMAs: rapid=%.2f (dist: %.2f%%) | lent=%.2f (dist: %.2f%%) | SOP=%.2f | RES=%.2f" % (
            c.get("mercado",{}).get("ema_rapida_5",0), c.get("mercado",{}).get("distancia_ema5_pct",0),
            c.get("mercado",{}).get("ema_lenta_20",0), c.get("mercado",{}).get("distancia_ema20_pct",0),
            c.get("mercado",{}).get("min_20_periodos",0), c.get("mercado",{}).get("max_20_periodos",0)
        ))
        fg = c.get("fundamental",{}).get("fear_greed",{})
        ls = c.get("fundamental",{}).get("long_short_ratio",{})
        fr = c.get("fundamental",{}).get("funding_rate",{}).get("funding_rate_str","?")
        oi = c.get("fundamental",{}).get("open_interest",{}).get("btc",0)
        print("F&G: %s/100 (%s) | L/S: %.2f (L:%.0f%% S:%.0f%%) | FR: %s | OI: %.0f BTC" % (
            fg.get("value","?"), fg.get("sentimiento","?"),
            ls.get("ratio",0), ls.get("long_pct",0), ls.get("short_pct",0),
            fr, oi
        ))
        liq = c.get("fundamental",{}).get("liquidaciones",{})
        tk = liq.get("taker_buy_sell_ratio",0)
        pc = liq.get("presion_compradora","?")
        oc = liq.get("oi_change_5m_pct",0)
        print("TAKER B/S: %.2f (%s) | OI 5m: %.2f%% | LIQ 24h: %.0f" % (tk, pc, oc, liq.get("total_24h",0)))
        ns = c.get("fundamental",{}).get("noticias",{}).get("resumen_noticias","")[:120]
        if ns: print("NOTICIAS: %s" % ns)
        oc_on = c.get("fundamental",{}).get("onchain",{})
        if oc_on.get("relacion_precio_media_1y"):
            print("ONCHAIN: precio/avg1y=%.2f | precio=%.2f | avg1y=%.2f" % (
                oc_on["relacion_precio_media_1y"], oc_on.get("precio_actual_onchain",0), oc_on.get("precio_promedio_1y",0)
            ))
        bs = c.get("fundamental", {}).get("black_swan", {})
        if bs.get("alertas"):
            print("ALERTA CISNE NEGRO: %s" % ", ".join(bs["alertas"]))
        sen = c.get("senal_tecnica",{}).get("senales",[])
        if sen: print("SENALES: [%s]" % " | ".join(sen))
        print("")
        print(">>> DEEPSEEK: %s | conf: %.0f%% | riesgo: %s" % (d.get("accion","?"), d.get("confianza",0)*100, d.get("riesgo","?")))
        if d.get("senal_tecnica") and d["senal_tecnica"]!="NO_DISPONIBLE":
            print("    patron: %s | razon: %s" % (d["senal_tecnica"], d.get("razon","")))
        if d.get("explicacion"):
            print("    analisis: %s" % d["explicacion"])
        print("")

        self.process_decision(decision, context)
        if self.position:
            pos = self.binance.get_position()
            if pos:
                logger.info("Pos %s | PnL: %.2f | entry: %.2f | mark: %.2f", pos["side"], pos["pnl"], pos["entry_price"], pos["mark_price"])

    def run(self):
        self.running = True
        print("")
        print("=" * 60)
        print("OPENBRIDGE DEEPSEEK TRADING ENGINE v3.1")
        print("Modo: %s | Simbolo: %s | Apalancamiento: %sx | Capital: %s USDT" % (TRADE_MODE, SYMBOL, LEVERAGE, CAPITAL_BASE))
        print("DeepSeek: %s | Temp: %s | Timeout: %ds" % (DEEPSEEK_MODEL, DEEPSEEK_TEMPERATURE, DEEPSEEK_TIMEOUT))
        print("=" * 60)
        print("")
        try: self.binance.set_leverage()
        except: pass
        bal = self.binance.get_account_balance()
        print("Balance: %.2f USDT | Capital efectivo: %.2f USDT" % (bal, bal * LEVERAGE))
        pos = self.binance.get_position()
        if pos:
            print("Posicion existente: %s @ %.2f" % (pos["side"], pos["entry_price"]))
            self.position = pos
        print("")
        while self.running:
            t0 = time.time()
            try: self.run_cycle()
            except Exception as e:
                logger.error("Error: %s", e)
                import traceback; traceback.print_exc()
            time.sleep(max(1.0, 6.0-(time.time()-t0)))

    def stop(self):
        self.running = False


if __name__ == "__main__":
    eng = DeepSeekTradingEngine()
    try: eng.run()
    except KeyboardInterrupt:
        print("\nDeteniendo...")
        eng.stop()
        if eng.position: eng.binance.close_position()
        eng.state.save()
        print("Detenido.")
    except Exception as e:
        print("Fatal: %s" % e)
        eng.stop()
