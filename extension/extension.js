// gsipper status — GNOME Shell extension (ES module, GNOME 45+).
//
// Adds a phone icon to the top bar that reflects the running
// gsipper instance's SIP status via D-Bus. The icon is hidden
// while gsipper is not running.
//
// D-Bus contract (matches gsipper/dbus.py):
//   bus name : com.pulpoff.gsipper
//   path     : /com/pulpoff/gsipper/Status
//   interface: com.pulpoff.gsipper.Status
//   props    : s Status        ("online" | "connecting" | "offline")
//              u MissedCalls
//   methods  : Show(), Quit()
//   signal   : IncomingCall(s peer)

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
    <property name="MissedCalls" type="u" access="read"/>
    <method name="Show"/>
    <method name="Quit"/>
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

        this._icon = new St.Icon({
            icon_name: 'call-start-symbolic',
            style_class: 'system-status-icon gsipper-status-icon gsipper-offline',
        });
        this.add_child(this._icon);

        this._statusItem = new PopupMenu.PopupMenuItem('Connecting…', {reactive: false});
        this.menu.addMenuItem(this._statusItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._showItem = new PopupMenu.PopupMenuItem('Show gsipper');
        this._showItem.connect('activate', () => this._invoke('Show'));
        this.menu.addMenuItem(this._showItem);

        this._quitItem = new PopupMenu.PopupMenuItem('Quit gsipper');
        this._quitItem.connect('activate', () => this._invoke('Quit'));
        this.menu.addMenuItem(this._quitItem);

        this._proxy = null;
        this._propsChangedId = 0;
        this._signalSubId = 0;

        // Hidden until gsipper actually owns the bus name.
        this.hide();

        this._watchId = Gio.bus_watch_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            () => this._connect(),
            () => this._disconnect(),
        );
    }

    _connect() {
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
                this._signalSubId = proxy.connectSignal(
                    'IncomingCall',
                    (_p, _sender, [peer]) => this._notifyIncoming(peer),
                );
                this._refresh();
                this.show();
            },
        );
    }

    _disconnect() {
        if (this._proxy) {
            if (this._propsChangedId)
                this._proxy.disconnect(this._propsChangedId);
            if (this._signalSubId)
                this._proxy.disconnectSignal(this._signalSubId);
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
        const missed = this._proxy.MissedCalls ?? 0;

        for (const c of ['gsipper-online', 'gsipper-connecting', 'gsipper-offline'])
            this._icon.remove_style_class_name(c);
        this._icon.add_style_class_name(`gsipper-${status}`);

        let label = status.charAt(0).toUpperCase() + status.slice(1);
        if (missed > 0)
            label += `  ·  ${missed} missed`;
        this._statusItem.label.text = label;
    }

    _notifyIncoming(peer) {
        Main.notify('Incoming call', peer || 'Unknown caller');
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
        this._disconnect();
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
