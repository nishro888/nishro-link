# Build NishroLink.exe - one file, no Python needed on the target machine.
#
#   powershell -ExecutionPolicy Bypass -File link\packaging\build-windows.ps1
#
# Run from the repository root. Produces dist\NishroLink.exe.
$ErrorActionPreference = "Stop"

$root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $root
Write-Host "Building in $root" -ForegroundColor Cyan

python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing PyInstaller..." -ForegroundColor Yellow
    python -m pip install --quiet pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "could not install PyInstaller" }
}
# The link's encryption (secure.py) - bundled into the exe with the rest.
python -c "import cryptography.hazmat.primitives.ciphers.aead" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing cryptography..." -ForegroundColor Yellow
    python -m pip install --quiet "cryptography>=3.4"
    if ($LASTEXITCODE -ne 0) { throw "could not install cryptography" }
}

# --windowed now that there is a real window: a console behind a GUI looks
# unfinished, and the log is visible IN the window as well as on disk. Startup
# failures are caught in entry.py and shown in a dialog, because a windowed app
# that dies silently tells the user nothing at all.
# --onefile so there is a single artifact to copy to the other machine.
# Name, version and publisher in the exe's Properties, as any program has -
# and the same version the installer and the .deb carry.
$ver = (python -c "import sys; sys.path.insert(0, r'$root'); import link; print(link.__version__)").Trim()
$parts = ($ver.Split(".") + @("0", "0", "0"))[0..3] -join ", "
New-Item -ItemType Directory -Force -Path "$root\build" | Out-Null
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($parts), prodvers=($parts), mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'nishro888'),
      StringStruct('FileDescription', 'Nishro Link - one mouse and keyboard across your computers'),
      StringStruct('FileVersion', '$ver'),
      StringStruct('InternalName', 'NishroLink'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 nishro888. MIT License.'),
      StringStruct('OriginalFilename', 'NishroLink.exe'),
      StringStruct('ProductName', 'Nishro Link'),
      StringStruct('ProductVersion', '$ver')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding UTF8 "$root\build\version.txt"

$pyi = @(
    "--onefile", "--windowed",
    "--name", "NishroLink",
    "--icon", "$root\link\packaging\assets\nishro-link.ico",
    "--version-file", "$root\build\version.txt",
    "--distpath", "$root\dist",
    "--workpath", "$root\build\pyinstaller",
    "--specpath", "$root\build",
    "--paths", $root,
    # The controls' look (ui_theme.THEME_DIR): Tcl files and images, not
    # Python, so PyInstaller would not find them by itself.
    "--add-data", "$root\link\theme;link\theme",
    # evdev is Linux-only; excluding it keeps the exe from carrying a broken
    # import and keeps the size down.
    "--exclude-module", "evdev",
    "--exclude-module", "pytest",
    "$root\link\packaging\entry.py"
)
python -m PyInstaller @pyi
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = "$root\dist\NishroLink.exe"
if (-not (Test-Path $exe)) { throw "expected $exe, which is not there" }
$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)

Write-Host ""
Write-Host "Built $exe  ($mb MB)" -ForegroundColor Green
Write-Host "Smoke test:" -ForegroundColor Cyan
# Failures go to stderr and the log, never to a dialog: a build check must not
# pop windows up on the desktop of whoever is building (see entry.py).
$env:NL_NO_DIALOG = "1"
# A windowed exe writes nothing to this console, so --help cannot be the smoke
# test any more. Check it starts, reads its config and exits cleanly instead.
& $exe --show | Out-Null
if ($LASTEXITCODE -ne 0) { throw "the built exe does not run (--show failed)" }
Write-Host "  --show exited cleanly"

# --show exits before the link is built, so it once passed a build that could
# not start at all: a local variable shadowed a module in main(), every launch
# ended in an error dialog, and it was found only after installing. So also
# START it - throwaway config, spare ports, no window - and wait for its control
# page to answer. Anything short of that fails the build.
$tmp = Join-Path $env:TEMP ("nl-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp | Out-Null
# Well apart: the single-instance lock takes the port BELOW the link port, and
# with the UI there the page never came up - a good build failed this check.
$ui = Get-Random -Minimum 20000 -Maximum 29000
$link = $ui + 10
$p = Start-Process -FilePath $exe -PassThru -ArgumentList @(
    "--no-window", "--config", (Join-Path $tmp "config.json"),
    "--port", "$link", "--ui-port", "$ui", "--node", "smoke")
$up = $false
for ($i = 0; $i -lt 40 -and -not $up -and -not $p.HasExited; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$ui/"
        $up = ($r.StatusCode -eq 200)
    } catch { }
}
Get-Process -Id $p.Id -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process NishroLink -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $exe } | Stop-Process -Force
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
Remove-Item Env:NL_NO_DIALOG -ErrorAction SilentlyContinue
if (-not $up) { throw "the built exe does not START: its control page never answered" }
Write-Host "  starts, and its control page answers"
python -c "import tkinter" 2>$null
if ($LASTEXITCODE -eq 0) { Write-Host "  tkinter is bundled (window will open)" }
# The setup wizard around it - what people download and double-click.
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
          "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
Write-Host ""
if ($iscc) {
    & $iscc /Q "/DAppVersion=$ver" "$root\link\packaging\windows\NishroLink.iss"
    if ($LASTEXITCODE -ne 0) { throw "the setup wizard did not build" }
    $setup = "$root\dist\NishroLink-Setup-$ver.exe"
    $smb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
    Write-Host "Built $setup  ($smb MB)" -ForegroundColor Green
    Write-Host "Install it by running that file." -ForegroundColor Cyan
} else {
    Write-Host "Inno Setup 6 not found - the setup wizard was not built." -ForegroundColor Yellow
    Write-Host "Install it with:  link\packaging\install-windows.ps1" -ForegroundColor Cyan
}
