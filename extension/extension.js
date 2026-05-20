// gsipper status — GNOME Shell extension (ES module, GNOME 45+).
//
// Adds a phone icon to the top bar that reflects the running
// gsipper instance's SIP status via D-Bus. The icon is hidden
// while gsipper is not running. Clicking the icon invokes the
// Show() method on the D-Bus interface (no popup menu).
//
// D-Bus contract (matches gsipper/dbus.py):
//   bus name : com.pulpoff.gsipper
//   path     : /com/pulpoff/gsipper/Status
//   interface: com.pulpoff.gsipper.Status
//   props    : s Status        ("online" | "connecting" | "offline")
//              u MissedCalls
//   methods  : Show(), Quit()
//   signal   : IncomingCall(s peer)

import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
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

        // Replace PanelMenu.Button's built-in 'open the popup menu'
        // behaviour with a direct D-Bus Show() call. We override the
        // PopupMenu instance methods rather than relying on
        // vfunc_event alone — PanelMenu.Button connects its own
        // 'event' handler in super._init, and signal-handler ordering
        // across GObject reflection isn't guaranteed enough to trust
        // vfunc_event will always intercept first. Overriding open()
        // and toggle() turns the menu into a no-op trigger that runs
        // our Show() handler instead of popping up.
        this.menu.open = () => this._invoke('Show');
        this.menu.toggle = () => this._invoke('Show');

        // No popup menu — clicking the icon should directly bring
        // the main window forward. The Status / MissedCalls tooltip
        // and the IncomingCall notification remain.
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

    // Intercept the panel-button click before PanelMenu.Button's
    // default open-the-menu handler runs. Single tap / left click
    // calls Show(); right-click is left to GNOME's standard
    // panel-button behaviour (currently a no-op since menu is empty).
    vfunc_event(event) {
        if (event.type() === Clutter.EventType.BUTTON_PRESS ||
            event.type() === Clutter.EventType.TOUCH_BEGIN) {
            const button = event.get_button?.() ?? 1;
            if (button === 1) {
                this._invoke('Show');
                return Clutter.EVENT_STOP;
            }
        }
        return Clutter.EVENT_PROPAGATE;
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

        // Tooltip = status + optional missed-call count. Lives on
        // the icon (St.Icon supports the standard `accessible-name`
        // / hover tooltip mechanism via Clutter actor properties).
        let label = status.charAt(0).toUpperCase() + status.slice(1);
        if (missed > 0)
            label += `  ·  ${missed} missed`;
        this._icon.set_accessible_name(`gsipper — ${label}`);
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
