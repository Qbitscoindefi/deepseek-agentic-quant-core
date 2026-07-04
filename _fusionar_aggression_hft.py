#!/usr/bin/env python3
"""
Ginger — Fusion de aggression_analyzer en trading_engine_hft.py
================================================================
Reemplaza la dependencia de orderflow_analyzer (OBI/CVD basado en depth)
por aggression_analyzer (aggTrade puro = dinero real).

Cambios:
1. Importa MarketAggressionAnalyzer en vez de BinanceOrderFlowStream
2. Reemplaza self.orderflow por self.aggression
3. Cambia logica de entrada: OBI/CVD -> Imbalance + Burst Detection
4. Mantiene intacta la herencia de LiveTradingEngine y toda su logica
"""

import sys

HFT_FILE = r"C:\OPENBRIDGE\BINANCE\trading_engine_hft.py"

with open(HFT_FILE, 'r', encoding='utf-8') as f:
    content = f.read()

cambios = 0

# ============================================================
# CAMBIO 1: Reemplazar import
# ============================================================
old_import = "from orderflow_analyzer import BinanceOrderFlowStream"
new_import = "from aggression_analyzer import MarketAggressionAnalyzer"

if old_import in content:
    content = content.replace(old_import, new_import)
    cambios += 1
    print(f'[OK] Import reemplazado: orderflow_analyzer -> aggression_analyzer')
else:
    print(f'[--] Import no encontrado exactamente')

# ============================================================
# CAMBIO 2: Reemplazar self.orderflow por self.aggression en __init__
# ============================================================
old_init = """        super().__init__()
        self.orderflow = BinanceOrderFlowStream(symbol="btcusdt")
        self.cvd_threshold = 2.0   # Requiere 2 BTC de presion neta para confirmar
        self.obi_threshold = 0.15  # Maximo 15% de muro en contra permitido"""

new_init = """        super().__init__()
        # AGGRESSION ANALYZER: 100% aggTrade (trades reales ejecutados)
        # NO usa depth/OBI (ordenes pendientes = manipulables por ballenas)
        self.aggression = MarketAggressionAnalyzer(symbol="btcusdt", window_seconds=5.0)
        self.aggression_min_imbalance = 0.70   # Requiere 70% de presion en una direccion
        self.aggression_min_volume = 2.0       # Minimo 2 BTC de volumen agresivo"""

if old_init in content:
    content = content.replace(old_init, new_init)
    cambios += 1
    print(f'[OK] __init__ actualizado: orderflow -> aggression')
else:
    print(f'[--] __init__ no encontrado exactamente')

# ============================================================
# CAMBIO 3: Reemplazar logica de entrada en hft_run_cycle
# ============================================================
old_logic = """            signal = analysis['signal']
            cvd = self.orderflow.cvd
            obi = self.orderflow.obi
            
            mode_str = "RANGE" if analysis.get('is_range_mode') else "TREND"
            logger.info(
                f"SEÑAL: {signal} (conf: {analysis['confidence']:.0%}) | "
                f"HFT -> CVD: {cvd:+.1f} | OBI: {obi:+.2f} | "
                f"Modo: {mode_str} | Razón: {analysis['signal_reason']}"
            )
            
            if signal == 'LONG':
                if cvd > self.cvd_threshold and obi > -self.obi_threshold:
                    logger.warning(f"[!] FUSIÓN HFT LONG: Velas + Ballenas Comprando (CVD:{cvd:+.1f})")
                    # Abrir posición real usando la lógica de LiveTradingEngine
                    await asyncio.to_thread(self.open_position, 'LONG', analysis)
                else:
                    logger.info(f"[BLOCK] HFT: Velas dicen LONG, pero Order Flow no respalda (CVD:{cvd:+.1f}). Abortado.")
            elif signal == 'SHORT':
                if cvd < -self.cvd_threshold and obi < self.obi_threshold:
                    logger.warning(f"[!] FUSIÓN HFT SHORT: Velas + Ballenas Vendiendo (CVD:{cvd:+.1f})")
                    await asyncio.to_thread(self.open_position, 'SHORT', analysis)
                else:
                    logger.info(f"[BLOCK] HFT: Velas dicen SHORT, pero Order Flow no respalda (CVD:{cvd:+.1f}). Abortado.")"""

new_logic = """            signal = analysis['signal']
            
            # AGGRESSION ANALYZER: metricas 100% aggTrade (trades reales)
            agg_metrics = self.aggression.latest_metrics
            imbalance = agg_metrics.imbalance
            total_vol = agg_metrics.total_volume
            
            mode_str = "RANGE" if analysis.get('is_range_mode') else "TREND"
            logger.info(
                f"SEÑAL: {signal} (conf: {analysis['confidence']:.0%}) | "
                f"AGG -> Imbalance: {imbalance:.2f} | Vol: {total_vol:.2f} BTC | "
                f"Modo: {mode_str} | Razón: {analysis['signal_reason']}"
            )
            
            if signal == 'LONG':
                # Confirmacion aggTrade: compradores agresivos dominan
                if imbalance >= self.aggression_min_imbalance and total_vol >= self.aggression_min_volume:
                    logger.warning(f"[!] FUSION aggTrade LONG: Tecnica + Compra Agresiva (Imbalance:{imbalance:.1%})")
                    await asyncio.to_thread(self.open_position, 'LONG', analysis)
                else:
                    logger.info(f"[BLOCK] AGG: Tecnica dice LONG, pero aggTrade no confirma (Imbalance:{imbalance:.1%}, Vol:{total_vol:.2f}). Abortado.")
            elif signal == 'SHORT':
                # Confirmacion aggTrade: vendedores agresivos dominan
                if imbalance <= (1.0 - self.aggression_min_imbalance) and total_vol >= self.aggression_min_volume:
                    logger.warning(f"[!] FUSION aggTrade SHORT: Tecnica + Venta Agresiva (Imbalance:{imbalance:.1%})")
                    await asyncio.to_thread(self.open_position, 'SHORT', analysis)
                else:
                    logger.info(f"[BLOCK] AGG: Tecnica dice SHORT, pero aggTrade no confirma (Imbalance:{imbalance:.1%}, Vol:{total_vol:.2f}). Abortado.")"""

if old_logic in content:
    content = content.replace(old_logic, new_logic)
    cambios += 1
    print(f'[OK] Logica de entrada reemplazada: OBI/CVD -> Imbalance/Volume')
else:
    print(f'[--] Logica de entrada no encontrada exactamente')

# ============================================================
# CAMBIO 4: Reemplazar referencias en run_async
# ============================================================
old_ws = "ws_task = asyncio.create_task(self.orderflow.connect())"
new_ws = "ws_task = asyncio.create_task(self.aggression.connect())"

if old_ws in content:
    content = content.replace(old_ws, new_ws)
    cambios += 1
    print(f'[OK] WebSocket task actualizada: orderflow.connect() -> aggression.connect()')
else:
    print(f'[--] WebSocket task no encontrada')

old_cleanup = "self.orderflow.is_running = False"
new_cleanup = "self.aggression.is_running = False"

if old_cleanup in content:
    content = content.replace(old_cleanup, new_cleanup)
    cambios += 1
    print(f'[OK] Limpieza actualizada: orderflow -> aggression')
else:
    print(f'[--] Limpieza no encontrada')

# ============================================================
# GUARDAR
# ============================================================
if cambios > 0:
    with open(HFT_FILE, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f'\n>>> {cambios} cambios aplicados. trading_engine_hft.py ahora usa aggTrade puro.')
    
    # Verificar sintaxis
    import py_compile
    try:
        py_compile.compile(HFT_FILE, doraise=True)
        print('>>> SINTAXIS OK - Motor compila correctamente!')
    except py_compile.PyCompileError as e:
        print(f'>>> ERROR DE SINTAXIS: {e}')
else:
    print(f'\n>>> Sin cambios aplicados.')
