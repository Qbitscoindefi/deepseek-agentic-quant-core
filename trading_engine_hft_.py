# trading_engine_hft_.py
# -*- coding: utf-8 -*-
"""
OPENBRIDGE HFT v3.1 (LIVE TRADING)
Motor event-driven aggTrade + ciclo técnico
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from trading_engine import (
    LiveTradingEngine,
    acquire_single_instance_lock,
    ENV_PATH,
    OPPOSITE_IMPULSE_MIN_LOSS_PCT,
)
from aggression_analyzer import MarketAggressionAnalyzer, AggressionMetrics

logger = logging.getLogger(__name__)

# Defaults audit v3.1 — override vía .env
_DEFAULT_CYCLE_TIME = 5.0
_DEFAULT_AGGRESSION_MIN_VOLUME = 1.0
_DEFAULT_AGGRESSION_MIN_IMBALANCE = 0.60
_DEFAULT_AGGRESSION_WINDOW_SEC = 3.0
_DEFAULT_BURST_THRESHOLD = 0.60
_DEFAULT_BURST_MIN_VOLUME = 1.0
_DEFAULT_TECH_ANALYSIS_TTL = 15.0
_DEFAULT_TECH_OPPOSITION_THRESHOLD = 0.35
_DEFAULT_BURST_ALIGNMENT_SEC = 5.0
_DEFAULT_SPIKE_MIN_STRENGTH_NEUTRAL = 1.25
_SPIKE_QUEUE_MAX = 32


def _load_env_float(key: str, default: float) -> float:
    """Lee un float opcional del archivo .env sin dependencias externas."""
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as env_file:
            for line in env_file:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return float(line.split("=", 1)[1].strip())
    except (OSError, ValueError) as exc:
        logger.debug("No se pudo leer %s del .env: %s", key, exc)
        return default


class AsyncHFTTradingEngine(LiveTradingEngine):
    """
    Motor HFT v3.1: Hereda guards LIVE del LiveTradingEngine e inyecta fusión
    aggTrade (CVD real) con entrada event-driven vía ``on_aggression_spike``.

    Capas:
    - Eyes: ``MarketAggressionAnalyzer`` (WebSocket aggTrade, solo encola eventos).
    - Brain: ``hft_run_cycle`` cachea análisis técnico periódico.
    - Hands: ``open_position`` (heredado) con cooldown, circuit breaker y slippage guard.
    """

    def __init__(self) -> None:
        super().__init__()

        self.cycle_time = _load_env_float("HFT_CYCLE_TIME", _DEFAULT_CYCLE_TIME)

        self.aggression_min_imbalance = _load_env_float(
            "HFT_AGGRESSION_MIN_IMBALANCE", _DEFAULT_AGGRESSION_MIN_IMBALANCE
        )
        self.aggression_min_volume = _load_env_float(
            "HFT_AGGRESSION_MIN_VOLUME", _DEFAULT_AGGRESSION_MIN_VOLUME
        )

        window_seconds = _load_env_float(
            "HFT_AGGRESSION_WINDOW_SEC", _DEFAULT_AGGRESSION_WINDOW_SEC
        )
        burst_threshold = _load_env_float(
            "HFT_BURST_THRESHOLD", _DEFAULT_BURST_THRESHOLD
        )
        burst_min_volume = _load_env_float(
            "HFT_BURST_MIN_VOLUME", _DEFAULT_BURST_MIN_VOLUME
        )

        self.tech_analysis_ttl = _load_env_float(
            "HFT_TECH_ANALYSIS_TTL", _DEFAULT_TECH_ANALYSIS_TTL
        )
        self.tech_opposition_threshold = _load_env_float(
            "HFT_TECH_OPPOSITION_THRESHOLD", _DEFAULT_TECH_OPPOSITION_THRESHOLD
        )
        self.burst_alignment_sec = _load_env_float(
            "HFT_BURST_ALIGNMENT_SEC", _DEFAULT_BURST_ALIGNMENT_SEC
        )
        self.spike_min_strength_neutral = _load_env_float(
            "HFT_SPIKE_MIN_STRENGTH_NEUTRAL", _DEFAULT_SPIKE_MIN_STRENGTH_NEUTRAL
        )

        self.aggression = MarketAggressionAnalyzer(
            symbol="btcusdt",
            window_seconds=window_seconds,
            burst_threshold=burst_threshold,
            burst_min_volume_btc=burst_min_volume,
        )
        self.aggression.on_aggression_spike = self._enqueue_aggression_spike

        self._latest_analysis: Optional[Dict[str, Any]] = None
        self._latest_analysis_time: float = time.time()
        self._spike_queue: Optional[asyncio.Queue] = None
        self._position_lock = asyncio.Lock()
        self._spike_events_handled = 0

        logger.info(
            "HFT v3.1 config | ciclo=%.1fs | agg_vol=%.2f BTC | imb=%.0f%% | ventana=%.1fs",
            self.cycle_time,
            self.aggression_min_volume,
            self.aggression_min_imbalance * 100,
            window_seconds,
        )

    # ─────────────────────────────────────────────
    # Eyes: WebSocket → Cola
    # ─────────────────────────────────────────────

    def _enqueue_aggression_spike(
        self,
        metrics: AggressionMetrics,
        direction: str,
        strength: float,
    ) -> None:
        """Callback sync desde aggTrade (Eyes). Solo encola — sin lógica de red ni trading."""
        if self._spike_queue is None:
            return
        try:
            self._spike_queue.put_nowait((metrics, direction, strength, time.time()))
        except asyncio.QueueFull:
            logger.warning("[SPIKE] Cola llena — evento descartado")
        except Exception as exc:
            logger.debug("[SPIKE] Error encolando burst: %s", exc)

    # ─────────────────────────────────────────────
    # Brain: Caché analítico
    # ─────────────────────────────────────────────

    def _cache_technical_analysis(self, analysis: Dict[str, Any]) -> None:
        """Actualiza cache brain usado por el handler event-driven."""
        if analysis.get("data_ok", False):
            self._latest_analysis = analysis
            self._latest_analysis_time = time.time()

    def _min_neutral_burst_strength(self) -> float:
        """Fuerza mínima del burst con técnica NEUTRAL; sube tras racha de pérdidas."""
        base = self.spike_min_strength_neutral
        losses = int(self.session.session_data.get("consecutive_losses", 0))
        if losses >= 4:
            return max(base, 1.50)
        if losses >= 2:
            return max(base, 1.35)
        return base

    # ─────────────────────────────────────────────
    # Brain: Filtros de contexto
    # ─────────────────────────────────────────────

    def _spike_context_blocks_entry(self, side: str, analysis: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Aplica los mismos filtros de contexto que el ciclo técnico (FOMO, extensión ATR).
        Evita que event-driven entre donde el polling ya devuelve NEUTRAL por guards.
        """
        block_reason = analysis.get("signal_reason", "")

        if side == "LONG" and "LONG_FOMO_BLOCK" in block_reason:
            return True, block_reason
        if side == "SHORT" and "SHORT_FOMO_BLOCK" in block_reason:
            return True, block_reason
        if side == "LONG" and "RANGE_ATR_DIST" in block_reason:
            return True, block_reason

        rsi = float(analysis.get("rsi", 50.0))
        price = float(analysis.get("price", 0.0))
        sma = float(analysis.get("sma_5_5m", price))
        atr = float(analysis.get("atr", 1.0))
        atr_distance = abs(price - sma) / max(atr, 1.0)
        is_range = bool(analysis.get("is_range_mode", False))

        if side == "LONG":
            if is_range and (rsi > 65 or atr_distance > 1.2):
                return True, f"context_fomo rsi={rsi:.1f} dist={atr_distance:.2f}ATR"
            if not is_range and (rsi > 62 or atr_distance > 0.6):
                return True, f"context_fomo rsi={rsi:.1f} dist={atr_distance:.2f}ATR"
        elif side == "SHORT":
            if is_range and (rsi < 35 or atr_distance > 1.2):
                return True, f"context_fomo rsi={rsi:.1f} dist={atr_distance:.2f}ATR"
            if not is_range and (rsi < 38 or atr_distance > 0.6):
                return True, f"context_fomo rsi={rsi:.1f} dist={atr_distance:.2f}ATR"

        return False, ""

    def _technical_opposes_side(self, side: str, analysis: Dict[str, Any]) -> bool:
        """
        True si la señal técnica contradice fuertemente el lado propuesto.
        Permite entrada en NEUTRAL o señal alineada/débil en contra.
        """
        signal = analysis.get("signal", "NEUTRAL")
        confidence = float(analysis.get("confidence", 0.0))

        if side == "LONG":
            return signal == "SHORT" and confidence >= self.tech_opposition_threshold
        if side == "SHORT":
            return signal == "LONG" and confidence >= self.tech_opposition_threshold
        return False

    def _agg_confirms_side(self, side: str, metrics: AggressionMetrics) -> bool:
        """Verifica imbalance + volumen aggTrade para el lado dado."""
        imbalance = metrics.imbalance
        total_vol = metrics.total_volume
        if total_vol < self.aggression_min_volume:
            return False
        if side == "LONG":
            return imbalance >= self.aggression_min_imbalance
        if side == "SHORT":
            return imbalance <= (1.0 - self.aggression_min_imbalance)
        return False

    # ─────────────────────────────────────────────
    # Hands: Gestión de salida
    # ─────────────────────────────────────────────

    def should_exit(self, position, analysis):
        """Salida HFT: hereda guards base y añade cierre por flujo aggTrade opuesto."""
        should_close, reason = super().should_exit(position, analysis)
        if should_close:
            return should_close, reason

        entry_reason = getattr(self, "entry_signal_reason", "") or ""
        if not entry_reason.startswith("AGG_SPIKE"):
            return False, None

        current_pnl_pct = position.get("pnl_pct_margin", position["pnl_pct"])
        if current_pnl_pct >= OPPOSITE_IMPULSE_MIN_LOSS_PCT:
            return False, None

        agg_metrics = self.aggression.latest_metrics
        if agg_metrics.total_volume < self.aggression_min_volume:
            return False, None

        if position["side"] == "LONG":
            if agg_metrics.imbalance <= (1.0 - self.aggression_min_imbalance):
                logger.warning(
                    "AGG_REVERSAL: flujo vendedor (imb=%.1f%%) en LONG AGG_SPIKE",
                    agg_metrics.imbalance * 100,
                )
                return True, "AGG_REVERSAL"
        elif position["side"] == "SHORT":
            if agg_metrics.imbalance >= self.aggression_min_imbalance:
                logger.warning(
                    "AGG_REVERSAL: flujo comprador (imb=%.1f%%) en SHORT AGG_SPIKE",
                    agg_metrics.imbalance * 100,
                )
                return True, "AGG_REVERSAL"

        return False, None

    def _side_confirmed_by_flow(self, side: str) -> Tuple[bool, str]:
        """
        Confirma lado con métricas actuales O burst reciente alineado.
        Resuelve desync ciclo 5-7s vs ventana aggTrade 3s.
        """
        agg_metrics = self.aggression.latest_metrics

        if self._agg_confirms_side(side, agg_metrics):
            return True, "live"

        burst = self.aggression.get_recent_burst(self.burst_alignment_sec)
        if not burst:
            return False, "none"

        burst_side = "LONG" if burst["direction"] == "BUY" else "SHORT"
        if burst_side != side:
            return False, "burst_opposite"

        burst_metrics = burst["metrics"]
        if self._agg_confirms_side(side, burst_metrics):
            age_ms = (time.time() - burst["time"]) * 1000
            return True, f"burst_{age_ms:.0f}ms"

        return False, "burst_weak"

    def close_position(self, reason: str = "MANUAL_SHUTDOWN") -> bool:
        """Cierra posición actual vía Binance client."""
        if not self.position:
            return False
        try:
            side = "SELL" if self.position["side"] == "LONG" else "BUY"
            size = round(self.position["size"], 3)
            result = self.client.place_market_order(side, size)
            if result:
                pnl = self.position.get("pnl", 0)
                self.session.add_trade(
                    self.position["side"],
                    self.position["entry_price"],
                    self.position.get("mark_price", 0),
                    pnl,
                )
                logger.info(f"Posición cerrada: {reason} | PnL: {pnl:.2f}")
                self.position = None
                return True
        except Exception as e:
            logger.error(f"Error cerrando posición: {e}")
        return False

    # ─────────────────────────────────────────────
    # Brain + Hands: Event-driven aggTrade
    # ─────────────────────────────────────────────

    async def _handle_aggression_spike(
        self,
        metrics: AggressionMetrics,
        direction: str,
        strength: float,
        spike_time: float,
    ) -> None:
        """Brain + Hands: evalúa burst aggTrade contra cache técnico y dispara entrada."""
        try:
            if self.position:
                return

            position = await asyncio.to_thread(self.client.get_position)
            if position:
                self.position = position
                return

            if self.session.check_circuit_breaker():
                return
            if not self.can_enter_new_position():
                return

            analysis = self._latest_analysis
            if not analysis:
                logger.info("[SPIKE] Sin análisis técnico aún — burst %s ignorado", direction)
                return

            analysis_age = time.time() - self._latest_analysis_time
            if analysis_age > self.tech_analysis_ttl:
                logger.info(
                    "[SPIKE] Análisis técnico expirado (edad=%.1fs) — burst %s ignorado",
                    analysis_age,
                    direction,
                )
                return

            side = "LONG" if direction == "BUY" else "SHORT"
            tech_signal = analysis.get("signal", "NEUTRAL")
            tech_conf = float(analysis.get("confidence", 0.0))

            blocked, block_detail = self._spike_context_blocks_entry(side, analysis)
            if blocked:
                logger.info("[SPIKE] %s burst bloqueado — contexto mercado (%s)", side, block_detail)
                return

            if self._technical_opposes_side(side, analysis):
                logger.info(
                    "[SPIKE] %s burst bloqueado — técnica opuesta %s conf:%.0f%%",
                    side,
                    tech_signal,
                    tech_conf * 100,
                )
                return

            min_strength = self._min_neutral_burst_strength()
            if tech_signal == "NEUTRAL" and strength < min_strength:
                logger.info(
                    "[SPIKE] %s burst débil (fuerza=%.2f < %.2f) con técnica NEUTRAL — ignorado",
                    side,
                    strength,
                    min_strength,
                )
                return

            if not self._agg_confirms_side(side, metrics):
                logger.info(
                    "[SPIKE] %s burst sin confirmación agg (imb:%.1f%%, vol:%.2f BTC)",
                    side,
                    metrics.imbalance * 100,
                    metrics.total_volume,
                )
                return

            spike_analysis = dict(analysis)
            spike_analysis["signal"] = side
            spike_analysis["signal_reason"] = f"AGG_SPIKE_{direction}"
            if tech_signal == "NEUTRAL":
                spike_analysis["confidence"] = min(0.55, 0.30 + strength * 0.05)

            latency_ms = (time.time() - spike_time) * 1000
            logger.warning(
                "⚡ EVENT-DRIVEN %s | burst %s | fuerza=%.2f | tech=%s conf:%.0f%% | latencia=%.0fms",
                side,
                direction,
                strength,
                analysis.get("signal"),
                float(analysis.get("confidence", 0.0)) * 100,
                latency_ms,
            )

            async with self._position_lock:
                if self.position:
                    return
                existing = await asyncio.to_thread(self.client.get_position)
                if existing:
                    self.position = existing
                    return
                opened = await asyncio.to_thread(self.open_position, side, spike_analysis)
                if opened:
                    self._spike_events_handled += 1

        except Exception as exc:
            logger.error("[SPIKE] Error en handler event-driven: %s", exc)

    async def _spike_handler_loop(self) -> None:
        """Consume cola de bursts aggTrade y delega al brain."""
        assert self._spike_queue is not None
        while self.is_running:
            try:
                metrics, direction, strength, spike_time = await self._spike_queue.get()
                await self._handle_aggression_spike(metrics, direction, strength, spike_time)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[SPIKE] Error en loop handler: %s", exc)
            await asyncio.sleep(0.1)

    # ─────────────────────────────────────────────
    # Brain: Ciclo técnico (polling)
    # ─────────────────────────────────────────────

    async def hft_run_cycle(self) -> None:
        """Ejecuta un ciclo de trading con fusión aggTrade (polling complementario)."""
        position = await asyncio.to_thread(self.client.get_position)
        analysis = await asyncio.to_thread(
            self.analyzer.analyze_market,
            self.session.session_data["consecutive_losses"],
        )

        if not analysis.get("data_ok"):
            return

        self._cache_technical_analysis(analysis)

        if position:
            self.position = position
            if not hasattr(self, "pre_trade_balance") or self.pre_trade_balance == 0:
                bal_info = await asyncio.to_thread(self.client.get_account_balance)
                self.pre_trade_balance = bal_info["available"]
            if not self.trailing_manager.entry_price or self.trailing_manager.entry_price == 0:
                self.trailing_manager.setup(position["entry_price"], position["side"])
            if not self.entry_time:
                self.entry_time = datetime.now()

            if not await asyncio.to_thread(self.monitor_position, position, analysis):
                self.position = None
        else:
            if self.session.check_circuit_breaker():
                return

            signal = analysis["signal"]
            agg_metrics = self.aggression.latest_metrics
            imbalance = agg_metrics.imbalance
            total_vol = agg_metrics.total_volume
            mode_str = "RANGE" if analysis.get("is_range_mode") else "TREND"

            logger.info(
                "SEÑAL: %s (conf: %s) | "
                "AGG -> Imbalance: %.2f | Vol: %.2f BTC | "
                "Modo: %s | Razón: %s",
                signal,
                f"{analysis['confidence']:.0%}",
                imbalance,
                total_vol,
                mode_str,
                analysis["signal_reason"],
            )

            if signal == "LONG":
                confirmed, source = self._side_confirmed_by_flow("LONG")
                if confirmed:
                    logger.warning(
                        "[!] FUSION aggTrade LONG (%s): Técnica + Compra Agresiva (Imbalance:%.1f%%)",
                        source,
                        imbalance * 100,
                    )
                    await asyncio.to_thread(self.open_position, "LONG", analysis)
                else:
                    logger.info(
                        "[BLOCK] AGG: Técnica LONG sin confirmación aggTrade "
                        "(Imbalance:%.1f%%, Vol:%.2f, burst=%s).",
                        imbalance * 100,
                        total_vol,
                        source,
                    )

            elif signal == "SHORT":
                confirmed, source = self._side_confirmed_by_flow("SHORT")
                if confirmed:
                    logger.warning(
                        "[!] FUSION aggTrade SHORT (%s): Técnica + Venta Agresiva (Imbalance:%.1f%%)",
                        source,
                        imbalance * 100,
                    )
                    await asyncio.to_thread(self.open_position, "SHORT", analysis)
                else:
                    logger.info(
                        "[BLOCK] AGG: Técnica SHORT sin confirmación aggTrade "
                        "(Imbalance:%.1f%%, Vol:%.2f, burst=%s).",
                        imbalance * 100,
                        total_vol,
                        source,
                    )

    # ─────────────────────────────────────────────
    # Main loop
    # ─────────────────────────────────────────────

    async def run_async(self) -> None:
        """Bucle principal asíncrono con handler event-driven de bursts."""
        print("\n" + "=" * 60)
        print("MOTOR OPENBRIDGE HFT v3.1 (LIVE TRADING)".center(60))
        print("EVENT-DRIVEN aggTrade + CICLO TÉCNICO".center(60))
        print("PRECAUCIÓN: OPERANDO CON FONDOS REALES EN FUTUROS".center(60))
        print("=" * 60 + "\n")

        self.initialize()

        logger.info("Precalentando análisis técnico (hasta 4 ciclos)...")
        for _ in range(4):
            warmup = await asyncio.to_thread(
                self.analyzer.analyze_market,
                self.session.session_data["consecutive_losses"],
            )
            if warmup.get("data_ok"):
                self._cache_technical_analysis(warmup)
                logger.info(
                    "Análisis técnico precalentado | señal=%s conf=%.0f%%",
                    warmup.get("signal"),
                    float(warmup.get("confidence", 0.0)) * 100,
                )
                break
            await asyncio.sleep(0.5)

        self._spike_queue = asyncio.Queue(maxsize=_SPIKE_QUEUE_MAX)
        spike_task = asyncio.create_task(self._spike_handler_loop())
        ws_task = asyncio.create_task(self.aggression.connect())

        logger.info("Llenando memoria HFT. Esperando %.0f segundos...", self.aggression.window_seconds)
        await asyncio.sleep(self.aggression.window_seconds)

        existing = await asyncio.to_thread(self.client.get_position)
        if existing:
            logger.warning("POSICIÓN EXISTENTE DETECTADA - %s", existing["side"])
            self.position = existing
            self.trailing_manager.setup(existing["entry_price"], existing["side"])
            self.entry_time = datetime.now()
            bal_info = await asyncio.to_thread(self.client.get_account_balance)
            self.pre_trade_balance = bal_info["available"]

        logger.info(
            "Memoria HFT lista. Motor LIVE v3.1 activo | ciclo=%.1fs | spikes=%d",
            self.cycle_time,
            self._spike_events_handled,
        )

        while self.is_running:
            try:
                start_time = time.time()
                await self.hft_run_cycle()
                elapsed = time.time() - start_time
                sleep_time = max(1.0, self.cycle_time - elapsed)
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error en bucle HFT: %s", exc)
                await asyncio.sleep(self.cycle_time)

        self.aggression.is_running = False
        spike_task.cancel()
        ws_task.cancel()


async def main() -> None:
    acquire_single_instance_lock()
    engine = AsyncHFTTradingEngine()
    try:
        await engine.run_async()
    except KeyboardInterrupt:
        logger.info("Deteniendo motor HFT...")
        if engine.position:
            logger.info("Cerrando posición abierta...")
            engine.close_position("MANUAL_SHUTDOWN")
        engine.session.save_state()
        logger.info("Motor HFT detenido")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
