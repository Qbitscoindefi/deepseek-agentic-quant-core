# Lanzar el proxy en ventana separada
$proc = Start-Process -WindowStyle Hidden -PassThru -FilePath '.venv_nuevo\Scripts\python.exe' -ArgumentList 'BINANCE\_proxy_gateway.py'
Write-Host "Proxy iniciado - PID: $($proc.Id)"

# Esperar a que arranque
Start-Sleep 3

# Verificar
if (!$proc.HasExited) {
    Write-Host "Proxy corriendo correctamente"
    
    # Probar endpoint
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:4000/v1/models" -UseBasicParsing -TimeoutSec 5
        Write-Host "Respuesta API:"
        Write-Host $resp.Content
    } catch {
        Write-Host "Error al consultar API: $_"
    }
} else {
    Write-Host "ERROR: Proxy termino. Codigo de salida: $($proc.ExitCode)"
}
