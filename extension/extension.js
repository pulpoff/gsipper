// gsipper status — GNOME Shell extension (ES module, GNOME 45+).
//
// Adds a small coloured dot to the top bar (green online, yellow
// connecting, red offline) that mirrors the running gsipper
// instance's SIP status via D-Bus. The icon is hidden while
// gsipper is not running. Clicking the dot invokes Show() on the
// D-Bus interface — no popup menu, the in-window status dot
// already exposes Connect / Disconnect / Reconnect / Exit.
//
// D-Bus contract (matches gsipper/dbus.py):
//   bus name : com.pulpoff.gsipper
//   path     : /com/pulpoff/gsipper/Status
//   interface: com.pulpoff.gsipper.Status
//   props    : s Status        ("online" | "connecting" | "offline")
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
        // The third arg to PanelMenu.Button._init is `dontCreateMenu`.
        // When true, no PopupMenu is attached to `this.menu`; the
        // parent's _onEvent guards with `if (this.menu)` and quietly
        // does nothing on click. Combined with our own click handler
        // below we get a clean single-click-runs-Show() behaviour
        // with zero menu surface.
        super._init(0.0, 'gsipper', true);

        // Coloured dot. media-record-symbolic is a filled circle in
        // every GNOME icon theme; using St.Icon (rather than a bare
        // St.Widget) lets the panel handle sizing + vertical
        // centring, and the standard `color:` CSS rules on
        // .gsipper-online / -connecting / -offline tint the
        // symbolic to the right state colour.
        this._icon = new St.Icon({
            icon_name: 'media-record-symbolic',
            style_class: 'system-status-icon gsipper-status-dot gsipper-offline',
        });
        this.add_child(this._icon);

        // Belt-and-suspenders: a few downstream/forked Shells ignore
        // dontCreateMenu. If `this.menu` somehow still exists, also
        // turn its open/toggle into Show() — never a popup.
        if (this.menu) {
            this.menu.open = () => this._invoke('Show');
            this.menu.toggle = () => this._invoke('Show');
        }

        // Direct click handler. button-press-event fires AFTER the
        // parent's 'event' signal, but since the parent does nothing
        // when this.menu is null, ours is the only thing responding
        // to a left-click on the panel icon. We call the standard
        // org.freedesktop.Application.Activate() (auto-exposed by
        // GApplication) rather than our own Show() so gnome-shell
        // injects an xdg-activation token into platform_data — the
        // app picks it up and present()s the window without
        // tripping the compositor's focus-stealing prevention,
        // which is what was producing the 'gsipper is ready'
        // notification instead of just raising the window.
        this.connect('button-press-event', (_actor, event) => {
            const button = event.get_button?.() ?? 1;
            if (button !== 1)
                return Clutter.EVENT_PROPAGATE;
            this._activateApp();
            return Clutter.EVENT_STOP;
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
        const missed = this._proxy.MissedCalls ?? 0;

        for (const c of ['gsipper-online', 'gsipper-connecting', 'gsipper-offline'])
            this._icon.remove_style_class_name(c);
        this._icon.add_style_class_name(`gsipper-${status}`);

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
        // Standard org.freedesktop.Application.Activate. Goes through
        // gnome-shell's own activation pipeline (which fills in the
        // xdg-activation token automatically), so the focused window
        // request is treated as user-initiated and the compositor
        // raises gsipper without the 'is ready' notification.
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
                    // Fallback: our own Show() — still better than
                    // nothing if the standard path errored out.
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
