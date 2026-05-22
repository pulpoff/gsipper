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
        // Pass dontCreateMenu=true so PanelMenu.Button doesn't
        // attach a popup to `this.menu` (it would otherwise open
        // on ANY click, including the left-click that we want to
        // route through Activate). We do want a popup, but only
        // on right-click — build our own below so we keep total
        // control over which button opens it.
        super._init(0.0, 'gsipper', true);

        // Coloured dot — color via the standard `color:` CSS rule
        // on .gsipper-online / -connecting / -offline.
        this._icon = new St.Icon({
            icon_name: 'media-record-symbolic',
            style_class: 'system-status-icon gsipper-status-dot gsipper-offline',
        });
        this.add_child(this._icon);

        // Build the right-click popup. PopupMenu attached to `this`
        // anchors itself under the panel button automatically.
        const menu = new PopupMenu.PopupMenu(this, 0.5, St.Side.TOP);
        Main.uiGroup.add_child(menu.actor);
        menu.actor.hide();
        this.setMenu(menu);

        this._connectItem = new PopupMenu.PopupMenuItem('Connect');
        this._connectItem.connect('activate', () => this._invoke('Connect'));
        menu.addMenuItem(this._connectItem);

        this._disconnectItem = new PopupMenu.PopupMenuItem('Disconnect');
        this._disconnectItem.connect('activate', () => this._invoke('Disconnect'));
        menu.addMenuItem(this._disconnectItem);

        this._reconnectItem = new PopupMenu.PopupMenuItem('Reconnect');
        this._reconnectItem.connect('activate', () => this._invoke('Reconnect'));
        menu.addMenuItem(this._reconnectItem);

        this._exitSep = new PopupMenu.PopupSeparatorMenuItem();
        menu.addMenuItem(this._exitSep);

        this._exitItem = new PopupMenu.PopupMenuItem('Exit');
        this._exitItem.connect('activate', () => this._invoke('Quit'));
        menu.addMenuItem(this._exitItem);

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

    // PanelMenu.Button connects its menu-toggle to the generic
    // 'event' signal, which fires for both left and right clicks.
    // Override vfunc_event so we get a single decision point: left
    // = Activate the app, right = toggle the popup, anything else
    // propagates. We deliberately do NOT call super.vfunc_event,
    // since the parent's default would also toggle the menu on
    // every click and that's exactly the behaviour we're replacing.
    vfunc_event(event) {
        const t = event.type();
        const isPress = (
            t === Clutter.EventType.BUTTON_PRESS ||
            t === Clutter.EventType.TOUCH_BEGIN
        );
        if (!isPress)
            return Clutter.EVENT_PROPAGATE;
        const button = event.get_button?.() ?? 1;
        if (button === 1) {
            this._activateApp();
            return Clutter.EVENT_STOP;
        }
        if (button === 3 && this.menu) {
            this.menu.toggle();
            return Clutter.EVENT_STOP;
        }
        return Clutter.EVENT_PROPAGATE;
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
        // Standard org.freedesktop.Application.Activate goes through
        // gnome-shell's activation pipeline, which fills in the
        // xdg-activation token automatically — the focused window
        // request is treated as user-initiated and the compositor
        // raises gsipper without the 'gsipper is ready' fallback
        // notification.
        Gio.DBus.session.call(
            BUS_NAME,
            '/com/pulpoff/gsipper',
            'org.freedesktop.Application',
            'Activate',
            new GLib.Variant('(a{sv})', [{}]),
            null,
            Gio.DBusCallFlags.NONE,
            -1,
            null,
            (conn, res) => {
                try {
                    conn.call_finish(res);
                } catch (e) {
                    console.warn(`gsipper: Activate failed: ${e.message}`);
                    this._invoke('Show');
                }
            },
        );
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
