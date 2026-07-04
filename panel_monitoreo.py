#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenBridge | Panel de Monitoreo del Francotirador
=================================================
Creado por: Ginger — Mano Derecha del Arquitecto
Propósito: Dashboard visual en terminal para monitorear la operativa del motor
            de trading en tiempo real. Se ejecuta en terminal separada.

Uso:
    python panel_monitoreo.py
    
    O en una ventana de terminal externa para ver la operativa mientras
    el motor corre en otra terminal.

Estilo: Interfaz limpia, oscura, con barras de progreso y colores.
"""

import os
import sys
import json
import time
import requests
from datetime import datetime
from typing import Optional, Dict, Any

# ── Rutas ────────────────────────────────────────────────────────────────────
BINANCE_DIR = r"C:\OPENBRIDGE\BINANCE"
ENGINE_STATE = os.path.join(BINANCE_DIR, "engine_state.json")
ENGINE_LOG = os.path.join(BINANCE_DIR, "trading_engine.log")
ENV_PATH = os.path.join(BINANCE_DIR, ".env")

# ── Colores ANSI ──────────────────────────────────────────────────────────────
class C:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    
    # Colores de fondo
    BG_BLACK = '\033[40m'
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'
    BG_MAGENTA = '\033[45m'
    BG_CYAN = '\033[46m'
    BG_DARK_GRAY = '\033[100m'
    
    # Colores de texto
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    DARK_GRAY = '\033[90m'
    ORANGE = '\033[38;5;214m'


def clear_screen():
    """Limpia la pantalla de la terminal"""
    os.system('cls' if os.name == 'nt' else 'clear')


def leer_estado_motor() -> Dict[str, Any]:
    """Lee el estado actual del motor desde engine_state.json"""
    try:
        if os.path.exists(ENGINE_STATE):
            with open(ENGINE_STATE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        return {"error": str(e)}


def leer_ultima_linea_log() -> str:
    """Lee la última línea del log del motor"""
    try:
        if os.path.exists(ENGINE_LOG):
            with open(ENGINE_LOG, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                # Buscar última línea con SEÑAL o MONITOREO
                for line in reversed(lines):
                    if 'SEÑAL:' in line or 'SEÑAL' in line or 'MONITOREO' in line or 'ORDEN' in line:
                        return line.strip()
                return lines[-1].strip() if lines else ""
        return ""
    except Exception:
        return ""


def leer_balance_binance() -> Optional[Dict]:
    """Lee el balance desde Binance (requiere claves)"""
    try:
        # Intentar leer del .env
        api_key, api_secret = "", ""
        if os.path.exists(ENV_PATH):
            with open(ENV_PATH, 'r') as f:
                for line in f:
                    if '=' in line:
                        k, v = line.strip().split('=', 1)
                        if k == 'BINANCE_API_KEY': api_key = v
                        if k == 'BINANCE_API_SECRET': api_secret = v
        
        if not api_key or not api_secret:
            return None
            
        # Endpoint público de ticker
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
            timeout=5
        )
        if r.status_code == 200:
            data = r.json()
            return {"btc_price": float(data.get("price", 0))}
    except Exception:
        pass
    return None


def barra_progreso(valor: float, maximo: float, ancho: int = 20, 
                   color_lleno: str = C.GREEN, color_vacio: str = C.DARK_GRAY,
                   color_texto: str = C.WHITE) -> str:
    """Genera una barra de progreso visual"""
    if maximo <= 0:
        return f"{color_vacio}[{' ' * ancho}]{C.RESET}"
    
    proporcion = min(valor / maximo, 1.0)
    lleno = int(proporcion * ancho)
    vacio = ancho - lleno
    
    # Elegir color basado en la proporción
    if proporcion > 0.7:
        color = C.RED
    elif proporcion > 0.4:
        color = C.YELLOW
    else:
        color = C.GREEN
    
    barra = f"{color}{'█' * lleno}{C.DARK_GRAY}{'░' * vacio}{C.RESET}"
    return f"{barra} {color_texto}{valor:.4f}/{maximo:.2f}{C.RESET}"


def mostrar_panel(estado: Dict, ultima_senal: str, mercado: Optional[Dict]):
    """Renderiza el panel principal en terminal"""
    
    # ── Encabezado ──────────────────────────────────────────────────────────
    print(f"{C.BG_DARK_GRAY}{C.BOLD}{C.WHITE}╔══════════════════════════════════════════════════════════════╗{C.RESET}")
    print(f"{C.BG_DARK_GRAY}{C.BOLD}{C.WHITE}║     🔫 OPENBRIDGE — FRANCOTIRADOR (Sniper) v2.0              ║{C.RESET}")
    print(f"{C.BG_DARK_GRAY}{C.BOLD}{C.WHITE}║     Monitoreado por Ginger — Mano Derecha del Arquitecto     ║{C.RESET}")
    print(f"{C.BG_DARK_GRAY}{C.BOLD}{C.WHITE}╚══════════════════════════════════════════════════════════════╝{C.RESET}")
    
    # ── Hora ────────────────────────────────────────────────────────────────
    ahora = datetime.now()
    print(f"\n{C.DIM}Última actualización: {ahora.strftime('%Y-%m-%d %H:%M:%S')}{C.RESET}")
    
    # ── Balance de Mercado ──────────────────────────────────────────────────
    if mercado and 'btc_price' in mercado:
        btc = mercado['btc_price']
        print(f"\n{C.BOLD}{C.CYAN}┌─ MERCADO BTC/USDT ──────────────────────────────────────────┐{C.RESET}")
        print(f"{C.CYAN}│{C.RESET}  Precio: ${btc:,.2f}")
        print(f"{C.CYAN}└──────────────────────────────────────────────────────────────────┘{C.RESET}")
    
    # ── Estado del Motor ────────────────────────────────────────────────────
    mode = estado.get('mode', 'N/A')
    mode_color = {
        'NORMAL': C.GREEN,
        'RECOVERY': C.YELLOW,
        'CIRCUIT_BREAKER': C.RED
    }.get(mode, C.WHITE)
    
    cb = estado.get('circuit_breaker_triggered', False)
    cb_status = f"{C.RED}⚠ ACTIVADO{C.RESET}" if cb else f"{C.GREEN}✓ INACTIVO{C.RESET}"
    cb_until = estado.get('circuit_breaker_until', 'N/A')
    if cb_until and cb_until != 'N/A':
        try:
            cb_dt = datetime.fromisoformat(cb_until)
            remaining = cb_dt - ahora
            if remaining.total_seconds() > 0:
                mins, secs = divmod(int(remaining.total_seconds()), 60)
                cb_until_str = f"{mins}m {secs}s restantes"
            else:
                cb_until_str = "Expirado (debería reanudar)"
        except:
            cb_until_str = str(cb_until)
    else:
        cb_until_str = "—"
    
    losses = estado.get('consecutive_losses', 0)
    
    print(f"\n{C.BOLD}{C.MAGENTA}┌─ ESTADO DEL MOTOR ───────────────────────────────────────────┐{C.RESET}")
    print(f"{C.MAGENTA}│{C.RESET}  Modo:        [{mode_color}{mode:^20}{C.RESET}]")
    print(f"{C.MAGENTA}│{C.RESET}  CB Estado:   {cb_status}")
    print(f"{C.MAGENTA}│{C.RESET}  CB Hasta:    {cb_until_str}")
    
    # Barra de pérdidas consecutivas
    loss_bar = barra_progreso(losses, 10, 20, 
                               color_lleno=C.RED if losses >= 5 else C.YELLOW)
    print(f"{C.MAGENTA}│{C.RESET}  Rachas Perd: {loss_bar}")
    print(f"{C.MAGENTA}└──────────────────────────────────────────────────────────────────┘{C.RESET}")
    
    # ── Última Señal ────────────────────────────────────────────────────────
    if ultima_senal:
        # Truncar si es muy larga
        senal = ultima_senal[:120] if len(ultima_senal) > 120 else ultima_senal
        print(f"\n{C.BOLD}{C.YELLOW}┌─ ÚLTIMA SEÑAL ────────────────────────────────────────────────┐{C.RESET}")
        
        # Colorear según contenido
        texto_coloreado = senal
        if 'LONG' in senal:
            texto_coloreado = senal.replace('LONG', f'{C.GREEN}LONG{C.RESET}')
        elif 'SHORT' in senal:
            texto_coloreado = senal.replace('SHORT', f'{C.RED}SHORT{C.RESET}')
        if 'NEUTRAL' in senal:
            texto_coloreado = senal.replace('NEUTRAL', f'{C.DARK_GRAY}NEUTRAL{C.RESET}')
            
        print(f"{C.YELLOW}│{C.RESET}  {texto_coloreado}")
        print(f"{C.YELLOW}└──────────────────────────────────────────────────────────────────┘{C.RESET}")
    
    # ── Resumen de Pérdidas Recientes ───────────────────────────────────────
    loss_history = estado.get('loss_history', [])
    if loss_history:
        total_loss = sum(l['amount'] for l in loss_history)
        print(f"\n{C.BOLD}{C.RED}┌─ PÉRDIDAS RECIENTES (rolling 4h) ─────────────────────────────┐{C.RESET}")
        loss_bar_rolling = barra_progreso(total_loss, 0.50, 20,
                                           color_lleno=C.RED, color_vacio=C.DARK_GRAY,
                                           color_texto=C.WHITE)
        print(f"{C.RED}│{C.RESET}  Total: {loss_bar_rolling}")
        print(f"{C.RED}│{C.RESET}  Límite CB: $0.50")
        
        for l in loss_history[-3:]:
            try:
                ts = l['ts']
                if len(ts) > 16: ts = ts[:16]
                print(f"{C.RED}│{C.RESET}  {C.DIM}{ts}{C.RESET} → ${l['amount']:.4f}")
            except:
                pass
        print(f"{C.RED}└──────────────────────────────────────────────────────────────────┘{C.RESET}")
    
    # ─── Estado de Archivos ──────────────────────────────────────────────────
    estado_archivos = []
    for fname, label in [("trading_engine.py", "Motor"), 
                          ("engine_state.json", "Estado"),
                          ("trading_engine.log", "Log")]:
        path = os.path.join(BINANCE_DIR, fname)
        if os.path.exists(path):
            size = os.path.getsize(path)
            estado_archivos.append(f"{C.GREEN}✓{C.RESET} {label} ({size//1024}KB)")
        else:
            estado_archivos.append(f"{C.RED}✗{C.RESET} {label}")
    
    print(f"\n{C.DIM}{' | '.join(estado_archivos)}{C.RESET}")
    
    # ── Leyenda ──────────────────────────────────────────────────────────────
    print(f"\n{C.DIM}────────────────────────────────────────────────────────────────────{C.RESET}")
    print(f"{C.DIM}[Ctrl+C para salir] • [El motor debe ejecutarse en terminal aparte]{C.RESET}")


def main():
    """Bucle principal del panel de monitoreo"""
    try:
        while True:
            clear_screen()
            
            estado = leer_estado_motor()
            ultima_senal = leer_ultima_linea_log()
            mercado = leer_balance_binance()
            
            mostrar_panel(estado, ultima_senal, mercado)
            
            time.sleep(2.5)  # Actualizar cada 2.5 segundos
            
    except KeyboardInterrupt:
        clear_screen()
        print(f"\n{C.GREEN}Panel de monitoreo detenido. ¡Hasta la vista, Jox!{C.RESET}\n")
    except Exception as e:
        print(f"\n{C.RED}Error en panel: {e}{C.RESET}")
        time.sleep(3)


if __name__ == "__main__":
    main()
