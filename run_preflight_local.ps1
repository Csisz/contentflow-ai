cd "C:\DEV\contentflow_ai\contentflow-ai"

$ExcelPath = "C:\DEV\contentflow_ai\contentflow-ai\SP_licin_migration_probe_CDEV_20260602.xlsx"
$ConfigPath = "C:\DEV\contentflow_ai\contentflow-ai\config\config.local.json"
$BaseName = [System.IO.Path]::GetFileNameWithoutExtension($ExcelPath)
$IssueCsv = "C:\DEV\contentflow_ai\contentflow-ai\reports\$($BaseName)_preflight_issues.csv"

Write-Host "ExcelPath:"
Write-Host $ExcelPath
Write-Host "ConfigPath:"
Write-Host $ConfigPath
Write-Host ""

Write-Host "=== ContentFlow AI local analyze ==="
.\.venv\Scripts\python.exe -m contentflow_ai.migration.cli analyze $ExcelPath --config $ConfigPath

Write-Host ""
Write-Host "=== ContentFlow AI Content Server read-only preflight ==="
.\.venv\Scripts\python.exe -m contentflow_ai.migration.cli preflight $ExcelPath --config $ConfigPath

Write-Host ""
Write-Host "=== Latest issue summary ==="

if (Test-Path $IssueCsv) {
    Import-Csv $IssueCsv |
    Group-Object code |
    Sort-Object Count -Descending |
    Format-Table Count, Name -AutoSize
} else {
    Write-Host "Issue CSV not found:"
    Write-Host $IssueCsv
}

Write-Host ""
Write-Host "Reports generated in:"
Write-Host "C:\DEV\contentflow_ai\contentflow-ai\reports"
