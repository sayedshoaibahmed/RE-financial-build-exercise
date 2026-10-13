# Checks the entry names of a zip. Exit 0 = clean, 1 = forbidden entry (or unreadable zip).
# Forbidden: .env (not .env.example), .venv or connector/.venv, outputs/, evals/results/,
# .git/, __pycache__, tokens.json, *.token. Never opens any entry, only reads names.
param([Parameter(Mandatory = $true)][string]$Zip)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
try { $archive = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $Zip).Path) }
catch { Write-Output "check_zip: cannot read $Zip"; exit 1 }
$names = @($archive.Entries | ForEach-Object { $_.FullName })
$archive.Dispose()

$bad = 0
foreach ($name in $names) {
    $p = $name.Replace('\', '/')
    $segs = $p.Split('/')
    $leaf = $segs[-1]
    $why = $null
    if ($leaf -eq '.env' -or ($leaf -like '.env.*' -and $leaf -ne '.env.example')) { $why = 'env file' }
    elseif ($segs -contains '.venv') { $why = '.venv' }
    elseif ($p -like 'outputs/*') { $why = 'outputs/' }
    elseif ($p -like 'evals/results/*') { $why = 'evals/results/' }
    elseif ($segs -contains '.git') { $why = '.git/' }
    elseif ($segs -contains '__pycache__') { $why = '__pycache__' }
    elseif ($leaf -eq 'tokens.json') { $why = 'tokens.json' }
    elseif ($leaf -like '*.token') { $why = '*.token' }
    if ($why) { Write-Output "FORBIDDEN-ENTRY  $name  ($why)"; $bad++ }
}
if ($bad -gt 0) { Write-Output "check_zip: $bad forbidden entr(ies)"; exit 1 }
Write-Output "check_zip: clean ($($names.Count) entries)"
exit 0
