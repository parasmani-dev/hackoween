param(
    [Parameter(Mandatory)]
    [string]$WebotsExe
)

$ErrorActionPreference = 'Stop'
$runtime = (Resolve-Path -LiteralPath $WebotsExe).Path
$workspace = Split-Path -Parent $PSScriptRoot
$output = Join-Path $workspace ('runs/scene-check-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $output | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'worlds'), (Join-Path $PSScriptRoot 'controllers') -Destination $output -Recurse

$controller = Join-Path $output 'controllers/scene_preview/scene_preview.py'
@'
from controller import Supervisor

robot = Supervisor()
camera = robot.getDevice("front_camera")
step = int(robot.getBasicTimeStep())
camera.enable(step)
for _ in range(8):
    if robot.step(step) == -1:
        raise RuntimeError("Webots stopped before camera capture")
if (camera.getWidth(), camera.getHeight()) != (640, 480):
    raise RuntimeError("Unexpected camera dimensions")
if not camera.getImage() or camera.saveImage("frame.png", 100) != 0:
    raise RuntimeError("Camera capture failed")
robot.simulationQuit(0)
'@ | Set-Content -LiteralPath $controller -Encoding ascii

$world = Join-Path $output 'worlds/fetch_room.wbt'
$frame = Join-Path $output 'controllers/scene_preview/frame.png'
$process = Start-Process -FilePath $runtime -ArgumentList @(
    '--batch', '--mode=fast', '--minimize', '--stdout', '--stderr', ('"' + $world + '"')
) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $output 'stdout.log') `
    -RedirectStandardError (Join-Path $output 'stderr.log') -PassThru

if (-not $process.WaitForExit(30000)) {
    Stop-Process -InputObject $process -ErrorAction SilentlyContinue
    throw "Webots timed out. Logs are in $output"
}
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $frame)) {
    throw "Webots camera check failed. Logs are in $output"
}
Write-Output "640x480 camera capture saved to $frame"
