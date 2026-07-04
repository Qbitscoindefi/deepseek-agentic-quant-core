"""
OpenBridge REAL-TIME Aggression Analyzer v5.0
Sistema de análisis de agresión del mercado basado 100% en aggTrade (trades reales ejecutados).

ELIMINA por completo la dependencia de 'depthUpdate' y 'OBI' (Order Book Imbalance),
ya que estas métricas se basan en intenciones (órdenes pendientes) y no en acciones reales.

Las ballenas pueden poner y cancelar órdenes falsas para engañar al bot.
Solo los trades ejecutados (aggTrade) son dinero real y por lo tanto, la única verdad.
"""

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


@dataclass
class AggressionMetrics:
    """Métricas de agresión calculadas en una ventana de tiempo"""
    timestamp: float
    buy_volume: float = 0.0        # Volumen comprado agresivamente (market orders)
    sell_volume: float = 0.0       # Volumen vendido agresivamente (market orders)
    buy_count: int = 0           # Número de trades compradores
    sell_count: int = 0          # Número de trades vendedores
    avg_buy_size: float = 0.0    # Tamaño promedio de compra
    avg_sell_size: float = 0.0   # Tamaño promedio de venta
    largest_buy: float = 0.0     # Compra más grande
    largest_sell: float = 0.0    # Venta más grande

    @property
    def total_volume(self) -> float:
        return self.buy_volume + self.sell_volume

    @property
    def net_delta(self) -> float:
        """Delta neto: positivo = más compradores agresivos"""
        return self.buy_volume - self.sell_volume

    @property
    def imbalance(self) -> float:
        """Ratio de desbalance: 0.0 (todo venta) a 1.0 (todo compra)"""
        total = self.total_volume
        if total == 0:
            return 0.5
        return self.buy_volume / total

    @property
    def buy_pressure(self) -> float:
        """Presión compradora normalizada (0.0 a 1.0+)"""
        total = self.total_volume
        if total == 0:
            return 0.0
        return self.buy_volume / total

    @property
    def sell_pressure(self) -> float:
        """Presión vendedora normalizada (0.0 a 1.0+)"""
        total = self.total_volume
        if total == 0:
            return 0.0
        return self.sell_volume / total


class MarketAggressionAnalyzer:
    """
    Analizador de agresión del mercado basado EXCLUSIVAMENTE en trades ejecutados.

    NO usa 'depthUpdate'. NO calcula OBI. NO confía en intenciones.
    Solo datos reales: aggTrade de Binance WebSocket.
    """

    def __init__(self,
                 symbol: str = "btcusdt",
                 window_seconds: float = 5.0,        # Ventana de análisis
                 history_minutes: float = 10.0,      # Historial para tendencias
                 min_trade_size_btc: float = 0.001,  # Filtrar ruido de bots muy pequeños
                 burst_threshold: float = 0.60,      # Ratio mínimo para burst (audit v3.1)
                 burst_min_volume_btc: float = 1.0,  # Volumen direccional mínimo en burst
                 burst_cooldown_seconds: float = 2.0):

        self.symbol = symbol.lower()
        self.ws_url = "wss://stream.binance.com:9443"
        self.window_seconds = window_seconds
        self.history_minutes = history_minutes
        self.min_trade_size = min_trade_size_btc
        self.burst_threshold = burst_threshold
        self.burst_min_volume_btc = burst_min_volume_btc

        # Estado del WebSocket
        self.is_running = False
        self.ws_connected = False
        self.last_message_time = 0.0

        # Buffer de trades reales (aggTrade)
        # Cada trade: (timestamp_ms, is_buy_aggressive, quantity, price)
        self.trade_buffer = deque(maxlen=10000)

        # Ventanas de análisis
        self.rolling_window = deque(maxlen=int(60 * history_minutes))  # Ventanas de 1 segundo
        self.current_second_trades = []  # Trades del segundo actual
        self.current_second_start = 0.0

        # Métricas calculadas
        self.latest_metrics = AggressionMetrics(timestamp=time.time())
        self.metrics_history = deque(maxlen=int(history_minutes * 60))

        # Estado de alerta
        self.alert_state = {
            'last_burst_time': 0.0,
            'burst_cooldown_seconds': burst_cooldown_seconds,
            'active_burst_direction': None,  # 'BUY', 'SELL', or None
        }
        # Snapshot del último burst (para alinear ciclo polling con ventana aggTrade)
        self._last_burst_snapshot: Optional[Dict] = None

        # Callbacks para el motor de trading
        self.on_aggression_spike = None  # Función callback: (metrics, direction, strength)

        logger.info(f"[AGGRESSION] Analizador inicializado | Ventana: {window_seconds}s | Symbol: {symbol.upper()}")

    def process_agg_trade(self, data: dict):
        """
        Procesa un mensaje aggTrade REAL de Binance.

        Estructura de data:
        {
            "e": "aggTrade",
            "E": 123456789,     # Event time
            "s": "BTCUSDT",     # Symbol
            "p": "96432.50",    # Price
            "q": "0.035",       # Quantity
            "T": 123456789,     # Trade time
            "m": True,          # True = vendedor agresivo (comprador pasivo/limit)
                                # False = comprador agresivo (vendedor pasivo/limit)
            "a": 12345          # Trade ID
        }
        """
        try:
            qty = float(data['q'])

            # Filtrar ruido de micro-bots
            if qty < self.min_trade_size:
                return

            price = float(data['p'])
            trade_time_ms = data.get('T', int(time.time() * 1000))
            is_seller_aggressive = data.get('m', False)

            # Calcular timestamp en segundos
            trade_time = trade_time_ms / 1000.0

            # Si cambiamos de segundo, consolidar el anterior
            if int(trade_time) > int(self.current_second_start):
                self._consolidate_second()
                self.current_second_start = trade_time
                self.current_second_trades = []

            # Agregar al buffer de trades
            self.trade_buffer.append({
                'time': trade_time,
                'is_buy_aggressive': not is_seller_aggressive,
                'qty': qty,
                'price': price
            })

            # Acumular en segundo actual
            self.current_second_trades.append({
                'is_buy_aggressive': not is_seller_aggressive,
                'qty': qty,
                'price': price
            })

            # Recalcular métricas para el motor
            self._calculate_current_metrics()

            # Detección de burst de agresión (para disparo inmediato)
            self._detect_aggression_burst()

        except (KeyError, ValueError) as e:
            logger.debug(f"[AGGRESSION] Error procesando aggTrade: {e}")

    def _consolidate_second(self):
        """Consolida los trades del segundo anterior en la ventana rolling"""
        if not self.current_second_trades:
            return

        buy_vol = sum(t['qty'] for t in self.current_second_trades if t['is_buy_aggressive'])
        sell_vol = sum(t['qty'] for t in self.current_second_trades if not t['is_buy_aggressive'])
        buy_count = sum(1 for t in self.current_second_trades if t['is_buy_aggressive'])
        sell_count = len(self.current_second_trades) - buy_count

        self.rolling_window.append({
            'timestamp': self.current_second_start,
            'buy_volume': buy_vol,
            'sell_volume': sell_vol,
            'buy_count': buy_count,
            'sell_count': sell_count
        })

    def _calculate_current_metrics(self):
        """Recalcula métricas de la ventana actual"""
        now = time.time()
        cutoff = now - self.window_seconds

        # Limpiar trades antiguos del buffer
        while self.trade_buffer and self.trade_buffer[0]['time'] < cutoff:
            self.trade_buffer.popleft()

        # Calcular métricas actuales
        buy_vol = sum(t['qty'] for t in self.trade_buffer if t['is_buy_aggressive'])
        sell_vol = sum(t['qty'] for t in self.trade_buffer if not t['is_buy_aggressive'])
        buy_count = sum(1 for t in self.trade_buffer if t['is_buy_aggressive'])
        sell_count = len(self.trade_buffer) - buy_count

        buy_sizes = [t['qty'] for t in self.trade_buffer if t['is_buy_aggressive']]
        sell_sizes = [t['qty'] for t in self.trade_buffer if not t['is_buy_aggressive']]

        self.latest_metrics = AggressionMetrics(
            timestamp=now,
            buy_volume=buy_vol,
            sell_volume=sell_vol,
            buy_count=buy_count,
            sell_count=sell_count,
            avg_buy_size=sum(buy_sizes) / len(buy_sizes) if buy_sizes else 0,
            avg_sell_size=sum(sell_sizes) / len(sell_sizes) if sell_sizes else 0,
            largest_buy=max(buy_sizes) if buy_sizes else 0,
            largest_sell=max(sell_sizes) if sell_sizes else 0
        )

    def _detect_aggression_burst(self):
        """
        Detecta un 'burst' de agresión en una dirección.
        Esto es lo que disparará el motor HFT para entrar en posición.
        """
        metrics = self.latest_metrics
        total_vol = metrics.total_volume

        if total_vol < 0.5:  # Menos de 0.5 BTC en la ventana = sin interés
            return

        now = time.time()

        # Verificar cooldown
        if now - self.alert_state['last_burst_time'] < self.alert_state['burst_cooldown_seconds']:
            return

        # Ratio de agresión (cuánto domina una dirección)
        buy_ratio = metrics.buy_volume / total_vol if total_vol > 0 else 0.5
        sell_ratio = metrics.sell_volume / total_vol if total_vol > 0 else 0.5

        burst_threshold = self.burst_threshold

        # DETERMINAR DIRECCIÓN DEL BURST
        if buy_ratio > burst_threshold and metrics.buy_volume > self.burst_min_volume_btc:
            strength = buy_ratio * metrics.buy_volume  # Fuerza ponderada

            logger.warning(
                f"🟢 [BURST DETECTADO] Compra Agresiva | "
                f"Ratio: {buy_ratio:.1%} | Vol: {metrics.buy_volume:.3f} BTC | "
                f"Fuerza: {strength:.2f}"
            )

            self.alert_state['last_burst_time'] = now
            self.alert_state['active_burst_direction'] = 'BUY'
            self._record_burst_snapshot(now, 'BUY', metrics, strength)

            # Llamar callback si existe
            if self.on_aggression_spike:
                self.on_aggression_spike(metrics, 'BUY', strength)

        elif sell_ratio > burst_threshold and metrics.sell_volume > self.burst_min_volume_btc:
            strength = sell_ratio * metrics.sell_volume  # Fuerza ponderada

            logger.warning(
                f"🔴 [BURST DETECTADO] Venta Agresiva | "
                f"Ratio: {sell_ratio:.1%} | Vol: {metrics.sell_volume:.3f} BTC | "
                f"Fuerza: {strength:.2f}"
            )

            self.alert_state['last_burst_time'] = now
            self.alert_state['active_burst_direction'] = 'SELL'
            self._record_burst_snapshot(now, 'SELL', metrics, strength)

            # Llamar callback si existe
            if self.on_aggression_spike:
                self.on_aggression_spike(metrics, 'SELL', strength)

    def _record_burst_snapshot(
        self,
        timestamp: float,
        direction: str,
        metrics: AggressionMetrics,
        strength: float,
    ) -> None:
        """Guarda el burst más reciente para fusión con el ciclo técnico (polling)."""
        self._last_burst_snapshot = {
            'time': timestamp,
            'direction': direction,
            'metrics': metrics,
            'strength': strength,
        }

    def get_recent_burst(self, max_age_seconds: float = 5.0) -> Optional[Dict]:
        """Retorna el último burst si ocurrió dentro de max_age_seconds."""
        snap = self._last_burst_snapshot
        if not snap:
            return None
        if time.time() - snap['time'] > max_age_seconds:
            return None
        return snap

    def is_aggressive_long_signal(self,
                                   min_imbalance: float = 0.70,
                                   min_volume_btc: float = 2.0) -> bool:
        """
        Verifica si hay una señal de COMPRA agresiva confirmada.

        Args:
            min_imbalance: Mínimo ratio de compra para confirmar (0.0 a 1.0)
            min_volume_btc: Mínimo volumen total en BTC en la ventana
        """
        metrics = self.latest_metrics

        if metrics.total_volume < min_volume_btc:
            return False

        imbalance = metrics.imbalance
        if imbalance >= min_imbalance:
            logger.info(f"✅ SEÑAL LONG AGRESIVA CONFIRMADA: Imbalance={imbalance:.1%}, Vol={metrics.total_volume:.3f} BTC")
            return True

        return False

    def is_aggressive_short_signal(self,
                                    max_imbalance: float = 0.30,
                                    min_volume_btc: float = 2.0) -> bool:
        """
        Verifica si hay una señal de VENTA agresiva confirmada.

        Args:
            max_imbalance: Máximo ratio de compra (por debajo = venta dominante)
            min_volume_btc: Mínimo volumen total en BTC en la ventana
        """
        metrics = self.latest_metrics

        if metrics.total_volume < min_volume_btc:
            return False

        imbalance = metrics.imbalance
        if imbalance <= max_imbalance:
            logger.info(f"✅ SEÑAL SHORT AGRESIVA CONFIRMADA: Imbalance={imbalance:.1%}, Vol={metrics.total_volume:.3f} BTC")
            return True

        return False

    async def connect(self):
        """
        Conecta únicamente al stream de aggTrade (trades reales ejecutados).
        No se conecta a depth@100ms (intenciones).
        """
        self.is_running = True
        stream_url = f"{self.ws_url}/ws/{self.symbol}@aggTrade"

        logger.info(f"[AGGRESSION] Conectando a aggTrade REAL: {stream_url}")

        while self.is_running:
            try:
                import websockets
                async with websockets.connect(
                    stream_url,
                    ping_interval=20,
                    ping_timeout=20
                ) as ws:
                    logger.info("[AGGRESSION] ✅ Conectado a aggTrade REAL (trades ejecutados)")
                    self.ws_connected = True

                    while self.is_running:
                        msg = await ws.recv()
                        data = json.loads(msg)

                        if data.get('e') == 'aggTrade':
                            self.last_message_time = time.time()
                            self.process_agg_trade(data)

            except Exception as e:
                logger.error(f"[AGGRESSION] ❌ Error en conexión WebSocket: {e}")
                self.ws_connected = False
                await asyncio.sleep(5)

    def get_summary(self) -> str:
        """Devuelve un resumen de las métricas actuales para logs"""
        m = self.latest_metrics
        return (
            f"🟢 Compra: {m.buy_volume:.3f} BTC ({m.buy_count} trades) | "
            f"🔴 Venta: {m.sell_volume:.3f} BTC ({m.sell_count} trades) | "
            f"⚖️ Imbalance: {m.imbalance:.1%}"
        )

    def get_detailed_stats(self) -> Dict:
        """Devuelve estadísticas detalladas del estado actual"""
        m = self.latest_metrics
        return {
            'timestamp': m.timestamp,
            'buy_volume': m.buy_volume,
            'sell_volume': m.sell_volume,
            'total_volume': m.total_volume,
            'imbalance': m.imbalance,
            'net_delta': m.net_delta,
            'buy_count': m.buy_count,
            'sell_count': m.sell_count,
            'avg_buy_size': m.avg_buy_size,
            'avg_sell_size': m.avg_sell_size,
            'largest_buy': m.largest_buy,
            'largest_sell': m.largest_sell,
            'trade_buffer_size': len(self.trade_buffer),
            'ws_connected': self.ws_connected
        }


# Ejemplo de uso y testing
if __name__ == "__main__":
    async def main():
        analyzer = MarketAggressionAnalyzer(symbol="btcusdt", window_seconds=5.0)

        # Callback de ejemplo
        def on_spike(metrics, direction, strength):
            print(f"\n🚨 SPIKE {direction}! Fuerza: {strength:.2f}")
            print(f"   Buy Volume: {metrics.buy_volume:.3f} BTC")
            print(f"   Sell Volume: {metrics.sell_volume:.3f} BTC")
            print(f"   Imbalance: {metrics.imbalance:.1%}")

        analyzer.on_aggression_spike = on_spike

        # Simular trades de prueba
        print("Simulando trades de prueba...")
        for i in range(20):
            # Simular un burst de compra
            is_seller_agg = False if i < 15 else True  # 15 compras, 5 ventas

            trade = {
                'e': 'aggTrade',
                'p': '96432.50',
                'q': f'{0.05 + (i * 0.01):.3f}',
                'T': int(time.time() * 1000),
                'm': is_seller_agg
            }
            analyzer.process_agg_trade(trade)
            time.sleep(0.1)

        print(f"\nResumen: {analyzer.get_summary()}")

    asyncio.run(main())
