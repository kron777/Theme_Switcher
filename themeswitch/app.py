"""ThemeSwitch entry point."""
from __future__ import annotations
import os
import sys

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gio

from . import ui

CSS_PATH = os.path.join(os.path.dirname(__file__), "style.css")


class ThemeSwitchApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.themeswitch.app",
                          flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window = None

    def do_activate(self):
        if self.window is None:
            self._load_css()
            self.window = ui.ThemeSwitchWindow(self)
        self.window.present()

    def _load_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_path(CSS_PATH)
        display = Gtk.Widget.get_default_direction  # noop to keep import tidy
        from gi.repository import Gdk
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def main():
    app = ThemeSwitchApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
