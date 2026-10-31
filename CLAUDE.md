# EmiBSD OS

The repository that assembles an EmiBSD system and cuts its releases: `MANIFEST.toml` pins
one commit of Kernel, Userland and DevTools, the recipes fetch them, build where they build,
assemble the sets and the media, boot them, and tag `YEAR.MONTH`. Repository `EmiBSD/OS`,
created 2026-10-31 under Governance's decision G-0012. It builds nothing of its own. The rules
that cross repositories are in `../Governance`; this file is the rules inside this one.

Everything in this repository (code, comments, docs, commits) is in English. The user is
answered in Spanish.

## Non-negotiables

- **OS assembles; the repositories build.** Nothing here compiles a kernel, a program or a
  toolchain except by delegating to the pinned checkout's own recipes under `src/`.
- **The manifest is the only state.** A release is a tag on a manifest commit; a pin moves in
  its own commit with its reason. `src/`, `target/` and `dist/` are never committed.
- **One OpenBSD.** The `[openbsd]` pin equals LandingZone's and Kernel's; a release states it.
- **Reproducible or not released.** `SOURCE_DATE_EPOCH` from the manifest commit; the sets'
  `SHA256` must rebuild identical.
- **arm64 only until Kernel's N10** (Governance G-0007); `[release] arch` says which.
- **Nothing downloaded or installed without the user's OK**; `just fetch` and `just check`
  use the network and run from the main session.
- Commit on the user's "dale", push on "súbelo", to `main`; never a branch.

## Directory map

```
MANIFEST.toml   the pins: kernel, userland, devtools, and the OpenBSD pin they share
tools/          manifest.py: show, check, fetch, consistent
docs/           RELEASE.md: the milestones R0..R2 and the checklist of a release
src/            the pinned checkouts (gitignored); target/ dist/ the builds and the assets
.claude/        settings, rules/assembly.md
```

## Milestones

| | Scope | Proof |
|---|---|---|
| R0 | manifest, fetch, the arm64 image and Kernel's smokes from the pins | `just check`, `just image`, `just smoke` from a clean clone |
| R1 | sets and media assembled here; `diff-openbsd`; the first `YEAR.MONTH` | the installer end to end in QEMU from the release media |
| R2 | amd64 (after N10); updates between releases | both architectures install from one release |

## Working

- `just show`, `just check`, `just fetch`, `just image`, `just smoke`; `just release` refuses
  until R1 scripts the checklist of `docs/RELEASE.md`.
- Commits: `manifest: <repo> to <12-hex>` for a pin, `<tools|docs|recipes>: <imperative
  summary>` for the rest, the body says what and why, then the session's `Co-Authored-By:` line.
- A pin bump, a new architecture, a release: the user's decision, handed back with the question.
