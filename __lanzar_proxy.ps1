$proc = Start-Process -NoNewWindow -PassThru -FilePath '.venv_nuevo\Scripts\python.exe' -ArgumentList 'BINANCE\_proxy_gateway.py'
Write-Host "Proxy PID: $($proc.Id)"

# Esperar y verificar
Start-Sleep 3

if (!$proc.HasExited) {
    Write-Host "Proxy corriendo correctamente"
    Write-Host ""
    Write-Host "Probando endpoint /v1/models..."
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:4000/v1/models" -UseBasicParsing
        Write-Host $resp.Content
    } catch {
        Write-Host "Error al conectar: $_"
    }
} else {
    Write-Host "ERROR: Proxy termino inmediatamente"
}
