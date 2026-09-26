# Nishro Link - install as a Windows service.
#
#   powershell -ExecutionPolicy Bypass -File install-windows-service.ps1
#   ...-File install-windows-service.ps1 -Uninstall
#
# As a service Nishro Link starts with Windows - before anyone signs in - and
# works on the lock screen and the sign-in screen, which Windows walls off from
# ordinary programs. It needs administrator rights once, to install; it asks.
#
# What it does:
#   - installs the program under Program Files: a service runs as SYSTEM, so
#     only administrators may be able to replace what it runs
#   - carries this account's pairing and settings across
#   - lets it through the firewall on private networks
#   - registers the "NishroLink" service (automatic, restarts if it stops)
#   - points the Start Menu at it: the window now attaches to the service
param(
    [switch]$Uninstall,
    [string]$FromConfig = "$env:APPDATA\NishroLink\config.json",
    [string]$UserRunKeyOwner = $env:USERNAME
)
$ErrorActionPreference = "Stop"

$name    = "NishroLink"
$dir     = Join-Path $env:ProgramFiles "Nishro Link"
$exe     = Join-Path $dir "NishroLink.exe"
$data    = Join-Path $env:ProgramData "NishroLink"
$private = Join-Path $data "private"
$lnk     = Join-Path ([Environment]::GetFolderPath("CommonPrograms")) "Nishro Link.lnk"
$rule    = "Nishro Link (service)"

function Say($m) { Write-Host "`n$m" -ForegroundColor Cyan }
function Ok($m)  { Write-Host "  ok   $m" }

# ---------------------------------------------------------- elevate, once
$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $argv = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
              "-FromConfig", "`"$FromConfig`"")
    if ($Uninstall) { $argv += "-Uninstall" }
    Write-Host "Nishro Link: Windows will ask for permission to install the service."
    $p = Start-Process powershell -Verb RunAs -ArgumentList $argv -Wait -PassThru
    Write-Host "Nishro Link: details in $data\install.log"
    exit $p.ExitCode
}
New-Item -ItemType Directory -Force -Path $data | Out-Null
Start-Transcript -Path (Join-Path $data "install.log") -Force | Out-Null

try {
    # ---------------------------------------------------------- uninstall
    if ($Uninstall) {
        Say "Removing the Nishro Link service"
        if (Get-Service $name -ErrorAction SilentlyContinue) {
            Stop-Service $name -Force -ErrorAction SilentlyContinue
            & sc.exe delete $name | Out-Null
            Ok "service"
        }
        Get-Process NishroLink -ErrorAction SilentlyContinue | Stop-Process -Force
        Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        if (Test-Path $dir) { Remove-Item -Recurse -Force $dir; Ok "program files" }
        if (Test-Path $lnk) { Remove-Item -Force $lnk; Ok "Start Menu entry" }
        Write-Host "`nSettings were kept in $data." -ForegroundColor Yellow
        exit 0
    }

    # ------------------------------------------------------------ install
    Say "Nishro Link - installing the service"
    $src = Join-Path $PSScriptRoot "NishroLink.exe"
    if (-not (Test-Path $src)) { $src = Join-Path $PSScriptRoot "..\..\dist\NishroLink.exe" }
    if (-not (Test-Path $src)) { throw "NishroLink.exe not found. Build it first: link\packaging\build-windows.ps1" }

    if (Get-Service $name -ErrorAction SilentlyContinue) {
        Stop-Service $name -Force -ErrorAction SilentlyContinue
        Ok "stopped the running service"
    }
    # A copy started by hand holds the ports and the devices.
    $running = Get-Process NishroLink -ErrorAction SilentlyContinue
    if ($running) { $running | Stop-Process -Force; Start-Sleep -Milliseconds 500; Ok "stopped the running copy" }

    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    Copy-Item (Resolve-Path $src) $exe -Force
    Ok "installed to $dir"

    # Settings: SYSTEM and Administrators only - they hold the group's password.
    New-Item -ItemType Directory -Force -Path $private | Out-Null
    if (-not (Test-Path (Join-Path $private "config.json")) -and (Test-Path $FromConfig)) {
        Copy-Item $FromConfig (Join-Path $private "config.json")
        Ok "your pairing and settings, carried across"
    }
    & icacls $private /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
    Ok "settings readable by SYSTEM and administrators only"

    if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName $rule -Direction Inbound -Program $exe `
            -Action Allow -Profile Private,Domain | Out-Null
    }
    Ok "firewall: link and discovery on private networks"

    if (-not (Get-Service $name -ErrorAction SilentlyContinue)) {
        New-Service -Name $name -BinaryPathName "`"$exe`" --service" `
            -DisplayName "Nishro Link" -StartupType Automatic `
            -Description "One mouse and keyboard across several computers - from boot, on the lock and sign-in screens." | Out-Null
    }
    & sc.exe failure $name reset= 60 actions= restart/2000/restart/5000/restart/10000 | Out-Null
    Start-Service $name
    Ok "service running (starts with Windows)"

    $sh = New-Object -ComObject WScript.Shell
    $s = $sh.CreateShortcut($lnk)
    $s.TargetPath = $exe
    $s.WorkingDirectory = $dir
    $s.Description = "Shared mouse and keyboard"
    $s.Save()
    Ok "Start Menu entry (opens a window onto the service)"

    # The per-user copy's login entry would only start a window that closes
    # again at once: the service is already running.
    Remove-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name NishroLink -ErrorAction SilentlyContinue
    $userLnk = Join-Path ([Environment]::GetFolderPath("Programs")) "Nishro Link.lnk"
    if (Test-Path $userLnk) { Remove-Item -Force $userLnk }

    Say "Done"
    Write-Host "  It now starts with Windows and works on the lock and sign-in screens."
    Write-Host "  Open 'Nishro Link' from the Start Menu for its window."
    exit 0
} catch {
    Write-Host "`nstopped: $_" -ForegroundColor Red
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
