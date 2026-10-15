# How work gets done here

Nishro Link is developed trunk-based: `main` is always releasable, and
everything else is a short-lived branch that becomes a pull request. This is
how most product teams work now, and it is deliberately plain - the ceremony
is in the review, not in the branch diagram.

Read this before your first branch. It is short on purpose.

## The rules, in one place

1. **`main` is protected.** No direct pushes, no force-pushes. Every change
   arrives by pull request with CI green.
2. **One branch, one concern.** A branch that fixes a bug does not also
   rename things. If you find a second thing, note it and open a second
   branch.
3. **Short-lived.** Days, not weeks. A branch that lives for a month will
   fight `main` when it lands, and you will spend longer on the merge than
   you did on the work.
4. **CI must pass before review**, not after. A red PR wastes the reviewer's
   time.
5. **The branch is yours until it merges.** Rebase it, rewrite it, tidy it.
   Once it is on `main` the history is everyone's and is never rewritten.

## Branch names

    <kind>/<short-kebab-description>

| Kind | For |
|---|---|
| `feat/` | something the product could not do before |
| `fix/` | something that was meant to work and did not |
| `perf/` | the same behaviour, less time or memory |
| `refactor/` | the same behaviour, clearer code |
| `test/` | tests only |
| `docs/` | documentation only |
| `chore/` | build, CI, tooling, dependencies |
| `release/` | preparing a version (see **Releases**) |

Good: `feat/move-windows`, `fix/arrow-after-restart`,
`chore/dev-workflow`. Bad: `my-branch`, `fixes`, `roni-2`.

## Which part of the product

A pull request carries a label for the area it touches, so the history reads
by subject rather than by date. These are the areas:

| Label | What it covers |
|---|---|
| `area/windows` | the Windows side: capture, injection, the service, the tray |
| `area/linux` | the Linux side: evdev, GNOME, the portals, the `.deb` |
| `area/link` | the protocol, pairing, discovery, reconnection |
| `area/windows-moving` | moving a window between computers |
| `area/files` | copying files over the link |
| `area/ui` | the window, the tray menus, the settings |
| `area/build` | packaging, signing, CI, releases |
| `area/docs` | the manual, the design notes, these notes |

One PR usually has one. More than two is a sign it should have been more than
one PR.

## The flow

```sh
git switch main && git pull                 # start from what is released
git switch -c feat/move-windows             # a branch of your own
# ... work, committing as you go ...
git push -u origin feat/move-windows
gh pr create --fill --label area/windows-moving
```

Then: CI runs, you read your own diff, a reviewer reads it, it merges.

**Reading your own diff before asking anyone else to** is the single habit
that improves reviews most. `git diff main...HEAD` - you will find something
every time.

## Keeping a branch current

`main` moves while you work. Before asking for review, and whenever `main`
has changed under you:

```sh
git fetch origin
git rebase origin/main
```

Rebase, not merge, while the branch is still yours: it keeps the history
linear and the branch readable as a story. If the rebase stops on a conflict:

```sh
# edit the conflicted files - keep BOTH intentions, not just yours
git add <the files>
git rebase --continue
# or, to get out of it entirely:
git rebase --abort
```

Then push the rewritten branch:

```sh
git push --force-with-lease
```

**`--force-with-lease`, never `--force`.** With a lease, the push is refused
if someone else has pushed to your branch since you last fetched; plain
`--force` would throw their work away without telling you.

## Merging

**Rebase and merge** is the default here, because the commit messages in this
repo carry the reasoning and squashing would throw it away. The result is a
linear history where every commit explains itself.

**Squash and merge** when a branch's commits are scrappy - "wip", "fix typo",
"actually fix it". Write the squashed message properly; it is the only one
that survives.

**Never merge your own PR without CI green**, and never with
"Merge without waiting for requirements".

## Commit messages

The subject line says what changed. The body says **why**, and what was tried
and rejected. Look at `git log` here for the house style: the reasoning is the
point, because in six months the diff will be obvious and the reason will not.

```
Draw the arrow whenever driving begins

After a restart the pointer moved and clicked perfectly but no arrow was
drawn until the laptop's own touchpad was touched. The first attempt asked
GetCursorInfo whether the arrow was showing - Windows answered yes while it
plainly was not drawing one, so the wake never fired.
```

A message that only says "fix bug" costs the next person an hour.

## Reviewing

Look for, in this order: **is it correct**, **does it say why**, **is it
tested**, **will the next person understand it**. Style is last and is
mostly the linter's job.

Say what you want changed and why. "This would break if the window closed
mid-capture" is a review. "I don't like this" is not.

## Releases

Releases are cut from `main` and nowhere else.

1. Branch `release/1.2.0`.
2. Set `__version__` and `__stage__` in `link/__init__.py` (`__stage__` is
   `""` for a real release, `"beta"` or `"dev"` otherwise).
3. Date the section in `CHANGELOG.md` and make sure it says what a person
   would want to know, not what the diff says.
4. Set the date in
   `link/packaging/deb/io.github.nishro888.nishro-link.metainfo.xml`.
5. PR, CI green, merge.
6. Tag `v1.2.0` on `main` and run the Release workflow.
7. Install the **published** builds on the real machines before telling
   anyone the release exists. `devnotes/RELEASING.md` has the hand checks.

## Practising the hard parts

The point of working this way is partly to meet the awkward bits on purpose,
while nothing is at stake:

- **Make a conflict.** Two branches that edit the same function, both merged.
  Rebase the second and resolve it.
- **Rewrite a branch.** `git rebase -i origin/main` to reorder, reword and
  squash before review. Learn what `--force-with-lease` protects you from.
- **Revert something.** `git revert <sha>` on `main`, by PR, and notice that
  a revert is itself a commit worth explaining.
- **Cherry-pick a fix** from a feature branch that is not ready, so the fix
  can ship without the feature.
- **Let a PR go stale on purpose**, then bring it back. It teaches you why
  rule 3 exists better than being told.
