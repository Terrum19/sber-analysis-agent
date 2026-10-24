# Запуск платформы на Windows одним файлом (#167): двойной клик по start.bat.
# Нужен только Docker Desktop; остальное скрипт делает сам и при повторном
# запуске пропускает готовое:
#   - запускает Docker Desktop, если он установлен, но не запущен;
#   - ставит (winget или тихий установщик) и запускает Ollama вне контейнера;
#   - поднимает docker compose и открывает http://localhost:3000.
# Модель backend скачивает сам; прогресс — в окне чата (/api/llm/status).
# Не Stop: в Windows PowerShell 5.1 stderr внешней программы (docker) с
# перенаправлением тогда бросает исключение; ошибки проверяем по $LASTEXITCODE.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"  # иначе Invoke-WebRequest в 5.1 качает в разы медленнее
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Set-Location (Split-Path -Parent $PSScriptRoot | Split-Path -Parent)

$AppUrl = "http://localhost:3000"
$OllamaUrl = "http://localhost:11434"
$ComposeMinMinor = 24  # Compose 2.24+: необязательный env_file
$DockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
$OllamaApp = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama app.exe"

function Say($Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }

function Fail($Text) {
    Write-Host "`nОШИБКА: $Text" -ForegroundColor Red
    Read-Host "Нажмите Enter, чтобы закрыть окно" | Out-Null
    exit 1
}

function Test-Command($Name) { [bool](Get-Command $Name -ErrorAction SilentlyContinue) }

function Wait-For($Seconds, [scriptblock]$Check) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Check) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Test-Docker {
    docker info *> $null
    return $LASTEXITCODE -eq 0
}

function Test-Ollama {
    try {
        Invoke-RestMethod -Uri "$OllamaUrl/api/version" -TimeoutSec 3 -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

# --- Docker ---
if (-not (Test-Command docker)) {
    Start-Process "https://www.docker.com/products/docker-desktop/"
    Fail "Docker Desktop не установлен. Установите его со страницы, которая открылась в браузере, и запустите start.bat снова."
}
if (-not (Test-Docker)) {
    if (-not (Test-Path $DockerDesktop)) {
        Fail "Docker не запущен. Запустите Docker Desktop и повторите."
    }
    Say "Запускаю Docker Desktop"
    Start-Process $DockerDesktop
    if (-not (Wait-For 180 { Test-Docker })) {
        Fail "Docker Desktop не запустился за 3 минуты. Запустите его вручную и повторите."
    }
}
$version = (docker compose version --short) 2>$null
if ($LASTEXITCODE -ne 0 -or -not $version) { Fail "Нет docker compose v2. Обновите Docker Desktop." }
$parts = $version.TrimStart("v").Split(".")
if ([int]$parts[0] -lt 2 -or ([int]$parts[0] -eq 2 -and [int]$parts[1] -lt $ComposeMinMinor)) {
    Fail "Нужен Docker Compose 2.$ComposeMinMinor или новее, сейчас $version. Обновите Docker Desktop."
}

# --- Ollama ---
if (-not (Test-Ollama)) {
    if (-not (Test-Path $OllamaApp)) {
        Say "Устанавливаю Ollama"
        $installed = $false
        if (Test-Command winget) {
            winget install --id Ollama.Ollama -e --silent --accept-package-agreements --accept-source-agreements
            $installed = Test-Path $OllamaApp
        }
        if (-not $installed) {
            $setup = Join-Path $env:TEMP "OllamaSetup.exe"
            try {
                Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $setup -ErrorAction Stop
            } catch {
                Fail "Не удалось скачать Ollama. Проверьте интернет или поставьте её с https://ollama.com/download"
            }
            Start-Process $setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
        }
        if (-not (Test-Path $OllamaApp)) { Fail "Ollama не установилась. Поставьте её с https://ollama.com/download и повторите." }
    }
    Say "Запускаю Ollama"
    Start-Process $OllamaApp
    if (-not (Wait-For 90 { Test-Ollama })) {
        Fail "Ollama не ответила на $OllamaUrl. Запустите приложение Ollama вручную и повторите."
    }
}

# --- Платформа ---
Say "Собираю и запускаю платформу. Первый запуск — 10–20 минут: сборка образов и модель на несколько ГБ"
docker compose up -d --build --wait --wait-timeout 3600
if ($LASTEXITCODE -ne 0) { Fail "Платформа не запустилась. Подробности: docker compose logs" }

Say "Готово: $AppUrl"
Start-Process $AppUrl
Write-Host "Модель при первом запуске ещё может скачиваться — статус виден над чатом."
Write-Host "Остановить платформу: stop.bat."
Read-Host "Нажмите Enter, чтобы закрыть окно" | Out-Null
