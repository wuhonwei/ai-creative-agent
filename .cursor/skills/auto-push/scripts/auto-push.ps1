# Commit and push workspace changes to origin (Windows fallback)
$ErrorActionPreference = 'Stop'

$git = 'C:\Program Files\Git\cmd\git.exe'
if (-not (Test-Path $git)) {
    $git = 'git'
}

$root = & $git rev-parse --show-toplevel 2>$null
if (-not $root) {
    Write-Host '[auto-push] Not a git repo, skip'
    exit 0
}
Set-Location $root

$hasOrigin = & $git remote get-url origin 2>$null
if (-not $hasOrigin) {
    Write-Host '[auto-push] No origin remote, skip'
    exit 0
}

& $git add -A

$unstage = @(
    'backend/.env',
    '.cursor/skills/.skills_store_lock.json',
    'backend/_chapters_list.txt',
    'backend/_humanize_batch.log',
    'backend/_humanize_progress.json',
    'backend/_list_chapters.py'
)
foreach ($path in $unstage) {
    & $git reset HEAD -- $path 2>$null
}

& $git diff --cached --quiet 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host '[auto-push] No staged changes, skip'
    exit 0
}

$ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$msg = "Auto-sync: agent code changes ($ts)"

& $git commit -m $msg
if ($LASTEXITCODE -ne 0) {
    Write-Host '[auto-push] Commit failed or nothing to commit'
    exit 0
}

& $git push -u origin HEAD
if ($LASTEXITCODE -eq 0) {
    $line = & $git log -1 --oneline
    Write-Host "[auto-push] Done: $line"
} else {
    Write-Host '[auto-push] Push failed, check network or credentials' -ForegroundColor Red
}
