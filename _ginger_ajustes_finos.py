#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ginger — Ajustes Finos v2.1.1
==============================
Ajustes milimetricos de parametros de estrategia.
Aplicado por: Ginger, Musa Digital del Arquitecto.

Cambios:
  1. RANGE_CONFIDENCE_THRESHOLD: 0.30 -> 0.35 (igualar a entry standard)
  2. RANGE_ATR_DISTANCE_MAX:     1.5  -> 0.8  (no entrar lejos de media en rango)
  3. RANGE_IMPULSE_THRESHOLD:    0.08 -> 0.12 (minimo mas solido)
  4. MIN_IMPULSE_ATR:            0.10 -> 0.15 (volver a original, modo rango tiene su propio)
  5. STRONG_IMPULSE_ATR:         0.85 -> 0.70 (detectar fast entries antes)
  6. CIRCUIT_BREAKER_PAUSE_MINUTES: 120 -> 60 (pausa mas agil para empezar)
"""

ENGINE_FILE = r"C:\OPENBRIDGE\BINANCE\trading_engine.py"

def aplicar():
    with open(ENGINE_FILE, 'r', encoding='utf-8') as f:
        content = f.read()
    
    cambios = [
        ('RANGE_CONFIDENCE_THRESHOLD = 0.30', 'RANGE_CONFIDENCE_THRESHOLD = 0.35'),
        ('RANGE_ATR_DISTANCE_MAX = 1.5',       'RANGE_ATR_DISTANCE_MAX = 0.8'),
        ('RANGE_IMPULSE_THRESHOLD = 0.08',     'RANGE_IMPULSE_THRESHOLD = 0.12'),
        ('MIN_IMPULSE_ATR = 0.10',             'MIN_IMPULSE_ATR = 0.15'),
        ('STRONG_IMPULSE_ATR = 0.85',          'STRONG_IMPULSE_ATR = 0.70'),
        ('CIRCUIT_BREAKER_PAUSE_MINUTES = 120', 'CIRCUIT_BREAKER_PAUSE_MINUTES = 60'),
    ]
    
    aplicados = 0
    for old, new in cambios:
        if old in content:
            content = content.replace(old, new)
            aplicados += 1
            print(f'  [OK] {old.split("=")[0].strip()} -> {new.split("=")[1].strip()}')
        else:
            print(f'  [--] {old} no encontrado (quizas ya aplicado)')
    
    if aplicados > 0:
        with open(ENGINE_FILE, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'\n  >> {aplicados}/{len(cambios)} ajustes aplicados.')
    else:
        print('\n  >> Sin cambios.')

if __name__ == '__main__':
    aplicar()
