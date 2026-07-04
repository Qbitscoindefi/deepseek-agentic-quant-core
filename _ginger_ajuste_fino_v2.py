#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ginger — Ajuste Fino v2.0
==========================
Cambios quirurgicos para la apertura de Nueva York.

1. Transicion RANGE→TREND mas temprana (ADX 22 en vez de 18)
   - El ADX promedio nocturno fue 21.25: el motor estaba en rango
     cuando el mercado ya tenia tendencia incipiente.

2. RANGE_ATR_DISTANCE_MAX como penalizacion gradual, no muro
   - Se anade RANGE_ATR_PENALTY para que el motor pueda entrar
     si la confianza supera el umbral + penalizacion.
"""

import re

ENGINE_FILE = r"C:\OPENBRIDGE\BINANCE\trading_engine.py"

def aplicar():
    with open(ENGINE_FILE, 'r', encoding='utf-8') as f:
        content = f.read()
    
    cambios = 0
    
    # =========================================
    # CAMBIO 1: RANGE_ADX_MAX = 18 -> 22
    # Permite que el modo rango persista un poco mas
    # dando tiempo a que la tendencia se confirme
    # =========================================
    old = 'RANGE_ADX_MAX = 18.0'
    new = 'RANGE_ADX_MAX = 22.0'
    if old in content:
        content = content.replace(old, new)
        cambios += 1
        print(f'[OK] {old} -> {new}')
    else:
        print(f'[--] {old} no encontrado (quizas ya ajustado)')
    
    # =========================================
    # CAMBIO 2: Añadir constante RANGE_ATR_PENALTY
    # Penalizacion gradual en vez de muro duro
    # =========================================
    # Buscar donde estan las constantes de rango
    idx = content.find('RANGE_ATR_DISTANCE_MAX = 0.8')
    if idx >= 0:
        # Ver si ya existe RANGE_ATR_PENALTY
        if 'RANGE_ATR_PENALTY' not in content:
            # Insertar despues de RANGE_ATR_DISTANCE_MAX
            line_end = content.find('\n', idx)
            insert = content[line_end:]
            new_line = f'{content[idx:line_end]}'
            content = content[:line_end] + f'\nRANGE_ATR_PENALTY = 0.08    # Penalizacion por distancia ATR (gradual, no muro)' + insert
            cambios += 1
            print(f'[OK] RANGE_ATR_PENALTY = 0.08 anadido')
        else:
            print(f'[--] RANGE_ATR_PENALTY ya existe')
    else:
        print(f'[--] RANGE_ATR_DISTANCE_MAX no encontrado')
    
    # =========================================
    # CAMBIO 3: Modificar la logica de RANGE_ATR_DISTANCE_MAX
    # Donde dice:
    #   if atr_distance > RANGE_ATR_DISTANCE_MAX:
    #       return result('NEUTRAL', 0, f'RANGE_ATR_DIST:{atr_distance:.2f}>{RANGE_ATR_DISTANCE_MAX}')
    # 
    # Cambiar a penalizacion gradual:
    #   if atr_distance > RANGE_ATR_DISTANCE_MAX and confidence < RANGE_CONFIDENCE_THRESHOLD + RANGE_ATR_PENALTY:
    #       [penalizacion en confianza en vez de bloqueo]
    # =========================================
    
    old_block = """            # Aplicar filtro de distancia ATR desde la media
            if atr_distance > RANGE_ATR_DISTANCE_MAX:
                return result('NEUTRAL', 0, f'RANGE_ATR_DIST:{atr_distance:.2f}>{RANGE_ATR_DISTANCE_MAX}')"""
    
    new_block = """            # Aplicar filtro de distancia ATR desde la media (gradual)
            if atr_distance > RANGE_ATR_DISTANCE_MAX:
                # Penalizar confianza gradualmente en vez de bloquear
                if is_range_mode:
                    # En modo rango, la penalizacion reduce confianza pero no bloquea
                    distance_penalty = min(0.15, (atr_distance - RANGE_ATR_DISTANCE_MAX) * 0.5)
                    long_confidence = max(0.0, long_confidence - distance_penalty)
                    short_confidence = max(0.0, short_confidence - distance_penalty)
                    # Solo bloquear si la penalizacion deja la confianza muy baja
                    if long_confidence < RANGE_CONFIDENCE_THRESHOLD - 0.10 and short_confidence < RANGE_CONFIDENCE_THRESHOLD - 0.10:
                        return result('NEUTRAL', 0, f'RANGE_ATR_DIST:{atr_distance:.2f}>{RANGE_ATR_DISTANCE_MAX}')"""
    
    if old_block in content:
        content = content.replace(old_block, new_block)
        cambios += 1
        print(f'[OK] Logica de distancia ATR cambiada a penalizacion gradual')
    else:
        print(f'[--] Bloque de distancia ATR no encontrado exactamente')
    
    # =========================================
    # GUARDAR
    # =========================================
    if cambios > 0:
        with open(ENGINE_FILE, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f'\n>>> {cambios} cambios aplicados. Motor ajustado para NY.')
    else:
        print('\n>>> Sin cambios.')

if __name__ == '__main__':
    aplicar()
