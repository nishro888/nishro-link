# Nishro Link - Windows installer.
#
#   powershell -ExecutionPolicy Bypass -File install-windows.ps1
#   ...-File install-windows.ps1 -NoSetup      install only
#   ...-File install-windows.ps1 -Uninstall    remove it
#
# Per-user. No administrator rights needed, and nothing is written outside
# your own profile.
param(
    [switch]$NoSetup,
    [switch]$Uninstall,
    [switch]$Autostart
)
$ErrorActionPreference = "Stop"

$dest     = "$env:LOCALAPPDATA\Programs\NishroLink"
$exe      = "$dest\NishroLink.exe"
$startDir = [Environment]::GetFolderPath("Programs")
$lnk      = "$startDir\Nishro Link.lnk"
$runKey   = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"

function Say($m) { Write-Host "`n$m" -ForegroundColor Cyan }
function Ok($m)  { Write-Host "  ok   $m" }

# ------------------------------------------------------------- uninstall
if ($Uninstall) {
    Say "Removing Nishro Link"
    Get-Process NishroLink -ErrorAction SilentlyContinue | Stop-Process -Force
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest; Ok "program files" }
    if (Test-Path $lnk)  { Remove-Item -Force $lnk;           Ok "Start Menu entry" }
    $p = Get-ItemProperty $runKey -Name NishroLink -ErrorAction SilentlyContinue
    if ($p) { Remove-ItemProperty $runKey -Name NishroLink; Ok "autostart entry" }
    Write-Host "`nYour settings were left in $env:APPDATA\NishroLink." -ForegroundColor Yellow
    Write-Host "Delete that folder too if you want them gone.`n"
    exit 0
}

# --------------------------------------------------------------- install
Say "Nishro Link - installing for $env:USERNAME"

# The program is a folder: NishroLink.exe and the files it runs from.
$src = Join-Path $PSScriptRoot "NishroLink"
if (-not (Test-Path (Join-Path $src "NishroLink.exe"))) {
    $src = Join-Path $PSScriptRoot "..\..\dist\NishroLink"
}
if (-not (Test-Path (Join-Path $src "NishroLink.exe"))) {
    throw "NishroLink not found. Build it first: link\packaging\build-windows.ps1"
}

# Replacing a running exe fails with a file lock rather than anything helpful,
# so stop it first and say so.
$running = Get-Process NishroLink -ErrorAction SilentlyContinue
if ($running) { $running | Stop-Process -Force; Start-Sleep -Milliseconds 400; Ok "stopped the running copy" }

if (Test-Path "$dest\_internal") { Remove-Item -Recurse -Force "$dest\_internal" }
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -Path (Join-Path (Resolve-Path $src) "*") -Destination $dest -Recurse -Force
Ok "installed to $dest"

$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $exe
$s.WorkingDirectory = $dest
$s.Description = "Shared mouse and keyboard"
$s.Save()
Ok "Start Menu entry"

if ($Autostart) {
    Set-ItemProperty $runKey -Name NishroLink -Value "`"$exe`" --background"
    Ok "starts at sign-in"
}

# A device that waits needs a hole in the firewall for BOTH protocols: TCP for
# the link, UDP for being found by name. One rule scoped to the program, any
# protocol, covers both. Adding it needs admin; without it Windows silently
# drops the other machine's attempts and the only symptom is a device that
# cannot be found or retries forever.
#
# Checked by PROGRAM, not by name: a rule called "Nishro Link" that opens only
# TCP 8770 (as an older setup made) used to satisfy this check, and the
# installed program was then never allowed to answer the search.
#
# Read through the firewall's COM interface: Get-NetFirewallApplicationFilter
# refuses to even READ without administrator rights, so from a normal install
# it found nothing, tried to add a rule, and reported failure for a program
# that was already allowed.
$fw = $null
try {
    $fw = (New-Object -ComObject HNetCfg.FwPolicy2).Rules | Where-Object {
        $_.ApplicationName -and $_.ApplicationName -ieq $exe -and
        $_.Direction -eq 1 -and $_.Action -eq 1 -and $_.Enabled }
} catch { }
if ($fw) { Ok "firewall already allows it" }
if (-not $fw) {
    try {
        New-NetFirewallRule -DisplayName "Nishro Link (program)" -Direction Inbound `
            -Program $exe -Action Allow -Profile Private,Domain `
            -ErrorAction Stop | Out-Null
        Ok "firewall rule: link and discovery (private networks)"
    } catch {
        Write-Host "  ..   could not add a firewall rule without administrator rights." -ForegroundColor Yellow
        Write-Host "       If this machine is the one that LISTENS, allow it when Windows asks," -ForegroundColor Yellow
        Write-Host "       or the other machine will retry forever with no visible reason." -ForegroundColor Yellow
    }
}

# ----------------------------------------------------------------- setup
if (-not $NoSetup) {
    Say "Setup"
    & $exe --setup
}

Say "Done"
Write-Host @"
  Run it            $exe
                    (or 'Nishro Link' in the Start Menu)
  Start at sign-in  Settings > Start when I log in (or re-run with -Autostart)
  Uninstall         re-run this installer with -Uninstall

  Failsafe: press BOTH Ctrl keys together to release all input, on either machine.
"@
