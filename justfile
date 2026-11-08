# EmiBSD OS: the recipes that assemble a system from the pinned repositories (MANIFEST.toml).
# This repository builds nothing of its own; it fetches, delegates, assembles, boots and tags.

set shell := ["zsh", "-cu"]

export PATH := "/opt/homebrew/opt/rustup/bin:" + env("PATH")
export SOURCE_DATE_EPOCH := `git log -1 --format=%ct 2>/dev/null || echo 0`
# DevTools' LLVM for the host makefs (host-tools), named here rather than left to the Userland
# checkout's default, which may name a tree DevTools has since replaced.
host_llvm := env("HOME") + "/devel/emibsd/toolchains/llvm-23.1.4+emibsd.2-aarch64-apple-darwin-arm64/bin"

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
# src/kernel (`tools = true`), with the checks Kernel's dist-arm64 ran before packaging it: the
# user-mode lines up to init's last check, then init's death panic, which is how a kernel
# without `qemu` answers the stand-in init's exit (QEMU is stopped once it is seen). The init
# stand-in is the harness's fixture, built there; it never ships. Logs in target/smoke.
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
            --arch $arch --kernel $kernel --ramdisk none --expect-ramdisk --until-seen \
            --expect "bsd: booted on $arch" --expect "EmiBSD 8.0 (GENERIC) #" \
            --expect "init: hello from user mode" --expect "init: EmiBSD 8.0" --expect "init: processes ok" \
            --expect "init: unveil ok" --expect "panic: init died (signal 0, exit ")
    done

# The base system's makefs for this host, the one compiled tool the media need, built by
# Userland's own tools in src/userland (`tools = true`): the upstreams of its imported programs
# (`upstream.py fetch --build`: zstd's mirror and the Kernel release; network), its make, then
# the tree and the host tools, into src/userland/target/host/bin. DevTools' LLVM; nothing is
# built for EmiBSD.
# R1: the host tools of the media (makefs), from the pinned Userland.
host-tools:
    @test -d src/userland || { echo "host-tools: no src/userland; run just fetch userland first" >&2; exit 1; }
    cd src/userland && export EMIBSD_LLVM_BIN=${EMIBSD_LLVM_BIN:-{{host_llvm}}} && \
        python3 -I tools/upstream.py fetch --build && just host-make && just world arm64 tree host
    @src/userland/target/host/bin/makefs 2>&1 | head -1 || true

# tools/media.py (docs/MEDIA.md): the tree from the fetched artifacts, checked again; bsd.rd by
# rdsetroot; SHA256; the two FATs written here, the cd9660 and the FFS by the host makefs; the
# ISO, the USB stick image and their SHA256; with `[release] signify`, both SHA256 signed into
# SHA256.sig by the user's key outside the repository (docs/MEDIA.md, Signing). Every time
# written is SOURCE_DATE_EPOCH, the manifest commit's.
# R1: the install media of every arch of [release], into dist/<version>/.
media:
    python3 -I tools/media.py build

# tools/smokemedia.py (docs/MEDIA.md): each image installs in QEMU (virt, EDK2) onto a fresh
# disk, the installer answered over the console, and the installed disk boots alone to login:.
# Under the machine's QEMU lock, /tmp/emibsd/ci.lock. Logs in target/smoke-media/<arch>/<kind>.
# R1: the install media install and boot: `just smoke-media`, or `iso` or `img` alone.
smoke-media *kinds:
    python3 -I tools/smokemedia.py {{kinds}}

# Cut a release: refuse until docs/RELEASE.md's checklist is scripted (R1).
release version:
    @echo "release {{version}}: not scripted yet; docs/RELEASE.md is the checklist" >&2; exit 1
