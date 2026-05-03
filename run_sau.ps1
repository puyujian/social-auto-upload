param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$ArgsFromUser
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (!(Test-Path ".venv\Scripts\sau.exe")) {
  Write-Error "sau not installed. Please check .venv in this directory."
  exit 1
}
& ".venv\Scripts\sau.exe" @ArgsFromUser
exit $LASTEXITCODE