#Requires -Version 5.1
<#
.SYNOPSIS
    Registers (or removes) the 9:30 AM daily ad build.

.DESCRIPTION
    Runs `ims-ads.py sync` every morning: pulls whatever Codex pushed to GitHub,
    verifies each package against that unit's own live listing, and files it into
    ready/ or issues/. It never publishes.

    Use -Command run instead to have this system read the site and build the package
    itself (production.mode = 'produce').

    TIME ZONE: Windows Task Scheduler fires on LOCAL time. This dealership is in
    Langley, BC, which is Pacific, so 09:30 local is 9:30 AM Pacific. If this is ever
    run on a machine in another zone, adjust -Time accordingly - the task has no way
    to know what Pacific means.

.EXAMPLE
    .\Register-Schedule.ps1
    Every day at 09:30.

.EXAMPLE
    .\Register-Schedule.ps1 -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday
    Weekdays only.

.EXAMPLE
    .\Register-Schedule.ps1 -DryRun
    Schedules a dry run instead - useful for a week of watching before going live.

.EXAMPLE
    .\Register-Schedule.ps1 -Unregister
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'IM Daily Social Ads',
    [string]$Time = '09:30',
    [ValidateSet('sync','run')][string]$Command = 'sync',
    [string[]]$DaysOfWeek,
    [switch]$DryRun,
    [switch]$RunNow,
    [switch]$Unregister
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $repo 'ims-ads.py'

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Green
    } else {
        Write-Host "No scheduled task named '$TaskName' was found." -ForegroundColor Yellow
    }
    return
}

if (-not (Test-Path -LiteralPath $entry)) { throw "Cannot find ims-ads.py at: $entry" }

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw 'python is not on PATH. Install Python 3.10+ and retry.' }

# Fail now, not at 09:30 tomorrow.
& $python -c "import playwright, PIL, requests" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Dependencies are missing. From $repo run:`n" +
          "  python -m pip install -r requirements.txt`n" +
          "  python -m playwright install chromium"
}

try {
    $at = [datetime]::ParseExact($Time, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
} catch {
    throw "Could not read -Time '$Time'. Use 24-hour HH:mm, for example 09:30."
}

# 'sync' is the live path: ChatGPT makes the creative, Codex pushes it to GitHub,
# this pulls it, verifies it against the unit's own listing, and files it.
# 'run' is the alternative for production.mode = 'produce', where this system reads
# the site and builds the package itself.
$arguments = '"{0}" {1}' -f $entry, $Command
if ($DryRun) { $arguments += ' --dry-run' }

$action = New-ScheduledTaskAction -Execute $python -Argument $arguments -WorkingDirectory $repo

if ($DaysOfWeek) {
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DaysOfWeek -At $at
    $schedule = 'every {0} at {1} local' -f ($DaysOfWeek -join ', '), $at.ToString('HH:mm')
} else {
    $trigger = New-ScheduledTaskTrigger -Daily -At $at
    $schedule = 'every day at {0} local' -f $at.ToString('HH:mm')
}

# StartWhenAvailable is ON: unlike a social post, a missed ad build is worth running
# late. Nothing is published by this task, so a catch-up run is harmless - it just
# files a package into ready/ for review.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 45)

# Interactive: the build drives a real Chrome window, which needs a desktop session.
$principal = New-ScheduledTaskPrincipal `
    -UserId ('{0}\{1}' -f $env:USERDOMAIN, $env:USERNAME) `
    -LogonType Interactive `
    -RunLevel Limited

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Replacing the existing '$TaskName' task." -ForegroundColor Yellow
}

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description 'Builds the daily International Motorsports ad package. Never publishes.' | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $TaskName

Write-Host ''
Write-Host 'Scheduled task registered.' -ForegroundColor Green
Write-Host ("  Task     : {0}" -f $TaskName)
Write-Host ("  Runs     : {0}" -f $schedule)
Write-Host ("  Command  : ims-ads.py {0}" -f $Command)
Write-Host ("  Mode     : {0}" -f $(if ($DryRun) { 'DRY RUN (nothing is filed)' } else { 'live' }))
Write-Host ("  Next run : {0}" -f $info.NextRunTime)
Write-Host ("  Repo     : {0}" -f $repo)
Write-Host ''
Write-Host 'This task never publishes. Packages land in ready/ for your review.' -ForegroundColor DarkGray
Write-Host ''
Write-Host 'Check on it with:' -ForegroundColor Cyan
Write-Host ('   python "{0}" status' -f $entry)
Write-Host ''

if ($RunNow) {
    Write-Host 'Running it now...' -ForegroundColor Cyan
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 6
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Host ("  Last run result: {0}" -f $info.LastTaskResult)
    Write-Host ("  Log: {0}" -f (Join-Path $repo 'logs'))
}
