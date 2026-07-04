#!/usr/bin/env python3
"""
OpenBridge BTC/USDT Market Analyzer v1.1
Conecta con Binance API para analisis tecnico, fundamental y on-chain de BTC/USDT.
Solo lectura. No ejecuta ordenes sin autorizacion previa explicita.
"""

import os
import sys
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode
from datetime import datetime

# --- Configuracion ---
BASE_REST = 'https://api.binance.com'
BASE_FUTURES = 'https://fapi.binance.com'
ENV_PATH = r"C:\OPENBRIDGE\BINANCE\.env"

# --- Lectura de credenciales ---
def get_keys():
    api_key, api_secret = "", ""
    if not os.path.isfile(ENV_PATH):
        print(f"[ERROR] Archivo .env no encontrado en {ENV_PATH}")
        return api_key, api_secret
    with open(ENV_PATH, "r") as f:
        for line in f:
            if "=" in line:
                key, val = line.strip().split("=", 1)
                if key == "BINANCE_API_KEY":
                    api_key = val
                elif key == "BINANCE_API_SECRET":
                    api_secret = val
    return api_key, api_secret

# --- Firma HMAC-SHA256 para endpoints privados ---
def signed_request(base_url, method, endpoint, params, api_key, api_secret):
    params['timestamp'] = int(time.time() * 1000)
    query_string = urlencode(params)
    signature = hmac.new(
        api_secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    query_string += f"&signature={signature}"
    url = f"{base_url}{endpoint}?{query_string}"
    headers = {'X-MBX-APIKEY': api_key}
    response = getattr(requests, method.lower())(url, headers=headers)
    return response.json(), response.status_code

# --- Request publico (no firma) ---
def public_request(base_url, endpoint, params=None):
    if params is None:
        params = {}
    url = f"{base_url}{endpoint}"
    response = requests.get(url, params=params)
    return response.json(), response.status_code

# --- Test de conectividad ---
def test_connection(api_key, api_secret):
    print("=" * 60)
    print("[TEST] CONECTIVIDAD BINANCE API")
    print("=" * 60)

    # Test 1: Conexion publica (ticker)
    data, status = public_request(BASE_REST, "/api/v3/ticker/price", {"symbol": "BTCUSDT"})
    if status == 200:
        print(f"[OK] API Publica: CONECTADA | BTC/USDT Price: {data.get('price', 'N/A')}")
    else:
        print(f"[FAIL] API Publica: FALLA (status {status})")

    # Test 2: Conexion privada (account)
    data, status = signed_request(BASE_REST, 'GET', '/api/v3/account', {}, api_key, api_secret)
    if status == 200:
        print(f"[OK] API Privada: CONECTADA | AccountType: {data.get('accountType', 'N/A')}")
        can_trade = data.get('canTrade', False)
        print(f"       CanTrade: {can_trade}")
    else:
        print(f"[FAIL] API Privada: FALLA (status {status}) - {data}")

    return status == 200

# --- Obtener snapshot del mercado BTC/USDT ---
def get_market_snapshot():
    print("\n" + "=" * 60)
    print("[SNAPSHOT] MERCADO BTC/USDT SPOT")
    print("=" * 60)

    # Precio actual
    data, _ = public_request(BASE_REST, "/api/v3/ticker/price", {"symbol": "BTCUSDT"})
    price = float(data.get('price', 0))
    print(f"[PRECIO] BTC/USDT Spot: ${price:,.2f}")

    # Ticker 24h
    data, _ = public_request(BASE_REST, "/api/v3/ticker/24hr", {"symbol": "BTCUSDT"})
    if data:
        change = float(data.get('priceChangePercent', 0))
        high = float(data.get('highPrice', 0))
        low = float(data.get('lowPrice', 0))
        volume = float(data.get('volume', 0))
        quote_volume = float(data.get('quoteVolume', 0))
        weighted_avg = float(data.get('weightedAvgPrice', 0))

        print(f"[24H] Cambio: {change:+.2f}%")
        print(f"[24H] High: ${high:,.2f} | Low: ${low:,.2f}")
        print(f"[24H] Volumen: {volume:,.4f} BTC (${quote_volume:,.2f} USDT)")
        print(f"[24H] VWAP: ${weighted_avg:,.2f}")
        print(f"[24H] Trades: {data.get('count', 'N/A')}")

    # Order Book (top 5)
    data, _ = public_request(BASE_REST, "/api/v3/depth", {"symbol": "BTCUSDT", "limit": 5})
    if data and 'asks' in data and 'bids' in data:
        asks = data['asks']
        bids = data['bids']
        print("\n[ORDER BOOK] Top 5 niveles:")
        print("   ASKS (Venta)            | BIDS (Compra)")
        for i in range(5):
            ask_str = f"${float(asks[i][0]):>12,.2f} x {float(asks[i][1]):.4f}" if i < len(asks) else " " * 28
            bid_str = f"${float(bids[i][0]):>12,.2f} x {float(bids[i][1]):.4f}" if i < len(bids) else ""
            print(f"   {ask_str} | {bid_str}")

    return price

# --- Datos de Futures BTCUSDT ---
def get_futures_data():
    print("\n" + "=" * 60)
    print("[FUTURES] BTCUSDT PERPETUAL")
    print("=" * 60)

    # Precio de mark y funding
    data, _ = public_request(BASE_FUTURES, "/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"})
    if data:
        mark_price = float(data.get('markPrice', 0))
        funding_rate = float(data.get('lastFundingRate', 0))
        index_price = float(data.get('indexPrice', 0))
        next_funding = datetime.fromtimestamp(data.get('nextFundingTime', 0) / 1000)

        print(f"[FUT] Mark Price: ${mark_price:,.2f}")
        print(f"[FUT] Index Price: ${index_price:,.2f}")
        print(f"[FUT] Funding Rate: {funding_rate:.6f} ({funding_rate*100:.4f}%)")
        print(f"[FUT] Proximo funding: {next_funding.strftime('%Y-%m-%d %H:%M:%S')}")

        # Spread vs spot
        spot_data, _ = public_request(BASE_REST, "/api/v3/ticker/price", {"symbol": "BTCUSDT"})
        if spot_data:
            spot_price = float(spot_data.get('price', 0))
            if spot_price > 0:
                spread = ((mark_price - spot_price) / spot_price) * 100
                print(f"\n[SPREAD] Spot-Futures: {spread:+.4f}%")
                if spread > 0.1:
                    print("         [ALERTA] Premium positivo alto - posible sobrecompra")
                elif spread < -0.1:
                    print("         [ALERTA] Premium negativo - posible presion bajista")

    # Open Interest
    oi_data, _ = public_request(BASE_FUTURES, "/fapi/v1/openInterest", {"symbol": "BTCUSDT"})
    if oi_data:
        oi = float(oi_data.get('openInterest', 0))
        print(f"\n[OI] Open Interest: {oi:,.4f} BTC")
        print(f"[OI] Valor aprox: ${oi * mark_price:,.2f} USDT")

    # Funding history
    funding_data, _ = public_request(BASE_FUTURES, "/fapi/v1/fundingRate", {
        "symbol": "BTCUSDT",
        "limit": 3
    })
    if funding_data and len(funding_data) > 0:
        print("\n[FUNDING] Historial reciente:")
        for f in funding_data[:3]:
            rate = float(f.get('fundingRate', 0))
            time_str = datetime.fromtimestamp(f.get('fundingTime', 0) / 1000).strftime('%Y-%m-%d %H:%M')
            print(f"         {time_str}: {rate:.6f} ({rate*100:.4f}%)")

# --- Velas recientes (Klines) ---
def get_klines(interval="1h", limit=24):
    print(f"\n[KLINES] Velas {interval} (ultimas {limit}):")
    data, _ = public_request(BASE_REST, "/api/v3/klines", {
        "symbol": "BTCUSDT",
        "interval": interval,
        "limit": limit
    })
    if data and len(data) > 0:
        latest = data[-1]
        prev = data[-2] if len(data) > 1 else data[-1]

        open_p = float(latest[1])
        high_p = float(latest[2])
        low_p = float(latest[3])
        close_p = float(latest[4])
        volume = float(latest[5])

        prev_close = float(prev[4])
        change_pct = ((close_p - prev_close) / prev_close) * 100 if prev_close > 0 else 0

        print(f"  [Ultima] O=${open_p:,.2f} H=${high_p:,.2f} L=${low_p:,.2f} C=${close_p:,.2f}")
        print(f"  [Vol] {volume:,.4f} BTC | Cambio: {change_pct:+.2f}%")

        # Estadisticas del rango de velas
        highs = [float(c[2]) for c in data]
        lows = [float(c[3]) for c in data]
        print(f"  [Rango {interval}] Max-high: ${max(highs):,.2f} | Min-low: ${min(lows):,.2f}")

    return data if data else None

# --- Analisis tecnico basico ---
def technical_analysis():
    print("\n" + "=" * 60)
    print("[ANALISIS TECNICO] BTC/USDT")
    print("=" * 60)

    # Medias moviles simples con velas diarias
    data, _ = public_request(BASE_REST, "/api/v3/klines", {
        "symbol": "BTCUSDT",
        "interval": "1d",
        "limit": 50
    })

    if data and len(data) >= 20:
        closes = [float(c[4]) for c in data]

        # SMA 7, 20, 50
        sma7 = sum(closes[-7:]) / 7
        sma20 = sum(closes[-20:]) / 20
        sma50 = sum(closes) / len(closes) if len(closes) >= 50 else None

        current = closes[-1]

        print(f"[TECNICO] Precio actual: ${current:,.2f}")
        print(f"[TECNICO] SMA(7):  ${sma7:,.2f}")
        print(f"[TECNICO] SMA(20): ${sma20:,.2f}")
        if sma50:
            print(f"[TECNICO] SMA(50): ${sma50:,.2f}")

        # Tendencia
        if current > sma7 > sma20:
            print("[SEÑAL] Tendencia ALCISTA (precio > SMA7 > SMA20)")
        elif current < sma7 < sma20:
            print("[SEÑAL] Tendencia BAJISTA (precio < SMA7 < SMA20)")
        else:
            print("[SEÑAL] Tendencia MIXTA / Consolidacion")

        # Soportes y resistencias recientes
        recent_lows = [float(c[3]) for c in data[-20:]]
        recent_highs = [float(c[2]) for c in data[-20:]]
        support = min(recent_lows)
        resistance = max(recent_highs)

        print(f"\n[NIVELES] Soporte reciente (20d): ${support:,.2f}")
        print(f"[NIVELES] Resistencia reciente (20d): ${resistance:,.2f}")

        # Distancia a niveles
        dist_support = ((current - support) / support) * 100
        dist_resistance = ((resistance - current) / current) * 100
        print(f"[NIVELES] Distancia a soporte: {dist_support:+.2f}%")
        print(f"[NIVELES] Distancia a resistencia: {dist_resistance:+.2f}%")

# --- MAIN ---
if __name__ == '__main__':
    print("=" * 60)
    print("[O] OPENBRIDGE BTC/USDT MARKET ANALYZER v1.1")
    print("=" * 60)

    api_key, api_secret = get_keys()
    if not api_key or not api_secret:
        print("[FATAL] No se encontraron credenciales validas. Abortando.")
        sys.exit(1)

    # Test de conexion
    connected = test_connection(api_key, api_secret)
    if not connected:
        print("\n[ERROR] No se pudo establecer conexion con Binance API.")
        sys.exit(1)

    # Obtener datos de mercado
    spot_price = get_market_snapshot()
    get_futures_data()

    # Velas en diferentes timeframes
    get_klines("1h", 24)
    get_klines("4h", 7)
    get_klines("1d", 7)

    # Analisis tecnico
    technical_analysis()

    print("\n" + "=" * 60)
    print("[OK] ANALISIS INICIAL COMPLETADO")
    print("=" * 60)
    print("\n[REMINDER] Solo lectura. Para operar, autorizacion explicita requerida.")
