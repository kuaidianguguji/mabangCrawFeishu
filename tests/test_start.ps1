param([switch]$FreshInstall)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
. (Join-Path $project 'scripts\start.ps1')

function Assert-True($Value, $Message) {
    if (-not $Value) { throw "FAIL: $Message" }
}

$current = Get-PythonInfo (Join-Path $project '.venv\Scripts\python.exe')
Assert-True (Test-SupportedPython $current) 'test environment requires Python 3.11+'
Assert-True (-not (Test-SupportedPython ([pscustomobject]@{ version = @(3, 10, 9) }))) 'reject Python 3.10'
Assert-True (-not (Test-SupportedPython $null)) 'reject unavailable Python'
$detected = @(Find-Pythons)
Assert-True ($detected.Count -gt 0) 'discover installed Python using launcher/PATH/registry'

$originalFind = ${function:Find-Pythons}
$originalRead = Get-Item Function:\Read-Host -ErrorAction SilentlyContinue
$script:answers = New-Object 'System.Collections.Generic.Queue[string]'
function Read-Host { param($Prompt); return $script:answers.Dequeue() }
try {
    function Find-Pythons {
        [pscustomobject]@{ executable = 'C:\Python311\python.exe'; version = @(3, 11, 9) }
        [pscustomobject]@{ executable = 'C:\Python312\python.exe'; version = @(3, 12, 1) }
    }
    $script:answers.Enqueue('invalid')
    $script:answers.Enqueue('2')
    Assert-True ((Select-Python) -eq 'C:\Python311\python.exe') 'multiple versions require explicit valid selection'
    function Find-Pythons { [pscustomobject]@{ executable = 'C:\single\python.exe'; version = @(3, 12, 1) } }
    Assert-True ((Select-Python) -eq 'C:\single\python.exe') 'single usable interpreter auto selected'
    function Find-Pythons { }
    $script:answers.Enqueue('')
    $failed = $false
    try { Select-Python } catch { $failed = $true }
    Assert-True $failed 'missing Python stops with instructions'
    $script:answers.Enqueue($current.executable)
    Assert-True ((Select-Python) -eq $current.executable) 'manual executable path fallback'
} finally {
    Set-Item Function:\Find-Pythons $originalFind
    if ($originalRead) { Set-Item Function:\Read-Host $originalRead.ScriptBlock }
    else { Remove-Item Function:\Read-Host }
}

$failed = $false
try { Invoke-PythonChecked $current.executable @('-c', 'raise SystemExit(7)') 'expected failure' }
catch { $failed = $true }
Assert-True $failed 'native command failure must stop setup'

if ($FreshInstall) {
    $fixture = Join-Path $project ('runtime\启动测试 with spaces-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $fixture | Out-Null
    Copy-Item -LiteralPath (Join-Path $project 'requirements.txt') -Destination $fixture
    Copy-Item -LiteralPath (Join-Path $project 'config.example.toml') -Destination $fixture
    Copy-Item -LiteralPath (Join-Path $project 'mabang_sync') -Destination $fixture -Recurse
    New-Item -ItemType Directory -Path (Join-Path $fixture '.venv') | Out-Null
    $script:basePython = Join-Path $current.base_prefix 'python.exe'
    $originalSelect = ${function:Select-Python}
    function Select-Python { return $script:basePython }
    try {
        Assert-True ((Start-Mabang $fixture -OnlySetup) -eq 0) 'fresh environment setup'
        Assert-True (Test-Path -LiteralPath (Join-Path $fixture 'config.toml')) 'first run creates config'
        Assert-True (@(Get-ChildItem -LiteralPath $fixture -Directory -Filter '.venv.backup-*').Count -eq 1) 'broken environment preserved'
        $stamp = Join-Path $fixture '.venv\.mabang-requirements.sha256'
        $stampTime = (Get-Item -LiteralPath $stamp).LastWriteTimeUtc
        $configHash = (Get-FileHash -LiteralPath (Join-Path $fixture 'config.toml')).Hash
        Assert-True ((Start-Mabang $fixture -OnlySetup) -eq 0) 'second launch reuses environment'
        Assert-True ((Get-Item -LiteralPath $stamp).LastWriteTimeUtc -eq $stampTime) 'healthy environment skips reinstall'
        Assert-True ((Get-FileHash -LiteralPath (Join-Path $fixture 'config.toml')).Hash -eq $configHash) 'existing config preserved'
        Write-Host "Local test fixture retained at: $fixture"
    } finally {
        Set-Item Function:\Select-Python $originalSelect
        Set-Location -LiteralPath $project
    }
}
Write-Host 'PASS: Python discovery, version selection, missing Python, failure handling and requested setup checks'
