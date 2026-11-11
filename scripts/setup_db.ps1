# One-time local database setup for ipmon.
# Prompts for the postgres superuser password, creates the ipmon role and
# database with a random password, and writes DATABASE_URL to ../.env.
$ErrorActionPreference = "Stop"
$psql = "C:\Program Files\PostgreSQL\17\bin\psql.exe"
$envFile = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"

$secure = Read-Host "postgres superuser password" -AsSecureString
$env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))

try {
    & $psql -U postgres -h localhost -w -tAc "select 1" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not log in as postgres. Wrong password?" }

    # Letters and digits only, so the URL needs no escaping.
    $chars = (48..57) + (65..90) + (97..122)
    $pw = -join ($chars | Get-Random -Count 24 | ForEach-Object { [char]$_ })

    $roleExists = & $psql -U postgres -h localhost -w -tAc "select 1 from pg_roles where rolname='ipmon'"
    if ($roleExists -eq "1") {
        & $psql -U postgres -h localhost -w -c "ALTER ROLE ipmon LOGIN PASSWORD '$pw'" | Out-Null
        Write-Host "Role ipmon existed: password reset."
    } else {
        & $psql -U postgres -h localhost -w -c "CREATE ROLE ipmon LOGIN PASSWORD '$pw'" | Out-Null
        Write-Host "Role ipmon created."
    }

    $dbExists = & $psql -U postgres -h localhost -w -tAc "select 1 from pg_database where datname='ipmon'"
    if ($dbExists -ne "1") {
        & $psql -U postgres -h localhost -w -c "CREATE DATABASE ipmon OWNER ipmon" | Out-Null
        Write-Host "Database ipmon created."
    } else {
        Write-Host "Database ipmon already exists."
    }

    # UTF-8 without BOM.
    [IO.File]::WriteAllText($envFile, "DATABASE_URL=postgresql://ipmon:$pw@localhost:5432/ipmon`n")
    Write-Host "Wrote $envFile"

    $env:PGPASSWORD = $pw
    $who = & $psql -U ipmon -h localhost -d ipmon -w -tAc "select current_user"
    if ($who -eq "ipmon") { Write-Host "SUCCESS: connected as ipmon." }
    else { throw "Final connection test failed." }
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}
