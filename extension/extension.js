// gsipper status — GNOME Shell extension (ES module, GNOME 45+).
//
// Adds a small coloured dot to the top bar (green online, yellow
// connecting, red offline) that mirrors the running gsipper
// instance's SIP status via D-Bus. The icon is hidden while
// gsipper is not running.
//
//   left click  → open the main app (xdg-activation via the
//                 standard org.freedesktop.Application.Activate,
//                 so no 'gsipper is ready' notification)
//   right click → popup menu matching the in-app status dot:
//                   normal mode:  Connect or Disconnect /
//                                 Reconnect / Exit
//                   kiosk mode:   Reconnect only
//
// D-Bus contract (matches gsipper/dbus.py):
//   bus name : com.pulpoff.gsipper
//   path     : /com/pulpoff/gsipper/Status
//   interface: com.pulpoff.gsipper.Status
//   props    : s Status        ("online" | "connecting" | "offline")
//              b FavoritesOnly
//              u MissedCalls
//   methods  : Show(), Quit(), Connect(), Disconnect(), Reconnect()
//   signal   : IncomingCall(s peer)

import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import Gio from 'gi://Gio';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'com.pulpoff.gsipper';
const OBJECT_PATH = '/com/pulpoff/gsipper/Status';
const DESKTOP_ID = 'com.pulpoff.gsipper.desktop';

const STATUS_IFACE = `
<node>
  <interface name="com.pulpoff.gsipper.Status">
    <property name="Status" type="s" access="read"/>
    <property name="FavoritesOnly" type="b" access="read"/>
    <property name="MissedCalls" type="u" access="read"/>
    <method name="Show"/>
    <method name="Quit"/>
    <method name="Connect"/>
    <method name="Disconnect"/>
    <method name="Reconnect"/>
    <signal name="IncomingCall">
      <arg type="s" name="peer"/>
    </signal>
  </interface>
</node>`;

const GsipperProxy = Gio.DBusProxy.makeProxyWrapper(STATUS_IFACE);

const GsipperIndicator = GObject.registerClass(
class GsipperIndicator extends PanelMenu.Button {
    _init() {
        // dontCreateMenu=true. We DO want a popup, but only on
        // right-click, and we manage it manually below — if we
        // attached it via setMenu() then PanelMenu.Button's
        // 'event'-signal handler would toggle it on EVERY click
        // (left included). Keeping `this.menu` undefined keeps the
        // parent's `if (this.menu)` guard happy and out of our way.
        super._init(0.0, 'gsipper', true);

        // Coloured dot — color via the standard `color:` CSS rule
        // on .gsipper-online / -connecting / -offline.
        this._icon = new St.Icon({
            icon_name: 'media-record-symbolic',
            style_class: 'system-status-icon gsipper-status-dot gsipper-offline',
        });
        this.add_child(this._icon);

        // Build the right-click popup manually so we control which
        // button opens it. Stored on `this._statusMenu` (NOT
        // `this.menu`) so PanelMenu.Button's auto-toggle stays
        // disabled.
        this._statusMenu = new PopupMenu.PopupMenu(this, 0.5, St.Side.TOP);
        Main.uiGroup.add_child(this._statusMenu.actor);
        this._statusMenu.actor.hide();
        this._menuManager = new PopupMenu.PopupMenuManager(this);
        this._menuManager.addMenu(this._statusMenu);

        this._connectItem = new PopupMenu.PopupMenuItem('Connect');
        this._connectItem.connect('activate', () => this._invoke('Connect'));
        this._statusMenu.addMenuItem(this._connectItem);

        this._disconnectItem = new PopupMenu.PopupMenuItem('Disconnect');
        this._disconnectItem.connect('activate', () => this._invoke('Disconnect'));
        this._statusMenu.addMenuItem(this._disconnectItem);

        this._reconnectItem = new PopupMenu.PopupMenuItem('Reconnect');
        this._reconnectItem.connect('activate', () => this._invoke('Reconnect'));
        this._statusMenu.addMenuItem(this._reconnectItem);

        this._exitSep = new PopupMenu.PopupSeparatorMenuItem();
        this._statusMenu.addMenuItem(this._exitSep);

        this._exitItem = new PopupMenu.PopupMenuItem('Exit');
        this._exitItem.connect('activate', () => this._invoke('Quit'));
        this._statusMenu.addMenuItem(this._exitItem);

        // Click handling. Stays on the 'button-press-event' signal
        // (NOT vfunc_event) — vfunc_event runs before gnome-shell
        // has finished processing the input, so a D-Bus Activate
        // fired from there doesn't get an xdg-activation token
        // attached and we're back to the 'gsipper is ready'
        // notification. button-press-event runs late enough that
        // Shell.App.activate() finds the user-input context it
        // needs.
        this.connect('button-press-event', (_actor, event) => {
            const button = event.get_button?.() ?? 1;
            if (button === 1) {
                this._activateApp();
                return Clutter.EVENT_STOP;
            }
            if (button === 3) {
                this._statusMenu.toggle();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });

        this._proxy = null;
        this._propsChangedId = 0;

        // Hidden until gsipper actually owns the bus name.
        this.hide();

        this._watchId = Gio.bus_watch_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            () => this._connectBus(),
            () => this._disconnectBus(),
        );
    }

    // Override removed: vfunc_event was suppressing gnome-shell's
    // own event tracking for the click, which is what populates the
    // xdg-activation token on the subsequent Activate D-Bus call.
    // Letting the event reach the button-press-event signal handler
    // restores the focus-stealing-prevention bypass.

    _connectBus() {
        if (this._proxy)
            return;
        new GsipperProxy(
            Gio.DBus.session,
            BUS_NAME,
            OBJECT_PATH,
            (proxy, error) => {
                if (error) {
                    console.warn(`gsipper: proxy error: ${error.message}`);
                    return;
                }
                this._proxy = proxy;
                this._propsChangedId = proxy.connect(
                    'g-properties-changed',
                    () => this._refresh(),
                );
                this._refresh();
                this.show();
            },
        );
    }

    _disconnectBus() {
        if (this._proxy) {
            if (this._propsChangedId)
                this._proxy.disconnect(this._propsChangedId);
            this._proxy = null;
        }
        this._propsChangedId = 0;
        this.hide();
    }

    _refresh() {
        if (!this._proxy)
            return;
        const status = this._proxy.Status ?? 'offline';
        const favoritesOnly = this._proxy.FavoritesOnly ?? false;
        const missed = this._proxy.MissedCalls ?? 0;

        for (const c of ['gsipper-online', 'gsipper-connecting', 'gsipper-offline'])
            this._icon.remove_style_class_name(c);
        this._icon.add_style_class_name(`gsipper-${status}`);

        // Mirror the in-app status-dot menu state-machine:
        //   favorites_only -> Reconnect only
        //   online         -> Disconnect + Reconnect + Exit
        //   connecting|off -> Connect + Exit
        const isOnline = status === 'online';
        if (favoritesOnly) {
            this._connectItem.visible = false;
            this._disconnectItem.visible = false;
            this._reconnectItem.visible = true;
            this._exitSep.visible = false;
            this._exitItem.visible = false;
        } else {
            this._connectItem.visible = !isOnline;
            this._disconnectItem.visible = isOnline;
            this._reconnectItem.visible = isOnline;
            this._exitSep.visible = true;
            this._exitItem.visible = true;
        }

        let label = status.charAt(0).toUpperCase() + status.slice(1);
        if (missed > 0)
            label += `  ·  ${missed} missed`;
        this._icon.set_accessible_name(`gsipper — ${label}`);
    }

    _invoke(method) {
        if (!this._proxy)
            return;
        this._proxy[`${method}Remote`](() => {});
    }

    _activateApp() {
        // Go through Shell.App.activate() rather than a raw D-Bus
        // Activate. gnome-shell builds the xdg-activation token
        // from the just-processed user-input event (panel click)
        // and bakes it into the platform_data of the resulting
        // org.freedesktop.Application.Activate call — so the
        // compositor treats gsipper's present() as user-initiated
        // and raises the window directly. A raw Gio.DBus.session
        // .call('Activate', {}) from the extension misses this
        // step and trips the focus-stealing fallback that pops up
        // 'gsipper is ready'.
        const appSys = Shell.AppSystem.get_default();
        const app = appSys.lookup_app(DESKTOP_ID);
        if (app) {
            try {
                app.activate();
                return;
            } catch (e) {
                console.warn(`gsipper: Shell.App.activate failed: ${e.message}`);
            }
        } else {
            console.warn(`gsipper: ${DESKTOP_ID} not found in AppSystem`);
        }
        // Fallback to our own Show() — still better than nothing
        // if the .desktop lookup or activate path errored out.
        this._invoke('Show');
    }

    destroy() {
        if (this._watchId) {
            Gio.bus_unwatch_name(this._watchId);
            this._watchId = 0;
        }
        this._disconnectBus();
        super.destroy();
    }
});

export default class GsipperExtension extends Extension {
    enable() {
        this._indicator = new GsipperIndicator();
        Main.panel.addToStatusArea('gsipper', this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
