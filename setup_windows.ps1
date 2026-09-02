$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$venvPythonw = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "正在创建 Python 虚拟环境..."
    py -3 -m venv .venv
}

Write-Host "正在安装桌面版依赖..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

Write-Host "安装完成，正在启动桌面版。"
Start-Process -FilePath $venvPythonw -ArgumentList (Join-Path $PSScriptRoot "desktop.py")
