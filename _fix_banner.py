#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

with open('BINANCE/_proxy_gateway.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Buscar y reemplazar el banner problemático
old = "    print(f\"\"\"\n\u2554\u2550\u2550\u2550"
idx = content.find('\u2554')
if idx >= 0:
    end_banner = content.find('\"\"\")', idx)
    if end_banner >= 0:
        # Replace the whole banner
        old_banner = content[idx:end_banner+5]
        new_banner = "    print('=== OpenBridge Proxy Gateway v1.0 ===')\n    print(f'Puerto: {PROXY_PORT} | Modelos: {len(MODEL_CHAIN)}')\n    print()"
        content = content.replace(old_banner, new_banner)
        
        with open('BINANCE/_proxy_gateway.py', 'w', encoding='utf-8') as f:
            f.write(content)
        print('OK - Banner reemplazado')
    else:
        print('No se encontro cierre del banner')
else:
    print('No se encontro el banner')
