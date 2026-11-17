# Backup on Windows (PowerShell). Schedule with Task Scheduler, daily 02:30:
#   powershell -ExecutionPolicy Bypass -File C:\path\to\LOAN_TRACKER\deploy\backup.ps1
# Writes a compressed, password-protected 7-Zip archive if 7-Zip is installed,
# otherwise a plain .sql file (then keep the backups folder on an encrypted drive).
# Copy the backups folder offsite (OneDrive / Google Drive / USB).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
New-Item -ItemType Directory -Force backups | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$sql = "backups\loan_tracker_$stamp.sql"
docker compose exec -T db pg_dump -U loan_tracker -d loan_tracker --no-owner | Out-File -Encoding utf8 $sql
$sevenZip = "C:\Program Files\7-Zip\7z.exe"
if ((Test-Path $sevenZip) -and $env:BACKUP_PASSPHRASE) {
    & $sevenZip a -t7z -mhe=on "-p$($env:BACKUP_PASSPHRASE)" "$sql.7z" $sql | Out-Null
    Remove-Item $sql
    Write-Host "wrote $sql.7z"
} else {
    Write-Host "wrote $sql (not encrypted: install 7-Zip and set BACKUP_PASSPHRASE)"
}
Get-ChildItem backups -Filter "loan_tracker_*" | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-60) } | Remove-Item

# RESTORE into an empty database:
#   docker compose exec -T db createdb -U loan_tracker loan_tracker_restore
#   Get-Content backups\FILE.sql | docker compose exec -T db psql -U loan_tracker -d loan_tracker_restore
