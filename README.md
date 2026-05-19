<p align="center">
  <img src="gsipper/resources/gsipper.svg" width="96" alt="gsipper icon">
</p>

<h1 align="center">gsipper</h1>

A modern GTK4 + libadwaita SIP client for the GNOME desktop, modelled on
the workflow of [MicroSIP](https://www.microsip.org/) but built from the
ground up with native GNOME widgets and PJSIP's PJSUA2 Python bindings.

<p align="center">
  <img src="gsipper.png" alt="gsipper screenshot" width="360">
</p>

## Features

**Calling**

- Outgoing calls from the dialer keypad, from a contact, or by clicking a
  row in the call history.
- Incoming-call popup window (Ringin) with answer / decline, plus a GNOME
  notification and a `sound-theme-freedesktop` ringtone.
- In-call status pane with peer, call state and live duration; one-tap
  hangup.
- E.164 dial-plan tweak: leading `+` is rewritten to `00` at dial time so
  Google-style contacts route through the trunk's international access
  prefix.

**Contacts**

- Per-contact list with search, multi-number entries (mobile / work /
  home / …) and per-number Call menu items.
- Edit and Delete from the row menu, with a confirmation dialog on
  Delete.
- Import from vCard (`.vcf`) or Google CSV.
- Click-to-call straight from a contact row.

**Call history**

- Outgoing / incoming / missed entries with a coloured direction icon
  and human-relative timestamps.
- Tap a row to drop the number back into the dialer for redial.

**Messaging (SIP IM)**

- Conversation list (newest first, unread badge on rows) and a chat view
  with incoming / outgoing bubbles, delivery status and a compose entry.
- New conversation dialog: pick a SIP URI / number and type the first
  message in one go.
- Per-contact name resolution on incoming messages — when the peer URI
  matches a stored contact, its display name is used.

**Account & registration**

- Adw.PreferencesDialog account editor (Basic + Advanced) with
  server / username / password, transport (UDP / TCP / TLS), STUN, codec
  priority list and a few NAT / keep-alive knobs.
- Status dot in the window header bar — green online, yellow connecting,
  red offline; tooltip carries the SIP reason text.

**Desktop integration**

- App-grid entry, `tel:` / `sip:` / `sips:` URI handler, hicolor icon.
- AyatanaAppIndicator tray icon (when available) mirroring the status
  dot.
- GNOME Shell status-bar extension (`gsipper@pulpoff.com`) for shells
  45-48, talking to the app over D-Bus.
- All PJSIP calls run on a dedicated worker thread so the GTK main loop
  never blocks on registration, INVITE/BYE, or `MESSAGE`.

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
