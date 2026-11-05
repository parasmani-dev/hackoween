# Requires PowerShell 7.4 for per-process environment variables.
# Run from any directory. Webots must use a Python interpreter with Pillow installed.
#Requires -Version 7.4
param(
    [Parameter(Mandatory)]
    [string]$WebotsExe,
    [ValidateRange(0, 40000)]
    [int]$PortOffset = 0
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$webots = (Resolve-Path -LiteralPath $WebotsExe).Path
$python = (Get-Command python).Source
$node = (Get-Command node).Source
$sitePort = 8113 + $PortOffset
$legacyPort = 8112 + $PortOffset
$runRoot = Join-Path $repo "runs/rover-lab-$sitePort"
$layoutPath = Join-Path $runRoot 'selected-layout.txt'
$children = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Start-ServiceProcess($Name, $Executable, $Arguments, $Variables) {
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $repo -Environment $Variables -WindowStyle Hidden -PassThru
    $children.Add($process)
    Write-Host "$Name PID $($process.Id)"
}

function Wait-Ready($Url) {
    $deadline = [DateTime]::UtcNow.AddSeconds(40)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return }
        } catch {
            Start-Sleep -Milliseconds 300
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for $Url"
}

foreach ($port in @($sitePort, $legacyPort, (18835 + $PortOffset), (18836 + $PortOffset), (18845 + $PortOffset), (18846 + $PortOffset), (1241 + $PortOffset), (1242 + $PortOffset))) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
    try { $listener.Start() }
    catch { throw "Port $port is already in use. Stop the existing demo or choose another -PortOffset." }
    finally { $listener.Stop() }
}

New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
'A' | Set-Content -LiteralPath $layoutPath -NoNewline -Encoding ascii

try {
    foreach ($scenario in 1, 2) {
        $bridgePort = 18825 + 10 * $scenario + $PortOffset
        $enginePort = $bridgePort + 1
        $instancePort = 1240 + $scenario + $PortOffset
        $scenarioRun = Join-Path $runRoot "scenario-$scenario"
        New-Item -ItemType Directory -Path $scenarioRun -Force | Out-Null
        $preview = Join-Path $scenarioRun 'front.jpg'
        $spectator = Join-Path $scenarioRun 'spectator.jpg'
        $environment = @{
            ROVER_WEBOTS_PORT = "$bridgePort"
            ROVER_PREVIEW_FILE = $preview
            ROVER_SPECTATOR_FILE = $spectator
            ROVER_LAB_RUN_DIR = $scenarioRun
            ROVER_DEMO_LAYOUT = $null
        }
        if ($scenario -eq 2) { $environment.ROVER_DEMO_LAYOUT = $layoutPath }
        $world = Join-Path $PSScriptRoot "worlds/scenario-$scenario.wbt"
        Start-ServiceProcess "Webots scenario $scenario" $webots @('--batch', '--minimize', '--mode=realtime', "--port=$instancePort", ('"{0}"' -f $world)) $environment
        $engineCode = "from engine.server import create_server; from simulation.adapter import WebotsAdapter; create_server(WebotsAdapter(port=$bridgePort), port=$enginePort).serve_forever()"
        Start-ServiceProcess "Engine scenario $scenario" $python ('-u -c "{0}"' -f $engineCode) @{}
        Wait-Ready "http://127.0.0.1:$enginePort/state"
        if ($scenario -eq 1) {
            Start-ServiceProcess 'Scenario 1 UI service' $node 'ui/server.mjs' @{
                ROVER_UI_PORT = "$legacyPort"
                ROVER_ENGINE_PORT = "$enginePort"
                ROVER_PREVIEW_FILE = $preview
                ROVER_SPECTATOR_FILE = $spectator
                ROVER_OFFLINE_DEMO = '1'
                ROVER_PYTHON = $python
            }
            Wait-Ready "http://127.0.0.1:$legacyPort/offline-demo"
        }
    }

    Start-ServiceProcess 'Rover Lab' $python @('rover_lab/dashboard.py', '--port', "$sitePort", '--engine-port', "$enginePort") @{
        ROVER_PREVIEW_FILE = $preview
        ROVER_SPECTATOR_FILE = $spectator
        ROVER_DEMO_LAYOUT = $layoutPath
        ROVER_LAB_RUN_DIR = $runRoot
        ROVER_LAB_SCENARIO_ONE_PORT = "$legacyPort"
    }
    $url = "http://127.0.0.1:$sitePort"
    Wait-Ready "$url/"
    Write-Output "Rover Lab: $url/"
    Write-Output "Run data: $runRoot"
    Write-Output "To verify: set ROVER_LAB_URL=$url and ROVER_LAB_RUN_DIR=$runRoot, then run python rover_lab/verify.py."
} catch {
    foreach ($process in $children) {
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
    }
    throw
}
