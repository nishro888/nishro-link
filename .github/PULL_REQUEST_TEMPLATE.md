<!-- docs/development.md has the workflow. Delete any heading that does not
     apply rather than writing "n/a" under it. -->

## What and why

<!-- What this changes, and the reason - what was tried and rejected, what
     was measured. This is the part worth reading in six months. Link the
     issue if there is one. -->

## How it was tested

<!-- Tests added or changed, and anything checked by hand on the real
     machines: which machine, and what you saw. "CI is green" is not an
     answer on its own for anything touching a real screen, a real keyboard
     or the network. -->

## Anything a reviewer should look at twice

<!-- The part you are least sure of. Saying it here is the fastest way to a
     useful review. -->

---

- [ ] `python -m pytest` passes
- [ ] `python -m ruff check .` passes
- [ ] Nothing new can block an input hook, or keep someone from getting their
      own mouse and keyboard back (DESIGN.md, P1 and the failsafe)
- [ ] One concern only (docs/development.md, rule 2)
- [ ] I have read my own diff: `git diff main...HEAD`
- [ ] The commit messages say *why*, not just what
- [ ] Nothing personal in the diff: no names, addresses, network names, IPs,
      account details or passwords, in code, tests, docs or screenshots
