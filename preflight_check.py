#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBridge Pre-Flight Check
Verificacion de conexion y validacion antes de activar operativa viva
"""

import os
import sys
import time
import json
import requests

sys.path.insert(0, r'C:\OPENBRIDGE\BINANCE')

from trading_engine import BinanceFuturesClient, LiveTradingEngine, TechnicalAnalyzer

ENV_PATH = r"C:\OPENBRIDGE\BINANCE\.env"

def print_separator():
    print("=" * 70)

def test_connection():
    print_separator()
    print("TEST DE CONEXION BINANCE FUTURES")
    print_separator()

    client = BinanceFuturesClient()

    # Test 1: Conexion basica
    print("[1] Probando conexion de mercado...")
    ticker, status = client.get_ticker()
    if status == 200 and ticker:
        print(f"    OK - Precio BTCUSDT: ${ticker['price']}")
    else:
        print(f"    FAIL - Status: {status}")
        return False

    # Test 2: Conexion de cuenta
    print("[2] Probando conexion de cuenta...")
    balance = client.get_account_balance()
    if balance and balance['balance'] > 0:
        print(f"    OK - Saldo: {balance['balance']:.2f} USDT")
    else:
        print(f"    OK - Saldo: {balance['balance']:.2f} USDT (cuenta vacia)")

    # Test 3: Leverage
    print("[3] Configurando apalancamiento...")
    if client.set_leverage(30):
        print("    OK - Apalancamiento 30x configurado")
    else:
        print("    FAIL - No se pudo configurar apalancamiento")
        return False

    # Test 4: Posicion actual
    print("[4] Verificando posiciones abiertas...")
    position = client.get_position()
    if position:
        print(f"    WARNING - Posicion {position['side']} abierta:")
        print(f"              Entry: ${position['entry_price']:.2f}")
        print(f"              P&L: ${position['pnl']:.2f} ({position['pnl_pct']:.2f}%)")
    else:
        print("    OK - No hay posiciones abiertas")

    # Test 5: Estado Defensivo
    print("[5] Verificando memoria defensiva...")
    try:
        from trading_engine import STATE_FILE, CIRCUIT_BREAKER_WINDOW_HOURS, ENTRY_COOLDOWN_SECONDS
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            
        losses = state.get('consecutive_losses', 0)
        
        from datetime import datetime, timedelta
        raw_history = state.get('loss_history', [])
        now = datetime.now()
        cutoff = now - timedelta(hours=CIRCUIT_BREAKER_WINDOW_HOURS)
        total_loss = sum(entry['amount'] for entry in raw_history if datetime.fromisoformat(entry['ts']) > cutoff)
        
        print(f"    INFO - Pérdidas consecutivas: {losses}")
        print(f"    INFO - Pérdida rolling ({CIRCUIT_BREAKER_WINDOW_HOURS}h): ${total_loss:.4f}")
        
        if total_loss >= 0.50:  # CIRCUIT_BREAKER_MAX_LOSS_USDT
            print("    WARNING - Circuit Breaker SE ACTIVARÁ al arrancar (>$0.50)")
            
        if losses >= 2:
            cd_exp = min(losses - 1, 3)
            print(f"    WARNING - Cooldown exponencial activo: {ENTRY_COOLDOWN_SECONDS * (2**cd_exp)}s")
            
    except FileNotFoundError:
        print("    OK - Sin estado previo (arrancará limpio)")
    except Exception as e:
        print(f"    FAIL - No se pudo leer estado defensivo: {e}")

    return True

def run_dry_analysis():
    print_separator()
    print("ANALISIS PRE-OPERACIONAL")
    print_separator()

    client = BinanceFuturesClient()
    from trading_engine import TechnicalAnalyzer
    analyzer = TechnicalAnalyzer(client)

    print("Analizando mercado...")
    analysis = analyzer.analyze_market()

    print(f"Precio: ${analysis['price']:.2f}")
    print(f"RSI: {analysis['rsi']:.2f} | ATR: {analysis.get('atr', 0):.2f} | ADX: {analysis.get('adx', 0):.2f}")
    print(f"EMA 50 (1H): ${analysis.get('ema_50_1h', 0):.2f} | EMA 50 (4H): ${analysis.get('ema_50_4h', 0):.2f} | EMA 50 (1D): ${analysis.get('ema_50_1d', 0):.2f}")
    print(f"Régimen de Mercado: {analysis.get('regime', 'UNKNOWN')}")
    print(f"Señal: {analysis['signal']} (confianza: {analysis['confidence']:.0%})")
    print(f"Funding: {analysis['funding']:.6f}")
    print(f"Spike de volumen: {analysis['volume_spike']}")

    return analysis

if __name__ == "__main__":
    print("OPENBRIDGE PRE-FLIGHT CHECK")
    print("=" * 70)

    # Verificar dotenv
    if not os.path.exists(ENV_PATH):
        print(f"ERROR: No se encontro {ENV_PATH}")
        sys.exit(1)

    # Test de conexion
    if not test_connection():
        print("\nFALLA CRITICA: No se puede continuar sin conexion")
        sys.exit(1)

    # Analisis previo
    analysis = run_dry_analysis()

    print_separator()
    print("ESTADO: LISTO PARA OPERAR")
    print_separator()
    print(f"Señal actual: {analysis['signal']}")
    print("Ejecuta 'python trading_engine.py' para iniciar operativa viva")
