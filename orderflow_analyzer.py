import asyncio
import json
import logging
import time
import websockets
from collections import deque

# Configurar logging con colores para diferenciar OBI y CVD
class ColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': '\033[36m',
        'INFO': '\033[32m',
        'WARNING': '\033[33m',
        'ERROR': '\033[31m',
        'CRITICAL': '\033[35m'
    }
    RESET = '\033[0m'
    def format(self, record):
        log_color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{log_color}{record.levelname}{self.RESET}"
        return super().format(record)

logger = logging.getLogger('OrderFlow')
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setFormatter(ColoredFormatter('%(asctime)s [%(levelname)s] %(message)s'))
logger.addHandler(ch)


class BinanceOrderFlowStream:
    def __init__(self, symbol="btcusdt"):
        self.symbol = symbol.lower()
        # Endpoints para Spot (El mercado Spot lidera a Futuros, mejor para CVD)
        self.ws_url = "wss://stream.binance.com:9443"
        
        # Order Book Data
        self.bids = {}
        self.asks = {}
        self.obi = 0.0  # Order Book Imbalance (-1.0 to 1.0)
        
        # Cumulative Volume Delta (CVD)
        self.cvd = 0.0
        self.buy_volume = 0.0
        self.sell_volume = 0.0
        self.recent_trades = deque(maxlen=5000)
        
        self.is_running = False

    def calculate_obi(self, levels=15):
        """Calcula el Order Book Imbalance (OBI) de los N mejores niveles de liquidez"""
        sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:levels]
        sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:levels]
        
        total_bid_vol = sum(vol for price, vol in sorted_bids)
        total_ask_vol = sum(vol for price, vol in sorted_asks)
        
        if total_bid_vol + total_ask_vol > 0:
            # Rango: -1.0 (Absoluta dominancia de Asks) a 1.0 (Absoluta dominancia de Bids)
            self.obi = (total_bid_vol - total_ask_vol) / (total_bid_vol + total_ask_vol)
        else:
            self.obi = 0.0

    def process_depth_message(self, data):
        """Procesa actualización del Order Book (Stream: depthUpdate)"""
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
                
        self.calculate_obi(levels=15)

    def process_trade_message(self, data):
        """Procesa operaciones ejecutadas (aggTrade) para calcular el Delta de Volumen"""
        # q = quantity, m = is_buyer_maker (True = Venta a mercado, False = Compra a mercado)
        qty = float(data['q'])
        is_sell_market_order = data['m'] 
        
        trade_vol = -qty if is_sell_market_order else qty
        self.cvd += trade_vol
        
        if is_sell_market_order:
            self.sell_volume += qty
        else:
            self.buy_volume += qty
            
        self.recent_trades.append({'v': trade_vol, 't': data['T']})

    async def connect(self):
        """Bucle principal de conexión asíncrona a WebSockets"""
        self.is_running = True
        # Suscribirse al stream combinado usando /stream?streams=
        streams = f"{self.symbol}@depth@100ms/{self.symbol}@aggTrade"
        stream_url = f"{self.ws_url}/stream?streams={streams}"
        
        logger.info(f"Iniciando conexión HFT WebSocket -> {stream_url}")
        
        while self.is_running:
            try:
                async with websockets.connect(stream_url, ping_interval=20, ping_timeout=20) as ws:
                    logger.info("Conectado exitosamente al Order Flow de Binance")
                    
                    while self.is_running:
                        msg = await ws.recv()
                        raw_data = json.loads(msg)
                        
                        # Al usar /stream?streams=, Binance envuelve los datos en un objeto con keys 'stream' y 'data'
                        if 'data' in raw_data:
                            data = raw_data['data']
                        else:
                            data = raw_data
                            
                        event_type = data.get('e')
                        if event_type == 'depthUpdate':
                            self.process_depth_message(data)
                        elif event_type == 'aggTrade':
                            self.process_trade_message(data)
                            
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"⚠️ Conexión WebSocket cerrada ({e}). Reconectando en 2s...")
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Error en WebSocket: {e}")
                await asyncio.sleep(5)

    async def monitor(self):
        """Monitor de consola: Imprime estadísticas 1 vez por segundo"""
        # Esperar a que se llenen los datos iniciales
        await asyncio.sleep(3)
        
        while self.is_running:
            # Imprimir estadísticas cada 1 segundo
            cvd_trend = "🟢 COMPRADORES AGRESIVOS" if self.cvd > 0 else "🔴 VENDEDORES AGRESIVOS"
            obi_trend = "🟢 BIDS (Muro Compra)" if self.obi > 0.15 else ("🔴 ASKS (Muro Venta)" if self.obi < -0.15 else "⚪ NEUTRAL")
            
            logger.info(
                f"📊 HFT | OBI: {self.obi:+.2f} [{obi_trend}] | "
                f"CVD Neto: {self.cvd:+.2f} BTC [{cvd_trend}] | "
                f"Vol (Buy/Sell): {self.buy_volume:.1f}/{self.sell_volume:.1f}"
            )
            await asyncio.sleep(1)

async def main():
    stream = BinanceOrderFlowStream("btcusdt")
    
    # Lanzar tareas en background
    task_ws = asyncio.create_task(stream.connect())
    task_monitor = asyncio.create_task(stream.monitor())
    
    try:
        await asyncio.gather(task_ws, task_monitor)
    except KeyboardInterrupt:
        logger.info("\n⏹️ Deteniendo Order Flow Analyzer...")
        stream.is_running = False
        task_ws.cancel()
        task_monitor.cancel()

if __name__ == "__main__":
    # Ejecutar el bucle de eventos de asyncio
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
