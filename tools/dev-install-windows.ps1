# Install the freshly built program folder on THIS Windows computer, the way
# the setup does - without the setup. For development only.
#
#   powershell -ExecutionPolicy Bypass -File link\packaging\build-windows.ps1
#   (elevated)  powershell -ExecutionPolicy Bypass -File tools\dev-install-windows.ps1
#
# Why: Windows Defender has been blocking setups compiled on a developer's
# machine (a false positive - see RELEASING.md); the program folder itself is
# fine. This stops the service and every NishroLink.exe, copies dist\NishroLink
# over the installed one, and runs --install-service, as the setup would. What
# happened goes to %TEMP%\nishro-link-dev-install.txt. Then start the tray:
#   & "C:\Program Files\Nishro Link\NishroLink.exe" --tray
$ErrorActionPreference = "Continue"
$out = Join-Path $env:TEMP "nishro-link-dev-install.txt"
"start $(Get-Date -Format s)" | Out-File $out
$dir = "C:\Program Files\Nishro Link"
$src = Join-Path $PSScriptRoot "..\dist\NishroLink"
if (-not (Test-Path (Join-Path $src "NishroLink.exe"))) {
    "no build: run link\packaging\build-windows.ps1 first" | Out-File $out -Append
    exit 1
}
& sc.exe stop NishroLink | Out-Null
Start-Sleep -Seconds 2
& taskkill.exe /F /IM NishroLink.exe 2>$null | Out-Null
Start-Sleep -Seconds 1
if (Test-Path "$dir\_internal") { Remove-Item -Recurse -Force "$dir\_internal" }
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Copy-Item -Path "$src\*" -Destination $dir -Recurse -Force
"copied: $((Get-ChildItem $dir -Recurse -File).Count) files" | Out-File $out -Append
$p = Start-Process -FilePath "$dir\NishroLink.exe" -ArgumentList "--install-service" -Wait -PassThru
"install-service exit: $($p.ExitCode)" | Out-File $out -Append
"service: $((Get-Service NishroLink).Status)" | Out-File $out -Append
Get-Content $out
