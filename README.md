<p align="center">
  <img src="gsipper/resources/gsipper.svg" width="96" alt="gsipper icon">
</p>

<h1 align="center">gsipper</h1>

A modern GTK4 + libadwaita SIP client for the GNOME desktop, modelled on
the workflow of [MicroSIP](https://www.microsip.org/) but built from the
ground up with native GNOME widgets and PJSIP's PJSUA2 Python bindings.

## Status

Step 1 of 9 — skeleton, packaging, and a launchable main window with the
MicroSIP-style tabs (Dialer, Contacts, Calls, Messages). No SIP stack
wired up yet.

## Run from source

```sh
./build.sh
```

On first run, `build.sh` will `apt install` any missing system packages
and then launch the app. There is no compilation step.

System packages (from apt):

```
python3 python3-pip python3-gi python3-gi-cairo
gir1.2-gtk-4.0 gir1.2-adw-1 python3-pjsua2
```

Or skip the install / launch steps individually:

```sh
./build.sh --deps     # install runtime dependencies, don't launch
./build.sh --run      # launch without re-checking dependencies
```

The app can also be launched directly once dependencies are installed:

```sh
python3 -m gsipper
```

## Roadmap

1. **Skeleton + packaging** ← *you are here*
2. SIP engine (PJSUA2) + Account dialog
3. Dialer + outgoing call + active-call view
4. Incoming call popup (Ringin) + ringtone
5. Call history
6. Contacts (CRUD, search, click-to-call)
7. Messages (SIP IM)
8. Settings (audio devices, codecs, STUN, recording, shortcuts, AA/DND/forwarding)
9. Polish (tray icon, notifications, .deb packaging)
