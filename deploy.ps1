# Pro Trader - push to GitHub then deploy on Render (free HTTPS hosting)
param(
  [string]$GitHubUser = "",
  [string]$RepoName = "pro-trader"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".git")) {
  git init
  git branch -M main
}

if (-not $GitHubUser) {
  Write-Host ""
  Write-Host "=== Pro Trader deploy ===" -ForegroundColor Cyan
  Write-Host "1. Create a new repo at https://github.com/new (name: pro-trader)"
  Write-Host "2. Run: .\deploy.ps1 -GitHubUser Rawlincoln"
  Write-Host ""
  Write-Host "3. Open https://dashboard.render.com/blueprints"
  Write-Host "   Connect the repo - Render reads render.yaml automatically."
  exit 0
}

$remote = "https://github.com/$GitHubUser/$RepoName.git"
$prevErr = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$existing = git remote get-url origin 2>$null
$ErrorActionPreference = $prevErr
if (-not $existing) {
  git remote add origin $remote
} else {
  git remote set-url origin $remote
}

& "$PSScriptRoot\sync-github.ps1" -Message "Pro Trader: deploy update"
git push -u origin main 2>$null
if ($LASTEXITCODE -ne 0) {
  git push -u origin main
}

Write-Host ""
Write-Host "Tip: run start-sync.bat to auto-push on every save." -ForegroundColor DarkGray
Write-Host "Open this link to deploy on Render (same as soccer-under-strategy):" -ForegroundColor Green
Write-Host "https://dashboard.render.com/blueprints/new?repo=https://github.com/$GitHubUser/$RepoName" -ForegroundColor Cyan
Write-Host "Render will build from render.yaml -> https://$RepoName.onrender.com"