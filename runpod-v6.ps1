$ErrorActionPreference = 'Stop'
$V6Arguments = @($args)
$ProjectRoot = $PSScriptRoot
$ProjectPython = Join-Path $ProjectRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $ProjectPython)) {
    $ProjectPython = (Get-Command python -ErrorAction Stop).Source
}
$Controller = Join-Path $ProjectRoot 'deployment\runpod\v6.py'
if (-not $V6Arguments -or $V6Arguments.Count -eq 0) {
    $V6Arguments = @('--help')
}
& $ProjectPython -B $Controller @V6Arguments
exit $LASTEXITCODE
