"""The Windows release build, checked where it is written down: it runs only
on GitHub (.github/workflows/release.yml), so a step quietly dropped from it
would show only as a setup that antivirus programs flag again.

What it must keep doing: compile PyInstaller's bootloader here instead of
using the prebuilt one, and - once SignPath is set up - sign the program
before the setup packs it, then sign the setup, checking each signature."""
import pathlib
import re
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "link" / "packaging"
RELEASE = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
SIGN_ACTION = "signpath/github-action-submit-signing-request@"


def windows_job():
    return RELEASE[RELEASE.index("\n  windows:\n"):RELEASE.index("\n  linux:\n")]


def steps(job):
    """Each step's text, in order."""
    return re.split(r"\n      - ", job)[1:]


def where(job, text):
    found = [i for i, s in enumerate(steps(job)) if text in s]
    assert len(found) == 1, f"one step with {text!r}, found {len(found)}"
    return found[0]


def test_the_release_compiles_pyinstallers_bootloader_itself():
    """The prebuilt bootloader sits at the front of every program built with
    PyInstaller - malware too - so antivirus programs distrust it."""
    job = windows_job()
    assert re.search(r"pyinstaller-from-source\.ps1 \d+\.\d+\.\d+", job), \
        "a pinned PyInstaller, built from its source"
    assert not re.search(r"pip install[^\n]*pyinstaller", RELEASE, re.I), \
        "no prebuilt PyInstaller beside it"


def test_the_bootloader_build_cannot_fall_back_to_the_prebuilt_one():
    """PyInstaller's source carries the prebuilt bootloaders too: they are
    deleted before compiling, so a compile that goes wrong fails the build
    instead of leaving them in place - and what is installed is checked."""
    script = (PACKAGING / "pyinstaller-from-source.ps1").read_text(encoding="utf-8")
    in_order = ['"$src\\PyInstaller\\bootloader" -Directory -Filter "Windows-*"',
                "$theirs | Remove-Item -Recurse -Force",
                "python ./waf --check-c-compiler=msvc --target-arch=64bit configure all",
                "python -m pip install --quiet $src",
                "is PyInstaller's prebuilt bootloader"]
    at = [script.index(s) for s in in_order]
    assert at == sorted(at)


def test_the_program_is_signed_before_the_setup_packs_it():
    job = windows_job()
    order = [where(job, "build-windows.ps1 -Step app"),
             where(job, "artifact-configuration-slug: program"),
             where(job, "check-signature.ps1 dist/NishroLink/NishroLink.exe"),
             where(job, "build-windows.ps1 -Step smoke"),
             where(job, "build-windows.ps1 -Step setup"),
             where(job, "artifact-configuration-slug: setup"),
             where(job, "check-signature.ps1 dist/NishroLink-Setup-"),
             where(job, "name: windows")]
    assert order == sorted(order)


def test_signing_waits_until_signpath_is_set_up():
    """Until the maintainer has a SignPath project, releases build unsigned,
    as before - signing must never be what stops a release from building."""
    job = windows_job()
    assert "SIGN: ${{ vars.SIGNPATH_ORGANIZATION_ID != '' }}" in job
    signing = [s for s in steps(job)
               if SIGN_ACTION in s or "check-signature.ps1" in s or "unsigned-" in s]
    assert len(signing) == 6
    assert all("if: env.SIGN == 'true'" in s for s in signing)
    assert all("${{ secrets.SIGNPATH_API_TOKEN }}" in s
               for s in signing if SIGN_ACTION in s)
    assert all("build-windows.ps1" not in s or "if:" not in s for s in steps(job)), \
        "the build itself runs either way"


def test_only_the_finished_files_are_published():
    """The unsigned copies handed to SignPath are artifacts of the run too, the
    setup under the same name as the signed one: the release takes the two
    finished sets by name, never everything the run has."""
    publish = RELEASE[RELEASE.index("\n  publish:\n"):]
    assert "merge-multiple" not in publish and "pattern:" not in publish
    assert re.findall(r"name: (\w+)\n\s+path: out\n", publish) == ["windows", "linux"]


def test_a_signature_is_checked_for_its_signer_and_timestamp():
    script = (PACKAGING / "check-signature.ps1").read_text(encoding="utf-8")
    assert "Get-AuthenticodeSignature" in script
    assert '"Valid"' in script and "SignPath Foundation" in script
    assert "TimeStamperCertificate" in script, \
        "timestamped, so it stays valid after the certificate expires"


def test_build_windows_runs_one_step_at_a_time_or_all():
    script = (PACKAGING / "build-windows.ps1").read_text(encoding="utf-8")
    assert '[ValidateSet("all", "app", "smoke", "setup")]' in script
    assert '[string]$Step = "all"' in script, "run by hand, it still builds everything"


def test_signed_files_carry_the_name_and_version_signpath_checks():
    """SignPath signs only files whose Properties say the project's name and
    this release's version - the same in the program and in the setup."""
    build = (PACKAGING / "build-windows.ps1").read_text(encoding="utf-8")
    assert "StringStruct('ProductName', 'Nishro Link')" in build
    assert "StringStruct('ProductVersion', '$ver')" in build
    iss = (PACKAGING / "windows" / "NishroLink.iss").read_text(encoding="utf-8")
    assert "VersionInfoProductName=Nishro Link" in iss
    assert "VersionInfoProductTextVersion={#AppVersion}" in iss
    ns = {"a": "http://signpath.io/artifact-configuration/v1"}
    for name, exe in [("program", "NishroLink.exe"),
                      ("setup", "NishroLink-Setup-${version}.exe")]:
        root = ET.parse(PACKAGING / "signpath" / f"{name}.xml").getroot()
        assert root.tag == "{%s}artifact-configuration" % ns["a"]
        assert root.find("a:parameters/a:parameter[@name='version']", ns) is not None
        pe = root.find("a:zip-file/a:pe-file", ns)
        assert pe.get("path") == exe
        assert pe.get("product-name") == "Nishro Link"
        assert pe.get("product-version") == "${version}"
        assert pe.find("a:authenticode-sign", ns) is not None
    assert RELEASE.count('version: "${{ steps.version.outputs.version }}"') == 2


def test_the_code_signing_policy_is_on_the_home_and_release_pages():
    """SignPath Foundation's terms: the policy under that name on the home page
    and the release pages, with its attribution, the team's roles and a
    privacy statement."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", readme)
    plain = re.sub(r"\s+", " ", plain)
    assert "## Code signing policy" in readme
    assert ("Free code signing provided by SignPath.io, certificate by "
            "SignPath Foundation") in plain
    assert "Committers and reviewers:" in plain and "Approvers:" in plain
    assert ("This program will not transfer any information to other networked "
            "systems unless specifically requested by the user or the person "
            "installing or operating it") in plain
    publish = RELEASE[RELEASE.index("\n  publish:\n"):]
    assert "#code-signing-policy" in publish and "Code signing policy" in publish


def test_the_setup_says_what_it_changes_on_the_computer():
    """No system change without saying so (SignPath's terms ask it too): the
    background service and the firewall rule, before anything is installed."""
    iss = (PACKAGING / "windows" / "NishroLink.iss").read_text(encoding="utf-8")
    welcome = next(l for l in iss.splitlines() if l.startswith("WelcomeLabel2="))
    assert "background" in welcome and "Windows Firewall" in welcome
