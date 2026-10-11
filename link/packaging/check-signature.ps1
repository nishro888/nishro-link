# Is each file signed with SignPath Foundation's certificate, and timestamped?
#
#   powershell -ExecutionPolicy Bypass -File link\packaging\check-signature.ps1 <file>...
#
# The release build runs it on what SignPath sends back: on the program before
# the setup packs it, and on the setup before it is published. Anything else -
# unsigned, a broken signature, someone else's certificate, no timestamp, no
# file at all - stops the release. Without the timestamp a signature would end
# when the certificate does; with it, it stays valid.
param([Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)][string[]]$Path)
$ErrorActionPreference = "Stop"

foreach ($p in $Path) {
    # A wildcard that matches nothing is no error to Get-Item: it returns
    # nothing - and a check of nothing must not pass.
    $files = @(Get-Item -Path $p)
    if ($files.Count -eq 0) { throw "no file to check: $p" }
    foreach ($f in $files) {
        $sig = Get-AuthenticodeSignature -FilePath $f.FullName
        if ($sig.Status -eq "NotSigned") { throw "$($f.Name): not signed" }
        if ($sig.Status -ne "Valid") {
            throw "$($f.Name): the signature is not valid ($($sig.Status))"
        }
        $who = $sig.SignerCertificate.Subject
        if ($who -notmatch "(^|,\s*)(CN|O)=SignPath Foundation(,|$)") {
            throw "$($f.Name): signed by $who, not SignPath Foundation"
        }
        if (-not $sig.TimeStamperCertificate) {
            throw "$($f.Name): signed, but not timestamped"
        }
        Write-Host "$($f.Name): signed by SignPath Foundation, timestamped" -ForegroundColor Green
    }
}
