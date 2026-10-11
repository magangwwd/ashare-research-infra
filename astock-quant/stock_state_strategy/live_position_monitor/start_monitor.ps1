param(
    [switch]$DryRun,
    [switch]$Once
)

$monitorRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$arguments = @((Join-Path $monitorRoot 'monitor.py'))
if (-not $Once) {
    $arguments += '--loop'
}
if ($DryRun) {
    $arguments += '--dry-run'
}

& python @arguments
exit $LASTEXITCODE
