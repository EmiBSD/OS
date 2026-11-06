# EmiBSD OS: the recipes that assemble a system from the pinned repositories (MANIFEST.toml).
# This repository builds nothing of its own; it fetches, delegates, assembles, boots and tags.

set shell := ["zsh", "-cu"]

export PATH := "/opt/homebrew/opt/rustup/bin:" + env("PATH")
export SOURCE_DATE_EPOCH := `git log -1 --format=%ct 2>/dev/null || echo 0`

default:
    @just --list

# The pins and the release version.
show:
    python3 -I tools/manifest.py show

# Every pinned commit exists on its repository (network, no clone kept).
check:
    python3 -I tools/manifest.py check
    python3 -I tools/manifest.py consistent
    python3 -I -m py_compile tools/manifest.py

# A released pin's artifacts (Kernel's kernels, Userland's sets) go to target/<name>/<tag>, by tag
# and sum; one with `tools = true` (Kernel) is also checked out in src/<name>.
# Clone the pinned repositories into src/<name>, fetch the released ones (network; the user's OK).
fetch *names:
    python3 -I tools/manifest.py fetch {{names}}

# bsd.mp from Kernel's artifact (target/kernel/<tag>), through Kernel's smoke harness in
# src/kernel (`tools = true`), with the checks Kernel's dist-arm64 ran before packaging it. The
# init stand-in is the harness's fixture, built there; it never ships. Logs in target/smoke.
# R0: the released kernel boots, on every arch of [release].
smoke:
    #!/bin/zsh
    set -eu
    tag=$(python3 -I -c 'import tomllib; print(tomllib.load(open("MANIFEST.toml", "rb"))["kernel"]["release"])')
    archs=($(python3 -I -c 'import tomllib; print(*tomllib.load(open("MANIFEST.toml", "rb"))["release"]["arch"])'))
    test -d src/kernel || { echo "smoke: no src/kernel; run just fetch kernel first" >&2; exit 1; }
    for arch in $archs; do
        kernel=$PWD/target/kernel/$tag/kernel-$tag-$arch/bsd.mp
        test -f $kernel || { echo "smoke: no $kernel; run just fetch kernel first" >&2; exit 1; }
        (cd src/kernel && just build-init-$arch && \
         EMIBSD_RUN_DIR=$OLDPWD/target/smoke/$arch cargo xtask smoke --reject 'uptime went backwards' --smp 2 \
            --arch $arch --kernel $kernel --ramdisk none --expect-ramdisk \
            --expect "bsd: booted on $arch" --expect "EmiBSD 8.0 (GENERIC) #" --expect "selftest: buffer cache ok" \
            --expect "init: hello from user mode" --expect "init: EmiBSD 8.0" --expect "init: processes ok" \
            --expect "init exited with status 0 (signal 0)")
    done

# Cut a release: refuse until docs/RELEASE.md's checklist is scripted (R1).
release version:
    @echo "release {{version}}: not scripted yet; docs/RELEASE.md is the checklist" >&2; exit 1
