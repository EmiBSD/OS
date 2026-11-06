#!/usr/bin/env python3
"""The install media's smoke (docs/MEDIA.md): each image of dist/<version>/ installs EmiBSD in
QEMU and the installed disk boots alone to `login:`.

usage: smokemedia.py [iso|img]...      both by default, every arch of [release]

Per image and arch, in target/smoke-media/<arch>/<kind>/: QEMU `virt` (acpi=off, cortex-a72,
1 GiB, 2 CPUs, EDK2 from QEMU's share) boots the image (the ISO on a scsi-cd, the stick on a
usb-storage behind its own qemu-xhci, both bootindex=0, the stick through a snapshot so the image
is never written) beside a fresh 3 GiB virtio-blk disk. The installer is answered over the
serial console as a person answers it: each question when it shows, so the order and the
optional ones do not matter. After `CONGRATULATIONS!` and the halt, the disk boots alone through
the loader installboot(8) put on it, root logs in, and `uname -a` must name EmiBSD.

QEMU runs under the machine's lock, /tmp/emibsd/ci.lock (a mkdir directory, its holder in
`owner`), shared with the other EmiBSD repositories. Stdlib only; QEMU from $PATH.
"""
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK = Path("/tmp/emibsd/ci.lock")
DISK_BYTES = 3 << 30

# Every question and its answers in turn. The fresh disk is sd0; the CD is cd0, the stick sd1
# (umass(4) attaches after the virtio disk).
COMMON = [
    ("(I)nstall, (U)pgrade, (A)utoinstall or (S)hell?", ["i"]),
    ("Terminal type?", [""]),
    ("System hostname?", ["emibsd"]),
    ("Network interface to configure?", ["done"]),
    ("DNS domain name?", [""]),
    ("DNS nameservers?", [""]),
    ("Password for root account?", ["emibsd"]),   # also its "(again)"
    ("Start sshd(8) by default?", ["no"]),
    ("Change the default console to com0?", ["yes"]),
    ("Which speed should com0 use?", [""]),
    ("Setup a user?", ["no"]),
    ("Which disk is the root disk?", ["sd0"]),
    ("Encrypt the root disk", ["no"]),
    ("Use (W)hole disk", ["whole"]),
    ("Use (A)uto layout", ["a"]),
    ("Which disk do you wish to initialize?", ["done"]),
    ("Set name(s)?", ["done"]),
    ("Continue without verification?", ["yes"]),
    ("What timezone are you in?", ["UTC"]),
    ("Exit to (S)hell, (H)alt or (R)eboot?", ["h"]),
]
SOURCES = {
    "iso": [
        ("Location of sets?", ["cd0", "done"]),
        ("Pathname to the sets?", [""]),
    ],
    "img": [
        ("Location of sets?", ["disk", "done"]),
        ("Is the disk partition already mounted?", ["no"]),
        ("Which disk contains the install media?", ["sd1"]),
        ("Which sd1 partition has the install sets?", ["a"]),
        ("Pathname to the sets?", ["{osrev}/{arch}"]),
    ],
}
FAILURES = ["panic:", "is not a valid choice", "not a valid timezone", "Checksum test for",
            "Installation of ", "Failed to install bootblocks", "found here anyway",
            "and found no", "No filesystems found on", "does not exist."]


def fail(msg):
    raise SystemExit(f"smoke-media: {msg}")


class Lock:
    """The machine's QEMU lock: waits for it, holds it, gives it back."""

    def __init__(self, what):
        self.what = what

    def __enter__(self):
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        for i in range(480):
            try:
                LOCK.mkdir()
                break
            except FileExistsError:
                if i == 0:
                    owner = (LOCK / "owner").read_text().strip() if (LOCK / "owner").exists() else "?"
                    print(f"smoke-media: waiting for {LOCK}: {owner}", flush=True)
                time.sleep(15)
        else:
            fail(f"{LOCK} still held after two hours")
        (LOCK / "owner").write_text(f"EmiBSD/OS smoke-media: {self.what}, {time.ctime()}\n")
        return self

    def __exit__(self, *_):
        (LOCK / "owner").unlink(missing_ok=True)
        LOCK.rmdir()


def qemu():
    exe = shutil.which("qemu-system-aarch64")
    if not exe:
        fail("no qemu-system-aarch64 on PATH")
    share = Path(exe).resolve().parent.parent / "share/qemu"
    code, vars_ = share / "edk2-aarch64-code.fd", share / "edk2-arm-vars.fd"
    if not code.is_file() or not vars_.is_file():
        fail(f"no EDK2 firmware in {share}")
    return exe, code, vars_


class Vm:
    """One QEMU run with its serial console on stdio, the transcript kept for the log."""

    def __init__(self, run, name, disk, medium=None):
        exe, code, vars_ = qemu()
        self.log = run / f"{name}.log"
        fd = run / f"{name}.vars.fd"
        shutil.copyfile(vars_, fd)
        cmd = [exe, "-M", "virt,acpi=off", "-cpu", "cortex-a72", "-m", "1024M", "-smp", "2",
               "-display", "none", "-monitor", "none", "-no-reboot", "-serial", "stdio",
               "-drive", f"if=pflash,format=raw,readonly=on,file={code}",
               "-drive", f"if=pflash,format=raw,file={fd}",
               "-drive", f"if=none,format=raw,file={disk},id=hd0", "-device", "virtio-blk-device,drive=hd0"]
        if medium and medium[0] == "iso":
            cmd += ["-device", "virtio-scsi-device,id=scsi0",
                    "-drive", f"if=none,format=raw,media=cdrom,readonly=on,file={medium[1]},id=cd0",
                    "-device", "scsi-cd,drive=cd0,bootindex=0"]
        elif medium and medium[0] == "img":
            cmd += ["-device", "qemu-xhci,id=stickhc",
                    "-drive", f"if=none,format=raw,snapshot=on,file={medium[1]},id=stick",
                    "-device", "usb-storage,bus=stickhc.0,drive=stick,bootindex=0"]
        self.vm = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
        self.buf = bytearray()
        self.mutex = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        self.start = time.time()

    def _read(self):
        while chunk := self.vm.stdout.read1(4096):
            with self.mutex:
                self.buf.extend(chunk)

    def text(self):
        with self.mutex:
            return self.buf.decode("utf-8", "replace")

    def type(self, line):
        time.sleep(0.3)
        for ch in line + "\n":
            self.vm.stdin.write(ch.encode())
            self.vm.stdin.flush()
            time.sleep(0.02)

    def drive(self, answers, expects, timeout, stall):
        """Answers each question when it shows after the last answer, until every expected
        pattern (a regular expression) is seen, then gives QEMU a few seconds to stop."""
        turns = {q: list(a) for q, a in answers}
        at, seen, changed = 0, 0, time.time()
        try:
            while True:
                time.sleep(0.2)
                text = self.text()
                bad = [f for f in FAILURES if f in text]
                if bad:
                    return f"failure line {bad[0]!r}"
                hits = [(text.find(q, at), q) for q in turns if turns[q] and text.find(q, at) >= 0]
                if hits:
                    i, q = min(hits)
                    reply = turns[q].pop(0) if len(turns[q]) > 1 else turns[q][0]
                    self.type(reply)
                    print(f"  {time.time() - self.start:5.0f}s  {q!r} -> {reply!r}", flush=True)
                    at = i + len(q)
                    continue
                if all(re.search(e, text) for e in expects):
                    end = time.time() + 5
                    while self.vm.poll() is None and time.time() < end:
                        time.sleep(0.5)
                    return None
                if self.vm.poll() is not None:
                    return f"QEMU ended ({self.vm.returncode}) before {[e for e in expects if not re.search(e, text)]}"
                if len(text) != seen:
                    seen, changed = len(text), time.time()
                elif time.time() - changed > stall:
                    return f"no console output for {stall} s"
                if time.time() - self.start > timeout:
                    return f"timed out after {timeout} s"
        finally:
            self.vm.kill()
            self.vm.wait()
            self.log.write_bytes(self.buf)


def smoke(arch, kind, image):
    run = ROOT / "target/smoke-media" / arch / kind
    shutil.rmtree(run, ignore_errors=True)
    run.mkdir(parents=True)
    disk = run / "disk.img"
    with open(disk, "wb") as fh:
        fh.truncate(DISK_BYTES)
    fmt = {"osrev": "8.0", "arch": arch}
    answers = [(q, [a.format(**fmt) for a in r]) for q, r in COMMON + SOURCES[kind]]
    print(f"smoke-media {arch} {kind}: install from {image.name}", flush=True)
    t0 = time.time()
    err = Vm(run, "install", disk, (kind, image)).drive(
        # The disk is written once the kernel has synced it; halt then waits for a key.
        answers, [r"CONGRATULATIONS!", r"syncing disks\.\.\.[ 0-9]*done"], timeout=1800, stall=300)
    if err:
        return f"install: {err} (log {run.relative_to(ROOT)}/install.log)"
    t1 = time.time()
    print(f"smoke-media {arch} {kind}: installed in {t1 - t0:.0f} s; booting the disk alone", flush=True)
    login = [("login:", ["root"]), ("Password:", ["emibsd"]),
             ("# ", ["uname -a; ls -l /bsd; echo up-$((6*7))", "halt -p"])]
    err = Vm(run, "boot", disk).drive(login, [r"up-42"], timeout=900, stall=300)
    if err:
        return f"boot: {err} (log {run.relative_to(ROOT)}/boot.log)"
    text = (run / "boot.log").read_text(errors="replace")
    # `uname -a`'s line, not the kernel's banner: "EmiBSD <host> 8.0 GENERIC#<n> <arch>".
    uname = next((l.strip() for l in text.splitlines()
                  if re.fullmatch(rf"EmiBSD \S+ \d+\.\d+ \S+ {arch}", l.strip())), None)
    if not uname:
        return f"boot: no `uname -a` line naming EmiBSD (log {run.relative_to(ROOT)}/boot.log)"
    print(f"smoke-media {arch} {kind}: ok, install {t1 - t0:.0f} s, boot {time.time() - t1:.0f} s: {uname}",
          flush=True)
    return None


def main():
    kinds = sys.argv[1:] or ["iso", "img"]
    if any(k not in SOURCES for k in kinds):
        print(__doc__, file=sys.stderr)
        return 2
    with open(ROOT / "MANIFEST.toml", "rb") as fh:
        doc = tomllib.load(fh)
    version = doc["release"]["version"]
    bad = 0
    with Lock(f"installing the {version} media"):
        for arch in doc["release"]["arch"]:
            for kind in kinds:
                image = ROOT / "dist" / version / f"emibsd-{version}-{arch}-install.{kind}"
                if not image.is_file():
                    fail(f"no {image.relative_to(ROOT)}; run just media first")
                err = smoke(arch, kind, image)
                if err:
                    print(f"smoke-media {arch} {kind}: FAILED: {err}", flush=True)
                    bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
