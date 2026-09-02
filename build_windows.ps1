$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv-build\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    py -3 -m venv .venv-build
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements-dev.txt

& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name batchmortal-cli `
    --collect-all seleniumbase `
    --collect-all openpyxl `
    main.py

& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name MajsoulMortalDesktop `
    --collect-all matplotlib `
    --collect-all openpyxl `
    desktop.py

$distPath = Join-Path $PSScriptRoot "dist"
$readmeCopy = Join-Path $distPath "README.md"
$versionLine = Select-String -LiteralPath (Join-Path $PSScriptRoot "desktop.py") -Pattern '^APP_VERSION = "([^"]+)"$'
if (-not $versionLine -or -not $versionLine.Matches[0].Groups[1].Value) {
    throw "无法从 desktop.py 读取 APP_VERSION"
}
$appVersion = $versionLine.Matches[0].Groups[1].Value
$archivePath = Join-Path $distPath "MajsoulMortalDesktop-Windows-x64-v$appVersion.zip"
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.md") -Destination $readmeCopy -Force
$userScriptCopy = Join-Path $distPath "majsoul_recent_100_export.user.js"
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "majsoul_recent_100_export.user.js") -Destination $userScriptCopy -Force
if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
}
Compress-Archive -Path @(
    (Join-Path $distPath "MajsoulMortalDesktop.exe"),
    (Join-Path $distPath "batchmortal-cli.exe"),
    $readmeCopy,
    $userScriptCopy
) -DestinationPath $archivePath -CompressionLevel Optimal

Write-Host "构建完成："
Write-Host "  dist\MajsoulMortalDesktop.exe"
Write-Host "  dist\batchmortal-cli.exe"
Write-Host "  dist\MajsoulMortalDesktop-Windows-x64-v$appVersion.zip"
Write-Host "分发时请将两个文件放在同一目录。"
