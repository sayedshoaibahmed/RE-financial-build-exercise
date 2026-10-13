# Scans a folder for likely secrets. Exit 0 = clean, 1 = findings.
# Never prints the matched text; only file, line number and rule name.
# Files with forbidden names (.env, tokens*, keys) are flagged by NAME and never opened.
param([string]$Path = (Split-Path -Parent $PSScriptRoot))

$ErrorActionPreference = 'Stop'
$skipDirs = '\\(\.git|\.venv|outputs|__pycache__|\.pytest_cache|dist)\\'
$textExt  = '.py','.ps1','.md','.txt','.yaml','.yml','.json','.ini','.cfg','.toml','.csv','.sh','.js','.html'
$badNames = '^\.env($|\.)', '^tokens', '\.pem$', '\.key$', '^credentials'
$rules = [ordered]@{
    'private-key'      = '-----BEGIN [A-Z ]*PRIVATE KEY-----'
    'anthropic-key'    = 'sk-ant-[A-Za-z0-9_-]{20,}'
    'aws-key'          = 'AKIA[0-9A-Z]{16}'
    'jwt'              = 'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.'
    'credential-assign' = '(?i)(client_secret|refresh_token|access_token|api[_-]?key|secret|password)\s*[:=]\s*[''"]?[A-Za-z0-9_\-\.]{16,}'
}

$root = (Resolve-Path -LiteralPath $Path).Path
$findings = 0
Get-ChildItem -LiteralPath $root -Recurse -File -Force | ForEach-Object {
    if (("\" + $_.FullName.Substring($root.Length).TrimStart('\')) -match $skipDirs) { return }
    $rel = $_.FullName.Substring($root.Length).TrimStart('\')
    foreach ($b in $badNames) {
        if ($_.Name -match $b) { Write-Output "FORBIDDEN-NAME  $rel"; $script:findings++; return }
    }
    if ($textExt -notcontains $_.Extension.ToLower()) { return }
    $n = 0
    foreach ($line in [System.IO.File]::ReadLines($_.FullName)) {
        $n++
        foreach ($k in $rules.Keys) {
            if ($line -match $rules[$k]) { Write-Output "$k  ${rel}:$n"; $script:findings++ }
        }
    }
}
if ($findings -gt 0) { Write-Output "secret_scan: $findings finding(s)"; exit 1 }
Write-Output "secret_scan: clean"
exit 0
