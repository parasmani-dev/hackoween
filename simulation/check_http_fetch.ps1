param(
    [Parameter(Mandatory)]
    [string]$EngineServer,
    [string]$WebotsExe = './runs/webots/msys64/mingw64/bin/webots.exe',
    [ValidateRange(1, 65535)]
    [int]$BridgePort = 18765,
    [ValidateRange(1, 65535)]
    [int]$EnginePort = 8080,
    [switch]$MeasureTiming,
    [string]$AgentRoot,
    [ValidateSet("claude", "openai")]
    [string]$Provider = "claude",
    [string]$Command,
    [string]$MaxDecisions,
    [string]$ExpectedFixtureKey,
    [switch]$RecordPov,
    [ValidateSet(1, 2, 3)]
    [int]$OfficialSlot,
    [string]$OfficialLedger,
    [string]$UiServer
)

$ErrorActionPreference = 'Stop'
if ($Command -and -not $PSBoundParameters.ContainsKey('MaxDecisions')) {
    throw '-MaxDecisions is required with -Command.'
}
if ($PSBoundParameters.ContainsKey('MaxDecisions')) {
    if ($MaxDecisions -notmatch '\A(?:[1-9]|[1-5][0-9]|60)\z') {
        throw '-MaxDecisions must be a whole number from 1 to 60.'
    }
    $MaxDecisions = [int]$MaxDecisions
}
$checkArgs = @("$EngineServer", '--bridge-port', "$BridgePort", '--engine-port', "$EnginePort")
if ($PSBoundParameters.ContainsKey("OfficialSlot")) { $checkArgs += @("--official-slot", "$OfficialSlot") }
if ($OfficialLedger) { $checkArgs += @("--official-ledger", $OfficialLedger) }
if ($UiServer) { $checkArgs += @('--ui-server', $UiServer) }
if ($MeasureTiming) { $checkArgs += '--measure-timing' }
$webotsArgs = @('--batch', '--mode=fast', '--minimize')
$window = 'Hidden'
if ($Command) {
    $checkArgs += @('--agent-root', "$AgentRoot", '--provider', $Provider, '--command', $Command, '--max-decisions', [string]$MaxDecisions)
    if ($ExpectedFixtureKey) {
        $checkArgs += @('--expected-fixture-key', $ExpectedFixtureKey)
    }
    if ($RecordPov) {
        $checkArgs += '--record-pov'
    }
    $webotsArgs = @('--batch', '--mode=realtime')
    $window = 'Normal'
}
# Validate before creating a world or starting Webots/the agent.
python -m simulation.check_http_fetch @checkArgs --preflight-only
if ($LASTEXITCODE -ne 0) { throw 'Launcher preflight failed. No Webots or model process started.' }
$exe = (Resolve-Path -LiteralPath $WebotsExe).Path
$output = Join-Path $PWD ('runs/bridge-check-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $output | Out-Null
Copy-Item simulation/worlds,simulation/controllers -Destination $output -Recurse
$world = Join-Path $output 'worlds/fetch_room.wbt'
(Get-Content $world -Raw).Replace('controller "scene_preview"','controller "bridge"') | Set-Content $world -Encoding ascii
$env:ROVER_WEBOTS_PORT = [string]$BridgePort
# Append the generated world only after preflight has succeeded.
$webotsArgs += ('"' + $world + '"')
$webotsArgs += @("--stdout", "--stderr")
$p=Start-Process $exe -ArgumentList $webotsArgs -WindowStyle $window -RedirectStandardOutput (Join-Path $output "stdout.log") -RedirectStandardError (Join-Path $output "stderr.log") -PassThru
try {
 python -m simulation.check_http_fetch @checkArgs
 $testCode=$LASTEXITCODE
} finally {
 if (-not $p.HasExited) { Stop-Process -InputObject $p }
    Write-Host "Webots bridge log: $(Join-Path $output 'bridge.log')"
}
exit $testCode
