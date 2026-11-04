param(
    [Parameter(Mandatory)]
    [string]$EngineServer,
    [string]$WebotsExe = './runs/webots/msys64/mingw64/bin/webots.exe',
    [int]$BridgePort = 18765,
    [string]$AgentRoot,
    [ValidateSet("claude", "openai")]
    [string]$Provider = "claude",
    [string]$Command,
    [string]$ExpectedFixtureKey,
    [switch]$RecordPov,
    [string]$UiServer
)

$ErrorActionPreference = 'Stop'
$output = Join-Path $PWD ('runs/bridge-check-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $output | Out-Null
Copy-Item simulation/worlds,simulation/controllers -Destination $output -Recurse
$world=Join-Path $output 'worlds/fetch_room.wbt'
(Get-Content $world -Raw).Replace('controller "scene_preview"','controller "bridge"') | Set-Content $world -Encoding ascii
$env:ROVER_WEBOTS_PORT = [string]$BridgePort
$exe=(Resolve-Path -LiteralPath $WebotsExe).Path
$checkArgs = @("$EngineServer", '--bridge-port', "$BridgePort")
if ($UiServer) {
    $checkArgs += @('--ui-server', $UiServer)
}
$webotsArgs = @('--batch', '--mode=fast', '--minimize', ('"' + $world + '"'))
$window = 'Hidden'
if ($Command) {
    $checkArgs += @('--agent-root', "$AgentRoot", '--provider', $Provider, '--command', $Command)
    if ($ExpectedFixtureKey) {
        $checkArgs += @('--expected-fixture-key', $ExpectedFixtureKey)
    }
    if ($RecordPov) {
        $checkArgs += '--record-pov'
    }
    $webotsArgs = @('--batch', '--mode=realtime', ('"' + $world + '"'))
    $window = 'Normal'
}
$p=Start-Process $exe -ArgumentList $webotsArgs -WindowStyle $window -PassThru
try {
 python -m simulation.check_http_fetch @checkArgs
 $testCode=$LASTEXITCODE
} finally {
 if (-not $p.HasExited) { Stop-Process -InputObject $p }
    Write-Host "Webots bridge log: $(Join-Path $output 'bridge.log')"
}
exit $testCode
