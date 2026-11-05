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

# Clone the pinned repositories into src/<name> at their commits (network; the user's OK).
fetch *names:
    python3 -I tools/manifest.py fetch {{names}}

# R0: the arm64 image from the pinned Kernel, built where Kernel builds it (src/kernel/target).
image: 
    @test -d src/kernel || { echo "image: run just fetch first"; exit 1; }
    cd src/kernel && just image-arm64

# R0: the base system and its install sets from the pinned Userland, built where Userland
# builds them (src/userland/target/arm64/sets), at this manifest's SOURCE_DATE_EPOCH: two runs
# of one manifest commit print the same SHA256.
userland:
    @test -d src/userland || { echo "userland: run just fetch first"; exit 1; }
    cd src/userland && just host-make && just world arm64
    cat src/userland/target/arm64/sets/SHA256

# R0: boot the pinned system through Kernel's smokes, on the pinned userland.
smoke:
    @test -d src/kernel || { echo "smoke: run just fetch first"; exit 1; }
    cd src/kernel && just smoke-boot

# Cut a release: refuse until docs/RELEASE.md's checklist is scripted (R1).
release version:
    @echo "release {{version}}: not scripted yet; docs/RELEASE.md is the checklist" >&2; exit 1
