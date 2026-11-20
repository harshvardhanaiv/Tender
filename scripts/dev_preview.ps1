<#
Local preview of the app with a test sign-in, for checking UI changes in a browser.

    powershell -ExecutionPolicy Bypass -File scripts\dev_preview.ps1          # copy the code in and (re)start
    powershell -ExecutionPolicy Bypass -File scripts\dev_preview.ps1 -Stop    # remove the preview container

Then open http://127.0.0.1:8093/api/auth/bypass : it signs in as the local "testuser"
(created on first use with 100 credits and "Test Company Ltd") and lands on the app.

Why a separate container on a copy of the code:
  - the dev container (8092) mounts the repo and reloads on every .py save; a second Flask
    server on the same mount once made it exit, so the preview runs on its own copy;
  - sign-in bypass, email, payments and Outreach sending are all switched off/on here only,
    never in .env or docker-compose.yml.
It uses the same local database as the dev container (testuser's rows live there).
Run this script again after changing code: it re-copies the files and restarts the container.
AI features (bid writer, summaries) still call the real AI provider with the key from .env.
#>
param(
    [switch]$Stop,
    [int]$Port = 8093
)
$ErrorActionPreference = "Stop"
$Name = "tenderflow-preview"
$Repo = Split-Path -Parent $PSScriptRoot
# Not under AppData: packaged apps (e.g. the Claude desktop app) get their AppData writes redirected
# to a private folder that Docker cannot see, so the container would mount an empty directory.
$Copy = Join-Path $env:USERPROFILE ".tenderflow-preview\app"

docker rm -f $Name 2>$null | Out-Null
if ($Stop) { Write-Host "Preview container removed."; exit 0 }

$db = docker ps --filter "name=tender_project_v2-db-1" --filter "status=running" -q
if (-not $db) { throw "The database container is not running. Start it with: docker compose up -d db" }

# Copy the code (not .git, the 2.7 GB data folder or scratch files). /MIR also removes files deleted in the repo.
New-Item -ItemType Directory -Force $Copy | Out-Null
& "$env:SystemRoot\System32\robocopy.exe" $Repo $Copy /MIR /NFL /NDL /NJH /NJS /NP /XD .git data scratch .claude __pycache__ .pytest_cache node_modules | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Copying the code failed (robocopy exit $LASTEXITCODE)." }

docker run -d --name $Name `
    --network tender_project_v2_default `
    --env-file (Join-Path $Repo ".env") `
    -e DB_HOST=db -e DB_PORT=5432 `
    -e ENABLE_AUTH_BYPASS=1 `
    -e ENABLE_SCHEDULERS=0 -e ENABLE_EMAIL_SCHEDULER=0 `
    -e SMTP_HOST= -e SMTP_USER= -e SMTP_PASSWORD= `
    -e STRIPE_SECRET_KEY= -e STRIPE_WEBHOOK_SECRET= `
    -e OUTREACH_ALLOW_LIVE=0 -e SENTRY_DSN= `
    -e SESSION_COOKIE_SECURE=0 `
    --dns 8.8.8.8 `
    -p "127.0.0.1:${Port}:8092" `
    -v "${Copy}:/app" `
    tender_project_v2-app:latest `
    flask --app server:create_app run --host 0.0.0.0 --port 8092 | Out-Null
if ($LASTEXITCODE -ne 0) { throw "docker run failed." }

Write-Host "Waiting for the preview to start..."
for ($i = 0; $i -lt 90; $i++) {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:$Port/login.html"
        if ($r.StatusCode -eq 200) {
            Write-Host "Ready. Sign in as the test user: http://127.0.0.1:$Port/api/auth/bypass"
            exit 0
        }
    } catch { }
    if (-not (docker ps -q --filter "name=$Name" --filter "status=running")) { break }
    Start-Sleep -Seconds 2
}
docker logs --tail 40 $Name
throw "The preview did not start within 3 minutes (logs above)."
