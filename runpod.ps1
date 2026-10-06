$ErrorActionPreference = 'Stop'
$RunpodArguments = @($args)
$ProjectRoot = $PSScriptRoot
$ProjectPython = Join-Path $ProjectRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $ProjectPython)) {
    $ProjectPython = (Get-Command python -ErrorAction Stop).Source
}
$Controller = Join-Path $ProjectRoot 'deployment\runpod\sync.py'
if (-not $RunpodArguments -or $RunpodArguments.Count -eq 0) {
    $RunpodArguments = @('--help')
}
& $ProjectPython -B $Controller @RunpodArguments
exit $LASTEXITCODE
