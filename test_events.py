import asyncio, websockets, json
async def test():
    async with websockets.connect('wss://fstream.binance.com/stream?streams=btcusdt@aggTrade') as ws:
        msg = await ws.recv()
        print(msg)
asyncio.run(test())
