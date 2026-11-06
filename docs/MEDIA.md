# The install media

Each release ships two install images per architecture, both assembled here from released
artifacts and nothing else:

- `emibsd-<version>-<arch>-install.iso`: for a CD or, mostly, a VM (UTM, QEMU; Parallels and
  VMware Fusion are untried). cd9660 with Rock Ridge, one El Torito entry, EFI only (a FAT image
  holding the loader), and the sets on it: it installs with no network.
- `emibsd-<version>-<arch>-install.img`: for a USB stick (`dd`), laid out as OpenBSD's
  `install80.img` is for that architecture: MBR, FAT boot partition with the loader, FFS1 `a`
  holding the tree below.

The user's decisions of 2026-11-05: the sets stay `.tar.zst`; no network install, so no
`SHA256.sig` and no signing key yet; arm64 only (`[release] arch`); one kernel, `bsd.mp`.

## What goes on them

Both images carry the same tree, `8.0/<arch>/`, the path the installer derives from the
kernel's `osrelease`. The file names outside the images say EmiBSD; inside they are the
installer's.

| File | From | How |
|---|---|---|
| `bsd.mp` | Kernel's artifact | as is; the installer installs it as `/bsd` |
| `bsd.rd` | Kernel's `bsd.RAMDISK` and Userland's `miniroot80.fs` | a copy of `bsd.RAMDISK` with the miniroot put in by `cargo xtask rdsetroot` (src/kernel); the miniroot is exactly the 65536 sectors the kernel reserves |
| `base80.tar.zst`, `comp80.tar.zst`, `game80.tar.zst`, `man80.tar.zst` | Userland's artifact | as is |
| `SHA256` | made here | over `bsd.mp`, `bsd.rd` and the four sets: the installer checks every file it takes against it |
| `INSTALL.<arch>` | open (below) | without it the installer asks `Use sets found here anyway?` |
| the loader | Kernel's `BOOTAA64.EFI` | on the FAT image or partition, `efi/boot/bootaa64.efi`; the same loader base installs in `/usr/mdec`, so medium and installed system boot alike |

Kernel's `abi_openbsd.ko` and `BUILDINFO` are not on the media. The images' own `SHA256` goes
beside them in the GitHub release.

## Who does what

OS assembles, the repositories build (`.claude/rules/assembly.md`). The images are written by
Kernel's xtask, which already writes both formats (`distrib.rs`, `iso9660.rs`), run from
src/kernel (`tools = true`); OS stages the tree and calls it.

- **Userland** (done in 2026.11.0, 03eb9875): the installer reads `.tar.zst` sets, takes
  `bsd.mp bsd.rd` as the kernel sets and installs `bsd.mp` as `/bsd`, offers only disk and cd
  as the sets' location; zstd in base and in the ramdisk; `miniroot80.fs` in the artifact.
- **Kernel**, to ask for:
  - `install-img` and an arm64 `install-iso` that take their inputs from outside its own
    `target/`: the staged tree, the loader, the output path and the time of every timestamp
    (`SOURCE_DATE_EPOCH`). Today `install-img` stages its own build's sets, and `cd-iso` is
    amd64's boot-only CD.
  - the host `makefs` they need without `just userland`'s whole build, or from where OS should
    take it.
  - an install smoke for the ISO, as `install80 --image FILE` is for the stick.
- **OS**:
  - `just media`: stage `target/media/<arch>/8.0/<arch>/`, make `bsd.rd`, write `SHA256`, call
    Kernel's writers, put the images and their `SHA256` in `dist/<version>/`.
  - `just smoke-media`: the stick through `cargo xtask install80 --image`, the ISO through
    Kernel's new smoke; each installs and boots the installed disk to `login:`.
  - `docs/RELEASE.md`'s checklist, steps 3 and 4: two runs of `just media` give the same sums.

## Reproducibility

Every byte on the media comes from an artifact checked by sum, or is made here from them with
`SOURCE_DATE_EPOCH` set to the manifest commit's time: `bsd.rd` (rdsetroot writes only the
image), `SHA256`, the file systems' and the ISO's timestamps. Two runs of `just media` must
give the same images.

Kernel's artifact is taken by sum, never rebuilt, but it is not rebuildable to the same sum
(its `BUILDINFO` records the wall-clock time of the build): a known difference for the release
notes until Kernel drops it.

## Open

- **`INSTALL.<arch>`**: OpenBSD's comes from `distrib/notes` in the base system's tree, which is
  Userland's; Userland ships it in its artifact, or OS writes a short one for the release.
- **The host `makefs`**: from Kernel, from Userland's host tools, or another way.

## Seen in Userland's proofs (2026-11-06), owned elsewhere

Its ISO, from the two releases, reached the installer in about 25 s, installed all four sets
from cd0 in 110 s, and the installed disk booted alone to `login:` in 43 s. For the release
notes and the repositories that own them:

- Kernel: after `halt`, an `Unknown kernel exception 0x00` into ddb; `newfs_msdos` handed
  `/dev/r0000000000000000.i` (a zero DUID) during disk setup, without harm to the install.
- DevTools: `reorder_libs` and the `ssh-agent` relink fail at boot without `cc`, `ld` and `ar`
  in comp; `reorder_kernel` fails without a `kernel.tgz`.
- Userland: `/etc/motd` still names the base system's old name; `/usr/libexec/vi.recover` is
  missing.

## Not now

Network install and signatures; amd64 (after Kernel's N10); real hardware: the kernel is built
with feature `qemu`, so the media are for QEMU and the VMs built on it.
