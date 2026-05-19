#!/usr/bin/env bash
# gsipper — one-shot install + run script for Debian / Ubuntu / derivatives.
#
# Pure Python (PyGObject + GTK4 + libadwaita) with PJSUA2 for SIP.
# python3-pjsua2 is NOT packaged on Debian/Ubuntu, so we compile
# pjproject + its SWIG bindings from source on first run, cached
# under ~/.cache/gsipper. The .deb path will ship a prebuilt
# _pjsua2.so instead.
#
# Usage:
#   ./build.sh           # install deps + build pjsua2 if missing, then run
#   ./build.sh --deps    # install runtime + build deps, don't launch
#   ./build.sh --pjsua2  # only (re)build pjsua2
#   ./build.sh --run     # only launch, skip dep checks
#   ./build.sh --deb     # build dist/gsipper_<version>_<arch>.deb

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
cd "$SCRIPT_DIR"

PYTHON=${PYTHON:-python3}

APT_PACKAGES=(
    python3
    python3-pip
    python3-gi
    python3-gi-cairo
    gir1.2-gtk-4.0
    gir1.2-adw-1
    gir1.2-gsound-1.0                      # ringtone playback
    gir1.2-ayatanaappindicator3-0.1        # tray status icon (optional)
    sound-theme-freedesktop                # provides phone-incoming-call
    ffmpeg                                 # WAV -> MP3 for call records
    gir1.2-gstreamer-1.0                   # playback of recorded calls
    gir1.2-gst-plugins-base-1.0            # playbin element
    gstreamer1.0-plugins-good              # MP3 decoder, etc.
)

# Build deps for compiling pjproject + Python bindings from source
# (used by build_pjsip — Debian/Ubuntu do not package python3-pjsua2)
PJ_BUILD_DEPS=(
    build-essential
    pkg-config
    swig
    git
    python3-dev
    python3-setuptools
    libasound2-dev
    libpulse-dev
    libssl-dev
    libopus-dev
    libsrtp2-dev
    uuid-dev
)

PJ_VERSION="2.15"   # 2.14.x SWIG bindings do not compile on GCC 13+
PJ_CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/gsipper/pjproject-${PJ_VERSION}"

install_deps() {
    if ! command -v apt-get >/dev/null 2>&1; then
        echo "warning: apt-get not found; install manually:" >&2
        echo "  ${APT_PACKAGES[*]}" >&2
        return
    fi
    apt_install "${APT_PACKAGES[@]}"
    build_pjsip
}

apt_install() {
    # Install a list of apt packages, only the ones not already present.
    local pkgs=("$@")
    local missing=()
    local pkg
    for pkg in "${pkgs[@]}"; do
        if ! dpkg -s "$pkg" >/dev/null 2>&1; then
            missing+=("$pkg")
        fi
    done
    if [ ${#missing[@]} -eq 0 ]; then
        return
    fi
    echo "Installing: ${missing[*]}"
    # apt-get update is allowed to fail (broken third-party repos
    # shouldn't block us); apt-get install will still succeed if the
    # cached lists already cover the package.
    local sudo_cmd=""
    if [ "$(id -u)" -ne 0 ]; then
        sudo_cmd="sudo"
    fi
    $sudo_cmd apt-get update || \
        echo "warning: apt-get update reported errors; continuing with cached lists" >&2
    $sudo_cmd apt-get install -y "${missing[@]}"
}

build_pjsip() {
    # Compile pjproject and its Python (SWIG) bindings.
    # Bindings install into the user site-packages, so no sudo needed
    # for the final step. The static pjproject .a archives stay in the
    # cache; we only re-run if pjsua2 is not importable.
    if "$PYTHON" -c "import pjsua2" >/dev/null 2>&1; then
        return
    fi

    if ! command -v apt-get >/dev/null 2>&1; then
        echo "error: pjsua2 not installed and apt-get unavailable;" >&2
        echo "       install pjproject + Python SWIG bindings manually." >&2
        return 1
    fi

    echo
    echo "==> Building pjproject ${PJ_VERSION} + pjsua2 Python bindings"
    echo "    (one-time, ~5 min, ~150 MB under $PJ_CACHE_DIR)"
    echo

    apt_install "${PJ_BUILD_DEPS[@]}"

    mkdir -p "$(dirname "$PJ_CACHE_DIR")"
    if [ ! -d "$PJ_CACHE_DIR" ]; then
        git clone --depth=1 --branch "${PJ_VERSION}" \
            https://github.com/pjsip/pjproject.git "$PJ_CACHE_DIR"
    fi

    cd "$PJ_CACHE_DIR"

    if [ ! -f .gsipper-built ]; then
        # Static build keeps the resulting _pjsua2.so self-contained,
        # so we don't have to install pjproject system libs.
        CFLAGS="-fPIC -O2 -DPJ_AUTOCONF=1" ./configure \
            --disable-video --disable-libwebrtc --disable-ffmpeg
        make dep
        make
        touch .gsipper-built
    fi

    cd pjsip-apps/src/swig/python
    # SWIG-generated pjsua2_wrap.cpp uses pre-C++17 iterator idioms
    # that GCC 13+ (Ubuntu 24.04 and newer) refuses by default.
    # -fpermissive demotes those errors to warnings.
    CFLAGS="-fPIC -fpermissive ${CFLAGS:-}" \
    CXXFLAGS="-fPIC -fpermissive ${CXXFLAGS:-}" \
    make
    "$PYTHON" setup.py install --user

    cd "$SCRIPT_DIR"

    if ! "$PYTHON" -c "import pjsua2" >/dev/null 2>&1; then
        echo "error: pjsua2 build appeared to succeed but module is still" >&2
        echo "       not importable. Check $HOME/.local/lib/python*/site-packages/" >&2
        return 1
    fi

    echo
    echo "==> pjsua2 installed to user site-packages."
}

install_extension() {
    # Copy the GNOME Shell extension into ~/.local/share/gnome-shell/extensions
    # if it isn't already there. Idempotent and never overwrites a user-edited
    # copy. The extension only adds the top-bar dot — gsipper itself works
    # without it.
    local uuid="gsipper@pulpoff.com"
    local src="$SCRIPT_DIR/extension"
    local dst="$HOME/.local/share/gnome-shell/extensions/$uuid"

    [ -d "$src" ] || return 0
    if [ -d "$dst" ] || [ -L "$dst" ]; then
        return 0
    fi
    mkdir -p "$(dirname "$dst")"
    cp -r "$src" "$dst"
    echo
    echo "GNOME extension installed at $dst"
    echo "To enable:"
    echo "  gnome-extensions enable $uuid"
    echo "(GNOME 45-48; you may need to restart the Shell — Alt+F2, r, Enter on X11,"
    echo " or log out/in on Wayland.)"
}

install_icon() {
    local icon_src="$SCRIPT_DIR/gsipper/resources/gsipper.svg"
    local desktop_src="$SCRIPT_DIR/gsipper/resources/com.pulpoff.gsipper.desktop"
    local icon_dir="$HOME/.local/share/icons/hicolor/scalable/apps"
    local desktop_dir="$HOME/.local/share/applications"

    if [ -f "$icon_src" ] && [ ! -f "$icon_dir/gsipper.svg" ]; then
        mkdir -p "$icon_dir" "$desktop_dir"
        cp "$icon_src" "$icon_dir/gsipper.svg"
        cp "$desktop_src" "$desktop_dir/com.pulpoff.gsipper.desktop" 2>/dev/null || true
        gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
    fi
}

run_app() {
    PYTHONPATH="$SCRIPT_DIR" exec "$PYTHON" -m gsipper "$@"
}

case "${1:-}" in
    --deps)
        install_deps
        ;;
    --pjsua2)
        build_pjsip
        ;;
    --deb)
        exec "$SCRIPT_DIR/packaging/build-deb.sh"
        ;;
    --run)
        shift || true
        run_app "$@"
        ;;
    --help|-h)
        sed -n '2,16p' "$0"
        ;;
    *)
        install_deps
        install_icon
        install_extension
        run_app "$@"
        ;;
esac
