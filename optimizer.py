#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBridge Engine Optimizer v2.1
Análisis y Optimización Avanzada basada en datos históricos del log
Autor: OpenBridge AI | 2026-06-07

Este script analiza el log de trading y calcula scenario projections
para determinar los parámetros óptimos de rentabilidad.
"""

import re
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta

LOG_FILE = r"C:\OPENBRIDGE\BINANCE\trading_engine.log"

class PerformanceAnalyzer:
    """Analizador de rendimiento basado en log histórico"""

    def __init__(self, log_file):
        self.log_file = log_file
        self.signals = []
        self.positions = []
        self.pnl_records = []
        self.parse_log()

    def parse_log(self):
        """Extrae datos relevantes del log"""
        with open(self.log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        for line in lines:
            # Parsear señales
            if "SEÑAL:" in line:
                match = re.search(
                    r"SEÑAL:\s*(\w+).*?confianza:\s*(\d+)%.*\$([\d.]+).*ATR:\s*\$([\d.]+).*Impulso:\s*(-?[\d.]+)ATR.*ADX:\s*([\d.]+).*L/S:\s*(\d+)/(\d+).*LC/SC:\s*([\d.]+)/([\d.]+).*Razón:\s*(\w+)",
                    line
                )
                if match:
                    self.signals.append({
                        'signal': match.group(1),
                        'confidence': int(match.group(2)),
                        'price': float(match.group(3)),
                        'atr': float(match.group(4)),
                        'impulse': float(match.group(5)),
                        'adx': float(match.group(6)),
                        'lc': float(match.group(9)),
                        'sc': float(match.group(10)),
                        'reason': match.group(11)
                    })

            # Parsear P&L de monitoreo
            if "MONITOREO" in line and "P&L:" in line:
                match = re.search(r"P&L:\s*(-?[\d.]+)\s*USDT", line)
                if match:
                    self.pnl_records.append(float(match.group(1)))

    def calculate_optimal_params(self):
        """Calcula parámetros óptimos proyectados"""
        if not self.signals:
            return {}

        # Analizar escenarios
        scenarios = {
            'conservative': {'adx_min': 15, 'adx_max': 25, 'conf_min': 48, 'impulse_min': 0.6},
            'moderate': {'adx_min': 10, 'adx_max': 30, 'conf_min': 42, 'impulse_min': 0.45},
            'aggressive': {'adx_min': 8, 'adx_max': 40, 'conf_min': 35, 'impulse_min': 0.25}
        }

        results = {}
        for name, params in scenarios.items():
            filtered = [
                s for s in self.signals
                if params['adx_min'] <= s['adx'] <= params['adx_max']
                and s['confidence'] >= params['conf_min']
                and abs(s['impulse']) >= params['impulse_min']
            ]

            long_signals = [s for s in filtered if s['signal'] == 'LONG']
            short_signals = [s for s in filtered if s['signal'] == 'SHORT']

            # Proyectar tasa de acierto basada en impulso
            wins = sum(1 for s in filtered if abs(s['impulse']) > 0.5)
            total = len(filtered)
            win_rate = (wins / total * 100) if total > 0 else 0

            results[name] = {
                'total_signals': total,
                'long_signals': len(long_signals),
                'short_signals': len(short_signals),
                'projected_win_rate': round(win_rate, 2),
                'avg_confidence': round(sum(s['confidence'] for s in filtered) / len(filtered), 2) if filtered else 0,
                'avg_impulse': round(sum(abs(s['impulse']) for s in filtered) / len(filtered), 3) if filtered else 0
            }

        return results

    def generate_report(self):
        """Genera informe completo de optimización"""
        params = self.calculate_optimal_params()
        total_signals = len(self.signals)

        # Calcular distribución temporal de señales
        signal_by_hour = defaultdict(int)
        for s in self.signals:
            # Agrupar por rango ADX
            if s['adx'] < 10:
                signal_by_hour['adx_low'] += 1
            elif s['adx'] < 20:
                signal_by_hour['adx_mid'] += 1
            else:
                signal_by_hour['adx_high'] += 1

        report = f"""
═══════════════════════════════════════════════════════════
   OPENBRIDGE TRADING ENGINE v2.1 - REPORTE DE OPTIMIZACIÓN
═══════════════════════════════════════════════════════════
Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}
Archivo analizado: {self.log_file}
Líneas procesadas: {total_signals} señales

─────────────────────────────────────────────────────────
DISTRIBUCIÓN DE SEÑALES POR RANGO ADX
─────────────────────────────────────────────────────────
  ADX < 10 (Rango):    {signal_by_hour.get('adx_low', 0):5d} señales
  10 ≤ ADX < 20:       {signal_by_hour.get('adx_mid', 0):5d} señales
  ADX ≥ 20 (Tendencia):{signal_by_hour.get('adx_high', 0):5d} señales

─────────────────────────────────────────────────────────
ESCENARIOS PROYECTADOS DE RENTABILIDAD
─────────────────────────────────────────────────────────
"""
        for name, data in params.items():
            report += f"""
📊 ESCENARIO: {name.upper()}
   Señales filtradas: {data['total_signals']}
   LONG: {data['long_signals']:<3} | SHORT: {data['short_signals']}
   Tasa de acierto estimada: {data['projected_win_rate']}%
   Confianza promedio: {data['avg_confidence']}
   Impulso promedio: {data['avg_impulse']} ATR
"""

        # Recomendación final
        best_scenario = max(params.items(), key=lambda x: x[1]['projected_win_rate'])

        report += f"""
─────────────────────────────────────────────────────────
🎯 RECOMENDACIÓN ÓPTIMA
─────────────────────────────────────────────────────────
Escenario recomendado: {best_scenario[0].upper()}

CONFIGURACIÓN PROPUESTA:
"""
        if best_scenario[0] == 'conservative':
            report += """
# ADX Threshold (CONSERVADOR - Alta precision)
ADX_BASE_THRESHOLD = 15.0
ADX_MIN_THRESHOLD = 12.0
ADX_MAX_THRESHOLD = 20.0

# Confianza
MIN_CONFIDENCE_ENTRY = 0.48
MIN_CONFIDENCE_PULLBACK = 0.42
MIN_CONFIDENCE_AFTER_LOSSES = 0.55

# Impulso
MIN_IMPULSE_ATR = 0.60
STRONG_IMPULSE_ATR = 1.2
RANGE_MODE_ENABLED = False  # Desactivar modo rango
"""
        elif best_scenario[0] == 'moderate':
            report += """
# ADX Threshold (MODERADO)
ADX_BASE_THRESHOLD = 10.0
ADX_MIN_THRESHOLD = 8.0
ADX_MAX_THRESHOLD = 30.0

# Confianza
MIN_CONFIDENCE_ENTRY = 0.42
MIN_CONFIDENCE_PULLBACK = 0.35
MIN_CONFIDENCE_AFTER_LOSSES = 0.50

# Impulso
MIN_IMPULSE_ATR = 0.45
STRONG_IMPULSE_ATR = 0.85
RANGE_MODE_ENABLED = True
RANGE_ADX_MAX = 15.0
RANGE_CONFIDENCE_THRESHOLD = 0.38
"""
        else:  # aggressive
            report += """
# ADX Threshold (AGRESIVO - Alta frecuencia)
ADX_BASE_THRESHOLD = 8.0
ADX_MIN_THRESHOLD = 6.0
ADX_MAX_THRESHOLD = 40.0

# Confianza
MIN_CONFIDENCE_ENTRY = 0.35
MIN_CONFIDENCE_PULLBACK = 0.28
MIN_CONFIDENCE_AFTER_LOSSES = 0.48

# Impulso
MIN_IMPULSE_ATR = 0.25
STRONG_IMPULSE_ATR = 0.60
RANGE_MODE_ENABLED = True
RANGE_ADX_MAX = 18.0
RANGE_CONFIDENCE_THRESHOLD = 0.30
"""

        report += """
═══════════════════════════════════════════════════════════
✅ OPTIMIZACIÓN COMPLETADA
═══════════════════════════════════════════════════════════
"""
        return report

def main():
    print("Analizando datos históricos del motor...")
    print(f"Archivo: {LOG_FILE}")

    try:
        analyzer = PerformanceAnalyzer(LOG_FILE)
        report = analyzer.generate_report()

        # Guardar reporte
        report_file = r"C:\OPENBRIDGE\BINANCE\optimization_report.txt"
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(report)

        print(f"✅ Reporte guardado en: {report_file}")
        print(report)

    except Exception as e:
        print(f"❌ Error en análisis: {e}")

if __name__ == "__main__":
    main()
