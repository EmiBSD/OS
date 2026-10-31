# How a release is made

A release is a `YEAR.MONTH` tag of this repository over a `MANIFEST.toml` whose pins, assembled,
boot and pass. The repositories build; OS assembles. Nothing is released from a working tree.

## Milestones

| | Scope | Proof |
|---|---|---|
| R0 | the manifest, `just fetch`, the arm64 image and Kernel's smokes from the pinned commits, nothing else | `just check`, `just image`, `just smoke` green from a clean clone |
| R1 | the install sets (`base`, `comp`, `man`, ...) and the media (`bsd.rd`, `install.img`, the ISO) assembled here from Kernel's, Userland's and DevTools' outputs; `diff-openbsd` against a real OpenBSD; the first `YEAR.MONTH` tag | the installer runs end to end in QEMU from the release media; the Website's `status.json` reads from the tag |
| R2 | amd64 joins (after Kernel's N10); `syspatch`-style updates between releases | both architectures install from the same release |

R0 can run today: Kernel alone produces a bootable system on OpenBSD's userland. R1 waits for
Userland's first bootable `base` set (Governance G-0013, U2) and DevTools' T3 artifacts.

## The checklist of a release (R1, to be scripted as `just release`)

1. The manifest's pins are tagged commits in their repositories; `just check` passes.
2. `just fetch` from a clean clone; `just image` and `just smoke` pass on every `arch`.
3. The sets are assembled from the pinned outputs with `SOURCE_DATE_EPOCH` set to the manifest
   commit's time; two runs produce the same `SHA256` file.
4. The install media boot and install in QEMU (Kernel's `smoke-install-*`, run from here).
5. `diff-openbsd`: the installed system compared with a real OpenBSD at the OpenBSD pin; every
   difference is a known one, listed in the release notes.
6. The release notes: the pins, the OpenBSD pin, the `unsafe` figures of Kernel at the pin,
   the known differences, the sets' `SHA256`.
7. The tag `YEAR.MONTH` on the manifest commit; the sets and media uploaded as the GitHub
   release's assets; the Website's `_data/status.json` and news updated.

## Versions

`YEAR.MONTH` for the system (Governance G-0009 kept this from Userland's rules); the
repositories keep their own milestones and the programs their semver. A second release in a
month is `YEAR.MONTH.1`.

## What never changes between a pin and a release

The pins. A fix found during the checklist goes into its repository, gets a commit there,
and the manifest moves to it in its own commit; the checklist starts again.
