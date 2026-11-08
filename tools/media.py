#!/usr/bin/env python3
"""The install media of a release: an ISO and a USB stick image per architecture (docs/MEDIA.md).

usage: media.py build                   every arch of [release]: the tree, bsd.rd, SHA256, the ISO and the
                                        stick into dist/<version>/, with their SHA256
       media.py rdsetroot KERNEL FS     put the miniroot FS into the RAMDISK kernel KERNEL, in place

Everything comes from the pinned artifacts that `manifest.py fetch` unpacked under target/
(Kernel's bsd.mp, bsd.RAMDISK and BOOTAA64.EFI; Userland's sets, miniroot80.fs and
INSTALL.<arch>), checked again against their SHA256 here. The one tool OS does not write is
the base system's makefs, built for the host by Userland's recipes in src/userland (`just
host-tools`). Every time written is $SOURCE_DATE_EPOCH (the manifest commit's, from the
justfile), so two runs give the same images.

Stdlib only.
"""
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "target"
DIST = ROOT / "dist"
MAKEFS = ROOT / "src/userland/target/host/bin/makefs"
SECTOR = 512
KEYS = ROOT / "keys"          # the public keys; the secret ones never enter a repository

# The stick, as OpenBSD's distrib/arm64/iso/Makefile lays out install80.img (FSSIZE=1433600;
# `fdisk -iy -b "16384@32768:c"`; `newfs -t msdos -L boot -c1 -F16`), recorded sector for sector in
# Kernel's docs/ARCHITECTURE.md ("The release images").
STICK = {
    "arm64": {
        "total": 1_433_600,
        "boot_start": 32_768,
        "boot_size": 16_384,
        "boot_type": 0x0C,
        "spt": 100,            # the vnd(4) label's sectors a track, which fdisk's CHS follow
        "heads": 1,
        # The boot partition as `newfs_msdos -L boot -c1 -F16` makes it on install80.img.
        "fat": dict(bits=16, sectors=16_384, spc=1, rde=512, media=0xF8, spf=64, spt=63, heads=1,
                    hidden=32_768, oem=b"BSD  4.4", label=b"BOOT", label_entry=True),
        "loader": "BOOTAA64.EFI",
        "duid": b"E80ARM64",
    },
}
ISO_LABEL = "EMIBSD_{rev}"
# The El Torito image, 2880 KiB as in Userland's proof (2026-11-06), laid out as the
# `makefs -t msdos -s 2880k` that proof used made it.
CDBOOT_FAT = dict(bits=12, sectors=5_760, spc=8, rde=512, media=0xF0, spf=3, spt=63, heads=255,
                  hidden=0, oem=b"NetBSD  ", label=b"NO NAME", label_entry=False)

DISKMAGIC = 0x82564557
DTYPE_VND = 12
MAXPARTITIONS = 16
FS_BSDFFS, FS_MSDOS = 7, 8
DOSPTYP_OPENBSD = 0xA6
DOSACTIVE = 0x80


def fail(msg):
    raise SystemExit(f"media: {msg}")


def manifest():
    with open(ROOT / "MANIFEST.toml", "rb") as fh:
        return tomllib.load(fh)


def epoch():
    e = os.environ.get("SOURCE_DATE_EPOCH", "")
    if not e.isdigit():
        fail("SOURCE_DATE_EPOCH is not set (run it through `just media`)")
    return int(e)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def checked(directory):
    """The files an artifact's SHA256 lists, each checked again: name -> path."""
    out = {}
    for line in (directory / "SHA256").read_text().splitlines():
        name, digest = line.split("(", 1)[1].split(")", 1)[0], line.rsplit("= ", 1)[1]
        path = directory / name
        if sha256(path) != digest:
            fail(f"{path} does not match its SHA256")
        out[name] = path
    return out


# --- rdsetroot -----------------------------------------------------------------------------

def rdsetroot(kernel, image):
    """The miniroot `image` written at the RAMDISK kernel's rd_root_image, as rdsetroot(8):
    the ELF64 symbols rd_root_size (32 bits) and rd_root_image, the PT_LOAD that holds both, and
    an image no larger than rd_root_size. Returns rd_root_size."""
    elf = bytearray(kernel.read_bytes())
    if elf[:4] != b"\x7fELF" or elf[4] != 2 or elf[5] != 1:
        fail(f"{kernel}: not an ELF64 little-endian file")
    phoff, shoff = struct.unpack_from("<QQ", elf, 32)
    phentsize, phnum, shentsize, shnum = struct.unpack_from("<HHHH", elf, 54)
    sections = [struct.unpack_from("<IIQQQQIIQQ", elf, shoff + i * shentsize) for i in range(shnum)]
    syms = {}
    for sh_name, sh_type, _, _, off, size, link, _, _, entsize in sections:
        if sh_type != 2:   # SHT_SYMTAB
            continue
        stroff = sections[link][4]
        for at in range(off, off + size, entsize):
            st_name, _, _, _, st_value, st_size = struct.unpack_from("<IBBHQQ", elf, at)
            end = elf.index(0, stroff + st_name)
            name = bytes(elf[stroff + st_name:end])
            if name in (b"rd_root_size", b"rd_root_image"):
                syms[name] = st_value
    if len(syms) != 2:
        fail(f"{kernel}: no rd_root_size and rd_root_image symbols (not a RAMDISK kernel)")

    def offset(addr):
        for i in range(phnum):
            p_type, _, p_offset, p_vaddr, _, p_filesz, _, _ = struct.unpack_from(
                "<IIQQQQQQ", elf, phoff + i * phentsize)
            if p_type == 1 and p_vaddr <= addr < p_vaddr + p_filesz:
                return p_offset + (addr - p_vaddr), p_offset + p_filesz
        fail(f"{kernel}: {addr:#x} is in no PT_LOAD segment")

    size_off, _ = offset(syms[b"rd_root_size"])
    image_off, seg_end = offset(syms[b"rd_root_image"])
    (rd_size,) = struct.unpack_from("<I", elf, size_off)
    data = image.read_bytes()
    if len(data) > rd_size or image_off + rd_size > seg_end:
        fail(f"{image}: {len(data)} bytes, the ramdisk holds {rd_size}")
    elf[image_off:image_off + len(data)] = data
    kernel.write_bytes(elf)
    return rd_size


# --- makefs --------------------------------------------------------------------------------

def makefs(kind, options, image, tree, t, size=None):
    if not MAKEFS.is_file():
        fail(f"no {MAKEFS.relative_to(ROOT)}; run just host-tools first")
    image.unlink(missing_ok=True)
    cmd = [str(MAKEFS), "-t", kind, "-T", str(t), "-o", options]
    if size is not None:
        cmd += ["-s", str(size)]
    r = subprocess.run(cmd + [str(image), str(tree)], capture_output=True, text=True,
                       env={**os.environ, "TZ": "UTC"})
    if r.returncode or not image.is_file():
        fail(f"makefs -t {kind} {image.name}: {r.stdout}{r.stderr}")


# --- signify --------------------------------------------------------------------------------
#
# With `[release] signify = "<key>"` (from Userland 2026.11.3 on, whose installer trusts only
# EmiBSD's keys) the media's <osrev>/<arch>/SHA256 and dist/<version>/SHA256 are signed with
# signify(1) into SHA256.sig beside them, as OpenBSD's releases are, and checked again with
# keys/<key>.pub. The secret key and its passphrase are the user's, outside every repository:
# $EMIBSD_SIGN_KEY (default ~/emibsd-keys/<key>.sec) and $EMIBSD_SIGN_PASS (default
# ~/emibsd-keys/key.txt, mode 0600), the passphrase file handed to signify as its stdin, never
# read here. Ed25519 signs deterministically: the same SHA256 gives the same SHA256.sig.

def signer(doc):
    key = doc["release"].get("signify")
    if not key:
        return None
    exe = shutil.which("signify")
    if not exe:
        fail("no signify on PATH (brew install signify-osx)")
    home = Path.home() / "emibsd-keys"
    sec = Path(os.environ.get("EMIBSD_SIGN_KEY") or home / f"{key}.sec")
    pw = Path(os.environ.get("EMIBSD_SIGN_PASS") or home / "key.txt")
    pub = KEYS / f"{key}.pub"
    for path, what in ((sec, "secret key ($EMIBSD_SIGN_KEY)"), (pw, "passphrase file ($EMIBSD_SIGN_PASS)"),
                       (pub, "public key")):
        if not path.is_file():
            fail(f"[release] signify = {key!r}: no {what} at {path}")
    if pw.stat().st_mode & 0o077:
        fail(f"{pw}: readable by others; chmod 600 it")
    return {"exe": exe, "sec": sec, "pw": pw, "pub": pub}


def sign(s, directory, t):
    """directory/SHA256.sig, signed and then checked against every file SHA256 lists."""
    if s is None:
        return
    sig = directory / "SHA256.sig"
    sig.unlink(missing_ok=True)
    with open(s["pw"], "rb") as fh:
        r = subprocess.run([s["exe"], "-S", "-e", "-s", str(s["sec"]), "-m", "SHA256", "-x", "SHA256.sig"],
                           cwd=directory, stdin=fh, capture_output=True, text=True)
    if r.returncode or not sig.is_file():
        fail(f"signify -S in {directory}: {r.stderr.strip()}")
    r = subprocess.run([s["exe"], "-C", "-q", "-p", str(s["pub"]), "-x", "SHA256.sig"],
                       cwd=directory, capture_output=True, text=True)
    if r.returncode:
        fail(f"signify -C in {directory} with {s['pub'].name}: {r.stdout}{r.stderr}".strip())
    os.utime(sig, (t, t))


# --- FAT -----------------------------------------------------------------------------------
#
# The two FAT file systems (the ISO's El Torito image, the stick's boot partition) are written
# here, not by makefs: the base system's msdosfs places a file's first cluster from microtime()
# and mkfs_msdos dates the label from gettimeofday(), so makefs -t msdos never makes the same
# image twice. This writer lays the clusters out in order from 2, names in 8.3 (lower case
# through the entry's case bits, as Windows NT and msdosfs read them; no long names), every time
# SOURCE_DATE_EPOCH in UTC, the volume ID its low 32 bits.

def dos_time(t):
    g = time.gmtime(t)
    if g.tm_year < 1980:
        fail(f"SOURCE_DATE_EPOCH {t} is before 1980, which FAT cannot date")
    return (g.tm_hour << 11 | g.tm_min << 5 | g.tm_sec // 2,
            (g.tm_year - 1980) << 9 | g.tm_mon << 5 | g.tm_mday)


def short_name(name):
    """The 8.3 entry name and its case bits (0x08 base, 0x10 extension in lower case)."""
    base, _, ext = name.partition(".")
    ok = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    case = 0
    if not 0 < len(base) <= 8 or len(ext) > 3:
        fail(f"FAT: {name!r} is not an 8.3 name")
    for part, bit in ((base, 0x08), (ext, 0x10)):
        if part != part.upper():
            if part != part.lower():
                fail(f"FAT: {name!r} mixes cases")
            case |= bit
        if not set(part.upper()) <= ok:
            fail(f"FAT: {name!r} is not an 8.3 name")
    return base.upper().ljust(8).encode() + ext.upper().ljust(3).encode(), case


def dirent(name11, attr, case, cluster, size, t):
    tm, dt = dos_time(t)
    return struct.pack("<11sBBBHHHHHHHI", name11, attr, case, 0, tm, dt, dt, 0, tm, dt, cluster, size)


def fat_image(p, files, volid, t):
    """A FAT12/16 file system of the layout `p` holding `files`, {"A/B/name": bytes}."""
    bps, res, nft = SECTOR, 1, 2
    rdsec = p["rde"] * 32 // bps
    data0 = res + nft * p["spf"] + rdsec
    clusters = (p["sectors"] - data0) // p["spc"]
    if (p["bits"] == 12) != (clusters < 4085) or clusters + 2 > p["spf"] * bps * 8 // p["bits"]:
        fail(f"FAT{p['bits']}: {clusters} clusters do not fit the layout")
    csize = p["spc"] * bps

    tree = {}
    for path, data in files.items():
        *dirs, leaf = path.split("/")
        node = tree
        for d in dirs:
            node = node.setdefault(d, {})
        node[leaf] = data

    img = bytearray(p["sectors"] * bps)
    fat = [0] * (clusters + 2)
    eoc = (1 << p["bits"]) - 1
    fat[0], fat[1] = eoc & ~0xFF | p["media"], eoc        # the media byte, then end of chain
    nxt = [2]

    def alloc(nbytes):
        n = max(1, -(-nbytes // csize))
        first = nxt[0]
        if first + n > clusters + 2:
            fail(f"FAT{p['bits']}: {nbytes} bytes do not fit")
        for c in range(first, first + n):
            fat[c] = c + 1
        fat[first + n - 1] = eoc
        nxt[0] += n
        return first

    def write(cluster, data):
        at = (data0 + (cluster - 2) * p["spc"]) * bps
        img[at:at + len(data)] = data

    def fill(node, parent):
        """The entries of one directory, its children written first (in name order)."""
        entries = []
        for name in sorted(node):
            n11, case = short_name(name)
            child = node[name]
            if isinstance(child, dict):
                c = alloc((len(child) + 2) * 32)
                body = (dirent(b".          ", 0x10, 0, c, 0, t)
                        + dirent(b"..         ", 0x10, 0, parent, 0, t)
                        + fill(child, c))
                write(c, body)
                entries.append(dirent(n11, 0x10, case, c, 0, t))
            else:
                c = alloc(len(child)) if child else 0
                if child:
                    write(c, child)
                entries.append(dirent(n11, 0x20, case, c, len(child), t))
        return b"".join(entries)

    root = b""
    if p["label_entry"]:
        root += dirent(p["label"].ljust(11), 0x08, 0, 0, 0, t)
    root += fill(tree, 0)
    if len(root) > p["rde"] * 32:
        fail(f"FAT{p['bits']}: the root directory holds {p['rde']} entries")
    at = (res + nft * p["spf"]) * bps
    img[at:at + len(root)] = root

    if p["bits"] == 12:            # two entries in three bytes
        pairs = fat + [0] * (len(fat) % 2)
        table = b"".join((a | b << 12).to_bytes(3, "little") for a, b in zip(pairs[::2], pairs[1::2]))
    else:
        table = b"".join(struct.pack("<H", v) for v in fat)
    table = table[:p["spf"] * bps]
    for k in range(nft):
        at = (res + k * p["spf"]) * bps
        img[at:at + len(table)] = table

    bs = bytearray(bps)
    bs[0:3] = b"\xEB\x3C\x90"
    bs[3:11] = p["oem"].ljust(8)
    struct.pack_into("<HBHBHHBHHHII", bs, 11, bps, p["spc"], res, nft, p["rde"],
                     p["sectors"] if p["sectors"] < 0x10000 else 0, p["media"], p["spf"],
                     p["spt"], p["heads"], p["hidden"], 0 if p["sectors"] < 0x10000 else p["sectors"])
    bs[38] = 0x29
    struct.pack_into("<I", bs, 39, volid)
    bs[43:54] = p["label"].ljust(11)
    bs[54:62] = f"FAT{p['bits']}".encode().ljust(8)
    bs[62:64] = b"\xF4\xEB"            # hlt; jmp to it: there is no BIOS boot code, EFI only
    bs[64] = 0xFD
    bs[510:512] = b"\x55\xaa"
    img[0:bps] = bs
    return bytes(img)


def ffs1_params(image):
    """The FFS1 superblock's block size, fragment size and cylinders per group."""
    with open(image, "rb") as fh:
        fh.seek(8192)
        sb = fh.read(2048)
    bsize, fsize, frag = struct.unpack_from("<III", sb, 48)
    (cpg,) = struct.unpack_from("<I", sb, 180)
    (magic,) = struct.unpack_from("<I", sb, 1372)
    if magic != 0x011954 or fsize == 0 or bsize // fsize != frag:
        fail(f"{image.name}: no FFS1 superblock (bsize {bsize} fsize {fsize} frag {frag})")
    return bsize, fsize, min(cpg, 0xFFFF)


# --- the stick's MBR and disklabel ---------------------------------------------------------

def chs(lba, spt, heads):
    """fdisk's CHS of `lba` (PRT_lba_to_chs), the maximum past what CHS can say."""
    cyl, head, sect = lba // (spt * heads), (lba // spt) % heads, lba % spt + 1
    if head > 254 or sect > 63 or cyl > 1023:
        head, sect, cyl = 254, 63, 1023
    return bytes([head, (sect & 0x3F) | ((cyl & 0x300) >> 2), cyl & 0xFF])


def mbr(l):
    """The MBR `fdisk -iy -b` writes: the boot partition first and active, OpenBSD's fourth."""
    def entry(flag, typ, start, size):
        return (bytes([flag]) + chs(start, l["spt"], l["heads"]) + bytes([typ])
                + chs(start + size - 1, l["spt"], l["heads"]) + struct.pack("<II", start, size))
    ob_start = l["boot_start"] + l["boot_size"]
    m = bytearray(SECTOR)
    m[446:462] = entry(DOSACTIVE, l["boot_type"], l["boot_start"], l["boot_size"])
    m[494:510] = entry(0, DOSPTYP_OPENBSD, ob_start, l["total"] - ob_start)
    m[510:512] = b"\x55\xaa"
    return bytes(m)


def fragblock(fsize, frag):
    """DISKLABELV1_FFS_FRAGBLOCK."""
    ffs = lambda v: (v & -v).bit_length()
    return 0 if fsize * frag == 0 else ((ffs(fsize * frag) - 13) << 3) | ffs(frag)


def disklabel(l, bsize, fsize, cpg):
    """The label `disklabel -wAT-` writes on the image's vnd(4): `a` the OpenBSD partition's
    FFS, `c` the disk, `i` the boot partition (spoofed from the MBR) as MSDOS."""
    total = l["total"]
    ob_start = l["boot_start"] + l["boot_size"]
    d = bytearray(SECTOR)
    struct.pack_into("<IH", d, 0, DISKMAGIC, DTYPE_VND)
    d[8:18] = b"vnd device"
    d[24:34] = b"fictitious"
    struct.pack_into("<IIIIII", d, 40, SECTOR, l["spt"], 1, total // l["spt"], l["spt"], total)
    d[64:72] = l["duid"]
    struct.pack_into("<II", d, 80, ob_start, total)          # d_bstart, d_bend
    struct.pack_into("<H", d, 114, 1)                         # d_version
    struct.pack_into("<I", d, 132, DISKMAGIC)                 # d_magic2
    struct.pack_into("<H", d, 138, MAXPARTITIONS)

    def part(letter, size, off, fstype, frag=0, cpg=0):
        at = 148 + 16 * (ord(letter) - ord("a"))
        struct.pack_into("<II", d, at, size, off)
        struct.pack_into("<BBH", d, at + 12, fstype, frag, cpg)

    part("a", total - ob_start, ob_start, FS_BSDFFS, fragblock(fsize, bsize // fsize), cpg)
    part("c", total, 0, 0)
    part("i", l["boot_size"], l["boot_start"], FS_MSDOS)
    end = 148 + 16 * MAXPARTITIONS
    s = 0
    for i in range(0, end, 2):
        s ^= d[i] | d[i + 1] << 8
    struct.pack_into("<H", d, 136, s)                         # d_checksum
    return bytes(d)


# --- the tree and the images ---------------------------------------------------------------

def fresh(path):
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


def put(path, data, mode, t):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)
    os.utime(path, (t, t))


def link_tree(src, dst):
    """dst as hard links to src's files (no copy of the release's 300 MB per image)."""
    for p in sorted(src.rglob("*")):
        q = dst / p.relative_to(src)
        if p.is_dir():
            q.mkdir(parents=True, exist_ok=True)
        else:
            q.parent.mkdir(parents=True, exist_ok=True)
            os.link(p, q)


def stage(arch, doc, work, t, signed):
    """target/media/<arch>/release/<osrev>/<arch>/: the release files, their SHA256 and, signed,
    its SHA256.sig."""
    ktag, utag = doc["kernel"]["release"], doc["userland"]["release"]
    k = checked(TARGET / "kernel" / ktag / f"kernel-{ktag}-{arch}")
    u = checked(TARGET / "userland" / utag / f"userland-{utag}-{arch}")
    base = [n for n in u if n.startswith("base") and n.endswith(".tar.zst")]
    if len(base) != 1:
        fail(f"userland {utag} {arch}: no single base set")
    rev = base[0].removeprefix("base").removesuffix(".tar.zst")       # "80"
    osrev = f"{rev[:-1]}.{rev[-1]}"                                    # "8.0"
    sets = sorted(n for n in u if n.endswith(f"{rev}.tar.zst"))
    rel = fresh(work / "release") / osrev / arch
    rel.mkdir(parents=True)
    for name, path in [("bsd.mp", k["bsd.mp"])] + [(n, u[n]) for n in sets + [f"INSTALL.{arch}"]]:
        put(rel / name, path.read_bytes(), 0o644, t)
    put(rel / "bsd.rd", k["bsd.RAMDISK"].read_bytes(), 0o644, t)
    rd_size = rdsetroot(rel / "bsd.rd", u[f"miniroot{rev}.fs"])
    os.utime(rel / "bsd.rd", (t, t))
    names = sorted(p.name for p in rel.iterdir())
    sums = "".join(f"SHA256 ({n}) = {sha256(rel / n)}\n" for n in names)
    put(rel / "SHA256", sums.encode(), 0o644, t)
    sign(signed, rel, t)
    return k, rev, osrev, rd_size


def make_iso(arch, k, rev, osrev, work, out, t):
    """The ISO: the release tree, /etc/boot.conf naming bsd.rd, the El Torito EFI image."""
    tree = fresh(work / "iso-tree")
    link_tree(work / "release", tree)
    put(tree / "etc/boot.conf", f"set image /{osrev}/{arch}/bsd.rd\n".encode(), 0o644, t)
    loader = STICK[arch]["loader"]
    cdbr = tree / osrev / arch / "cdbr"
    put(cdbr, fat_image(CDBOOT_FAT, {f"EFI/BOOT/{loader}": k[loader].read_bytes()}, t & 0xFFFFFFFF, t),
        0o644, t)
    makefs("cd9660", f"rockridge,label={ISO_LABEL.format(rev=rev)},bootimage=efi;{cdbr},no-emul-boot",
           out, tree, t)


def make_stick(arch, k, osrev, work, out, t):
    """The stick: MBR, the FAT boot partition with the loader, the OpenBSD partition's FFS1
    holding the release tree and /bsd, /bsd.rd (links to it), then the disklabel."""
    l = STICK[arch]
    loader = l["loader"].lower()
    fat = work / "stick-boot.fat"
    if l["fat"]["sectors"] != l["boot_size"]:
        fail(f"{arch}: the boot FAT is not the boot partition's size")
    put(fat, fat_image(l["fat"], {f"efi/boot/{loader}": k[l["loader"]].read_bytes(),
                                  "efi/boot/startup.nsh": f"{loader}\n".encode()}, t & 0xFFFFFFFF, t),
        0o644, t)

    tree = fresh(work / "stick-tree")
    link_tree(work / "release", tree)
    rd = tree / osrev / arch / "bsd.rd"
    os.link(rd, tree / "bsd.rd")
    os.link(rd, tree / "bsd")
    ob_start = l["boot_start"] + l["boot_size"]
    ob_size = l["total"] - ob_start
    ffs = work / "stick-a.ffs"
    makefs("ffs", "version=1,bsize=16384,fsize=2048,minfree=0,optimization=space,density=524288",
           ffs, tree, t, size=ob_size * SECTOR)
    if ffs.stat().st_size != ob_size * SECTOR:
        fail(f"{ffs.name}: {ffs.stat().st_size} bytes, not the partition's {ob_size * SECTOR}")
    bsize, fsize, cpg = ffs1_params(ffs)

    out.unlink(missing_ok=True)
    with open(out, "wb") as img:
        img.truncate(l["total"] * SECTOR)
        img.write(mbr(l))
        for at, src in ((l["boot_start"], fat), (ob_start, ffs)):
            img.seek(at * SECTOR)
            with open(src, "rb") as fh:
                shutil.copyfileobj(fh, img, 1 << 20)
        img.seek((ob_start + 1) * SECTOR)
        img.write(disklabel(l, bsize, fsize, cpg))


def cmd_build(_):
    doc = manifest()
    t = epoch()
    signed = signer(doc)
    version = doc["release"]["version"]
    dest = DIST / version
    dest.mkdir(parents=True, exist_ok=True)
    made = []
    for arch in doc["release"]["arch"]:
        if arch not in STICK:
            fail(f"no stick layout for {arch}")
        work = fresh(TARGET / "media" / arch)
        k, rev, osrev, rd_size = stage(arch, doc, work, t, signed)
        iso = dest / f"emibsd-{version}-{arch}-install.iso"
        img = dest / f"emibsd-{version}-{arch}-install.img"
        make_iso(arch, k, rev, osrev, work, iso, t)
        make_stick(arch, k, osrev, work, img, t)
        print(f"media {arch}: {osrev}/{arch}/ with bsd.rd (rd_root_size {rd_size}); "
              f"{iso.name} {iso.stat().st_size} bytes, {img.name} {img.stat().st_size} bytes")
        made += [iso, img]
    sums = "".join(f"SHA256 ({p.name}) = {sha256(p)}\n" for p in sorted(dest.glob("emibsd-*")))
    (dest / "SHA256").write_text(sums)
    sign(signed, dest, t)
    print(sums, end="")
    print(f"media: signed with {signed['pub'].stem}, SHA256.sig in {osrev}/<arch>/ and {dest.relative_to(ROOT)}/"
          if signed else "media: unsigned ([release] has no signify key)")
    return 0


def cmd_rdsetroot(args):
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    print(rdsetroot(Path(args[0]), Path(args[1])))
    return 0


def main():
    cmds = {"build": cmd_build, "rdsetroot": cmd_rdsetroot}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__, file=sys.stderr)
        return 2
    return cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
