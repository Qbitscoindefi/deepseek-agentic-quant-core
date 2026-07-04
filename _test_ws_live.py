#!/usr/bin/env python3
"""Test de conexión WebSocket aggTrade LIVE con Binance"""
import asyncio, json, sys, time

try:
    import websockets
    print('[INFO] Test WebSocket Binance aggTrade LIVE')

    async def test_ws():
        uri = 'wss://stream.binance.com:9443/ws/btcusdt@aggTrade'
        print(f'[INFO] URI: {uri}')

        async with websockets.connect(uri, ping_interval=20, ping_timeout=20) as ws:
            print('[OK] WebSocket conectado!')
            trades = []
            start = time.time()

            while time.time() - start < 10:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=15)
                    data = json.loads(msg)
                    trades.append({
                        'price': float(data['p']),
                        'qty': float(data['q']),
                        'm': data['m'],
                        'time': data['T']
                    })
                    if len(trades) % 5 == 0:
                        print(f'[DATA] {len(trades)} trades...', end='\r')
                except asyncio.TimeoutError:
                    print('[WARN] Timeout')
                    break

            print(f'\n[OK] Total: {len(trades)} trades en 10s')
            if trades:
                buy_vol = sum(t['qty'] for t in trades if not t['m'])
                sell_vol = sum(t['qty'] for t in trades if t['m'])
                print(f'  Buy vol: {buy_vol:.3f} BTC')
                print(f'  Sell vol: {sell_vol:.3f} BTC')
                last_price = trades[-1]['price']
                print(f'  Last price: {last_price:.2f} USDT')
                return True
            return False

    result = asyncio.run(test_ws())
    if result:
        print('[PASS] WebSocket aggTrade LIVE funciona OK')
    else:
        print('[FAIL] No se recibieron datos')

except ImportError:
    print('[FAIL] websockets no instalado')
except Exception as e:
    print(f'[FAIL] {type(e).__name__}: {e}')
