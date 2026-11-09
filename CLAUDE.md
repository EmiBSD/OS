# EmiBSD OS

The repository that assembles an EmiBSD system and cuts its releases: `MANIFEST.toml` pins
one commit of Kernel, Userland and DevTools, the recipes fetch them, build where they build,
make the media from Kernel's kernels and Userland's sets as they are, boot them, and tag
`YEAR.MONTH`. Repository `EmiBSD/OS`,
created 2026-10-31. It builds nothing of its own. This file is the rules of this repository; what
OS needs from Kernel, Userland and DevTools it takes from their pinned checkouts or, for a pin
with `release` (Kernel, Userland), from that GitHub release's artifacts by tag and sum, one per
architecture, nothing else. A released pin with `tools = true` (Kernel, Userland) is also
checked out under `src/`, for its tools only; what ships is always the artifact.

Everything in this repository (code, comments, docs, commits) is in English. The user is
answered in Spanish.

## Non-negotiables

- **OS integrates; the repositories build their pieces.** Kernel only makes and debugs the
  kernel, Userland the user tools, DevTools the toolchains. OS puts them together and makes the
  system's image and its install media, with its own tools (`tools/`, stdlib); nothing here
  compiles a kernel, a program or a toolchain except by delegating to the pinned checkout's own
  recipes under `src/` (Userland's host `makefs`, the media's one compiled tool).
- **The manifest is the only state.** A release is a tag on a manifest commit; a pin moves in
  its own commit with its reason. `src/`, `target/` and `dist/` are never committed.
- **One OpenBSD.** The `[openbsd]` pin equals LandingZone's and Kernel's; a release states it.
- **Reproducible or not released.** `SOURCE_DATE_EPOCH` from the manifest commit; the sets'
  `SHA256` must rebuild identical.
- **arm64 only until Kernel's N10**; `[release] arch` says which.
- **Nothing downloaded or installed without the user's OK**; `just fetch` and `just check`
  use the network and run from the main session.
- **emibsd.org's file services**: OS is the only writer of the R2 buckets `emibsd-cdn` (cdn,
  ftplist1, later ftp.emibsd.org) and `emibsd-firmware` (firmware.emibsd.org, plain http kept);
  Userland writes only `emibsd-man`. Content from another repository for these buckets reaches
  OS as a release artifact and OS publishes it. Every publish ends by regenerating the bucket's
  listings from its contents (`tools/mirror.py`: an index.html per directory, an index.txt per
  set directory, deepest first, `/` last) and is finished only when the public check passes.
  Credentials: `~/emibsd-keys/emibsd-r2`, sourced, never printed or copied.
- Commit on the user's "dale", push on "súbelo", to `main`; never a branch.

## Directory map

```
MANIFEST.toml   the pins: kernel, userland, devtools, and the OpenBSD pin they share
tools/          manifest.py: show, check, fetch, consistent; media.py, smokemedia.py, mirror.py
keys/           the public signify keys (emibsd-80-base.pub); the secret ones never enter a repo
docs/           RELEASE.md: the milestones R0..R2 and the checklist of a release;
                MEDIA.md: the install media, what goes on them and who makes what
src/            the pinned checkouts (gitignored); target/ dist/ the builds and the assets
.claude/        settings, rules/assembly.md
```

## Milestones

| | Scope | Proof |
|---|---|---|
| R0 | manifest, fetch, Kernel's and Userland's artifacts from their releases, the released kernel booted | `just check`, `just fetch`, `just smoke` from a clean clone |
| R1 | media assembled here from Userland's sets as they are; `diff-openbsd`; the first `YEAR.MONTH` | the installer end to end in QEMU from the release media |
| R2 | amd64 (after N10); updates between releases | both architectures install from one release |

## Working

- `just show`, `just check`, `just fetch`, `just smoke`; `just release` refuses
  until R1 scripts the checklist of `docs/RELEASE.md`.
- Commits: `manifest: <repo> to <12-hex>` for a pin, `<tools|docs|recipes>: <imperative
  summary>` for the rest, the body says what and why, then the session's `Co-Authored-By:` line.
- A pin bump, a new architecture, a release: the user's decision, handed back with the question.
