#!/usr/bin/env python3
"""The mirror: a signed release's tree on cdn.emibsd.org (Cloudflare R2), for network installs,
sysupgrade and syspatch (docs/MEDIA.md, The mirror).

usage: mirror.py plan                    what publish would upload and delete; touches nothing
       mirror.py publish                 upload the tree, rewrite the listings, then run check
       mirror.py index [--out DIR]       rewrite the listings only (into DIR instead: no upload)
       mirror.py check                   the public side: SHA256.sig verifies, every file matches,
                                         the listings answer

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

The listings, last (the user's wish, 2026-11-08, by way of Userland's session): R2 serves no
directory index, and a Cloudflare URL Rewrite serves a path ending in "/" as <path>index.html,
so every directory of the bucket gets an `index.html` in the style of a web server's
autoindex (relative links, a directory's ending in "/", which the rewrite needs), and every set
directory (one holding SHA256.sig) an `index.txt` in the style of `ls -lT`, which the installer
reads with `sed -En 's/^.* ([a-zA-Z][a-zA-Z0-9._-]+)$/\\1/p'`. They are made from the bucket's
own listing after everything else is up: index.txt first, then index.html deepest first, `/`
last, then the index files of directories that no longer exist are deleted, so a listing never
names what is not there. `cgi-bin/` and the index files themselves are never listed. A directory
asked for without its "/" stays 404 (the user's choice).

The credentials are the user's, outside every repository: $EMIBSD_R2_CREDS (default
~/emibsd-keys/emibsd-r2, mode 0600), shell assignments of AWS_ACCESS_KEY_ID,
AWS_SECRET_ACCESS_KEY and R2_ENDPOINT. They are sourced by the shell that runs `aws` and never
read, printed or logged here.

The bucket is OS's alone (the user's decision, 2026-11-08): nothing else writes to it, so its
listings stay complete. That includes `cgi-bin/ftplist.cgi`, the installer's mirror list behind
ftplist1.emibsd.org, which every publish rewrites with its one line, the mirror's URL.

Stdlib and the `aws` CLI.
"""
import datetime
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "target"
KEYS = ROOT / "keys"
BUCKET = "emibsd-cdn"
PREFIX = "pub/EmiBSD"
PUBLIC = "https://cdn.emibsd.org"
FTPLIST = "https://ftplist1.emibsd.org/cgi-bin/ftplist.cgi"
FTPLIST_KEY = "cgi-bin/ftplist.cgi"
MIRROR = f"{PUBLIC}/{PREFIX}"         # the mirror list's one line
CACHE = "max-age=300"
TEXT = {"SHA256", "SHA256.sig"}
SKIP = {"cdbr"}
UA = "EmiBSD-OS-mirror-check"
INDEXES = {"index.html", "index.txt"}
HIDDEN = ("cgi-bin/",)
NAME = re.compile(r"^.* ([a-zA-Z][a-zA-Z0-9._-]+)$")   # the installer's sed, install.sub

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
    r = subprocess.run(["/bin/zsh", "-c", AWS, "zsh", str(cred), "s3", "ls", f"s3://{BUCKET}/{prefix}/"],
                       capture_output=True, text=True)
    if r.returncode and (r.returncode != 1 or r.stdout or r.stderr.strip()):
        fail(f"aws s3 ls {prefix}/: {(r.stderr or r.stdout).strip()}")
    text = r.stdout                      # `aws s3 ls` exits 1, silent, for a prefix with nothing
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
        for f in sorted(set(there) - set(files) - INDEXES):
            print(f"  delete {f}")
        kept = remote(cred, archive(version, arch))
        print(f"  copy all to s3://{BUCKET}/{archive(version, arch)}/"
              + (f" (it holds {len(kept)} objects already)" if kept else ""))
    print("  then rewrite index.html in every directory of the bucket and index.txt in every "
          "set directory (mirror.py index --out DIR shows them)")
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
        for f in sorted(set(remote(cred, prefix)) - set(files) - INDEXES):   # the listings are index's
            aws(cred, "s3", "rm", "--only-show-errors", f"s3://{BUCKET}/{prefix}/{f}")
            print(f"mirror {arch}: deleted {prefix}/{f}", flush=True)
        keep = archive(doc["release"]["version"], arch)
        for f in order(files):   # inside R2, with the object's metadata; R2 has no object tagging
            aws(cred, "s3api", "copy-object", "--bucket", BUCKET, "--copy-source", f"{BUCKET}/{prefix}/{f}",
                "--key", f"{keep}/{f}", "--metadata-directive", "COPY", capture=True)
        print(f"mirror {arch}: {len(files)} files kept in {keep}/", flush=True)
    write_ftplist(cred)
    write_indexes(cred)
    return cmd_check([])


def write_ftplist(cred):
    """The installer's mirror list: one line, the mirror's URL."""
    with tempfile.TemporaryDirectory(prefix="emibsd-ftplist-") as tmp:
        (Path(tmp) / "ftplist.cgi").write_text(MIRROR + "\n")
        aws(cred, "s3", "cp", "--only-show-errors", str(Path(tmp) / "ftplist.cgi"),
            f"s3://{BUCKET}/{FTPLIST_KEY}", "--content-type", "text/plain", "--cache-control", CACHE)
    print(f"mirror: {FTPLIST_KEY}: {MIRROR}", flush=True)


def objects(cred):
    """[(key, size, last modified)] of the whole bucket."""
    out = aws(cred, "s3api", "list-objects-v2", "--bucket", BUCKET, "--output", "json", capture=True)
    doc = json.loads(out) if out.strip() else {}
    return [(o["Key"], o["Size"], datetime.datetime.fromisoformat(o["LastModified"]).astimezone(datetime.UTC))
            for o in doc.get("Contents") or []]


def directories(objs):
    """{directory ("" is /, else "a/b/"): ({file: (size, time)}, {subdirectory: newest time})}
    of what a listing shows: no index files, nothing under cgi-bin/."""
    dirs = {"": ({}, {})}
    for key, size, t in objs:
        if key.endswith("/") or key.startswith(HIDDEN) or key.rsplit("/", 1)[-1] in INDEXES:
            continue
        parts = key.split("/")
        for i in range(len(parts) - 1):
            parent, sub = "".join(p + "/" for p in parts[:i]), parts[i]
            dirs.setdefault(parent + sub + "/", ({}, {}))
            subs = dirs.setdefault(parent, ({}, {}))[1]
            subs[sub] = max(subs.get(sub, t), t)
        dirs.setdefault("".join(p + "/" for p in parts[:-1]), ({}, {}))[0][parts[-1]] = (size, t)
    return dirs


def index_html(d, files, subs):
    title = html.escape(f"Index of /{d}")
    rows = [] if d == "" else ['<a href="../">../</a>']
    entries = [(s + "/", subs[s], "-") for s in sorted(subs)] + \
              [(f, files[f][1], str(files[f][0])) for f in sorted(files)]
    for name, t, size in entries:
        link = f'<a href="{html.escape(urllib.parse.quote(name), quote=True)}">{html.escape(name)}</a>'
        rows.append(f"{link}{' ' * max(1, 51 - len(name))}{t:%d-%b-%Y %H:%M}{size:>20}")
    return ("<!DOCTYPE html>\n<html>\n<head><meta charset=\"utf-8\"><title>" + title +
            "</title></head>\n<body>\n<h1>" + title + "</h1><hr><pre>" + "\n".join(rows) +
            "\n</pre><hr>\n</body>\n</html>\n")


def index_txt(d, files):
    lines = []
    for f in sorted(files):
        if not NAME.match(f" {f}"):
            fail(f"{d}{f}: a name the installer's index.txt parser cannot read")
        size, t = files[f]
        lines.append(f"-rw-r--r--  1 root  wheel  {size:>10} {t:%b} {t.day:>2} {t:%H:%M:%S %Y} {f}")
    return "\n".join(lines) + "\n"


def indexes(objs):
    """[(key, bytes, content type)] in upload order: every index.txt, then every index.html
    deepest first, / last."""
    dirs = directories(objs)
    txt = [(d + "index.txt", index_txt(d, files).encode(), "text/plain; charset=utf-8")
           for d, (files, _) in sorted(dirs.items()) if "SHA256.sig" in files]
    htm = [(d + "index.html", index_html(d, files, subs).encode(), "text/html; charset=utf-8")
           for d, (files, subs) in sorted(dirs.items(), key=lambda e: (-e[0].count("/"), e[0]))]
    return txt + htm


def write_indexes(cred, out=None):
    objs = objects(cred)
    made = indexes(objs)
    if out:
        for key, data, _ in made:
            (Path(out) / key).parent.mkdir(parents=True, exist_ok=True)
            (Path(out) / key).write_bytes(data)
        print(f"mirror index: {len(made)} listings written under {out}, nothing uploaded")
        return
    with tempfile.TemporaryDirectory(prefix="emibsd-index-") as tmp:
        for i, (key, data, kind) in enumerate(made):
            (Path(tmp) / str(i)).write_bytes(data)
            aws(cred, "s3", "cp", "--only-show-errors", str(Path(tmp) / str(i)), f"s3://{BUCKET}/{key}",
                "--content-type", kind, "--cache-control", CACHE)
            print(f"mirror index: {key}", flush=True)
    keep = {key for key, _, _ in made}
    for key, _, _ in objs:
        if key.rsplit("/", 1)[-1] in INDEXES and not key.startswith(HIDDEN) and key not in keep:
            aws(cred, "s3", "rm", "--only-show-errors", f"s3://{BUCKET}/{key}")
            print(f"mirror index: deleted {key}", flush=True)


def cmd_index(args):
    out = None
    if args:
        if args[0] != "--out" or len(args) != 2:
            fail("usage: mirror.py index [--out DIR]")
        out = args[1]
    write_indexes(creds(), out)
    return 0


def get(url, dest=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=120) as r:
        if dest is None:
            return r.read()
        with open(dest, "wb") as fh:
            shutil.copyfileobj(r, fh, 1 << 20)


def page(url):
    """(status, content type, body) of url, an HTTP error included; a fresh query string each
    time, so no cached answer (a 404 from before the listings were up) comes back."""
    url += f"?v={time.time_ns()}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), ""


def check_listing(path, names):
    """The listing of /path/ (path "" or ending in "/"): 200 text/html linking every name."""
    status, kind, body = page(f"{PUBLIC}/{path}")
    if status != 200 or not kind.startswith("text/html"):
        direct, _, _ = page(f"{PUBLIC}/{path}index.html")
        why = (" (index.html itself answers 200: the URL Rewrite for '/' is not live)"
               if direct == 200 else "")
        print(f"mirror check: {PUBLIC}/{path}: FAILED, {status} {kind or '-'}{why}")
        return False
    links = set(re.findall(r'<a href="([^"]+)"', body))
    missing = [n for n in names if urllib.parse.quote(n) not in links]
    print(f"mirror check: {PUBLIC}/{path}: {'ok' if not missing else 'FAILED'}, listing"
          + (f" lacks {missing}" if missing else f" links {len(names)} entries"))
    return not missing


def check_index_txt(path, files):
    status, _, body = page(f"{PUBLIC}/{path}index.txt")
    names = {m.group(1) for m in map(NAME.match, body.splitlines()) if m}
    ok = status == 200 and names == set(files)
    print(f"mirror check: {PUBLIC}/{path}index.txt: {'ok' if ok else 'FAILED'}, "
          + (f"the installer's sed reads {len(names)} names, those of the tree" if ok else
             f"{status}, read {sorted(names)} for {sorted(files)}"))
    return ok


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
        live = f"{PREFIX}/{osrev}/{arch}/"
        kept_dir = f"{archive(doc['release']['version'], arch)}/"
        for d in (live, kept_dir):
            bad += not check_listing(d, files)
            bad += not check_index_txt(d, files)
        for d in sorted({p for d in (live, kept_dir) for p in parents(d)}):   # / links down to the sets
            child = [c for c in (live, kept_dir) if c.startswith(d) and c != d]
            bad += not check_listing(d, sorted({c[len(d):].split("/")[0] + "/" for c in child}))
    line = get(FTPLIST).decode(errors="replace").strip()
    print(f"mirror check: {FTPLIST}: {line!r}")
    if line != MIRROR:
        bad += 1
        print(f"mirror check: the mirror list is not {MIRROR!r}")
    return 1 if bad else 0


def parents(d):
    """"", "pub/", ... up to d's parent."""
    parts = d.rstrip("/").split("/")
    return ["".join(p + "/" for p in parts[:i]) for i in range(len(parts))]


def main():
    cmds = {"plan": cmd_plan, "publish": cmd_publish, "index": cmd_index, "check": cmd_check}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__, file=sys.stderr)
        return 2
    return cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
