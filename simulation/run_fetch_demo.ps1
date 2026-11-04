param(
    [string]$WebotsExe = (Join-Path (Split-Path -Parent $PSScriptRoot) 'runs/webots/msys64/mingw64/bin/webots.exe'),
    [switch]$Fast
)

$ErrorActionPreference = 'Stop'
$runtime = (Resolve-Path -LiteralPath $WebotsExe).Path
$output = Join-Path (Split-Path -Parent $PSScriptRoot) ('runs/fetch-demo-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $output -Force | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'worlds'), (Join-Path $PSScriptRoot 'controllers') -Destination $output -Recurse
$world = Join-Path $output 'worlds/fetch_room.wbt'
(Get-Content -LiteralPath $world -Raw).Replace('controller "scene_preview"', 'controller "fetch_demo"') |
    Set-Content -LiteralPath $world -Encoding ascii

$mode = if ($Fast) { '--mode=fast' } else { '--mode=realtime' }
$arguments = @('--batch', $mode, '--stdout', '--stderr', ('"' + $world + '"'))
$window = if ($Fast) { 'Hidden' } else { 'Normal' }
$stdout = Join-Path $output 'stdout.log'
$stderr = Join-Path $output 'stderr.log'
$process = Start-Process -FilePath $runtime -ArgumentList $arguments -WindowStyle $window -PassThru `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr
if (-not $process.WaitForExit(60000)) {
    Stop-Process -InputObject $process -ErrorAction SilentlyContinue
    throw "FAIL: Webots timed out. Logs are in $output"
}
Get-Content -LiteralPath $stdout, $stderr
$actions = Join-Path $output 'controllers/fetch_demo/actions.log'
if (Test-Path -LiteralPath $actions) {
    Get-Content -LiteralPath $actions
}
$result = Join-Path $output 'controllers/fetch_demo/result.txt'
if (-not (Test-Path -LiteralPath $result)) {
    throw "FAIL: Webots ended before reporting a result. Logs are in $output"
}
$status = Get-Content -LiteralPath $result -Raw
if ($process.ExitCode -ne 0 -or -not $status.StartsWith('PASS:')) {
    throw "$status. Logs are in $output"
}
Write-Output $status
Write-Output "Logs: $output"
