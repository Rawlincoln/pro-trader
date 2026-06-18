# Pro Trader - background watcher: auto-commit and push on file changes
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$pollSeconds = 5
$debounceSeconds = 8
$syncScript = Join-Path $PSScriptRoot "sync-github.ps1"

function Write-Log([string]$Message, [string]$Color = "White") {
  $ts = Get-Date -Format "HH:mm:ss"
  Write-Host "[$ts] $Message" -ForegroundColor $Color
}

Write-Log "Pro Trader GitHub auto-sync watching $PSScriptRoot" "Cyan"
Write-Log "Pushes ~$debounceSeconds s after changes. Keep this window open. Ctrl+C to stop." "DarkGray"

function Test-HasChanges {
  $prev = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  git add -A 2>&1 | Out-Null
  git diff --cached --quiet 2>&1 | Out-Null
  $changed = $LASTEXITCODE -ne 0
  $ErrorActionPreference = $prev
  return $changed
}

function Invoke-Sync {
  try {
    & $syncScript -Quiet
    Write-Log "Pushed to GitHub." "Green"
  } catch {
    Write-Log "Sync failed: $_" "Red"
  }
}

$dirtySince = $null

while ($true) {
  if (Test-HasChanges) {
    if (-not $dirtySince) {
      $dirtySince = Get-Date
      Write-Log "Changes detected, waiting $debounceSeconds s..." "DarkYellow"
    } elseif (((Get-Date) - $dirtySince).TotalSeconds -ge $debounceSeconds) {
      Invoke-Sync
      $dirtySince = $null
    }
  } else {
    $dirtySince = $null
  }
  Start-Sleep -Seconds $pollSeconds
}