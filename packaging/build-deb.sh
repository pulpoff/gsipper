#!/usr/bin/env bash
# Build a Debian package for gsipper.
#
#   ./packaging/build-deb.sh            # build dist/gsipper_<version>_amd64.deb
#   ARCH=arm64 ./packaging/build-deb.sh # cross-target architecture string
#
# Output: dist/gsipper_<version>_<arch>.deb (lintian-clean for the
# fields we care about; PJSUA2 stays out-of-band, see Suggests).

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

VERSION="$(python3 - <<'PY'
ns = {}
exec(open("gsipper/__init__.py").read(), ns)
print(ns["__version__"])
PY
)"
ARCH="${ARCH:-$(dpkg --print-architecture 2>/dev/null || echo amd64)}"

PKG_NAME="gsipper_${VERSION}_${ARCH}"
DIST_DIR="$ROOT_DIR/dist"
STAGE="$DIST_DIR/$PKG_NAME"

echo ">>> building $PKG_NAME"
rm -rf "$STAGE"
mkdir -p "$STAGE"

# 1. Python module → /usr/lib/gsipper/gsipper
install -d "$STAGE/usr/lib/gsipper"
cp -r gsipper "$STAGE/usr/lib/gsipper/"
# Also stash a copy of build.sh so the launcher's --install-pjsua2
# can reuse the existing build_pjsip flow.
install -m 755 build.sh "$STAGE/usr/lib/gsipper/build.sh"
find "$STAGE/usr/lib/gsipper" -type d -name __pycache__ \
    -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/usr/lib/gsipper" -type f -name '*.pyc' -delete

# 2. /usr/bin/gsipper launcher
install -d "$STAGE/usr/bin"
cat > "$STAGE/usr/bin/gsipper" <<'LAUNCH'
#!/usr/bin/env python3
"""gsipper launcher (installed by the gsipper deb).

Adds /usr/lib/gsipper to sys.path and dispatches to gsipper.app.main,
unless invoked with --install-pjsua2, in which case it shells out to
the bundled build.sh to compile the PJSUA2 Python bindings.
"""

import os
import sys

_INSTALL_ROOT = "/usr/lib/gsipper"

if "--install-pjsua2" in sys.argv[1:]:
    helper = os.path.join(_INSTALL_ROOT, "build.sh")
    if not os.path.exists(helper):
        sys.stderr.write(
            f"gsipper: helper {helper} not found; reinstall the package.\n")
        sys.exit(1)
    os.execv(helper, [helper, "--pjsua2"])

sys.path.insert(0, _INSTALL_ROOT)
from gsipper.app import main  # noqa: E402

sys.exit(main())
LAUNCH
chmod 755 "$STAGE/usr/bin/gsipper"

# 3. Desktop file + icon (hicolor, scalable)
install -d "$STAGE/usr/share/applications"
install -m 644 gsipper/resources/com.pulpoff.gsipper.desktop \
    "$STAGE/usr/share/applications/com.pulpoff.gsipper.desktop"

install -d "$STAGE/usr/share/icons/hicolor/scalable/apps"
install -m 644 gsipper/resources/gsipper.svg \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/gsipper.svg"

# 4. GNOME-Shell extension, system-wide
EXT_UUID="gsipper@pulpoff.com"
install -d "$STAGE/usr/share/gnome-shell/extensions/$EXT_UUID"
install -m 644 extension/extension.js   "$STAGE/usr/share/gnome-shell/extensions/$EXT_UUID/"
install -m 644 extension/metadata.json  "$STAGE/usr/share/gnome-shell/extensions/$EXT_UUID/"
install -m 644 extension/stylesheet.css "$STAGE/usr/share/gnome-shell/extensions/$EXT_UUID/"

# 5. Docs
DOC_DIR="$STAGE/usr/share/doc/gsipper"
install -d "$DOC_DIR"
install -m 644 README.md "$DOC_DIR/README.md"
gzip -9n -f "$DOC_DIR/README.md"
install -m 644 packaging/debian/copyright "$DOC_DIR/copyright"
install -m 644 packaging/debian/changelog "$DOC_DIR/changelog.Debian"
gzip -9n -f "$DOC_DIR/changelog.Debian"

# 6. DEBIAN control directory
install -d -m 755 "$STAGE/DEBIAN"
sed -e "s|__VERSION__|$VERSION|g" -e "s|__ARCH__|$ARCH|g" \
    packaging/debian/control.in > "$STAGE/DEBIAN/control"
install -m 755 packaging/debian/postinst "$STAGE/DEBIAN/postinst"
install -m 755 packaging/debian/prerm    "$STAGE/DEBIAN/prerm"
install -m 755 packaging/debian/postrm   "$STAGE/DEBIAN/postrm"

# Installed-Size field (in 1 KiB units, excluding control)
size_kib="$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)"
printf 'Installed-Size: %s\n' "$size_kib" >> "$STAGE/DEBIAN/control"

# 7. Build the .deb
DEB_OUT="$DIST_DIR/$PKG_NAME.deb"
if command -v fakeroot >/dev/null 2>&1; then
    fakeroot dpkg-deb --build --root-owner-group "$STAGE" "$DEB_OUT"
else
    dpkg-deb --build --root-owner-group "$STAGE" "$DEB_OUT"
fi

rm -rf "$STAGE"

echo
echo ">>> built: $DEB_OUT"
echo
echo "Install with:  sudo apt install $DEB_OUT"
echo "Then run:      sudo gsipper --install-pjsua2  # if pjsua2 isn't available"
