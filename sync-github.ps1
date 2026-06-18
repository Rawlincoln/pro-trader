# Pro Trader — commit and push local changes to GitHub
param(
  [string]$Message = "",
  [switch]$Quiet
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".git")) {
  throw "No git repo in $PSScriptRoot. Run .\deploy.ps1 -GitHubUser Rawlincoln first."
}

$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$remote = git remote get-url origin 2>$null
$ErrorActionPreference = $prevErr
if (-not $remote) {
  throw "No git remote. Run .\deploy.ps1 -GitHubUser Rawlincoln first."
}

$prev = $ErrorActionPreference
$ErrorActionPreference = "Continue"
git add -A 2>&1 | Out-Null
git diff --cached --quiet 2>&1 | Out-Null
$ErrorActionPreference = $prev
if ($LASTEXITCODE -eq 0) {
  if (-not $Quiet) { Write-Host "No changes to sync." -ForegroundColor DarkGray }
  exit 0
}

if (-not $Message) {
  $files = @(git diff --cached --name-only)
  $count = $files.Count
  $preview = ($files | Select-Object -First 4) -join ", "
  if ($count -gt 4) { $preview += " (+$($count - 4) more)" }
  $Message = "Pro Trader: update $preview"
}

git commit -m $Message
if ($LASTEXITCODE -ne 0) { throw "git commit failed" }

if (-not $Quiet) { Write-Host "Pushing to $remote ..." -ForegroundColor Cyan }
git push origin main
if ($LASTEXITCODE -ne 0) { throw "git push failed" }

if (-not $Quiet) {
  Write-Host "GitHub synced." -ForegroundColor Green
  Write-Host "https://github.com/Rawlincoln/pro-trader" -ForegroundColor DarkGray
}