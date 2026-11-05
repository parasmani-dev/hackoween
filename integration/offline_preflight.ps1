param(
    [Parameter(Mandatory)][string]$WebotsExe,
    [Parameter(Mandatory)][string]$FfmpegBin,
    [ValidateRange(1,65535)][int]$BridgePort = 18796,
    [ValidateRange(1,65535)][int]$EnginePort = 18886
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$webots = (Resolve-Path -LiteralPath $WebotsExe).Path
$ffmpeg = (Resolve-Path -LiteralPath $FfmpegBin).Path
foreach ($name in @('ffmpeg.exe','ffprobe.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $ffmpeg $name))) { throw "Missing $name" }
}
$oldPath = $env:PATH
$secrets = @{}
foreach ($name in @('ANTHROPIC_API_KEY','OPENAI_API_KEY','FFMPEG_PATH','FFPROBE_PATH')) {
    $secrets[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $null, 'Process')
}
Push-Location $root
$log = Join-Path $root ('runs/offline-preflight-' + [guid]::NewGuid().ToString('N') + '.log')
New-Item -ItemType Directory -Force (Join-Path $root 'runs') | Out-Null
try {
    $env:PATH = "$ffmpeg;$oldPath"
    $env:FFMPEG_PATH = Join-Path $ffmpeg "ffmpeg.exe"
    $env:FFPROBE_PATH = Join-Path $ffmpeg "ffprobe.exe"
    Start-Transcript -Path $log | Out-Null
    git rev-parse HEAD
    python -B -m unittest agent.test_clients agent.test_runner engine.test_run_control simulation.test_launcher simulation.test_capture simulation.test_provenance simulation.test_motor simulation.test_offline_preflight simulation.test_preview rover_lab.test_live -v
    if ($LASTEXITCODE -ne 0) { throw 'Python regressions failed' }
    node --test ui/preview.test.mjs
    if ($LASTEXITCODE -ne 0) { throw 'Preview regressions failed' }
    node --test recording/package-submission.test.mjs
    if ($LASTEXITCODE -ne 0) { throw 'Recording regressions failed' }
    # Child shells isolate scripts that use exit; only scripted modes are exposed here.
    $shell = (Get-Process -Id $PID).Path
    & $shell -NoProfile -File ./simulation/run_fetch_demo.ps1 -WebotsExe $webots -Fast -MotorCheck
    if ($LASTEXITCODE -ne 0) { throw 'Physical motor/stall check failed' }
    & $shell -NoProfile -File ./simulation/check_http_fetch.ps1 -EngineServer ./engine/server.py -WebotsExe $webots -BridgePort $BridgePort -EnginePort $EnginePort -UiServer ./ui/server.mjs -MeasureTiming -OfflineAgentCheck
    if ($LASTEXITCODE -ne 0) { throw 'HTTP/recording/scripted-agent integration failed' }
    Write-Output 'PASS: offline preflight. This is not live autonomy or organizer acceptance.'
} finally {
    Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
    $env:PATH = $oldPath
    foreach ($name in $secrets.Keys) { [Environment]::SetEnvironmentVariable($name, $secrets[$name], 'Process') }
    Pop-Location
    Write-Output "Evidence: $log"
}
