import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

from trading_engine import (
    LiveTradingEngine, acquire_single_instance_lock, CYCLE_TIME,
    CIRCUIT_BREAKER_MAX_LOSS_USDT, CIRCUIT_BREAKER_WINDOW_HOURS
)
from orderflow_analyzer_v2 import BinanceOrderFlowEngine, SignalEvent

logger = logging.getLogger(__name__)


class HFTSignalFusion:
    """
    Sistema de fusión de señales: Combina análisis técnico + Order Flow
    en tiempo real sin bloquear el loop principal.
    """

    def __init__(self,
                 of_threshold_cvd: float = 2.0,
                 of_threshold_obi: float = 0.15,
                 tech_min_confidence: float = 0.35,
                 alignment_window_sec: float = 3.0):
        # Thresholds de Order Flow
        self.cvd_threshold = of_threshold_cvd
        self.obi_threshold = of_threshold_obi
        self.tech_min_confidence = tech_min_confidence

        # Ventana de alineación: cuánto tiempo esperamos que ambas señales coincidan
        self.alignment_window = alignment_window_sec

        # Estado
        self._last_tech_signal = None  # (timestamp, signal, confidence, analysis)
        self._last_of_signal = None    # (timestamp, SignalEvent)

        # Stats
        self.fusion_hits = 0
        self.false_positives_blocked = 0

    def process_tech_signal(self, analysis: dict) -> Optional[dict]:
        """
        Recibe señal del análisis técnico. Si hay una señal de Order Flow
        alineada recientemente, retorna la fusión.
        """
        now = time.time()
        signal = analysis.get('signal')
        confidence = analysis.get('confidence', 0.0)

        if signal not in ('LONG', 'SHORT'):
            return None

        if confidence < self.tech_min_confidence:
            return None

        self._last_tech_signal = (now, signal, confidence, analysis)

        # Buscar alineación con Order Flow
        return self._check_alignment(now, signal, confidence, analysis)

    def process_orderflow_signal(self, signal_event: SignalEvent):
        """
        Recibe señal del Order Flow. Almacena para posible fusión futura.
        """
        self._last_of_signal = (time.time(), signal_event)

    def _check_alignment(self, now, tech_signal, tech_confidence, analysis):
        """Verifica si hay alineación técnica + Order Flow"""
        if not self._last_of_signal:
            return None

        of_time, of_event = self._last_of_signal
        if now - of_time > self.alignment_window:
            return None  # Señal de OF muy vieja

        # Validar dirección
        direction_match = (
            (tech_signal == 'LONG' and of_event.signal_type == 'LONG_TRIGGER') or
            (tech_signal == 'SHORT' and of_event.signal_type == 'SHORT_TRIGGER')
        )

        if not direction_match:
            self.false_positives_blocked += 1
            return None

        # Calcular confianza fusionada
        fused_confidence = (tech_confidence + of_event.confidence) / 2
        self.fusion_hits += 1

        return {
            'signal': tech_signal,
            'confidence': fused_confidence,
            'tech_confidence': tech_confidence,
            'of_confidence': of_event.confidence,
            'of_cvd': of_event.cvd,
            'of_obi': of_event.obi,
            'of_aggression': of_event.aggression_delta,
            'alignment_time_ms': (now - of_time) * 1000,
            'source': 'FUSION',
            'analysis': analysis
        }


class UltraFastTradingEngine(LiveTradingEngine):
    """
    Motor HFT v4.0 - Event-Driven con latencia mínima

    Arquitectura:
    - Trading Loop: ~7s (RSI, ADX, análisis complejo)
    - WebSocket Loop: ~100ms (Order Flow, precios)
    - Fusion Loop: Evento-disparado (cuando ambas señales coinciden)
    - Positions Loop: Mono-thread asíncrono para no bloquear
    """

    def __init__(self):
        super().__init__()

        # Motor de Order Flow Ultra-Fast
        self.orderflow = BinanceOrderFlowEngine(
            symbol="btcusdt",
            cvd_threshold=2.0,
            obi_threshold=0.15,
            aggression_window_ms=1000,
            momentum_burst_threshold=5.0
        )

        # Sistema de fusión
        self.fusion = HFTSignalFusion(
            of_threshold_cvd=2.0,
            of_threshold_obi=0.15,
            tech_min_confidence=0.35,
            alignment_window_sec=2.5  # Alineación válida por 2.5 segundos
        )

        # Estado HFT
        self.in_position = False
        self.position_lock = asyncio.Lock()  # Pre doble disparo
        self.pending_tech_analysis = None    # Cache del análisis más reciente
        self.last_tech_update = 0.0

        # Configuración de loops
        self.tech_loop_interval = CYCLE_TIME  # 7 segundos, análisis complejo
        self.position_check_interval = 1.0    # 1 segundo, check ligero
        self.ws_latency_target_ms = 150       # Target de latencia WebSocket

        # Estadísticas HFT
        self.hft_stats = {
            'ws_signals_received': 0,
            'tech_cycles': 0,
            'fusion_events': 0,
            'position_opens': 0,
            'avg_fusion_latency_ms': 0.0
        }

    async def tech_analysis_loop(self):
        """
        Loop de análisis técnico: Ejecuta análisis complejo cada 7 segundos.
        Esta es la parte 'pesada' que no podemos acelerar.
        """
        while self.is_running:
            start = time.time()

            try:
                # Análisis técnico completo (puede tardar 2-3s)
                analysis = await asyncio.to_thread(
                    self.analyzer.analyze_market,
                    self.session.session_data['consecutive_losses']
                )

                if analysis.get('data_ok', False):
                    self.pending_tech_analysis = analysis
                    self.last_tech_update = time.time()

                    # Intentar fusión inmediata
                    if not self.in_position:
                        await self._evaluate_fusion(analysis)

                    self.hft_stats['tech_cycles'] += 1

                    # Log cada ciclo técnico
                    mode = "🔄 RANGE" if analysis.get('is_range_mode') else "📈 TREND"
                    logger.info(
                        f"[TECH] {analysis['signal']} conf:{analysis['confidence']:.0%} | "
                        f"ADX:{analysis['adx']:.1f} | Impulso:{analysis.get('impulse_raw',0):.3f} | "
                        f"{mode} | CVD:{self.orderflow.cvd:+.1f} | OBI:{self.orderflow.obi:+.2f}"
                    )

                elapsed = time.time() - start
                sleep = max(0.1, self.tech_loop_interval - elapsed)
                await asyncio.sleep(sleep)

            except Exception as e:
                logger.error(f"[TECH] Error en análisis técnico: {e}")
                await asyncio.sleep(2)

    async def position_monitor_loop(self):
        """
        Loop de monitoreo de posiciones: Cada 1 segundo verifica
        si hay que cerrar (trailing stop, hard stop, etc.)
        """
        while self.is_running:
            try:
                if not self.in_position and not self.position:
                    await asyncio.sleep(0.5)
                    continue

                # Verificar posición actual en Binance (blocking call)
                position = await asyncio.to_thread(self.client.get_position)

                if not position:
                    self.in_position = False
                    self.position = None
                    await asyncio.sleep(1)
                    continue

                self.position = position
                self.in_position = True

                # Análisis para decisiones de salida (necesita data técnica)
                if self.pending_tech_analysis:
                    should_close, reason = await asyncio.to_thread(
                        self.should_exit, position, self.pending_tech_analysis
                    )
                    if should_close:
                        await self._safe_close_position(f"{reason} (fast-loop)")

                # Logging ligero
                pnl = position.get('pnl', 0)
                logger.info(
                    f"[POS] {position['side']} | P&L: ${pnl:.2f} | "
                    f"Entry: ${position['entry_price']:.2f}")

                await asyncio.sleep(self.position_check_interval)

            except Exception as e:
                logger.error(f"[POS] Error monitoreando posición: {e}")
                await asyncio.sleep(1)

    async def orderflow_event_handler(self):
        """
        Handler de eventos de Order Flow: Recibe señales del WebSocket
        y evalúa disparo inmediato si hay análisis técnico fresco.
        """
        while self.is_running:
            try:
                # Obtener señal con espera no bloqueante (100ms max)
                signal = await self.orderflow.get_signal(timeout=0.1)

                if signal is None:
                    continue

                self.hft_stats['ws_signals_received'] += 1
                self.fusion.process_orderflow_signal(signal)

                # Si tenemos análisis técnico reciente (<3s), evaluar fusión
                if self.pending_tech_analysis:
                    age = time.time() - self.last_tech_update
                    if age < 3.0 and not self.in_position:
                        await self._evaluate_fusion(self.pending_tech_analysis)

                # Log de evento de alta intensidad
                if abs(signal.aggression_delta) > 10.0:
                    direction = "🟢 LONG" if signal.signal_type == 'LONG_TRIGGER' else "🔴 SHORT"
                    logger.warning(
                        f"[OF-EVENT] {direction} burst! "
                        f"Agg:{signal.aggression_delta:+.2f} BTC | "
                        f"CVD:{signal.cvd:+.1f} | OBI:{signal.obi:+.2f}"
                    )

            except Exception as e:
                logger.error(f"[OF] Error en handler OF: {e}")
                await asyncio.sleep(0.1)

    async def _evaluate_fusion(self, analysis: dict):
        """Evalúa fusión técnica + Order Flow para disparar entrada"""
        if self.in_position:
            return

        fusion_result = self.fusion.process_tech_signal(analysis)

        if fusion_result:
            self.hft_stats['fusion_events'] += 1

            side = fusion_result['signal']
            confidence = fusion_result['confidence']

            logger.warning(
                f"🔥 FUSIÓN HFT {side}! conf:{confidence:.0%} | "
                f"OF-CVD:{fusion_result['of_cvd']:+.1f} | "
                f"OF-OBI:{fusion_result['of_obi']:+.2f} | "
                f"Agg:{fusion_result['of_aggression']:+.2f} | "
                f"Align:{fusion_result['alignment_time_ms']:.0f}ms"
            )

            # Disparo con protección contra ejecución múltiple
            async with self.position_lock:
                if not self.in_position:
                    self.hft_stats['position_opens'] += 1
                    await asyncio.to_thread(
                        self.open_position, side, analysis
                    )

    async def run_async(self):
        """Bucle principal: Lanza todos los loops concurrentemente"""
        print("\n" + "="*65)
        print("MOTOR OPENBRIDGE HFT v4.0 - ULTRA-FAST".center(65))
        print("EVENT-DRIVEN | WEBSOCKET + TECH FUSION".center(65))
        print("="*65 + "\n")

        # Inicialización
        self.initialize()

        # Lanza todos los loops como tareas concurrentes
        tasks = [
            asyncio.create_task(self.orderflow.connect()),
            asyncio.create_task(self.orderflow_event_handler()),
            asyncio.create_task(self.tech_analysis_loop()),
            asyncio.create_task(self.position_monitor_loop()),
            asyncio.create_task(self._stats_reporter()),
        ]

        logger.info("[HFT] Motor v4.0 iniciado. Multi-loop concurrente activo.")
        logger.info(f"[HFT] Tech loop: {self.tech_loop_interval}s | Pos check: {self.position_check_interval}s | WS: <150ms")

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[HFT] Error crítico: {e}")
        finally:
            for task in tasks:
                task.cancel()
            logger.info("[HFT] Motor detenido.")

    async def _stats_reporter(self):
        """Reportero de estadísticas cada 30 segundos"""
        while self.is_running:
            await asyncio.sleep(30)

            stats = self.hft_stats
            of_stats = self.orderflow.stats

            # Calcular latencia promedio de fusión (aprox)
            if stats['fusion_events'] > 0:
                avg_latency = 100.0 / stats['fusion_events']
            else:
                avg_latency = 0

            logger.info(
                f"[STATS] Tech cycles: {stats['tech_cycles']} | "
                f"WS signals: {stats['ws_signals_received']} | "
                f"Fusions: {stats['fusion_events']} | "
                f"Pos. abiertas: {stats['position_opens']} | "
                f"OF-CVD: {of_stats.get('cvd', 0):+.1f} | "
                f"OF msgs: {of_stats.get('messages_processed', 0)} | "
                f"WS healthy: {of_stats.get('ws_healthy', False)}"
            )


async def main():
    """Entry point del motor HFT v4.0"""
    acquire_single_instance_lock()
    engine = UltraFastTradingEngine()

    try:
        await engine.run_async()
    except KeyboardInterrupt:
        logger.info("[HFT] Shutdown solicitado por usuario.")
        engine.is_running = False
        if engine.position:
            engine.close_position("MANUAL_SHUTDOWN")
        engine.session.save_state()
    except Exception as e:
        logger.critical(f"[HFT] Error fatal: {e}")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
