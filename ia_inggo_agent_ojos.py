#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IA-INGGO 2.0 — AGENTE 1: OJOS
WebSocket aggTrade + klines en tiempo real desde Binance Futures.
Solo datos reales ejecutados (no intenciones del order book).

Autor: OpenBridge AI | Fecha: 2026-07-04
Arquitectura: Pipeline asíncrono multi-agente
"""

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Callable, Awaitable

logger = logging.getLogger("IA-INGGO.OJOS")


@dataclass
class TickEvent:
    """Evento de mercado en tiempo real — la unidad mínima de verdad."""
    timestamp: float
    price: float
    quantity: float
    is_buyer_maker: bool  # True = vendió agresivamente, False = compró agresivamente
    trade_id: int = 0

    @property
    def side(self) -> str:
        return "SELL" if self.is_buyer_maker else "BUY"

    @property
    def notional(self) -> float:
        return self.price * self.quantity


@dataclass
class AggressionWindow:
    """Ventana deslizante de agresión — CVD (Cumulative Volume Delta) real."""
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    buy_count: int = 0
    sell_count: int = 0
    net_delta: float = 0.0
    largest_trade: float = 0.0
    burst_detected: bool = False
    burst_side: str = "NEUTRAL"
    burst_volume: float = 0.0

    def reset(self):
        self.buy_volume = 0.0
        self.sell_volume = 0.0
        self.buy_count = 0
        self.sell_count = 0
        self.net_delta = 0.0
        self.largest_trade = 0.0
        self.burst_detected = False
        self.burst_side = "NEUTRAL"
        self.burst_volume = 0.0


class AgenteOjos:
    """
    AGENTE 1 — OJOS
    Responsabilidad: Capturar datos de mercado en tiempo real vía WebSocket.
    - Conexión a stream aggTrade de Binance (trades ejecutados, no intenciones)
    - Ventana deslizante de agresión para detectar bursts de ballenas
    - Klines de 1m y 5m para el Cerebro
    - Reconexión automática con backoff exponencial
    - Publica eventos en cola asíncrona para el Agente 2 (Cerebro)
    """

    WS_AGG_TRADE_URL = "wss://fstream.binance.com/ws/{symbol}@aggTrade"
    WS_KLINE_1M_URL = "wss://fstream.binance.com/ws/{symbol}@kline_1m"
    WS_KLINE_5M_URL = "wss://fstream.binance.com/ws/{symbol}@kline_5m"

    def __init__(
        self,
        symbol: str = "btcusdt",
        window_seconds: float = 3.0,
        burst_threshold: float = 0.65,
        burst_min_volume: float = 2.0,
        output_queue: Optional[asyncio.Queue] = None,
    ):
        self.symbol = symbol.lower()
        self.window_seconds = window_seconds
        self.burst_threshold = burst_threshold
        self.burst_min_volume = burst_min_volume

        # Cola de salida hacia el Cerebro
        self.output_queue = output_queue or asyncio.Queue(maxsize=256)

        # Ventana deslizante de agresión
        self.tick_buffer: deque[TickEvent] = deque()
        self.aggression = AggressionWindow()

        # Klines en vivo
        self.klines_1m: deque[dict] = deque(maxlen=120)
        self.klines_5m: deque[dict] = deque(maxlen=100)

        # Estado
        self.is_running = False
        self._ws_agg = None
        self._ws_kline_1m = None
        self._ws_kline_5m = None
        self._reconnect_attempts = 0
        self._max_reconnect_delay = 60

        # Métricas
        self.ticks_received = 0
        self.bursts_detected = 0
        self.last_heartbeat = 0.0

    async def _connect_websocket(self, url: str, handler: Callable[[dict], Awaitable[None]]):
        """Conecta a un stream WebSocket con reconexión automática."""
        backoff = 1
        while self.is_running:
            try:
                async with asyncio.timeout(30):  # timeout de conexión
                    ws = await asyncio.get_event_loop().create_connection(
                        lambda: _WebSocketClientProtocol(handler),
                        host="fstream.binance.com",
                        port=443,
                        ssl=True,
                    )
                logger.info(f"Conectado a {url}")
                self._reconnect_attempts = 0
                # Mantener vivo
                while self.is_running:
                    await asyncio.sleep(1)
            except Exception as e:
                if not self.is_running:
                    break
                self._reconnect_attempts += 1
                delay = min(backoff * (2 ** (self._reconnect_attempts - 1)), self._max_reconnect_delay)
                logger.warning(f"WebSocket caído ({e}). Reconectando en {delay}s...")
                await asyncio.sleep(delay)

    async def _handle_agg_trade(self, data: dict):
        """Procesa un evento aggTrade y actualiza la ventana de agresión."""
        try:
            price = float(data["p"])
            quantity = float(data["q"])
            is_buyer_maker = data["m"]  # True = el comprador pasivo recibe = el vendedor es agresivo
            trade_id = data.get("a", 0)
            timestamp = data.get("T", time.time() * 1000) / 1000.0

            tick = TickEvent(
                timestamp=timestamp,
                price=price,
                quantity=quantity,
                is_buyer_maker=is_buyer_maker,
                trade_id=trade_id,
            )

            # Insertar en buffer y podar ventana
            self.tick_buffer.append(tick)
            cutoff = time.time() - self.window_seconds
            while self.tick_buffer and self.tick_buffer[0].timestamp < cutoff:
                self.tick_buffer.popleft()

            # Recalcular ventana de agresión
            self._recalc_aggression()

            # Detectar burst de ballena
            self._detect_burst()

            # Publicar en cola de salida para el Cerebro
            if self.output_queue.full():
                # Descartar el más antiguo si cola llena (el cerebro no puede procesar a esta velocidad)
                try:
                    self.output_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                await self.output_queue.put({
                    "type": "agg_tick",
                    "tick": tick,
                    "aggression": {
                        "buy_volume": self.aggression.buy_volume,
                        "sell_volume": self.aggression.sell_volume,
                        "net_delta": self.aggression.net_delta,
                        "imbalance": self.aggression.buy_volume / max(self.aggression.buy_volume + self.aggression.sell_volume, 0.001),
                        "burst": self.aggression.burst_detected,
                        "burst_side": self.aggression.burst_side,
                        "burst_volume": self.aggression.burst_volume,
                    },
                }), timeout=0.5)
            else:
                await self.output_queue.put({
                    "type": "agg_tick",
                    "tick": tick,
                    "aggression": {
                        "buy_volume": self.aggression.buy_volume,
                        "sell_volume": self.aggression.sell_volume,
                        "net_delta": self.aggression.net_delta,
                        "imbalance": self.aggression.buy_volume / max(self.aggression.buy_volume + self.aggression.sell_volume, 0.001),
                        "burst": self.aggression.burst_detected,
                        "burst_side": self.aggression.burst_side,
                        "burst_volume": self.aggression.burst_volume,
                    },
                })

            self.ticks_received += 1
            self.last_heartbeat = time.time()

        except Exception as e:
            logger.debug(f"Error procesando aggTrade: {e}")

    def _recalc_aggression(self):
        """Recalcula métricas de agresión desde el buffer."""
        buy_vol = 0.0
        sell_vol = 0.0
        buy_cnt = 0
        sell_cnt = 0
        largest = 0.0

        for tick in self.tick_buffer:
            notional = tick.notional
            if notional > largest:
                largest = notional
            if tick.is_buyer_maker:
                sell_vol += notional
                sell_cnt += 1
            else:
                buy_vol += notional
                buy_cnt += 1

        self.aggression.buy_volume = buy_vol
        self.aggression.sell_volume = sell_vol
        self.aggression.buy_count = buy_cnt
        self.aggression.sell_count = sell_cnt
        self.aggression.net_delta = buy_vol - sell_vol
        self.aggression.largest_trade = largest

    def _detect_burst(self):
        """Detecta si hay un burst de ballena en la ventana actual."""
        total_vol = self.aggression.buy_volume + self.aggression.sell_volume
        if total_vol < self.burst_min_volume:
            self.aggression.burst_detected = False
            self.aggression.burst_side = "NEUTRAL"
            self.aggression.burst_volume = 0.0
            return

        imbalance = self.aggression.buy_volume / total_vol if total_vol > 0 else 0.5

        if imbalance >= self.burst_threshold:
            self.aggression.burst_detected = True
            self.aggression.burst_side = "BUY"
            self.aggression.burst_volume = self.aggression.buy_volume
            self.bursts_detected += 1
        elif imbalance <= (1.0 - self.burst_threshold):
            self.aggression.burst_detected = True
            self.aggression.burst_side = "SELL"
            self.aggression.burst_volume = self.aggression.sell_volume
            self.bursts_detected += 1
        else:
            self.aggression.burst_detected = False
            self.aggression.burst_side = "NEUTRAL"
            self.aggression.burst_volume = 0.0

    async def _handle_kline_1m(self, data: dict):
        """Procesa vela de 1 minuto."""
        kline = data.get("k", {})
        if kline.get("x", False):  # Solo velas cerradas
            self.klines_1m.append({
                "time": kline["t"] / 1000.0,
                "open": float(kline["o"]),
                "high": float(kline["h"]),
                "low": float(kline["l"]),
                "close": float(kline["c"]),
                "volume": float(kline["v"]),
            })

    async def _handle_kline_5m(self, data: dict):
        """Procesa vela de 5 minutos."""
        kline = data.get("k", {})
        if kline.get("x", False):
            self.klines_5m.append({
                "time": kline["t"] / 1000.0,
                "open": float(kline["o"]),
                "high": float(kline["h"]),
                "low": float(kline["l"]),
                "close": float(kline["c"]),
                "volume": float(kline["v"]),
            })

    def get_latest_klines_5m(self, count: int = 50) -> list[dict]:
        """Devuelve las últimas N velas de 5m para análisis técnico."""
        return list(self.klines_5m)[-count:]

    def get_latest_klines_1m(self, count: int = 30) -> list[dict]:
        """Devuelve las últimas N velas de 1m."""
        return list(self.klines_1m)[-count:]

    def get_aggression_snapshot(self) -> dict:
        """Snapshot de agresión actual."""
        total = self.aggression.buy_volume + self.aggression.sell_volume
        return {
            "buy_volume": self.aggression.buy_volume,
            "sell_volume": self.aggression.sell_volume,
            "net_delta": self.aggression.net_delta,
            "imbalance": self.aggression.buy_volume / max(total, 0.001),
            "burst": self.aggression.burst_detected,
            "burst_side": self.aggression.burst_side,
            "largest_trade": self.aggression.largest_trade,
            "tick_count": len(self.tick_buffer),
        }

    async def start(self):
        """Inicia los streams WebSocket."""
        self.is_running = True
        logger.info(f"AGENTE OJOS iniciando streams para {self.symbol.upper()}...")

        # Lanzar 3 streams en paralelo
        await asyncio.gather(
            self._connect_websocket(
                f"wss://fstream.binance.com/ws/{self.symbol}@aggTrade",
                self._handle_agg_trade,
            ),
            self._connect_websocket(
                f"wss://fstream.binance.com/ws/{self.symbol}@kline_1m",
                self._handle_kline_1m,
            ),
            self._connect_websocket(
                f"wss://fstream.binance.com/ws/{self.symbol}@kline_5m",
                self._handle_kline_5m,
            ),
        )

    async def stop(self):
        """Detiene todos los streams."""
        self.is_running = False
        logger.info("AGENTE OJOS detenido.")


# ─── Protocolo WebSocket mínimo (sin dependencia externa) ────────────────────────

class _WebSocketClientProtocol(asyncio.Protocol):
    """Protocolo asyncio mínimo para WebSocket sobre TCP+TLS."""

    def __init__(self, message_handler: Callable[[dict], Awaitable[None]]):
        self._handler = message_handler
        self._buffer = b""
        self._transport = None

    def connection_made(self, transport):
        self._transport = transport
        # Enviar handshake WebSocket
        # (En producción usaríamos websockets library, esto es skeleton mínimo)
        logger.debug("Conexión TCP establecida")

    def data_received(self, data):
        try:
            # Asumimos JSON lines (simplificado para el skeleton)
            for line in data.decode("utf-8", errors="ignore").split("\n"):
                line = line.strip()
                if line:
                    msg = json.loads(line)
                    asyncio.create_task(self._handler(msg))
        except Exception:
            pass

    def connection_lost(self, exc):
        logger.debug(f"Conexión cerrada: {exc}")