import os
import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode

# Base URLs para la API
BASE_URL = 'https://api.binance.com'

def get_keys():
    """Lee las claves desde el archivo .env y devuelve una tupla (api_key, api_secret).
    Si el archivo no existe o las claves no se encuentran, se registra un error claro.
    """
    env_path = r"C:\OPENBRIDGE\BINANCE\.env"
    api_key = ""
    api_secret = ""

    if not os.path.isfile(env_path):
        print(f"[ERROR] Archivo .env no encontrado en {env_path}")
        return api_key, api_secret

    try:
        with open(env_path, "r") as f:
            for line in f:
                if "=" in line:
                    key, val = line.strip().split("=", 1)
                    if key == "BINANCE_API_KEY":
                        api_key = val
                    elif key == "BINANCE_API_SECRET":
                        api_secret = val
    except Exception as e:
        print(f"[ERROR] No se pudo leer el .env: {e}")
        return "", ""

    if not api_key or not api_secret:
        print("[ERROR] Las claves API no están definidas en el archivo .env")

    return api_key, api_secret

def dispatch_request(method, endpoint, params, api_key, api_secret):
    """Firma el request de acuerdo a la especificacion de seguridad de Binance (HMAC-SHA256)"""
    # 1. Agregar timestamp a los parametros
    params['timestamp'] = int(time.time() * 1000)

    # 2. Generar el query string
    query_string = urlencode(params)

    # 3. Firmar matemáticamente
    signature = hmac.new(
        api_secret.encode('utf-8'),
        query_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    # 4. Acoplar firma
    query_string += f"&signature={signature}"
    url = f"{BASE_URL}{endpoint}?{query_string}"

    # 5. Inyectar llave publica en Headers
    headers = {
        'X-MBX-APIKEY': api_key
    }

    response = getattr(requests, method.lower())(url, headers=headers)
    return response.json(), response.status_code


def fetch_account_balances():
    """Obtiene los saldos de la cuenta Binance usando la API signed.
    Devuelve el JSON con información de balances o imprime errores.
    """
    api_key, api_secret = get_keys()
    if not api_key or not api_secret:
        print("[FAIL] No se pueden obtener saldos sin claves API válidas.")
        return
    endpoint = '/api/v3/account'
    params = {}
    data, status = dispatch_request('GET', endpoint, params, api_key, api_secret)
    if status == 200:
        print("[+] Saldos obtenidos exitosamente.")
        # Mostrar balances relevantes (solo aquellos con cantidad > 0)
        balances = data.get('balances', [])
        for bal in balances:
            asset = bal.get('asset')
            free = bal.get('free')
            locked = bal.get('locked')
            if float(free) > 0 or float(locked) > 0:
                print(f"{asset}: libre={free}, bloqueado={locked}")
    else:
        print(f"[ERROR] Falló al obtener balances (status {status})")
        print(data)


def fetch_usdc_optimism_address():
    """Obtiene la dirección de depósito de WLD en la red Optimism (ejemplo de uso de SAPI).
    """
    print("=== BINANCE SAPI (CAPITAL DEPOSIT ADDRESS EXTRACTOR) ===")
    api_key, api_secret = get_keys()

    if not api_key or not api_secret:
        print("[FAIL] Claves API no detectadas. Abortando misión.")
        return

    # Endpoint correcto según la doc oficial de Binance
    endpoint = '/sapi/v1/capital/deposit/address'

    params = {
        'coin': 'WLD',
        'network': 'OPTIMISM'
    }

    print("[*] Contactando a los cuarteles de Binance y validando permisos de la clave...")
    data, status = dispatch_request('GET', endpoint, params, api_key, api_secret)

    if status == 200:
        print("\n[+] CONEXIÓN ESTABLECIDA EXitosamente: La API es Válida.")
        address = data.get('address')
        url = data.get('url')  # Algunos networks usan tags/memos
        tag = data.get('tag')

        print("="*60)
        print(f"\tMONEDA: {data.get('coin')}")
        print(f"\tDIRECCIÓN DE DEPÓSITO EXACTA (RED OPTIMISM):")
        print(f"\n\t-->  {address}  <--")
        print("\n="*60)
    else:
        print(f"\n[-] ERROR AL CONECTAR CON BINANCE (Status: {status})")
        print(data)


if __name__ == '__main__':
    # Por defecto, muestra balances. Comenta/descomenta según necesidad.
    fetch_account_balances()
    # fetch_usdc_optimism_address()
