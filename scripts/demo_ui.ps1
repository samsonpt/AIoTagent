# 一键演示：先跑 demo_live（默认无云端），再启动 Streamlit 看板。
# 用法:
#   .\scripts\demo_ui.ps1
#   .\scripts\demo_ui.ps1 -Db runs/demo_twin.db -Ticks 24 -NoCloud
#   .\scripts\demo_ui.ps1 -Live   # 仿真与看板并行（边跑边看）

param(
    [string]$Db = "runs/demo.db",
    [int]$Ticks = 24,
    [string]$Scenario = "bench/scenarios/nominal.yaml",
    [switch]$NoCloud = $true,
    [switch]$WithCloud,
    [switch]$Live,
    [switch]$SkipSim
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$python = (Get-Command python -ErrorAction Stop).Source
$demoArgs = @(
    "scripts/demo_live.py",
    "--db", $Db,
    "--ticks", "$Ticks",
    "--scenario", $Scenario,
    "--overwrite"
)
if ($WithCloud) {
    # 显式开云端
} elseif ($NoCloud) {
    $demoArgs += "--no-cloud"
}

Write-Host "cwd: $Root"
Write-Host "db : $Db"

if (-not $SkipSim) {
    if ($Live) {
        Write-Host "并行启动 demo_live（边跑边看）..."
        Start-Process -FilePath $python -ArgumentList $demoArgs -WorkingDirectory $Root -NoNewWindow
        Start-Sleep -Seconds 2
    } else {
        Write-Host "运行 demo_live..."
        & $python @demoArgs
        if ($LASTEXITCODE -ne 0) {
            throw "demo_live 失败，exit=$LASTEXITCODE"
        }
    }
}

Write-Host "启动 Streamlit: python -m streamlit run ui/app.py -- --db $Db"
& $python -m streamlit run ui/app.py -- --db $Db
