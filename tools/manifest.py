#!/usr/bin/env python3
"""The pins of MANIFEST.toml: show them, check they exist on their repositories, fetch them.

usage: manifest.py show                 the table of pins
       manifest.py check                every commit exists on its repository (blob-less fetch of one object), and
                                        a released one's tag names that commit and carries an artifact per arch (network)
       manifest.py fetch [<name>...]    clone each repository into src/<name> at its pin; a released one's artifacts
                                        into target/<name>/<tag>/<arch>, checked (network; the user's OK)
       manifest.py consistent           the OpenBSD pin here equals Kernel's and LandingZone's when their clones sit beside this one

A pin with `release` is taken from its GitHub release, never cloned or built: one artifact per
architecture of [release] arch, <name>-<tag>-<arch>.tar, whose sum is the pin's sha256.<arch>;
unpacked, its sets match the SHA256 it carries. Needs `gh` (logged in), as Kernel's toolchain
recipe does.

Stdlib only. Nothing here builds anything: OS assembles what the pinned repositories produce.
"""
import hashlib
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TARGET = ROOT / "target"
PINNED = {"Kernel": "reference/PINNED.md", "LandingZone": "reference/PINNED.md"}


def manifest():
    with open(ROOT / "MANIFEST.toml", "rb") as fh:
        return tomllib.load(fh)


def pins(doc):
    for name, sec in doc.items():
        if isinstance(sec, dict) and "commit" in sec:
            yield name, sec["repo"], sec["commit"]


def run(*cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


def has_commit(url, sha):
    with tempfile.TemporaryDirectory() as tmp:
        run("git", "init", "-q", tmp)
        return run("git", "-C", tmp, "fetch", "-q", "--depth=1", "--filter=blob:none", url, sha).returncode == 0


def gh_repo(url):
    """owner/name of a GitHub URL."""
    return "/".join(url.rstrip("/").removesuffix(".git").split("/")[-2:])


def artifact(name, tag, arch):
    return f"{name}-{tag}-{arch}.tar"


def released(doc):
    for name, sec in doc.items():
        if isinstance(sec, dict) and "release" in sec:
            yield name, sec


def cmd_show(_):
    doc = manifest()
    print(f"release {doc['release']['version']}  arch {','.join(doc['release']['arch'])}")
    for name, repo, commit in pins(doc):
        tag = doc[name].get("release")
        print(f"{name:10} {commit[:12]}  {repo}" + (f"  release {tag}" if tag else ""))
    return 0


def cmd_check(_):
    doc = manifest()
    bad = 0
    for name, repo, commit in pins(doc):
        ok = has_commit(repo, commit)
        print(f"{name:10} {commit[:12]}  {'ok' if ok else 'MISSING on ' + repo}")
        bad += 0 if ok else 1
    for name, sec in released(doc):
        tag = sec["release"]
        # An annotated tag's commit is its peeled ref (^{}).
        refs = run("git", "ls-remote", sec["repo"], f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}").stdout.split()
        named = refs[-2] if refs else None
        ok = named == sec["commit"]
        print(f"{name:10} {tag:12}  {'tag names the pin' if ok else 'tag MISSING or names ' + str(named)[:12]}")
        bad += 0 if ok else 1
        r = run("gh", "release", "view", tag, "--repo", gh_repo(sec["repo"]), "--json", "assets", "-q", ".assets[].name")
        assets = set(r.stdout.split())
        for arch in doc["release"]["arch"]:
            want = artifact(name, tag, arch)
            ok = want in assets and arch in sec.get("sha256", {})
            print(f"{name:10} {arch:12}  {'ok' if ok else ('no sha256.' + arch + ' pinned' if want in assets else want + ' MISSING on the release')}")
            bad += 0 if ok else 1
    return 1 if bad else 0


def fetch_release(doc, name, sec):
    """Each architecture's artifact of a released pin, checked and unpacked."""
    tag = sec["release"]
    for arch in doc["release"]["arch"]:
        want = sec.get("sha256", {}).get(arch)
        if not want:
            print(f"fetch: {name} {tag} has no sha256.{arch} pinned: no artifact for {arch}", file=sys.stderr)
            return 1
        dest = TARGET / name / tag
        dest.mkdir(parents=True, exist_ok=True)
        tarball = dest / artifact(name, tag, arch)
        if not tarball.exists() or hashlib.sha256(tarball.read_bytes()).hexdigest() != want:
            tarball.unlink(missing_ok=True)
            r = run("gh", "release", "download", tag, "--repo", gh_repo(sec["repo"]), "--pattern", tarball.name,
                    "--dir", str(dest))
            if r.returncode:
                print(r.stderr, file=sys.stderr)
                return 1
        got = hashlib.sha256(tarball.read_bytes()).hexdigest()
        if got != want:
            print(f"fetch: {tarball.name} is {got}, the pin says {want}", file=sys.stderr)
            return 1
        top = tarball.name.removesuffix(".tar")
        with tarfile.open(tarball) as tar:
            tar.extractall(dest, filter="data")
        unpacked = dest / top
        sums = (unpacked / "SHA256").read_text().splitlines()
        for line in sums:   # SHA256 (base80.tgz) = <hex>
            file, digest = line.split("(", 1)[1].split(")", 1)[0], line.rsplit("= ", 1)[1]
            if hashlib.sha256((unpacked / file).read_bytes()).hexdigest() != digest:
                print(f"fetch: {top}/{file} does not match its SHA256", file=sys.stderr)
                return 1
        print(f"{name:10} {tag:12}  {arch}: target/{name}/{tag}/{top} ({len(sums)} sets, sums ok)")
    return 0


def cmd_fetch(args):
    want = set(args)
    doc = manifest()
    for name, repo, commit in pins(doc):
        if name == "openbsd" or (want and name not in want):
            continue
        if "release" in doc[name]:
            if fetch_release(doc, name, doc[name]):
                return 1
            continue
        dest = SRC / name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            r = run("git", "clone", "-q", "--filter=blob:none", repo, str(dest))
            if r.returncode:
                print(r.stderr, file=sys.stderr)
                return 1
        r = run("git", "-C", str(dest), "fetch", "-q", "origin", commit)
        r = run("git", "-C", str(dest), "checkout", "-q", "--detach", commit)
        if r.returncode:
            print(r.stderr, file=sys.stderr)
            return 1
        print(f"{name:10} {commit[:12]}  src/{name}")
    return 0


def cmd_consistent(_):
    ours = manifest()["openbsd"]["commit"]
    bad = 0
    for repo, rel in PINNED.items():
        f = ROOT.parent / repo / rel
        if not f.exists():
            print(f"{repo:12} no clone beside this one; skipped")
            continue
        theirs = next((l.split(":", 1)[1].strip() for l in f.read_text().splitlines() if l.startswith("Commit:")), None)
        ok = theirs == ours
        print(f"{repo:12} {str(theirs)[:12]}  {'ok' if ok else 'DIFFERS from manifest ' + ours[:12]}")
        bad += 0 if ok else 1
    return 1 if bad else 0


def main():
    cmds = {"show": cmd_show, "check": cmd_check, "fetch": cmd_fetch, "consistent": cmd_consistent}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__, file=sys.stderr)
        return 2
    return cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
