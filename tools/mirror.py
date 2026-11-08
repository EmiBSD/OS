#!/usr/bin/env python3
"""The mirror: a signed release's tree on cdn.emibsd.org (Cloudflare R2), for network installs,
sysupgrade and syspatch (docs/MEDIA.md, The mirror).

usage: mirror.py plan                    what publish would upload and delete; touches nothing
       mirror.py publish                 upload the tree, then run check
       mirror.py check                   the public side: SHA256.sig verifies, every file matches

The tree is media.py's <osrev>/<arch>/ of every arch of [release], minus the El Torito image
`cdbr`: bsd.mp, bsd.rd, the sets, INSTALL.<arch>, SHA256 and SHA256.sig. Only a signed tree
goes up (`[release] signify`): before Userland 2026.11.3 the installer trusts OpenBSD's keys and
looks at openbsd.org, so a mirror would serve nobody. The tree's SHA256.sig is checked with
keys/<key>.pub before anything is uploaded.

Upload order, so a client never pairs a new SHA256.sig with old files: every other file, then
SHA256, then SHA256.sig, then the objects no longer in the tree are deleted. The file names
repeat in every release, so every object is Cache-Control max-age=300. Then the same files are
copied inside the bucket to pub/EmiBSD/releases/<version>/<arch>/, the release's own copy (the
user's decision, 2026-11-07), never deleted by a later release.

The credentials are the user's, outside every repository: $EMIBSD_R2_CREDS (default
~/emibsd-keys/emibsd-r2, mode 0600), shell assignments of AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY and R2_ENDPOINT. They are sourced by the shell that runs `aws` and never
read, printed or logged here. `cgi-bin/ftplist.cgi` is the bucket's and is left as it is.

Stdlib and the `aws` CLI.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "target"
KEYS = ROOT / "keys"
BUCKET = "emibsd-cdn"
PREFIX = "pub/EmiBSD"
PUBLIC = "https://cdn.emibsd.org"
FTPLIST = "https://ftplist1.emibsd.org/cgi-bin/ftplist.cgi"
CACHE = "max-age=300"
TEXT = {"SHA256", "SHA256.sig"}
SKIP = {"cdbr"}
UA = "EmiBSD-OS-mirror-check"

# The shell that runs aws: it sources the credentials file ($1) itself, so their values never
# pass through this program.
AWS = ('set -a; . "$1"; set +a; export AWS_DEFAULT_REGION=auto AWS_ENDPOINT_URL="$R2_ENDPOINT" '
       'AWS_PAGER=; shift; exec aws "$@"')


def fail(msg):
    raise SystemExit(f"mirror: {msg}")


def manifest():
    with open(ROOT / "MANIFEST.toml", "rb") as fh:
        return tomllib.load(fh)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def signify():
    exe = shutil.which("signify")
    if not exe:
        fail("no signify on PATH")
    return exe


def creds():
    path = Path(os.environ.get("EMIBSD_R2_CREDS") or Path.home() / "emibsd-keys/emibsd-r2")
    if not path.is_file():
        fail(f"no R2 credentials at {path} ($EMIBSD_R2_CREDS)")
    if path.stat().st_mode & 0o077:
        fail(f"{path}: readable by others; chmod 600 it")
    if not shutil.which("aws"):
        fail("no aws CLI on PATH")
    return path


def aws(cred, *args, capture=False):
    r = subprocess.run(["/bin/zsh", "-c", AWS, "zsh", str(cred), *args],
                       capture_output=True, text=True)
    if r.returncode:
        fail(f"aws {args[0]} {args[1]}: {(r.stderr or r.stdout).strip()}")
    return r.stdout if capture else None


def trees(doc):
    """[(arch, osrev, local tree, files)] for every arch, each tree's signature checked."""
    key = doc["release"].get("signify")
    if not key:
        fail("[release] has no signify key: only signed releases go to the mirror")
    pub = KEYS / f"{key}.pub"
    out = []
    for arch in doc["release"]["arch"]:
        rel = TARGET / "media" / arch / "release"
        dirs = [d for d in rel.glob(f"*/{arch}") if d.is_dir()] if rel.is_dir() else []
        if len(dirs) != 1:
            fail(f"no media tree for {arch} under {rel.relative_to(ROOT)}; run just media first")
        tree = dirs[0]
        osrev = tree.parent.name
        r = subprocess.run([signify(), "-C", "-q", "-p", str(pub), "-x", "SHA256.sig"], cwd=tree,
                           capture_output=True, text=True)
        if r.returncode:
            fail(f"{tree.relative_to(ROOT)}: SHA256.sig does not verify with {pub.name}: "
                 f"{(r.stdout + r.stderr).strip()}")
        files = sorted(p.name for p in tree.iterdir() if p.is_file() and p.name not in SKIP)
        listed = {l.split("(", 1)[1].split(")", 1)[0] for l in (tree / "SHA256").read_text().splitlines()}
        if set(files) - TEXT != listed:
            fail(f"{tree.relative_to(ROOT)}: the files and SHA256 disagree: {sorted(set(files) ^ (listed | TEXT))}")
        out.append((arch, osrev, tree, files))
    return out


def order(files):
    """Every other file, then SHA256, then SHA256.sig."""
    return [f for f in files if f not in TEXT] + ["SHA256", "SHA256.sig"]


def remote(cred, prefix):
    """The object names directly under prefix/."""
    text = aws(cred, "s3", "ls", f"s3://{BUCKET}/{prefix}/", capture=True)
    return sorted(l.split(None, 3)[3] for l in text.splitlines() if len(l.split(None, 3)) == 4)


def archive(version, arch):
    """The release's own copy, kept for good."""
    return f"{PREFIX}/releases/{version}/{arch}"


def cmd_plan(_):
    doc = manifest()
    cred = creds()
    version = doc["release"]["version"]
    for arch, osrev, tree, files in trees(doc):
        prefix = f"{PREFIX}/{osrev}/{arch}"
        there = remote(cred, prefix)
        print(f"mirror {version} {arch}: s3://{BUCKET}/{prefix}/")
        for f in order(files):
            print(f"  upload {f} ({(tree / f).stat().st_size} bytes)")
        for f in sorted(set(there) - set(files)):
            print(f"  delete {f}")
        kept = remote(cred, archive(version, arch))
        print(f"  copy all to s3://{BUCKET}/{archive(version, arch)}/"
              + (f" (it holds {len(kept)} objects already)" if kept else ""))
    return 0


def cmd_publish(_):
    doc = manifest()
    cred = creds()
    for arch, osrev, tree, files in trees(doc):
        prefix = f"{PREFIX}/{osrev}/{arch}"
        for f in order(files):
            kind = "text/plain" if f in TEXT or f.startswith("INSTALL.") else "application/octet-stream"
            aws(cred, "s3", "cp", "--only-show-errors", str(tree / f), f"s3://{BUCKET}/{prefix}/{f}",
                "--content-type", kind, "--cache-control", CACHE)
            print(f"mirror {arch}: {prefix}/{f}", flush=True)
        for f in sorted(set(remote(cred, prefix)) - set(files)):
            aws(cred, "s3", "rm", "--only-show-errors", f"s3://{BUCKET}/{prefix}/{f}")
            print(f"mirror {arch}: deleted {prefix}/{f}", flush=True)
        keep = archive(doc["release"]["version"], arch)
        for f in order(files):   # inside R2, with the object's metadata
            aws(cred, "s3", "cp", "--only-show-errors", f"s3://{BUCKET}/{prefix}/{f}", f"s3://{BUCKET}/{keep}/{f}")
        print(f"mirror {arch}: {len(files)} files kept in {keep}/", flush=True)
    return cmd_check([])


def get(url, dest=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=120) as r:
        if dest is None:
            return r.read()
        with open(dest, "wb") as fh:
            shutil.copyfileobj(r, fh, 1 << 20)


def cmd_check(_):
    """From the public side: SHA256 and SHA256.sig as the CDN serves them, the signature with the
    .pub, every file downloaded and checked against them, and the mirror list."""
    doc = manifest()
    pub = KEYS / f"{doc['release']['signify']}.pub"
    bad = 0
    for arch, osrev, tree, files in trees(doc):
        base = f"{PUBLIC}/{PREFIX}/{osrev}/{arch}"
        with tempfile.TemporaryDirectory(prefix="emibsd-mirror-") as tmp:
            for f in files:
                get(f"{base}/{f}?v={sha256(tree / 'SHA256')[:12]}", Path(tmp) / f)
            r = subprocess.run([signify(), "-C", "-p", str(pub), "-x", "SHA256.sig"], cwd=tmp,
                               capture_output=True, text=True)
            same = (Path(tmp) / "SHA256").read_bytes() == (tree / "SHA256").read_bytes()
        ok = r.returncode == 0 and same
        bad += not ok
        print(f"mirror check {arch}: {base}/: {'ok' if ok else 'FAILED'}; "
              f"{len(files)} files downloaded, signature {'verified' if r.returncode == 0 else 'NOT verified'}, "
              f"SHA256 {'matches' if same else 'DIFFERS from'} the local tree")
        if r.returncode:
            print((r.stdout + r.stderr).strip())
        kept = f"{PUBLIC}/{archive(doc['release']['version'], arch)}"
        tag = sha256(tree / "SHA256")[:12]
        same_kept = all(get(f"{kept}/{f}?v={tag}") == (tree / f).read_bytes() for f in TEXT)
        bad += not same_kept
        print(f"mirror check {arch}: {kept}/: SHA256 and SHA256.sig "
              f"{'match' if same_kept else 'DO NOT match'} the local tree")
    line = get(FTPLIST).decode(errors="replace").strip()
    print(f"mirror check: {FTPLIST}: {line!r}")
    if not line.startswith(f"{PUBLIC}/{PREFIX}"):
        bad += 1
        print("mirror check: the mirror list does not name the cdn")
    return 1 if bad else 0


def main():
    cmds = {"plan": cmd_plan, "publish": cmd_publish, "check": cmd_check}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__, file=sys.stderr)
        return 2
    return cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
