# Signing the Windows release

Windows Defender's machine-learning check once took an unsigned Nishro Link
setup for a trojan (`Trojan:Win32/Bearfoos.A!ml`, a false positive): a new,
unsigned program that hooks the keyboard and mouse, injects input, runs as a
service and listens on the network looks like remote-control malware to a
model that has nothing else to go on. A signature says who published the file
and that it is unchanged since, and lets reputation build on the certificate
instead of starting from nothing with every release.

[SignPath Foundation](https://signpath.org) signs open-source projects for
free, with its own certificate: Windows shows "SignPath Foundation" as the
publisher. It signs only files built by GitHub Actions from this repository,
and only once the maintainer approves each request.

The release workflow ([release.yml](../../../.github/workflows/release.yml))
does the rest:

1. compiles PyInstaller's bootloader from source
   ([pyinstaller-from-source.ps1](../pyinstaller-from-source.ps1)) and builds
   the program;
2. hands `NishroLink.exe` to SignPath ([program.xml](program.xml)), waits for
   it signed, checks the signature ([check-signature.ps1](../check-signature.ps1))
   and starts the signed program once;
3. builds the setup around it, hands the setup to SignPath
   ([setup.xml](setup.xml)), waits, and checks that signature too.

Until it is set up, the steps that sign are skipped and the release is built
unsigned, as before.

## Setting it up, once

1. **Apply** at [signpath.org/apply](https://signpath.org/apply): the
   repository, its MIT license, the releases page, and what the program does.
2. **Two-factor sign-in** on GitHub and on SignPath, for everyone on the
   project - SignPath Foundation's terms require it.
3. **In SignPath**, once accepted:
   - the project `nishro-link`, linked to the trusted build system
     *GitHub.com*;
   - the signing policy `release-signing`: SignPath Foundation's certificate,
     each request approved by hand;
   - the artifact configurations `program` and `setup`: the contents of
     [program.xml](program.xml) and [setup.xml](setup.xml);
   - a CI user allowed to submit signing requests, and its API token.
4. **In GitHub**, Settings → Secrets and variables → Actions:
   - the secret `SIGNPATH_API_TOKEN`: that token;
   - the variable `SIGNPATH_ORGANIZATION_ID`: SignPath's organization id.

   The next release run signs.
5. **Then the documents:** the *Code signing policy* in the README, from
   "applied for" to signed, and the "not code-signed yet" notes in the README
   (Install, Limitations, Roadmap), `docs/install-windows.md` and
   `docs/security.md`.

## Each release

The run stops twice - **Sign the program**, then **Sign the setup** - until
the request is approved on SignPath (it sends an e-mail). It waits up to an
hour for each, then fails; run it again.

## Worth knowing

- **What is signed:** `NishroLink.exe` and the setup. The libraries beside
  the program in `_internal\` (Python's, Tcl/Tk's, cryptography's) are other
  projects' and keep their own signatures, or none: SignPath Foundation signs
  a project's own files only.
- **The uninstaller is not signed.** Inno Setup makes `unins000.exe` from the
  setup when installing, and signs it only by running a signing tool in the
  middle of building the setup - but SignPath Foundation takes requests only
  from the workflow itself, which can verify they come from a GitHub build of
  this repository. It still runs from Apps & features; Windows asks for
  permission as it does for any unsigned program.
- **If SignPath refuses the setup over its name or version:** Inno Setup
  writes them padded with spaces ("Nishro Link" and "1.1.0", then spaces), and
  the check may not ignore those. The program's are exact.
- **Builds on a computer** (`build-windows.ps1`) are never signed, and Defender
  may still flag them; only the release run's files are.
