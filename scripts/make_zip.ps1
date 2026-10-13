# Builds a zip of COMMITTED files only (git archive HEAD). Exit 0 = ok, 1 = failed.
# Uncommitted/untracked/ignored files are never included.
param(
    [string]$Repo = (Split-Path -Parent $PSScriptRoot),
    [string]$Out  = (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\submission.zip')
)

# 'Continue': in Windows PowerShell 5.1, 'Stop' turns git's stderr into a terminating error.
$ErrorActionPreference = 'Continue'
$null = git -C $Repo rev-parse --verify HEAD 2>&1
if ($LASTEXITCODE -ne 0) { Write-Output "make_zip: repo has no commits; nothing to archive"; exit 1 }

$dirty = git -C $Repo status --porcelain
if ($dirty) { Write-Output "make_zip: WARNING uncommitted changes exist and are NOT in the zip" }

$dir = Split-Path -Parent $Out
if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
git -C $Repo archive --format=zip -o $Out HEAD
if ($LASTEXITCODE -ne 0) { Write-Output "make_zip: git archive failed"; exit 1 }
& (Join-Path $PSScriptRoot 'check_zip.ps1') -Zip $Out
if ($LASTEXITCODE -ne 0) { Remove-Item -LiteralPath $Out -Force; Write-Output "make_zip: zip failed the entry check and was deleted"; exit 1 }
Write-Output "make_zip: wrote $Out"
exit 0
