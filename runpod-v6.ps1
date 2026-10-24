$ErrorActionPreference = 'Stop'
$V6Arguments = @($args)
$ProjectRoot = $PSScriptRoot
$ProjectPython = Join-Path $ProjectRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $ProjectPython)) {
    $ProjectPython = (Get-Command python -ErrorAction Stop).Source
}
$Controller = Join-Path $ProjectRoot 'deployment\runpod\local_v6.py'

if (-not $V6Arguments -or $V6Arguments.Count -eq 0) {
    Write-Host 'Runpod v6: local working-set + native Syncthing. Prepare is offline; launch requires an explicitly authorized Pod.'
    Write-Host 'Runbook: docs/plans/runpod-v6-agent-operational-plan.md'
    $V6Arguments = @('--help')
}
& $ProjectPython -B $Controller @V6Arguments
exit $LASTEXITCODE
