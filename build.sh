#!/usr/bin/env bash
# gsipper — one-shot install + run script for Debian / Ubuntu / derivatives.
#
# Pure Python (PyGObject + GTK4 + libadwaita) with PJSUA2 for SIP, so
# "building" means installing the runtime dependencies.
#
# Usage:
#   ./build.sh          # install deps if missing, then run the app
#   ./build.sh --deps   # only install deps, don't launch
#   ./build.sh --run    # only launch, skip the dep check

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
    python3-pjsua2
)

install_deps() {
    if ! command -v apt-get >/dev/null 2>&1; then
        echo "warning: apt-get not found; install manually:" >&2
        echo "  ${APT_PACKAGES[*]}" >&2
        return
    fi

    local missing=()
    for pkg in "${APT_PACKAGES[@]}"; do
        if ! dpkg -s "$pkg" >/dev/null 2>&1; then
            missing+=("$pkg")
        fi
    done

    if [ ${#missing[@]} -gt 0 ]; then
        echo "Installing missing system packages: ${missing[*]}"
        if [ "$(id -u)" -eq 0 ]; then
            apt-get update
            apt-get install -y "${missing[@]}"
        else
            sudo apt-get update
            sudo apt-get install -y "${missing[@]}"
        fi
    fi
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
    --run)
        shift || true
        run_app "$@"
        ;;
    --help|-h)
        sed -n '2,11p' "$0"
        ;;
    *)
        install_deps
        install_icon
        run_app "$@"
        ;;
esac
