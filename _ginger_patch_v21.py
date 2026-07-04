#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ginger Patch v2.1 — Correcciones de persistencia y circuit breaker
Aplicado por: Ginger, Mano Derecha del Arquitecto
Fecha: 2026-06-07
"""

import re

ENGINE_FILE = r"C:\OPENBRIDGE\BINANCE\trading_engine.py"

def aplicar_patch():
    with open(ENGINE_FILE, 'r', encoding='utf-8') as f:
        content = f.read()
    
    cambios = 0
    
    # =========================================================
    # PATCH 1: load_state() — Restaurar TODOS los campos + CB
    # =========================================================
    old_load = '''    def load_state(self):
        """Carga estado previo si existe"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    saved_state = json.load(f)
                    # Restaurar campos relevantes
                    for key in ['total_trades', 'winning_trades', 'losing_trades',
                               'total_pnl', 'consecutive_losses']:
                        if key in saved_state:
                            self.session_data[key] = saved_state[key]
            except Exception as e:
                logger.warning(f"No se pudo cargar estado previo: {e}")'''
    
    new_load = '''    def load_state(self):
        """Carga estado previo si existe — CORREGIDO v2.1 (Ginger)"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    saved_state = json.load(f)
                    # Restaurar TODOS los campos, incluyendo circuit breaker
                    full_restore = [
                        'total_trades', 'winning_trades', 'losing_trades',
                        'total_pnl', 'largest_win', 'largest_loss',
                        'consecutive_losses', 'circuit_breaker_triggered',
                        'circuit_breaker_until', 'mode', 'last_trades',
                        'loss_history'
                    ]
                    for key in full_restore:
                        if key in saved_state:
                            self.session_data[key] = saved_state[key]
                    
                    # Si el CB estaba activo, restaurar modo
                    if saved_state.get('circuit_breaker_triggered', False):
                        self.session_data['mode'] = 'CIRCUIT_BREAKER'
                    
                    # Verificar perdida rolling contra limite del CB
                    loss_history = saved_state.get('loss_history', [])
                    if loss_history:
                        from datetime import datetime, timedelta
                        now = datetime.now()
                        cutoff = now - timedelta(hours=CIRCUIT_BREAKER_WINDOW_HOURS)
                        rolling_loss = sum(
                            entry['amount'] for entry in loss_history
                            if datetime.fromisoformat(entry['ts']) > cutoff
                        )
                        if rolling_loss >= CIRCUIT_BREAKER_MAX_LOSS_USDT:
                            if not self.session_data.get('circuit_breaker_triggered', False):
                                logger.warning(
                                    f"PERDIDA ROLLING ${rolling_loss:.4f} SUPERA LIMITE ${CIRCUIT_BREAKER_MAX_LOSS_USDT:.2f}. "
                                    f"Activando circuit breaker automaticamente."
                                )
                                self.session_data['circuit_breaker_triggered'] = True
                                until = now + timedelta(minutes=CIRCUIT_BREAKER_PAUSE_MINUTES)
                                self.session_data['circuit_breaker_until'] = until.isoformat()
                                self.session_data['mode'] = 'CIRCUIT_BREAKER'
                                logger.info(f"CB activado hasta {until.strftime('%H:%M:%S')}")
            except Exception as e:
                logger.warning(f"No se pudo cargar estado previo: {e}")'''
    
    if old_load in content:
        content = content.replace(old_load, new_load)
        cambios += 1
        print(f"[OK] Patch 1 aplicado: load_state() mejorado")
    else:
        print("[?] Patch 1: texto no encontrado exactamente, buscando alternativa...")
        # Fallback: buscar por fragmentos
        if 'def load_state' in content:
            print("    -> def load_state SI existe, pero el bloque exacto no coincide")
    
    # =========================================================
    # PATCH 2: save_state() — Guardar datos completos
    # =========================================================
    old_save = '''    def save_state(self):
        """Guarda estado actual"""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.session_data, f, indent=2)
        except Exception as e:
            logger.warning(f"No se pudo guardar estado: {e}")'''
    
    new_save = '''    def save_state(self):
        """Guarda estado actual con datos completos para persistencia (Ginger v2.1)"""
        try:
            # Asegurar que se guardan datos criticos del CB
            save_data = {
                'total_trades': self.session_data['total_trades'],
                'winning_trades': self.session_data['winning_trades'],
                'losing_trades': self.session_data['losing_trades'],
                'total_pnl': self.session_data['total_pnl'],
                'largest_win': self.session_data['largest_win'],
                'largest_loss': self.session_data['largest_loss'],
                'consecutive_losses': self.session_data['consecutive_losses'],
                'circuit_breaker_triggered': self.session_data['circuit_breaker_triggered'],
                'circuit_breaker_until': self.session_data.get('circuit_breaker_until'),
                'mode': self.session_data.get('mode', 'NORMAL'),
                'last_trades': self.session_data.get('last_trades', []),
                'last_exit_time': datetime.now().isoformat(),
                'saved_at': datetime.now().isoformat()
            }
            
            # Mantener historial de perdidas si existe
            if hasattr(self, '_loss_history'):
                save_data['loss_history'] = self._loss_history
            
            with open(self.state_file, 'w') as f:
                json.dump(save_data, f, indent=2)
        except Exception as e:
            logger.warning(f"No se pudo guardar estado: {e}")'''
    
    if old_save in content:
        content = content.replace(old_save, new_save)
        cambios += 1
        print(f"[OK] Patch 2 aplicado: save_state() mejorado")
    else:
        print("[?] Patch 2: texto no encontrado exactamente")
    
    # =========================================================
    # PATCH 3: record_trade() — historial de perdidas persistente
    # =========================================================
    # Buscar el método record_trade y reemplazarlo
    pattern_start = r'    def record_trade\(self, pnl, side, confidence, reason\):'
    pattern_end = r'        self\.save_state\(\)'
    
    # Encontrar inicio del método
    match = re.search(pattern_start, content)
    if match:
        start = match.start()
        # Encontrar la línea de save_state (fin del método)
        end_match = re.search(r'        self\.save_state\(\)', content[start:])
        if end_match:
            end = start + end_match.end()
            # También incluir la línea en blanco después
            new_record = """    def record_trade(self, pnl, side, confidence, reason):
        \"\"\"Registra un trade completado con historial de perdidas persistente (Ginger v2.1)\"\"\"
        self.session_data['total_trades'] += 1
        self.session_data['total_pnl'] += pnl

        # Inicializar historial de perdidas si no existe
        if not hasattr(self, '_loss_history'):
            self._loss_history = []

        if pnl > 0:
            self.session_data['winning_trades'] += 1
            self.session_data['consecutive_losses'] = 0
            if pnl > self.session_data['largest_win']:
                self.session_data['largest_win'] = pnl
            # Resetear historial de perdidas en ganancia
            self._loss_history = []
        else:
            self.session_data['losing_trades'] += 1
            self.session_data['consecutive_losses'] += 1
            if pnl < self.session_data['largest_loss']:
                self.session_data['largest_loss'] = pnl
            # Registrar perdida en el historial rolling
            self._loss_history.append({
                'ts': datetime.now().isoformat(),
                'amount': abs(pnl)
            })
            # Mantener solo ultimas 20 perdidas
            if len(self._loss_history) > 20:
                self._loss_history = self._loss_history[-20:]

        # Mantener ultimos 10 trades
        self.session_data['last_trades'].append({
            'time': datetime.now().isoformat(),
            'pnl': pnl,
            'side': side,
            'confidence': confidence,
            'reason': reason
        })
        self.session_data['last_trades'] = self.session_data['last_trades'][-10:]

        self.save_state()"""
            
            content = content[:start] + new_record + content[end:]
            cambios += 1
            print(f"[OK] Patch 3 aplicado: record_trade() con historial persistente")
    
    # =========================================================
    # GUARDAR
    # =========================================================
    if cambios > 0:
        with open(ENGINE_FILE, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"\n=== PATCH COMPLETADO: {cambios} cambios aplicados ===")
    else:
        print(f"\n=== SIN CAMBIOS: No se encontraron patrones para parchear ===")

if __name__ == '__main__':
    aplicar_patch()
