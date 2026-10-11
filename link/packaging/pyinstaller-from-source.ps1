# PyInstaller, with its bootloader compiled here from source.
#
#   powershell -ExecutionPolicy Bypass -File link\packaging\pyinstaller-from-source.ps1 6.22.3
#
# The bootloader is the small program at the front of NishroLink.exe that
# starts Python. PyInstaller publishes it prebuilt, and those same bytes are at
# the front of every program anyone builds with it - malware included - so
# antivirus programs' machine-learning checks have learned to distrust them
# (PyInstaller's own documentation gives that as a reason to compile it).
# Compiled here, it is the same source, built by this release alone.
#
# The release build runs this (.github/workflows/release.yml). It needs Visual
# Studio 2017 or later with its C++ tools, or just its Build Tools - GitHub's
# Windows runners have them.
#
# PyInstaller's source carries the prebuilt bootloaders too. They are deleted
# before compiling, so a compile that goes wrong fails here instead of leaving
# them in place, and what is installed is checked against them at the end.
param([Parameter(Mandatory = $true)][string]$Version)
$ErrorActionPreference = "Stop"

$work = Join-Path ([IO.Path]::GetTempPath()) ("pyinstaller-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    python -m pip download --quiet --no-deps --no-binary :all: "pyinstaller==$Version" -d $work
    if ($LASTEXITCODE -ne 0) { throw "could not download PyInstaller $Version's source" }
    # Python's tarfile, not Windows' tar: the source holds a few symbolic links
    # (in its notes and tests), which Windows lets only some accounts create -
    # tar gives up on them, tarfile copies what they point to.
    python -I -c "import sys, tarfile; tarfile.open(sys.argv[1]).extractall(sys.argv[2], **({'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}))" "$work\pyinstaller-$Version.tar.gz" $work
    if ($LASTEXITCODE -ne 0) { throw "could not unpack PyInstaller's source" }
    $src = "$work\pyinstaller-$Version"

    # Remember the prebuilt ones (to check against at the end), then remove them.
    $prebuilt = @{}
    $theirs = @(Get-ChildItem "$src\PyInstaller\bootloader" -Directory -Filter "Windows-*")
    foreach ($f in ($theirs | Get-ChildItem -File)) {
        $prebuilt[(Get-FileHash $f.FullName -Algorithm SHA256).Hash] = $f.Name
    }
    if ($prebuilt.Count -eq 0) { throw "PyInstaller's source has no prebuilt bootloaders to check against" }
    $theirs | Remove-Item -Recurse -Force

    # With Microsoft's compiler only, 64-bit: if it is not found, waf would
    # quietly take another (MinGW's gcc, which GitHub's runners have too).
    Write-Host "Compiling PyInstaller $Version's bootloader..." -ForegroundColor Cyan
    Push-Location "$src\bootloader"
    try {
        python ./waf --check-c-compiler=msvc --target-arch=64bit configure all
        if ($LASTEXITCODE -ne 0) {
            throw "the bootloader did not compile (it needs Visual Studio 2017 or later, with its C++ tools)"
        }
    } finally {
        Pop-Location
    }
    foreach ($exe in "run.exe", "runw.exe") {
        if (-not (Test-Path "$src\PyInstaller\bootloader\Windows-64bit-intel\$exe")) {
            throw "the compile left no 64-bit $exe"
        }
    }

    python -m pip install --quiet $src
    if ($LASTEXITCODE -ne 0) { throw "could not install PyInstaller from its source" }

    # The bootloaders PyInstaller will use: there, and none of them a prebuilt one.
    $dir = (python -c "import os, PyInstaller; print(os.path.dirname(PyInstaller.__file__))").Trim()
    $ours = @(Get-ChildItem "$dir\bootloader\Windows-64bit-intel" -File -Filter "run*.exe")
    if ($ours.Count -eq 0) { throw "PyInstaller was installed without a bootloader" }
    foreach ($f in $ours) {
        if ($prebuilt.ContainsKey((Get-FileHash $f.FullName -Algorithm SHA256).Hash)) {
            throw "$($f.Name) is PyInstaller's prebuilt bootloader, not the one compiled here"
        }
    }
    Write-Host "PyInstaller $Version installed, its bootloader compiled here:" -ForegroundColor Green
    foreach ($f in $ours) {
        Write-Host ("  {0,-12} sha256 {1}" -f $f.Name, (Get-FileHash $f.FullName -Algorithm SHA256).Hash)
    }
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
