# The install media

Each release ships two install images per architecture, both made here from released
artifacts and nothing else:

- `emibsd-<version>-<arch>-install.iso`: for a CD or, mostly, a VM (QEMU, UTM; Parallels and
  VMware Fusion are untried). cd9660 with Rock Ridge, one El Torito entry, EFI only, and the
  sets on it: it installs with no network.
- `emibsd-<version>-<arch>-install.img`: for a USB stick (`dd`), laid out as OpenBSD's
  `install80.img` is for that architecture: MBR, FAT boot partition with the loader, an
  OpenBSD partition whose disklabel's `a` is an FFS1 holding the same tree.

The user's decisions: the sets stay `.tar.zst`; no network install, so no `SHA256.sig` and no
signing key yet; arm64 only (`[release] arch`); one kernel, `bsd.mp` (2026-11-05). OS is the
integrator: it puts the pieces together and makes the system's image and both media; Kernel
only makes and debugs the kernel, Userland only the user tools (2026-11-06).

## What goes on them

Both images carry the same tree. `8.0/<arch>/` is the path the installer derives from the
kernel's `osrelease`; the names outside the images say EmiBSD.

| Path | From | How |
|---|---|---|
| `8.0/<arch>/bsd.mp` | Kernel's artifact | as is; the installer installs it as `/bsd` |
| `8.0/<arch>/bsd.rd` | Kernel's `bsd.RAMDISK`, Userland's `miniroot80.fs` | a copy of `bsd.RAMDISK` with the miniroot written at `rd_root_image` (rdsetroot); the miniroot is exactly the kernel's `rd_root_size`, 65536 sectors |
| `8.0/<arch>/{base,comp,game,man}80.tar.zst` | Userland's artifact | as is |
| `8.0/<arch>/INSTALL.<arch>` | Userland's artifact | as is; with it the installer does not ask `Use sets found here anyway?` |
| `8.0/<arch>/SHA256` | made here | over `bsd.mp`, `bsd.rd`, the four sets and `INSTALL.<arch>`; the installer checks every file it takes against it |
| `etc/boot.conf` | made here | `set image /8.0/<arch>/bsd.rd`: the loader reads it from the medium's file system and boots the installer; without it, it looks for `/bsd` and stops at its prompt |
| the loader | Kernel's `BOOTAA64.EFI` | `EFI/BOOT/BOOTAA64.EFI` on the FAT image (ISO) or partition (stick); base installs the same file in `/usr/mdec`, so medium and installed system boot alike |

Kernel's `abi_openbsd.ko` and `BUILDINFO` are not on the media. The images' own `SHA256` goes
beside them in the GitHub release.

## How they are made

All of it is OS's: `tools/media.py` (stdlib, as `manifest.py`) and the recipes. The one tool
OS does not write is the base system's `makefs`, for the FAT, FFS and cd9660 file systems; it
comes built for the host from the pinned Userland checkout by Userland's own recipe (below).

- **`bsd.rd`**: rdsetroot in `media.py`. It reads the ELF64 headers, finds `rd_root_size` and
  `rd_root_image` in `.symtab`, maps them to file offsets through their `PT_LOAD` segment and
  writes the miniroot there, refusing one larger than `rd_root_size`, as the base system's
  rdsetroot(8) does.
- **The ISO**, as Userland's proof made it (2026-11-06: installer in 25 s, install of all four
  sets from cd0 in 110 s, the installed disk to `login:` in 43 s):
  - `makefs -t msdos -s 2880k` makes the El Torito image, FAT12 with only
    `EFI/BOOT/BOOTAA64.EFI`, kept in the tree as `8.0/<arch>/cdbr`;
  - `makefs -t cd9660 -o rockridge,label=EMIBSD_80,bootimage='efi;8.0/<arch>/cdbr',no-emul-boot`
    makes the ISO from the tree.
- **The stick**, laid out as Kernel's `docs/ARCHITECTURE.md` ("The release images") records
  OpenBSD's arm64 `install80.img` sector for sector:
  - `media.py` writes the MBR: a FAT partition of type `0x0C` at sector 32768, 16384 sectors,
    active, then the OpenBSD partition (`0xA6`) to sector 1433600, with fdisk's CHS values;
  - the boot partition: `makefs -t msdos`, FAT16 labelled `BOOT`, with `EFI/BOOT/BOOTAA64.EFI`
    (OpenBSD's Raspberry Pi files are not ours);
  - `a`: `makefs -t ffs -o version=1` (16 KB blocks, 2 KB fragments, `-m 0`, `-o space`), the
    tree above;
  - `media.py` writes the disklabel in the OpenBSD partition's label sector: `a` the FFS, `c`
    the disk, `i` the boot partition as MSDOS, a fixed `d_uid`.
- **Host `makefs`**: the Userland pin has `tools = true`, so `src/userland` is checked out at
  its commit. Userland's recipes build the base system's host tools there: `just host-make`,
  then `just world arm64 tree host`, giving `target/host/bin/makefs`. That build needs DevTools'
  LLVM (`EMIBSD_LLVM_BIN`), takes a few minutes, and builds nothing for EmiBSD. Nothing from
  that checkout goes on the media but what makefs writes.

Recipes:
- `just media`: stage `target/media/<arch>/`, make `bsd.rd` and `SHA256`, write both images
  and their `SHA256` into `dist/<version>/`.
- `just smoke-media`: boots each image in QEMU `virt` (`acpi=off`, cortex-a72, 1 GiB, 2 CPUs,
  EDK2 from QEMU's share). The ISO goes on a `scsi-cd` with `bootindex=0`, the stick on a
  `usb-storage` behind a `qemu-xhci`, and a fresh 3 GiB `virtio-blk` is the target. The run
  answers the installer over the serial console, as a person does, until `CONGRATULATIONS!`.
  Then the installed disk boots alone to `login:`, and `uname -a` and `/bsd`'s size are
  checked. The runner is OS's (`tools/`, stdlib).

## Reproducibility

Every byte on the media comes from an artifact checked by sum, or is made here from them with
`SOURCE_DATE_EPOCH` set to the manifest commit's time. Two runs of `just media` must give the
same images. What makes them so:

- rdsetroot and `SHA256` write only what they are given;
- Userland's host makefs clamps every inode's times to `SOURCE_DATE_EPOCH` and seeds the FFS
  label's UID from it (`-T <epoch>`);
- the FAT images take a fixed `-o volume_id=`, since their volume ID otherwise comes from the
  clock;
- the cd9660 timestamps: to be checked when `just media` first runs twice.

Kernel's artifact is taken by sum, never rebuilt, but it is not rebuildable to the same sum:
its `BUILDINFO` records the wall-clock time of the build. That is a known difference for the
release notes until Kernel drops it.

## Seen in Userland's proofs (2026-11-06), owned elsewhere

For the release notes and the repositories that own them:

- Kernel:
  - after `halt`, an `Unknown kernel exception 0x00` into ddb;
  - `newfs_msdos` was handed `/dev/r0000000000000000.i`, a zero DUID, during disk setup,
    without harm to the install.
- DevTools:
  - `reorder_libs` and the `ssh-agent` relink fail at boot without `cc`, `ld` and `ar` in comp;
  - `reorder_kernel` fails without a `kernel.tgz`.
- Userland: `/etc/motd` still names the base system's old name; `/usr/libexec/vi.recover` is
  missing.

## Not now

- Network install and signatures.
- amd64, until Kernel's N10.
- Real hardware: the kernel is built with feature `qemu`, so the media are for QEMU and the VMs
  built on it.
