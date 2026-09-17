param(
    [switch]$ScheduledOnly,
    [switch]$SetupOnly
)

# UTF-8 BOM is intentional: Windows PowerShell 5.1 must read Chinese correctly.
$ErrorActionPreference = 'Stop'
# A parent PowerShell 7 / IDE can pass a module path that omits Windows modules.
$builtInModules = Join-Path $PSHOME 'Modules'
$env:PSModulePath = $builtInModules + ';' + $env:PSModulePath
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'

function Get-PythonInfo {
    param([string]$Executable)
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $null }
    # Skip Windows Store execution aliases: probing them may open the Store.
    if ((Split-Path -Parent $Executable) -match '\\Microsoft\\WindowsApps$') { return $null }
    try {
        $code = 'import json,sys; print(json.dumps(dict(executable=sys.executable, version=list(sys.version_info[:3]), prefix=sys.prefix, base_prefix=sys.base_prefix)))'
        $lines = @(& $Executable -I -c $code 2>$null)
        if ($LASTEXITCODE -ne 0) { return $null }
        $info = ($lines -join "`n") | ConvertFrom-Json
        if (-not $info.executable -or -not $info.version -or -not $info.prefix -or -not $info.base_prefix) { return $null }
        return $info
    } catch { return $null }
}

function Test-SupportedPython {
    param($Info)
    return ($null -ne $Info -and $Info.version[0] -eq 3 -and $Info.version[1] -ge 11)
}

function Find-Pythons {
    $paths = New-Object 'System.Collections.Generic.List[string]'
    foreach ($name in @('python.exe', 'python3.exe')) {
        foreach ($command in @(Get-Command $name -All -CommandType Application -ErrorAction SilentlyContinue)) {
            $paths.Add($command.Source)
        }
    }
    # The Python launcher knows about installations that are not on PATH.
    foreach ($launcher in @(Get-Command py.exe -All -CommandType Application -ErrorAction SilentlyContinue)) {
        if ((Split-Path -Parent $launcher.Source) -match '\\Microsoft\\WindowsApps$') { continue }
        try {
            $listing = @(& $launcher.Source -0p 2>$null)
            foreach ($line in $listing) {
                if ([string]$line -match '([A-Za-z]:\\.*?python(?:3(?:\.\d+)?)?\.exe)\s*$') {
                    $paths.Add($Matches[1])
                }
            }
        } catch { }
    }
    # PEP 514 registry entries include per-user, all-users and 32-bit installs.
    foreach ($root in @('HKCU:\Software\Python', 'HKLM:\Software\Python',
                         'HKLM:\Software\WOW6432Node\Python')) {
        foreach ($key in @(Get-ChildItem -LiteralPath $root -Recurse -ErrorAction SilentlyContinue |
                           Where-Object { $_.PSChildName -eq 'InstallPath' })) {
            $executable = $key.GetValue('ExecutablePath')
            if (-not $executable -and $key.GetValue('')) {
                $executable = Join-Path $key.GetValue('') 'python.exe'
            }
            if ($executable) { $paths.Add([string]$executable) }
        }
    }
    $seen = @{}
    foreach ($path in ($paths | Select-Object -Unique)) {
        $info = Get-PythonInfo $path
        if ($info -and -not $seen.ContainsKey($info.executable)) {
            $seen[$info.executable] = $true
            $info
        }
    }
}

function Select-Python {
    $all = @(Find-Pythons)
    $supported = @($all | Where-Object { Test-SupportedPython $_ } |
                   Sort-Object { [version]($_.version -join '.') } -Descending)
    foreach ($info in @($all | Where-Object { -not (Test-SupportedPython $_) })) {
        Write-Host "忽略不兼容版本：Python $($info.version -join '.') — $($info.executable)"
    }
    if ($supported.Count -eq 1) {
        Write-Host "使用 Python $($supported[0].version -join '.')：$($supported[0].executable)"
        return $supported[0].executable
    }
    if ($supported.Count -gt 1) {
        Write-Host '检测到多个可用 Python，请自行选择：'
        for ($index = 0; $index -lt $supported.Count; $index++) {
            Write-Host "  $($index + 1). Python $($supported[$index].version -join '.') — $($supported[$index].executable)"
        }
        while ($true) {
            $choice = Read-Host "输入编号 1-$($supported.Count)，或 Q 退出"
            if ($choice -eq 'q') { throw '用户取消 Python 选择。' }
            $number = 0
            if ([int]::TryParse($choice, [ref]$number) -and $number -ge 1 -and $number -le $supported.Count) {
                return $supported[$number - 1].executable
            }
            Write-Host '编号无效，请重新输入。'
        }
    }
    Write-Host '未检测到可用的 Python 3.11+。'
    Write-Host '请安装 Python（安装时勾选 Add python.exe to PATH），然后重新运行 start.cmd。'
    Write-Host '安装地址：https://www.python.org/downloads/windows/'
    $manual = Read-Host '如已安装但未识别，请输入 python.exe 完整路径；直接回车退出'
    if ($manual) {
        $info = Get-PythonInfo ($manual.Trim().Trim('"'))
        if (Test-SupportedPython $info) { return $info.executable }
    }
    throw '缺少可用 Python 3.11+，未创建环境。'
}

function Invoke-PythonChecked {
    param([string]$Python, [string[]]$Arguments, [string]$Failure)
    & $Python -X utf8 @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) { throw $Failure }
}

function Initialize-Environment {
    param([string]$Project)
    $venv = Join-Path $Project '.venv'
    $python = Join-Path $venv 'Scripts\python.exe'
    $info = Get-PythonInfo $python
    $valid = (Test-SupportedPython $info) -and
             ([IO.Path]::GetFullPath($info.prefix).TrimEnd('\') -eq [IO.Path]::GetFullPath($venv).TrimEnd('\')) -and
             ($info.prefix -ne $info.base_prefix)
    if (-not $valid) {
        $base = Select-Python
        if (Test-Path -LiteralPath $venv) {
            # Preserve an incompatible or interrupted environment instead of deleting it.
            $backup = '.venv.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
            Write-Host "原 .venv 无法使用，保留为 $backup，随后重建。"
            Rename-Item -LiteralPath $venv -NewName $backup
        }
        Write-Host '正在创建项目虚拟环境 .venv ...'
        Invoke-PythonChecked $base @('-I', '-m', 'venv', $venv) '创建虚拟环境失败，请检查 Python 安装、目录权限和磁盘空间。'
        $info = Get-PythonInfo $python
        if (-not (Test-SupportedPython $info)) { throw '新建的虚拟环境无法启动。' }
    } else {
        Write-Host "复用项目环境：Python $($info.version -join '.') — $python"
    }
    $requirements = Join-Path $Project 'requirements.txt'
    $hash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
    $stamp = Join-Path $venv '.mabang-requirements.sha256'
    $installed = ''
    if (Test-Path -LiteralPath $stamp) { $installed = (Get-Content -LiteralPath $stamp -Raw).Trim() }
    $healthy = $false
    if ($installed -eq $hash) {
        try {
            & $python -I -c "import DrissionPage, requests, filelock, tomllib; from zoneinfo import ZoneInfo; ZoneInfo('Asia/Shanghai'); ZoneInfo('Etc/GMT+3')" 2>$null
            if ($LASTEXITCODE -eq 0) {
                & $python -I -m pip check 2>$null | Out-Host
                $healthy = ($LASTEXITCODE -eq 0)
            }
        } catch { $healthy = $false }
    }
    if (-not $healthy) {
        $hasPip = $false
        try {
            & $python -I -m pip --version 2>$null | Out-Host
            $hasPip = ($LASTEXITCODE -eq 0)
        } catch { }
        if (-not $hasPip) {
            Invoke-PythonChecked $python @('-I', '-m', 'ensurepip', '--upgrade') '初始化 pip 失败，请修复 Python 安装。'
        }
        Write-Host '首次安装、依赖变更或环境检查失败，正在按 requirements.txt 安装依赖 ...'
        Invoke-PythonChecked $python @('-I', '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '-r', $requirements) '依赖安装失败。请检查网络/代理后重新运行 start.cmd；不会继续启动任务。'
        Invoke-PythonChecked $python @('-I', '-m', 'pip', 'check') '依赖存在冲突，请根据上方输出修复环境后重试。'
        Invoke-PythonChecked $python @('-I', '-c', "import DrissionPage, requests, filelock, tomllib; from zoneinfo import ZoneInfo; ZoneInfo('Asia/Shanghai'); ZoneInfo('Etc/GMT+3')") '依赖导入或时区检查失败，请检查上方错误。'
        [IO.File]::WriteAllText($stamp, $hash, [Text.Encoding]::ASCII)
    } else {
        Write-Host '依赖已就绪，requirements.txt 未变更，无需重新下载。'
    }
    return $python
}

function Start-Mabang {
    param([string]$Project, [switch]$OnlySetup, [switch]$OnlyScheduled)
    Set-Location -LiteralPath $Project
    Write-Host "项目目录：$Project"
    $python = Initialize-Environment $Project
    $config = Join-Path $Project 'config.toml'
    $created = -not (Test-Path -LiteralPath $config)
    if ($created) {
        Copy-Item -LiteralPath (Join-Path $Project 'config.example.toml') -Destination $config
        Write-Host '已生成 config.toml，请填写马帮账号、飞书凭证和六张表 ID；需要上传时设置 feishu.enabled=true。'
        Write-Host '电脑还需安装 Chrome/Chromium；找不到浏览器时，在 browser.executable 填写路径。'
        if (-not $OnlySetup) {
            Write-Host '即将打开配置文件；保存并关闭记事本后回到此窗口。'
            Start-Process -FilePath notepad.exe -ArgumentList ('"{0}"' -f $config) -Wait
            [void](Read-Host '确认配置已保存，按回车继续；Ctrl+C 退出')
        }
    }
    Invoke-PythonChecked $python @('-c', "from pathlib import Path; from mabang_sync.config import load_config; load_config(Path('config.toml')); print('Config OK')") 'config.toml 配置无效，请修正上方错误后重新运行。'
    if ($OnlySetup) {
        Write-Host '环境准备完成。运行 start.cmd 可开始采集与常驻调度。'
        return 0
    }
    if ($created) {
        $check = "from pathlib import Path; from mabang_sync.config import load_config; c=load_config(Path('config.toml')); raise SystemExit(0 if c['account']['username'] and c['account']['password'] else 2)"
        & $python -c $check
        if ($LASTEXITCODE -ne 0) { throw '首次配置尚未填写马帮用户名和密码，请编辑 config.toml 后重新运行。' }
    }
    $arguments = @('-m', 'mabang_sync', 'schedule', '--config', $config)
    if (-not $OnlyScheduled) { $arguments += '--now' }
    Write-Host '启动常驻任务；按 Ctrl+C 停止。关闭此窗口或电脑休眠会影响定时执行。'
    if ($OnlyScheduled) { Write-Host '等待配置中的下一个北京时间。' }
    else { Write-Host '立即采集一次，随后按配置中的北京时间每日执行。' }
    & $python -X utf8 @arguments | Out-Host
    return $LASTEXITCODE
}

# Dot-sourcing exposes functions to the local test harness without starting a task.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        $project = Split-Path -Parent $PSScriptRoot
        $result = Start-Mabang $project -OnlySetup:$SetupOnly -OnlyScheduled:$ScheduledOnly
        exit $result
    } catch {
        Write-Host "启动失败：$($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}
