#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Snapshot del mercado ahora mismo - para Jox"""
import requests

try:
    r = requests.get('https://fapi.binance.com/fapi/v1/ticker/24hr?symbol=BTCUSDT', timeout=5)
    t = r.json()
    print('=== BTC/USDT FUTURES - AHORA ===')
    print(f'Precio:         ${float(t["lastPrice"]):,.2f}')
    print(f'Cambio 24h:     {float(t["priceChangePercent"]):+.2f}%')
    print(f'Max 24h:        ${float(t["highPrice"]):,.2f}')
    print(f'Min 24h:        ${float(t["lowPrice"]):,.2f}')
    print(f'Vol 24h:        {float(t["volume"]):,.0f} BTC')
    print(f'Vol USD:        ${float(t["quoteVolume"]):,.0f}')
except Exception as e:
    print(f'Error ticker: {e}')

print()

try:
    r = requests.get('https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1', timeout=5)
    data = r.json()
    if data:
        fr = float(data[0]['fundingRate'])
        print(f'Funding rate:         {fr*100:.4f}%')
        if fr < -0.003:
            print('  >> Cortos financiando largos = presion ALCISTA latente')
        elif fr > 0.003:
            print('  >> Largos financiando cortos = presion BAJISTA latente')
        else:
            print('  >> Funding neutro, sin presion direccional')
except Exception as e:
    print(f'Error funding: {e}')

print()

try:
    r = requests.get('https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT', timeout=5)
    oi = r.json()
    oi_val = float(oi['openInterest'])
    # Obtener precio para calcular USD
    r2 = requests.get('https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT', timeout=5)
    price = float(r2.json()['price'])
    print(f'Open Interest:         {oi_val:,.0f} BTC')
    print(f'Open Interest (USD):   ${oi_val * price:,.0f}')
except Exception as e:
    print(f'Error OI: {e}')

print()

try:
    r = requests.get('https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=24', timeout=5)
    klines = r.json()
    closes = [float(k[4]) for k in klines]
    high = max(float(k[2]) for k in klines)
    low = min(float(k[3]) for k in klines)
    current = closes[-1]
    range_pct = ((high - low) / low) * 100
    
    print(f'RANGO 24H (horas):')
    print(f'  Alto:   ${high:,.2f}')
    print(f'  Bajo:   ${low:,.2f}')
    print(f'  Rango:  {range_pct:.2f}%')
    
    if range_pct < 1.5:
        print(f'  >> RANGO ESTRECHO (<1.5%) - modo rango activo')
    else:
        print(f'  >> RANGO NORMAL - modo tendencia disponible')
        
    # Ver si el precio esta cerca del max o min
    if current > high * 0.95:
        print(f'  >> Precio cerca del MAX 24h - posible ruptura o rechazo')
    elif current < low * 1.05:
        print(f'  >> Precio cerca del MIN 24h - posible soporte o ruptura')
    else:
        print(f'  >> Precio en medio del rango - lateralizacion')
        
except Exception as e:
    print(f'Error klines: {e}')

print()
print('===========================================')
print('Domingo noche: liquidez baja, spreads altos.')
print('El Francotirador esta en modo rango.') 
print('Analizando, no forzando entradas.') if range_pct < 1.5 else print('Buscando tendencia.')
