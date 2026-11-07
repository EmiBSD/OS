# How a release is made

A release is a `YEAR.MONTH` tag of this repository over a `MANIFEST.toml` whose pins, assembled,
boot and pass. The repositories build; OS assembles. Nothing is released from a working tree.

## Milestones

| | Scope | Proof |
|---|---|---|
| R0 | the manifest, `just fetch`, Kernel's and Userland's artifacts from their pinned releases, the released kernel booted through Kernel's smoke harness, nothing else | `just check`, `just fetch`, `just smoke` green from a clean clone |
| R1 | the install sets (`base`, `comp`, `man`, ...) and the media (`bsd.rd`, `install.img`, the ISO) assembled here from Kernel's, Userland's and DevTools' outputs; `diff-openbsd` against a real OpenBSD; the first `YEAR.MONTH` tag | the installer runs end to end in QEMU from the release media; the Website's `status.json` reads from the tag |
| R2 | amd64 joins (after Kernel's N10); `syspatch`-style updates between releases | both architectures install from the same release |

R0 runs from released artifacts only. Kernel releases its kernel per architecture
(`kernel-<tag>-<arch>.tar.zst`: `bsd.mp`, the one kernel; `bsd.RAMDISK`, the same kernel with an
empty ramdisk; `BOOTAA64.EFI`, `BUILDINFO`), and its pin's `tools = true` keeps
a checkout of the same commit for Kernel's smoke harness. Userland releases the inherited base
system's `base`, `comp`, `game` and `man` sets, one artifact per architecture
(`userland-<tag>-<arch>.tar.zst`), reproducibly: Userland's `just dist <arch>` at the tag gives
the same sums. OS fetches each by tag and checks it against the manifest's `sha256.<arch>`.
Userland's artifact also carries the installer's ramdisk `miniroot80.fs` and `INSTALL.<arch>`,
and its installer reads the `.tar.zst` sets from local media and installs `bsd.mp` as `/bsd`. R1
waits for the media OS makes from them (`docs/MEDIA.md`: `just media`, `just smoke-media`) and
for DevTools' T3 artifacts.

## The checklist of a release (R1, to be scripted as `just release`)

1. The manifest's pins are tagged commits in their repositories; `just check` passes.
2. `just fetch` from a clean clone; `just smoke` passes on every `arch`.
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

`YEAR.MONTH` for the system; the repositories keep their own milestones and the programs their
semver. A second release in a month is `YEAR.MONTH.1`.

## What never changes between a pin and a release

The pins. A fix found during the checklist goes into its repository, gets a commit there,
and the manifest moves to it in its own commit; the checklist starts again.
