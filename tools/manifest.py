#!/usr/bin/env python3
"""The pins of MANIFEST.toml: show them, check they exist on their repositories, fetch them.

usage: manifest.py show                 the table of pins
       manifest.py check                every commit exists on its repository (blob-less fetch of one object; network)
       manifest.py fetch [<name>...]    clone each repository into src/<name> at its pin (network; the user's OK)
       manifest.py consistent           the OpenBSD pin here equals Kernel's and LandingZone's when their clones sit beside this one

Stdlib only. Nothing here builds anything: OS assembles what the pinned repositories produce.
"""
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
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


def cmd_show(_):
    doc = manifest()
    print(f"release {doc['release']['version']}  arch {','.join(doc['release']['arch'])}")
    for name, repo, commit in pins(doc):
        print(f"{name:10} {commit[:12]}  {repo}")
    return 0


def cmd_check(_):
    bad = 0
    for name, repo, commit in pins(manifest()):
        ok = has_commit(repo, commit)
        print(f"{name:10} {commit[:12]}  {'ok' if ok else 'MISSING on ' + repo}")
        bad += 0 if ok else 1
    return 1 if bad else 0


def cmd_fetch(args):
    want = set(args)
    for name, repo, commit in pins(manifest()):
        if name == "openbsd" or (want and name not in want):
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
