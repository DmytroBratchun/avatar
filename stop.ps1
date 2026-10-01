# Helper for stop.sh - stops uvicorn (backend) and the vite dev server (frontend)
# by matching process command lines, since npm on Windows launches vite through a
# wrapper process whose PID isn't the real node process.
$stopped = $false

Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'uvicorn\s+app\.main' } |
    ForEach-Object {
        Write-Host "  stopping backend (pid $($_.ProcessId))"
        Stop-Process -Id $_.ProcessId -Force
        $stopped = $true
    }

Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
    Where-Object { $_.CommandLine -match 'vite' } |
    ForEach-Object {
        Write-Host "  stopping frontend (pid $($_.ProcessId))"
        Stop-Process -Id $_.ProcessId -Force
        $stopped = $true
    }

if (-not $stopped) {
    Write-Host "  nothing matching was running"
}
