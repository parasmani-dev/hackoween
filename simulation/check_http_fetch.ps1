param(
    [Parameter(Mandatory)]
    [string]$EngineServer,
    [string]$WebotsExe = './runs/webots/msys64/mingw64/bin/webots.exe',
    [int]$BridgePort = 18765
)

$ErrorActionPreference = 'Stop'
$output = Join-Path $PWD ('runs/bridge-check-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $output | Out-Null
Copy-Item simulation/worlds,simulation/controllers -Destination $output -Recurse
$world=Join-Path $output 'worlds/fetch_room.wbt'
(Get-Content $world -Raw).Replace('controller "scene_preview"','controller "bridge"') | Set-Content $world -Encoding ascii
$env:ROVER_WEBOTS_PORT = [string]$BridgePort
$exe=(Resolve-Path -LiteralPath $WebotsExe).Path
$p=Start-Process $exe -ArgumentList @('--batch','--mode=fast','--minimize',('"' + $world + '"')) -WindowStyle Hidden -PassThru
try {
 python -m simulation.check_http_fetch "$EngineServer" --bridge-port $BridgePort
 $testCode=$LASTEXITCODE
} finally {
 if (-not $p.HasExited) { Stop-Process -InputObject $p }
}
exit $testCode
