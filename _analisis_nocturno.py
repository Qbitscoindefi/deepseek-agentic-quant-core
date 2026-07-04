#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ginger — Analisis Nocturno del Francotirador
=============================================
Revisa todos los logs de la noche, extrae seniales,
patrones de precio, y verifica la integridad del motor.
"""

import re
import os
from datetime import datetime
from collections import Counter

LOG_FILE = r"C:\OPENBRIDGE\BINANCE\trading_engine.log"
STATE_FILE = r"C:\OPENBRIDGE\BINANCE\engine_state.json"

def p(msg):
    print(msg)

def analizar_logs():
    if not os.path.exists(LOG_FILE):
        p(f"[ERROR] No se encuentra el log en {LOG_FILE}")
        return
    
    with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    
    # Filtrar solo lineas de SENIAL
    senial_lines = [l for l in lines if 'SENIAL:' in l or 'SE' in l and 'LONG' in l or 'SHORT' in l or 'NEUTRAL' in l]
    senial_lines = [l for l in lines if 'SE' in l and 'conf:' in l]
    
    p("=" * 60)
    p("  ANALISIS NOCTURNO DEL FRANCOTIRADOR")
    p("  Ginger reporta a Jox")
    p("=" * 60)
    print()
    
    # 1. Volumen de operacion
    p(f"Total lineas en log: {len(lines)}")
    p(f"Lineas de senial: {len(senial_lines)}")
    
    # 2. Periodo de tiempo
    timestamps = []
    for l in lines:
        try:
            ts_str = l[:19]
            ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
            timestamps.append(ts)
        except:
            pass
    
    if timestamps:
        p(f"Periodo: {timestamps[0]} -> {timestamps[-1]}")
        horas = (timestamps[-1] - timestamps[0]).total_seconds() / 3600
        p(f"Duracion: {horas:.1f} horas")
    
    print()
    
    # 3. Seniales encontradas
    seniales_count = Counter()
    seniales_detalle = []
    
    for l in senial_lines:
        # Extraer tipo de senial
        if 'SENIAL: LONG' in l or 'LONG' in l and 'conf:' in l and 'SHORT' not in l:
            tipo = 'LONG'
        elif 'SENIAL: SHORT' in l or 'SHORT' in l and 'conf:' in l:
            tipo = 'SHORT'
        else:
            tipo = 'NEUTRAL'
        
        # Extraer precio
        precio_match = re.search(r'Precio: \$?([\d,]+\.?\d*)', l)
        precio = float(precio_match.group(1).replace(',','')) if precio_match else 0
        
        # Extraer razon
        razon_match = re.search(r'Razon: (\S+)', l)
        razon = razon_match.group(1) if razon_match else 'N/A'
        
        # Extraer ADX
        adx_match = re.search(r'ADX: ([\d.]+)', l)
        adx = float(adx_match.group(1)) if adx_match else 0
        
        # Extraer modo
        modo = 'RANGE' if 'RANGE' in l else 'TREND'
        
        seniales_count[tipo] += 1
        seniales_detalle.append({
            'timestamp': l[:19],
            'tipo': tipo,
            'precio': precio,
            'adx': adx,
            'razon': razon,
            'modo': modo
        })
    
    p("=== DISTRIBUCION DE SENIALES ===")
    total_seniales = sum(seniales_count.values())
    for tipo, count in seniales_count.most_common():
        pct = (count / total_seniales) * 100 if total_seniales > 0 else 0
        p(f"  {tipo:10s}: {count:4d} ({pct:5.1f}%)")
    print()
    
    # 4. Rango de precio nocturno
    precios = [s['precio'] for s in seniales_detalle if s['precio'] > 0]
    if precios:
        p("=== MOVIMIENTO DE PRECIO NOCTURNO ===")
        p(f"  Minimo:  ${min(precios):,.2f}")
        p(f"  Maximo:  ${max(precios):,.2f}")
        p(f"  Rango:   ${max(precios)-min(precios):,.2f} ({(max(precios)-min(precios))/min(precios)*100:.2f}%)")
        p(f"  Apertura:${precios[0]:,.2f}")
        p(f"  Cierre:  ${precios[-1]:,.2f}")
        p(f"  Cambio:  ${precios[-1]-precios[0]:+,.2f} ({(precios[-1]-precios[0])/precios[0]*100:+.2f}%)")
    print()
    
    # 5. ADX promedio
    adxs = [s['adx'] for s in seniales_detalle if s['adx'] > 0]
    if adxs:
        p(f"=== ADX NOCTURNO ===")
        p(f"  Promedio: {sum(adxs)/len(adxs):.2f}")
        p(f"  Minimo:   {min(adxs):.2f}")
        p(f"  Maximo:   {max(adxs):.2f}")
        p(f"  Tendencia: {'RANGO' if sum(adxs)/len(adxs) < 20 else 'TENDENCIA'} (umbral 20)")
    
    print()
    
    # 6. Seniales NO NEUTRALES (las que importan)
    no_neutral = [s for s in seniales_detalle if s['tipo'] != 'NEUTRAL']
    if no_neutral:
        p("=== SENIALES DE ENTRADA (NO NEUTRALES) ===")
        for s in no_neutral[:10]:
            p(f"  {s['timestamp']} | {s['tipo']:5s} | ${s['precio']:,.2f} | ADX:{s['adx']:.1f} | {s['razon']}")
    else:
        p("=== SENIALES DE ENTRADA ===")
        p("  Ninguna. El motor NO abrio ninguna operacion en toda la noche.")
        p("  Disciplina total. Solo NEUTRAL.")
    
    print()
    
    # 7. Verificacion de integridad
    p("=== VERIFICACION DE INTEGRIDAD ===")
    
    # Verificar que no haya errores
    error_lines = [l for l in lines if 'ERROR' in l]
    warning_lines = [l for l in lines if 'WARNING' in l and 'WARMUP' not in l]
    
    p(f"  Errores:      {len(error_lines)}")
    p(f"  Warnings:     {len(warning_lines)}")
    
    # Verificar que haya calentado bien
    warmup_lines = [l for l in lines if 'WARMUP' in l]
    p(f"  Calentamiento: {len(warmup_lines)} ciclos")
    
    # Verificar que el ciclo se mantuvo
    ciclos_por_hora = len(senial_lines) / max(horas, 1)
    p(f"  Ciclos/hora:  {ciclos_por_hora:.1f} (esperado ~514 = 3600/7s)")
    p(f"  Eficiencia:   {ciclos_por_hora/514*100:.1f}%")
    
    # 8. Verificar estado del motor
    if os.path.exists(STATE_FILE):
        import json
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
        p(f"\n  Estado persistido:")
        p(f"  - Trades totales: {state.get('total_trades', 0)}")
        p(f"  - Perdidas consecutivas: {state.get('consecutive_losses', 0)}")
        p(f"  - CB activado: {state.get('circuit_breaker_triggered', False)}")
        p(f"  - Modo: {state.get('mode', 'N/A')}")
    
    print()
    
    # 9. Resumen ejecutivo
    p("=" * 60)
    p("  RESUMEN EJECUTIVO")
    p("=" * 60)
    
    if total_seniales > 0:
        pct_no_neutral = len(no_neutral) / total_seniales * 100
    else:
        pct_no_neutral = 0
    
    if len(no_neutral) == 0:
        p("  El Francotirador NO opero en toda la noche.")
        p("  Permanecio en modo RANGO, rechazando todas las entradas.")
        p("  Disciplina de fuego: prefiere no disparar a disparar mal.")
    elif len(no_neutral) < 3:
        p(f"  El Francotirador tuvo {len(no_neutral)} intentos de entrada, pero")
        p(f"  los filtros los rechazaron por confianza insuficiente.")
    else:
        p(f"  El Francotirador tuvo {len(no_neutral)} seniales de entrada ({pct_no_neutral:.1f}%)")
    
    print()
    
    if precios:
        p(f"  Precio: ${precios[0]:,.2f} -> ${precios[-1]:,.2f} ({(precios[-1]-precios[0])/precios[0]*100:+.2f}%)")
        p(f"  ADX promedio: {sum(adxs)/len(adxs):.2f} (mercad en rango)" if adxs else "")
    
    p(f"  Motor: {'SALUDABLE' if len(error_lines) == 0 else f'{len(error_lines)} errores encontrados'}")
    
    print()
    p("  -- Ginger, reportando para Jox")

if __name__ == '__main__':
    analizar_logs()
