#!/usr/bin/env bash
# Cross-build gsipper for arm64 using Docker + qemu-user-static.
# The host runs as usual (your amd64 box); Docker transparently
# emulates the target ISA via binfmt_misc, so the regular
# ./build.sh inside the container produces a deb whose bundled
# _pjsua2.so is native to arm64.
#
# Usage:
#   ./packaging/build-cross.sh arm64
#   ./packaging/build-cross.sh amd64      # equivalent to plain --deb
#
# Output: dist/gsipper_<version>_<arch>.deb on the host filesystem.
#
# Prereqs (one-time on the host):
#   sudo apt install docker.io qemu-user-static binfmt-support
#   sudo systemctl enable --now docker
#   # Then re-register qemu binfmt handlers for arm64:
#   sudo docker run --rm --privileged multiarch/qemu-user-static \
#       --reset -p yes
# Docker Desktop on macOS / Windows ships qemu + binfmt out of the
# box, no extra steps.

set -euo pipefail

if [ "${1:-}" = "" ]; then
    sed -n '2,22p' "$0" >&2
    exit 1
fi

case "$1" in
    amd64)  PLATFORM="linux/amd64" ;;
    arm64)  PLATFORM="linux/arm64" ;;
    *)
        echo "error: unknown arch '$1' (use amd64 or arm64)" >&2
        exit 2
        ;;
esac
ARCH="$1"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

if ! command -v docker >/dev/null 2>&1; then
    echo "error: docker is required for cross builds." >&2
    echo "       sudo apt install docker.io qemu-user-static binfmt-support" >&2
    exit 3
fi

echo ">>> cross-building gsipper for $ARCH ($PLATFORM)"

# Use ubuntu:24.04 — same toolchain we test on. The build runs as
# root inside the container, but its output ends up in the host's
# dist/ via the bind mount.
docker run --rm \
    --platform="$PLATFORM" \
    -v "$ROOT_DIR:/src" -w /src \
    -e DEBIAN_FRONTEND=noninteractive \
    ubuntu:24.04 \
    bash -c '
        set -e
        apt-get update
        apt-get install -y --no-install-recommends \
            python3 python3-pip python3-gi python3-gi-cairo \
            gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gsound-1.0 \
            gir1.2-ayatanaappindicator3-0.1 sound-theme-freedesktop \
            ffmpeg gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
            gstreamer1.0-plugins-good build-essential pkg-config swig \
            git python3-dev python3-setuptools libasound2-dev \
            libpulse-dev libssl-dev libopus-dev libsrtp2-dev \
            uuid-dev fakeroot dpkg-dev sudo ca-certificates
        ./build.sh --pjsua2
        ARCH='"$ARCH"' ./build.sh --deb
    '

echo
echo ">>> done: $ROOT_DIR/dist/gsipper_*_$ARCH.deb"
ls -lh "$ROOT_DIR/dist/"*_"$ARCH.deb" 2>/dev/null || true
