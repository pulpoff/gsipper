"""Quick-dial favourites grid.

Shown as the main window content when General > Favorites only is
enabled. A 2-column grid of large buttons — one per favourited
contact — that places a call immediately on click. There's no
dial entry; this is meant as a kiosk-style quick-dial screen.

While a call is active the MainWindow swaps the main content stack
back to the tabs page so the regular DialerView in-call subwidget
takes over; FavoritesView is hidden then and reappears when the
call ends.
"""

from __future__ import annotations

import logging
from typing import List

import gi

gi.require_version("Gtk", "4.0")

_USE_ADW = False
try:
    gi.require_version("Adw", "1")
    from gi.repository import Adw  # noqa: E402
    _USE_ADW = True
except (ValueError, ImportError):
    pass

from gi.repository import GObject, Gtk  # noqa: E402

from ..storage import contacts as contacts_store
from ..storage.contacts import Contact


logger = logging.getLogger(__name__)

# Each card is at least this tall — without a minimum, FlowBox lays
# them out at the natural label height which looks lost on a typical
# softphone window. 200 px gives the avatar + name room to breathe
# and matches the 'half the window height' brief from the spec when
# the window is at the default 360x560.
_CARD_MIN_HEIGHT = 200
# Number of distinct tints in the favorites palette. Must match the
# number of '.fav-card-<n>' classes defined in resources/style.css.
_PALETTE_SIZE = 8


def _palette_class(contact: Contact) -> str:
    """Pick a CSS class name from the favorites palette. Stable across
    launches — uses a plain character-sum hash of the contact id (or
    name) so the same contact lands on the same tint every time. We
    avoid Python's hash() here because it is salt-randomised per
    process and would shuffle the colours on each app start."""
    key = contact.id or contact.name or ""
    bucket = sum(ord(c) for c in key) % _PALETTE_SIZE
    return f"fav-card-{bucket}"


class FavoritesView(Gtk.Box):
    __gsignals__ = {
        # Single payload: SIP URI or raw phone number from
        # contact.primary_target(). Wired in window.py to the same
        # dial path as Contacts row 'Call'.
        "call-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        # Adw.StatusPage when the user has Favorites-only mode on but
        # no contact is starred yet — guides them to the Contacts tab.
        if _USE_ADW:
            self._empty = Adw.StatusPage(
                icon_name="starred-symbolic",
                title="No favourites",
                description="Open the Contacts tab and use 'Add to favorites' "
                            "on a contact's row menu to populate this screen.",
            )
        else:
            self._empty = Gtk.Label(label="No favourites")
            self._empty.add_css_class("dim-label")

        self._flowbox = Gtk.FlowBox()
        self._flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flowbox.set_min_children_per_line(2)
        self._flowbox.set_max_children_per_line(2)
        self._flowbox.set_homogeneous(True)
        self._flowbox.set_row_spacing(8)
        self._flowbox.set_column_spacing(8)
        self._flowbox.set_margin_top(12)
        self._flowbox.set_margin_bottom(12)
        self._flowbox.set_margin_start(12)
        self._flowbox.set_margin_end(12)

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_vexpand(True)
        self._scrolled.set_child(self._flowbox)

        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)
        self._stack.add_named(self._empty, "empty")
        self._stack.add_named(self._scrolled, "grid")
        self.append(self._stack)

        self.refresh()

    def refresh(self) -> None:
        """Reload contacts from disk and rebuild the card grid. Called
        from MainWindow on view-show and whenever the user toggles a
        contact's favorite flag elsewhere."""
        child = self._flowbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._flowbox.remove(child)
            child = nxt

        favourites: List[Contact] = [
            c for c in contacts_store.load_contacts() if c.favorite
        ]
        if not favourites:
            self._stack.set_visible_child_name("empty")
            return

        favourites.sort(key=lambda c: c.name.casefold())
        for contact in favourites:
            self._flowbox.append(self._make_card(contact))
        self._stack.set_visible_child_name("grid")

    def _make_card(self, contact: Contact) -> Gtk.Widget:
        # Whole card is a button so the tap target is the entire
        # tile, not just the label. .card adds a rounded background
        # + subtle shadow that matches Adwaita's status-page tiles.
        # .fav-card-<n> overlays a stable, muted accent colour
        # (see resources/style.css) — same contact always lands on
        # the same tint so the user builds spatial memory.
        card = Gtk.Button()
        card.add_css_class("card")
        card.add_css_class(_palette_class(contact))
        card.set_hexpand(True)
        card.set_vexpand(True)
        card.set_size_request(-1, _CARD_MIN_HEIGHT)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        spacing=6, valign=Gtk.Align.CENTER,
                        margin_top=10, margin_bottom=10,
                        margin_start=10, margin_end=10)

        avatar = Gtk.Image.new_from_icon_name("avatar-default-symbolic")
        avatar.set_pixel_size(56)
        avatar.set_halign(Gtk.Align.CENTER)
        inner.append(avatar)

        name = Gtk.Label(label=contact.name or "(unnamed)",
                         halign=Gtk.Align.CENTER,
                         justify=Gtk.Justification.CENTER,
                         wrap=True)
        name.add_css_class("heading")
        inner.append(name)

        # Optional second line with the primary phone, dimmer than
        # the name so the card visual stays compact.
        primary = contact.primary_target() or ""
        if primary:
            secondary = Gtk.Label(label=primary,
                                  halign=Gtk.Align.CENTER,
                                  justify=Gtk.Justification.CENTER)
            secondary.add_css_class("dim-label")
            secondary.add_css_class("caption")
            inner.append(secondary)

        card.set_child(inner)
        card.connect(
            "clicked",
            lambda *_: self._fire_call(contact),
        )
        return card

    def _fire_call(self, contact: Contact) -> None:
        target = contact.primary_target()
        if not target:
            return
        logger.info("favorites quick-dial: %s -> %s", contact.name, target)
        self.emit("call-requested", target)
