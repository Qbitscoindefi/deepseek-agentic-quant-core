#!/usr/bin/env python3
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

with open('BINANCE/_proxy_gateway.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Buscar la funcion run_proxy completa
idx = content.find('def run_proxy():')
if idx >= 0:
    # Encontrar el inicio del logger.info dentro de run_proxy
    start_func = idx
    
    # Buscar el final de run_proxy (siguiente def o el final del archivo)
    rest = content[idx:]
    end_func = rest.find('\ndef ') 
    if end_func >= 0:
        end_func = idx + end_func
    else:
        end_func = len(content)
    
    # Nueva implementacion limpia
    new_func = '''def run_proxy():
    """Inicia el servidor proxy"""
    server = HTTPServer(('127.0.0.1', PROXY_PORT), ProxyHandler)
    
    print('=== OpenBridge Proxy Gateway v1.0 ===')
    print(f'Puerto: {PROXY_PORT} | Modelos: {len(MODEL_CHAIN)} configurados')
    print(f'Rotacion automatica entre proveedores: ACTIVADA')
    print()
    
    logger.info(f"Proxy Gateway iniciado en http://127.0.0.1:{PROXY_PORT}")
    logger.info("Modelos disponibles:")
    for i, m in enumerate(MODEL_CHAIN):
        logger.info(f"  {i+1}. {m['name']} (via {m['type']})")
    logger.info("")
    logger.info("Endpoints de monitoreo:")
    logger.info("  /health  - Estado del proxy")
    logger.info("  /status  - Estadisticas detalladas")
    logger.info("")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Proxy detenido por el usuario")
        server.shutdown()
'''
    
    content = content[:idx] + new_func + content[end_func:]
    
    with open('BINANCE/_proxy_gateway.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('OK - run_proxy reemplazada correctamente')
else:
    print('ERROR: def run_proxy no encontrada')
