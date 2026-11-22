$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ProjectPython = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $ProjectPython)) {
    $ProjectPython = "python"
}

& $ProjectPython (Join-Path $ProjectDir "scripts\build_app.py")
exit $LASTEXITCODE
