param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = 'Stop'

$baseRoot = Join-Path $env:USERPROFILE '.codex\memories\scrapperlanas-pytest-runs'
New-Item -ItemType Directory -Force -Path $baseRoot | Out-Null

$runId = [guid]::NewGuid().ToString('N')
$baseTemp = Join-Path $baseRoot $runId
New-Item -ItemType Directory -Force -Path $baseTemp | Out-Null

python -m pytest `
    -p no:cacheprovider `
    -o tmp_path_retention_policy=none `
    --basetemp="$baseTemp" `
    @PytestArgs
