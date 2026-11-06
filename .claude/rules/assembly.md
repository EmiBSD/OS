# Assembly: the practice

OS builds nothing of its own. Every byte of a release comes from a pinned commit of Kernel,
Userland or DevTools; what is ours is the manifest, the recipes that fetch and delegate, the
checklist, the release notes and the tag.

- **The manifest is the state.** A pin moves in its own commit, `manifest: <repo> to <12-hex>`,
  with the reason and what it brings. Never two repositories in one commit unless one needs the
  other (an ABI change in Kernel with its Userland side).
- **`src/` is a checkout, not a workspace.** Fetched at the pin, detached, never edited, never
  committed. A fix goes to its repository; the manifest follows it. The guard applies: no write
  under `src/`, no `cd` into it except through the recipes that delegate a build there.
- **A released pin ships its artifact.** With `release` the bytes that go into a release are
  the GitHub release's artifact, checked by `sha256.<arch>`; with `tools = true` its commit is
  also checked out in `src/<name>`, but only its tools run from there (rdsetroot, the image
  writers, the smoke harness), never a build of what ships.
- **Delegate, do not duplicate.** `just image` runs Kernel's `just image-arm64` in `src/kernel`;
  `just smoke` runs Kernel's smokes. OS gains its own recipes only for what no repository can
  do alone: assembling sets from several outputs, the media, `diff-openbsd` over the whole
  system, the tag.
- **Reproducible.** `SOURCE_DATE_EPOCH` is the manifest commit's time; the sets' `SHA256` is
  the proof; a release that cannot be rebuilt to the same sums is not cut.
- **The OpenBSD pin is recorded, not fetched.** `[openbsd]` states which OpenBSD this system
  answers to; `just check` compares it with Kernel's and LandingZone's `reference/PINNED.md`
  when their clones sit beside this one.
- **Nothing downloaded without the user's OK**: `just fetch` and `just check` use the network
  and run from the main session.
- Commit on the user's "dale", push on "súbelo", to `main`; never a branch, never a push
  otherwise.
