"""
OpenBridge Fraud Detector v5.0
Detector de manipulación del mercado (spoofing/distribución).

OBJETIVO: Detectar cuando las ballenas intentan engañar al bot
para que dispare en la dirección equivocada usando el libro de órdenes,
y proteger al motor de trading real frente a estas trampas.

PRINCIPIO CLAVE: Ordenes pendientes (depthUpdate) = INTENCIONES
                     Trades ejecutados (aggTrade) = REALIDAD

Las intenciones se pueden falsificar. La realidad, NO.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class FraudAlert:
    """Alerta de manipulación detectada"""
    timestamp: float
    manipulation_type: str
    severity: str  # 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
    direction: str  # 'BUY', 'SELL'
    description: str
    confidence: float  # 0.0 a 1.0


class MarketFraudDetector:
    """
    Detector de manipulación del mercado basado en discrepancia
    entre intenciones (depth) y acciones reales (aggTrade).
    """

    def __init__(self,
                 symbol: str = "btcusdt",
                 detection_window_seconds: float = 10.0,
                 min_discrepancy_threshold: float = 0.65):

        self.symbol = symbol.lower()
        self.detection_window = detection_window_seconds
        self.min_discrepancy = min_discrepancy_threshold

        # Estado del mercado en tiempo real (solo datos REALES de aggTrade)
        self.real_buy_volume = 0.0   # Compras agresivas REALES (m=False)
        self.real_sell_volume = 0.0  # Ventas agresivas REALES (m=True)
        self.real_trades = deque(maxlen=5000)

        # Historial de alertas
        self.alerts = deque(maxlen=100)
        self.last_alert_time = 0.0
        self.alert_cooldown = 3.0  # Segundos entre alertas similares

        # Métricas de confianza del mercado
        self.market_confidence = 1.0  # 1.0 = mercado honesto, 0.0 = manipulación total

        logger.info(f"[FRAUD] Detector inicializado | Ventana: {detection_window_seconds}s | Symbol: {symbol.upper()}")

    def process_real_trade(self, is_seller_aggressive: bool, quantity: float, price: float, timestamp_ms: int):
        """
        Registra un trade REAL ejecutado para análisis.
        Este es el ÚNICO tipo de dato en el que confiamos.
        """
        trade = {
冤案f 'timestamp': timestamp_ms / 1000.0,
            'is_seller_aggressive': is_seller_aggressive,
            'quantity': quantity,
            'price': price,
            'direction': 'SELL' if is_seller_aggressive else 'BUY'
        }
        self.real_trades.append(trade)

        # Acumular volumen real por dirección
        if is_seller_aggressive:
            self.real_sell_volume += quantity
        else:
            self.real_buy_volume += quantity

    def analyze_market_integrity(self) -> Dict:
        """
        Analiza la integridad del mercado comparando volumen real vs intenciones.
        Retorna métricas de confianza.
        """
        now = time.time()
        cutoff = now - self.detection_window

        # Filtrar trades antiguos
        valid_trades = [t for t in self.real_trades if t['timestamp'] > cutoff]

        if not valid_trades:
            return {'confidence': 1.0, 'discrepancy': 0.0, 'status': 'INSUFFICIENT_DATA'}

        # Calcular ratios reales
        total_real_volume = sum(t['quantity'] for t in valid_trades)
        real_buy_ratio = sum(t['quantity'] for t in valid_trades if t['direction'] == 'BUY') / total_real_volume if total_real_volume > 0 else 0.5
        real_sell_ratio = 1.0 - real_buy_ratio

        # Si la mayoría de las acciones reales son compras, pero hay un muro de venta
        # grande en el libro de órdenes, eso es una DISCREPANCIA (potencial manipulación)

        # Por ahora, solo calculamos basado en acciones reales
        # La discrepancia con intenciones se detectaría si tuviéramos depth

        max_ratio = max(real_buy_ratio, real_sell_ratio)
        dominance = max_ratio - 0.5  # Cuánto domina una dirección sobre el otro

        # Regla de integridad: Si hay dominancia extrema (>80%) en trades reales,
        # el mercado está actuando con convicción real
        if max_ratio > 0.80:
            status = 'STRONG_AGREEMENT'
            confidence = 0.90 + (max_ratio - 0.80) * 0.5  # Hasta 0.95
        elif max_ratio > 0.65:
            status = 'MODERATE_AGREEMENT'
            confidence = 0.75 + (max_ratio - 0.65) * 0.33  # Hasta 0.85
        else:
            status = 'MIXED_SIGNALS'
            confidence = 0.50 + (max_ratio - 0.50) * 1.0  # Entre 0.50 y 0.65

        return {
            'confidence': min(1.0, confidence),
            'real_buy_ratio': real_buy_ratio,
            'real_sell_ratio': real_sell_ratio,
            'dominance': dominance,
            'status': status,
            'total_real_volume': total_real_volume,
            'trade_count': len(valid_trades)
        }

    def should_block_trade(self, proposed_direction: str,
                           min_real_volume_btc: float = 3.0) -> bool:
        """
        Verifica si se debe BLOQUEAR un trade propuesto debido a manipulación detectada.

        Args:
            proposed_direction: 'LONG' o 'SHORT'
            min_real_volume_btc: Volumen real mínimo necesario para validar

        Returns:
            True si DEBE bloquearse (manipulación detectada)
            False si es seguro proceder
        """
        now = time.time()

        # Si no hay suficiente volumen real, BLOQUEAR
        total_real = self.real_buy_volume + self.real_sell_volume
        if total_real < min_real_volume_btc:
            logger.warning(
                f"🛡️ [FRAUD BLOCK] Volumen real insuficiente: {total_real:.3f} BTC < {min_real_volume_btc} BTC. "
                f"Posible mercado dormido o manipulación por falta de liquidez."
            )
            return True

        # Calcular dirección REAL del mercado
        if total_real > 0:
            real_buy_ratio = self.real_buy_volume / total_real
            real_sell_ratio = self.real_sell_volume / total_real
        else:
            real_buy_ratio = 0.5
            real_sell_ratio = 0.5

        # Si queremos ir LONG pero el mercado REAL está vendiendo -> BLOQUEAR
        if proposed_direction == 'LONG' and real_sell_ratio > 0.60:
            logger.warning(
                f"🛡️ [FRAUD BLOCK] Intentando LONG pero mercado REAL está vendiendo "
                f"({real_sell_ratio:.1%} sell vs {real_buy_ratio:.1%} buy). Posible trampa."
            )
            self._record_alert('SIGNAL_AGAINST_FLOW', 'HIGH', 'SELL',
                               "Trade LONG bloqueado: mercado real vendiendo", 0.85)
            return True

        # Si queremos ir SHORT pero el mercado REAL está comprando -> BLOQUEAR
        if proposed_direction == 'SHORT' and real_buy_ratio > 0.60:
            logger.warning(
                f"🛡️ [FRAUD BLOCK] Intentando SHORT pero mercado REAL está comprando "
                f"({real_buy_ratio:.1%} buy vs {real_sell_ratio:.1%} sell). Posible trampa."
            )
            self._record_alert('SIGNAL_AGAINST_FLOW', 'HIGH', 'BUY',
                               "Trade SHORT bloqueado: mercado real comprando", 0.85)
            return True

        # Verificar integridad del mercado
        integrity = self.analyze_market_integrity()
        if integrity['confidence'] < 0.55:
            logger.warning(
                f"🛡️ [FRAUD BLOCK] Integridad del mercado muy baja ({integrity['confidence']:.1%}). "
                f"Estado: {integrity['status']}. Comercio bloqueado."
            )
            self._record_alert('LOW_INTEGRITY', 'MEDIUM', proposed_direction,
                               f"Mercado con señales mixtas. Confianza: {integrity['confidence']:.1%}",
                               0.70)
            return True

        return False  # No hay fraude, proceder con el trade

    def _record_alert(self, manipulation_type: str, severity: str, direction: str,
                      description: str, confidence: float):
        """Registra una alerta de manipulación"""
        now = time.time()

        # Cooldown para no saturar de alertas idénticas
        if now - self.last_alert_time < self.alert_cooldown:
            return

        alert = FraudAlert(
            timestamp=now,
            manipulation_type=manipulation_type,
            severity=severity,
            direction=direction,
            description=description,
            confidence=confidence
        )

        self.alerts.append(alert)
        self.last_alert_time = now

        # Actualizar confianza del mercado global
        if severity == 'CRITICAL':
            self.market_confidence = max(0.0, self.market_confidence - 0.3)
        elif severity == 'HIGH':
            self.market_confidence = max(0.0, self.market_confidence - 0.2)
        elif severity == 'MEDIUM':
            self.market_confidence = max(0.0, self.market_confidence - 0.1)

        logger.warning(
            f"🚨 [FRAUD ALERT] {severity} | {manipulation_type} | {direction} | "
            f"{description} (Conf: {confidence:.1%})"
        )

    def reset_state(self):
        """Reinicia el estado del detector (útil entre trades)"""
        self.real_buy_volume = 0.0
        self.real_sell_volume = 0.0
        self.real_trades.clear()
        self.market_confidence = 1.0
        logger.info("[FRAUD] Estado reiniciado")

    def get_summary(self) -> str:
        """Devuelve resumen del estado actual del detector"""
        integrity = self.analyze_market_integrity()
        return (
            f"🛡️ Confianza Mercado: {integrity['confidence']:.1%} | "
            f"🟢 Compra Real: {self.real_buy_volume:.3f} BTC | "
            f"🔴 Venta Real: {self.real_sell_volume:.3f} BTC | "
            f"📊 Estado: {integrity['status']}"
        )


if __name__ == "__main__":
    """Testing del fraud detector"""
    detector = MarketFraudDetector()

    print("Simulando mercado con manipulación...\n")

    # Simular trades reales: mayoría VENTA
    for i in range(50):
        is_seller = True if i < 40 else False  # 80% venta
        detector.process_real_trade(
            is_seller_aggressive=is_seller,
            quantity=0.05 + (i * 0.001),
            price=96432.50,
            timestamp_ms=int(time.time() * 1000) + (i * 100)
        )

    print(f"Análisis de integridad: {detector.analyze_market_integrity()}")
    print(f"\nIntentando LONG: {detector.should_block_trade('LONG')}")
    print(f"Intentando SHORT: {detector.should_block_trade('SHORT')}")
    print(f"\n{detector.get_summary()}")
