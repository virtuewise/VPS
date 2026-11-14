$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python 3.12+ is required. Install Python from https://www.python.org/downloads/windows/"
}

if (-not $env:VPS_AGENT_TOKEN -or $env:VPS_AGENT_TOKEN.Length -lt 32) {
    $env:VPS_AGENT_TOKEN = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(48))
    Write-Host "Generated temporary node token for this session:"
    Write-Host $env:VPS_AGENT_TOKEN
    Write-Warning "Save this token securely and configure the same token in the control plane before connecting."
}

py -3 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:VPS_HYPERV_VM_ROOT = if ($env:VPS_HYPERV_VM_ROOT) { $env:VPS_HYPERV_VM_ROOT } else { "C:\VirtueWise\VMs" }
New-Item -ItemType Directory -Force -Path $env:VPS_HYPERV_VM_ROOT | Out-Null

Write-Host "Starting VirtueWise VPS node agent on 127.0.0.1:8765"
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
