import asyncio
import json
import logging
import time
import websockets
from collections import deque, defaultdict
from dataclasses import dataclass
from typing import Optional, Callable

logger = logging.getLogger('OrderFlowV2')


@dataclass
class SignalEvent:
    """Evento de señal generado por condiciones de mercado"""
    timestamp: float
    signal_type: str  # 'LONG_TRIGGER', 'SHORT_TRIGGER', 'EXIT_LONG', 'EXIT_SHORT'
    cvd: float
    obi: float
    price: float
    aggression_delta: float  # Diferencia entre buy/sell pressure reciente
    confidence: float  # 0.0 - 1.0


class BinanceOrderFlowEngine:
    """
    Motor de Order Flow v2.0 - Event-driven con pipeline de baja latencia

    Mejoras:
    - Queue de señales asíncrona (no bloqueante)
    - Cálculo de agresión por ventana deslizante (1 segundo)
    - Detección de momentum burst (impulso súbito)
    - Estado del motor: COLD -> WARM -> HOT (listo para disparar)
    """

    def __init__(self, symbol="btcusdt",
                 cvd_threshold: float = 2.0,
                 obi_threshold: float = 0.15,
                 aggression_window_ms: int = 1000,
                 momentum_burst_threshold: float = 5.0):
        self.symbol = symbol.lower()
        self.ws_url = "wss://stream.binance.com:9443"

        # Thresholds configurables
        self.cvd_threshold = cvd_threshold
        self.obi_threshold = obi_threshold
        self.aggression_window_ms = aggression_window_ms
        self.momentum_burst_threshold = momentum_burst_threshold

        # Estado del Order Book
        self.bids = {}
        self.asks = {}
        self.obi = 0.0
        self.best_bid = 0.0
        self.best_ask = 0.0
        self.mid_price = 0.0

        # Estado del CVD y volumen
        self.cvd = 0.0
        self.cvd_1s_ago = 0.0  # Para detectar momentum burst
        self.buy_volume = 0.0
        self.sell_volume = 0.0

        # Ventana de agresión (último segundo)
        self.recent_aggression = deque(maxlen=1000)  # (timestamp, delta, qty)

        # Estado del motor
        self.is_running = False
        self.last_message_time = 0.0
        self.ws_healthy = False

        # Queue de señales para el trading engine
        self.signal_queue = asyncio.Queue(maxsize=50)

        # Callbacks registrables
        self.on_signal = None  # type: Optional[Callable]
        self.on_price_update = None  # type: Optional[Callable]

        # Estadísticas
        self.messages_processed = 0
        self.signals_generated = 0
        self.start_time = 0.0

    @property
    def current_price(self) -> float:
        """Precio medio actual basado en best bid/ask"""
        if self.best_bid > 0 and self.best_ask > 0:
            return (self.best_bid + self.best_ask) / 2
        return self.mid_price

    @property
    def aggression_1s(self) -> float:
        """Agresión neta del último segundo en BTC"""
        cutoff = time.time() * 1000 - self.aggression_window_ms
        return sum(delta for ts, delta, qty in self.recent_aggression if ts > cutoff)

    @property
    def order_flow_quality(self) -> float:
        """
        Calidad del Order Flow: 0.0 (malo) a 1.0 (excelente)
        Mide la consistencia de la dirección del flujo
        """
        if not self.recent_aggression:
            return 0.0
        recent = list(self.recent_aggression)[-100:]  # Últimos 100 eventos
        if not recent:
            return 0.0

        deltas = [delta for ts, delta, qty in recent]
        if not deltas:
            return 0.0

        # Dirección consistente = calidad alta
        positive = sum(1 for d in deltas if d > 0)
        negative = sum(1 for d in deltas if d < 0)
        total = positive + negative
        if total == 0:
            return 0.0

        # Mayoría clara = calidad alta
        majority = max(positive, negative)
        return majority / total

    def calculate_obi(self, levels: int = 15):
        """Calcula OBI y actualiza precios"""
        sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=1)[:levels]
        sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:levels]

        total_bid_vol = sum(vol for price, vol in sorted_bids)
        total_ask_vol = sum(vol for price, vol in sorted_asks)

        if total_bid_vol + total_ask_vol > 0:
            self.obi = (total_bid_vol - total_ask_vol) / (total_bid_vol + total_ask_vol)
        else:
            self.obi = 0.0

        # Actualizar precios
        if sorted_bids:
            self.best_bid = sorted_bids[0][0]
        if sorted_asks:
            self.best_ask = sorted_asks[0][0]
        self.mid_price = (self.best_bid + self.best_ask) / 2 if self.best_bid and self.best_ask else self.mid_price

    def process_depth_message(self, data):
        """Procesa actualización del Order Book"""
        for price_str, qty_str in data.get('b', []):
            price, qty = float(price_str), float(qty_str)
            if qty == 0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = qty

        for price_str, qty_str in data.get('a', []):
            price, qty = float(price_str), float(qty_str)
            if qty == 0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = qty

        self.calculate_obi()

    def process_trade_message(self, data):
        """Procesa trade y preload the aggression window"""
        qty = float(data['q'])
        is_sell_market = data['m']

        # Agresión: positivo = compra, negativo = venta
        aggression = qty if not is_sell_market else -qty
        now_ms = data.get('T', int(time.time() * 1000))
        self.recent_aggression.append((now_ms, aggression, qty))

        # Actualizar CVD
        self.cvd += aggression

        if is_sell_market:
            self.sell_volume += qty
        else:
            self.buy_volume += qty

        # Evaluar condiciones de disparo
        self._evaluate_triggers(data.get('p', self.current_price))

    def _evaluate_triggers(self, price: float):
        """
        Evalúa si las condiciones del Order Flow justifican una señal.
        Esta es la lógica de disparo ULTRA-RÁPIDA que corre en cada trade.
        """
        now = time.time()
        aggression = self.aggression_1s

        # Solo evaluar si tenemos suficiente data
        if len(self.recent_aggression) < 10:
            return

        momentum_burst = abs(self.cvd - self.cvd_1s_ago) > self.momentum_burst_threshold

        # --- DISPARO LONG ---
        if self.cvd > self.cvd_threshold and self.obi > -self.obi_threshold:
            if aggression > 0 and momentum_burst:  # Agresión compradora + burst de momento
                signal = SignalEvent(
                    timestamp=now,
                    signal_type='LONG_TRIGGER',
                    cvd=self.cvd,
                    obi=self.obi,
                    price=price,
                    aggression_delta=aggression,
                    confidence=min(1.0, abs(aggression) / 5.0)  # Normalizar
                )
                asyncio.create_task(self._emit_signal(signal))

        # --- DISPARO SHORT ---
        elif self.cvd < -self.cvd_threshold and self.obi < self.obi_threshold:
            if aggression < 0 and momentum_burst:
                signal = SignalEvent(
                    timestamp=now,
                    signal_type='SHORT_TRIGGER',
                    cvd=self.cvd,
                    obi=self.obi,
                    price=price,
                    aggression_delta=aggression,
                    confidence=min(1.0, abs(aggression) / 5.0)
                )
                asyncio.create_task(self._emit_signal(signal))

        # Actualizar referencia para momentum burst
        if now % 1.0 < 0.1:  # Aproximadamente cada segundo
            self.cvd_1s_ago = self.cvd

    async def _emit_signal(self, signal: SignalEvent):
        """Emite señal a través del callback y la queue"""
        self.signals_generated += 1

        # Callback síncrono
        if self.on_signal:
            try:
                self.on_signal(signal)
            except Exception as e:
                logger.error(f"Error en callback on_signal: {e}")

        # Queue asíncrona
        try:
            self.signal_queue.put_nowait(signal)
        except asyncio.QueueFull:
            logger.warning("Queue de señales llena, descartando señal antigua")
            # Desechar la más antigua y reintentar
            try:
                self.signal_queue.get_nowait()
                self.signal_queue.put_nowait(signal)
            except asyncio.QueueEmpty:
                pass

    async def get_signal(self, timeout: float = 0.0) -> Optional[SignalEvent]:
        """
        Obtiene la siguiente señal de forma no bloqueante.
        timeout=0.0: No bloquear, retornar None si no hay señal.
        timeout>0.0: Esperar hasta timeout segundos.
        """
        try:
            return await asyncio.wait_for(self.signal_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def connect(self):
        """Bucle de conexión WebSocket con Health Check"""
        self.is_running = True
        self.start_time = time.time()

        streams = f"{self.symbol}@depth@100ms/{self.symbol}@aggTrade"
        stream_url = f"{self.ws_url}/stream?streams={streams}"

        logger.info(f"[HFT] Conectando a {stream_url}")

        while self.is_running:
            try:
                async with websockets.connect(
                    stream_url,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=5
                ) as ws:
                    logger.info("[HFT] WebSocket conectado. Escuchando señales...")
                    self.ws_healthy = True

                    while self.is_running:
                        msg = await asyncio.wait_for(ws.recv(), timeout=25.0)
                        self.last_message_time = time.time()
                        self.messages_processed += 1

                        raw_data = json.loads(msg)
                        if 'data' in raw_data:
                            data = raw_data['data']
                        else:
                            data = raw_data

                        event_type = data.get('e')
                        if event_type == 'depthUpdate':
                            self.process_depth_message(data)
                        elif event_type == 'aggTrade':
                            self.process_trade_message(data)

            except asyncio.TimeoutError:
                logger.warning("[HFT] Timeout esperando mensaje. Reconectando...")
                self.ws_healthy = False
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"[HFT] Conexión cerrada ({e}). Reconectando en 1s...")
                self.ws_healthy = False
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"[HFT] Error: {e}. Reconectando en 3s...")
                self.ws_healthy = False
                await asyncio.sleep(3)

    @property
    def stats(self) -> dict:
        """Estadísticas del motor"""
        uptime = time.time() - self.start_time if self.start_time else 0
        return {
            'messages_processed': self.messages_processed,
            'signals_generated': self.signals_generated,
            'cvd': self.cvd,
            'obi': self.obi,
            'uptime_seconds': int(uptime),
            'ws_healthy': self.ws_healthy,
            'queue_size': self.signal_queue.qsize(),
            'aggression_1s': self.aggression_1s,
            'order_flow_quality': self.order_flow_quality
        }
