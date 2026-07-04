"""Script para inyectar las API keys reales en el proxy gateway"""
import os
import re

proxy_file = r"C:\OPENBRIDGE\BINANCE\_proxy_gateway.py"

with open(proxy_file, 'r', encoding='utf-8') as f:
    content = f.read()

# Obtener keys del entorno
nvidia_key = os.environ.get('NVIDIA_API_KEY', '')
openrouter_key = os.environ.get('OPENROUTER_API_KEY', '')

# Reemplazar las líneas de api_key
content = content.replace(
    "'api_key': os.environ.get('NVIDIA_API_KEY', ''),",
    f"'api_key': '{nvidia_key}',"
)
content = content.replace(
    "'api_key': os.environ.get('OPENROUTER_API_KEY', ''),",
    f"'api_key': '{openrouter_key}',"
)

with open(proxy_file, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"NVIDIA API Key: {nvidia_key[:15]}...{'✓' if nvidia_key else '✗ VACÍA'}")
print(f"OpenRouter API Key: {openrouter_key[:15]}...{'✓' if openrouter_key else '✗ VACÍA'}")
print("Proxy gateway configurado correctamente")
