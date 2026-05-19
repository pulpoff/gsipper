<p align="center">
  <img src="gsipper/resources/gsipper.svg" width="96" alt="gsipper icon">
</p>

<h1 align="center">gsipper</h1>

A modern GTK4 + libadwaita SIP client for the GNOME desktop, modelled on
the workflow of [MicroSIP](https://www.microsip.org/) but built from the
ground up with native GNOME widgets and PJSIP's PJSUA2 Python bindings.

## Run from source

```sh
./build.sh
```

On first run, `build.sh` will `apt install` any missing system packages
and then launch the app. PJSUA2 is **not** packaged on Debian/Ubuntu,
so the script will also compile pjproject and its SWIG bindings from
source into `~/.cache/gsipper` (one-time, ~5 min, ~150 MB).

Other `build.sh` modes:

```sh
./build.sh --deps     # install runtime + build dependencies, don't launch
./build.sh --pjsua2   # only (re)build pjsua2
./build.sh --run      # launch without re-checking dependencies
./build.sh --deb      # build dist/gsipper_<version>_<arch>.deb
```

Once dependencies are installed, the app can also be launched directly:

```sh
python3 -m gsipper
```

## Debian package

```sh
./build.sh --deb
sudo apt install ./dist/gsipper_1.0.1_amd64.deb
sudo gsipper --install-pjsua2     # compile PJSUA2 bindings (one-time)
```

The package installs:

| Path | What |
|------|------|
| `/usr/bin/gsipper` | Launcher (also handles `--install-pjsua2`) |
| `/usr/lib/gsipper/gsipper/` | Python module |
| `/usr/share/applications/com.pulpoff.gsipper.desktop` | App-grid entry + `sip:` / `tel:` URI handler |
| `/usr/share/icons/hicolor/scalable/apps/gsipper.svg` | App icon |
| `/usr/share/gnome-shell/extensions/gsipper@pulpoff.com/` | Status-bar extension |

After install, enable the status-bar extension once:

```sh
gnome-extensions enable gsipper@pulpoff.com
# X11: Alt+F2, type 'r', Enter   |   Wayland: log out and back in
```

## Roadmap

1. Skeleton + packaging
2. SIP engine (PJSUA2) + Account dialog
3. Dialer + outgoing call + active-call view
4. Incoming call popup (Ringin) + ringtone
5. Call history
6. Contacts (CRUD, search, click-to-call)
7. Messages (SIP IM)
8. Settings (audio devices, codecs, STUN, recording, shortcuts, AA/DND/forwarding)
9. Polish (tray icon, notifications, .deb packaging) ← *you are here*
