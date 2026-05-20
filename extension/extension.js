// gsipper status — GNOME Shell extension (ES module, GNOME 45+).
//
// Adds a small coloured dot to the top bar (green online, yellow
// connecting, red offline) that mirrors the running gsipper
// instance's SIP status via D-Bus. The icon is hidden while gsipper
// is not running. Left-click opens a popup menu that matches the
// in-app status-dot menu:
//
//     normal mode:    Show gsipper
//                     ───────────
//                     Disconnect  (when online)
//                     Connect     (when connecting / offline)
//                     Reconnect
//                     ───────────
//                     Exit
//
//     favorites_only: Show gsipper
//                     ───────────
//                     Reconnect
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
import St from 'gi://St';
import Gio from 'gi://Gio';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'com.pulpoff.gsipper';
const OBJECT_PATH = '/com/pulpoff/gsipper/Status';

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
        super._init(0.0, 'gsipper');

        // Coloured dot (not an icon). St.Widget with the dot styled
        // entirely from CSS — background colour comes from
        // .gsipper-online / -connecting / -offline; size + radius
        // come from .gsipper-status-dot. Wrapped in a Bin so the
        // panel's vertical centring works the same as for an icon.
        this._dot = new St.Widget({
            style_class: 'gsipper-status-dot gsipper-offline',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._dotBin = new St.Bin({
            child: this._dot,
            style_class: 'gsipper-status-icon',
            y_align: Clutter.ActorAlign.CENTER,
        });
        this.add_child(this._dotBin);

        // Menu items — built once, labels/visibility refreshed from
        // _refresh() whenever Status or FavoritesOnly changes.
        this._showItem = new PopupMenu.PopupMenuItem('Show gsipper');
        this._showItem.connect('activate', () => this._invoke('Show'));
        this.menu.addMenuItem(this._showItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._connectItem = new PopupMenu.PopupMenuItem('Connect');
        this._connectItem.connect('activate', () => this._invoke('Connect'));
        this.menu.addMenuItem(this._connectItem);

        this._disconnectItem = new PopupMenu.PopupMenuItem('Disconnect');
        this._disconnectItem.connect('activate', () => this._invoke('Disconnect'));
        this.menu.addMenuItem(this._disconnectItem);

        this._reconnectItem = new PopupMenu.PopupMenuItem('Reconnect');
        this._reconnectItem.connect('activate', () => this._invoke('Reconnect'));
        this.menu.addMenuItem(this._reconnectItem);

        this._exitSep = new PopupMenu.PopupSeparatorMenuItem();
        this.menu.addMenuItem(this._exitSep);

        this._exitItem = new PopupMenu.PopupMenuItem('Exit');
        this._exitItem.connect('activate', () => this._invoke('Quit'));
        this.menu.addMenuItem(this._exitItem);

        this._proxy = null;
        this._propsChangedId = 0;
        this._signalSubId = 0;

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
        this._signalSubId = 0;
        this.hide();
    }

    _refresh() {
        if (!this._proxy)
            return;
        const status = this._proxy.Status ?? 'offline';
        const favoritesOnly = this._proxy.FavoritesOnly ?? false;
        const missed = this._proxy.MissedCalls ?? 0;

        for (const c of ['gsipper-online', 'gsipper-connecting', 'gsipper-offline'])
            this._dot.remove_style_class_name(c);
        this._dot.add_style_class_name(`gsipper-${status}`);

        // Match the in-app dot menu state-machine:
        //   favorites_only -> Show + Reconnect only
        //   online         -> Show / Disconnect+Reconnect / Exit
        //   connecting|off -> Show / Connect / Exit
        const isOnline = status === 'online';
        // GNOME 40+ PopupMenuItems extend St.BoxLayout directly, so
        // .visible lives on the item itself (no .actor wrapper).
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
        this._dot.set_accessible_name(`gsipper — ${label}`);
        this._dotBin.set_accessible_name(`gsipper — ${label}`);
    }

    _invoke(method) {
        if (!this._proxy)
            return;
        this._proxy[`${method}Remote`](() => {});
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
