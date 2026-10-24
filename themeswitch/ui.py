"""GTK4 main window for ThemeSwitch."""
from __future__ import annotations
import os
import subprocess
import threading

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gio, GLib, Pango

from . import config
from . import analyzer
from . import installer
from . import pipeline
from . import backup as backup_mod


class ComponentRow(Gtk.Box):
    def __init__(self, component: installer.Component):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.component = component
        self.add_css_class("ts-component-row")

        self.check = Gtk.CheckButton(active=True)
        self.check.connect("toggled", self._on_toggle)
        self.append(self.check)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        kind_label = Gtk.Label(label=component.kind.upper().replace("-", " "), xalign=0)
        kind_label.add_css_class("ts-component-kind")
        name_label = Gtk.Label(label=component.name, xalign=0)
        name_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        box.append(kind_label)
        box.append(name_label)
        self.append(box)

    def _on_toggle(self, btn):
        self.component.selected = btn.get_active()


class ThemeSwitchWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="THEMESWITCH")
        self.set_default_size(760, 760)

        self.pipeline = pipeline.Pipeline(log_cb=self._append_log)
        self.extracted_dir = None
        self.current_url = ""
        self.current_theme_name = ""
        self.detected_components: list[installer.Component] = []

        backup_mod.ensure_baseline()

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(root)

        root.append(self._build_header())

        scroller = Gtk.ScrolledWindow(vexpand=True)
        root.append(scroller)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                           margin_top=16, margin_bottom=16, margin_start=18, margin_end=18)
        scroller.set_child(content)

        content.append(self._build_url_panel())
        content.append(self._build_actions_panel())

        self.results_panel = self._build_results_panel()
        content.append(self.results_panel)
        self.results_panel.set_visible(False)

        content.append(self._build_log_panel())

        self._refresh_status_pill()

    # ---------------------------------------------------------------- header
    def _build_header(self) -> Gtk.Widget:
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header.add_css_class("ts-header")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Label(label="THEMESWITCH", xalign=0)
        title.add_css_class("ts-title")
        subtitle = Gtk.Label(label="DESKTOP CONFIGURATION SYSTEM", xalign=0)
        subtitle.add_css_class("ts-subtitle")
        title_box.append(title)
        title_box.append(subtitle)
        top.append(title_box)

        self.status_pill = Gtk.Label(label="READY")
        self.status_pill.add_css_class("ts-status-pill")
        top.append(self.status_pill)
        header.append(top)

        desktop = GLib.getenv("XDG_CURRENT_DESKTOP") or "unknown"
        session = GLib.getenv("XDG_SESSION_TYPE") or "unknown"
        info = Gtk.Label(label=f"session: {desktop} / {session}", xalign=0)
        info.add_css_class("ts-label")
        header.append(info)

        return header

    def _refresh_status_pill(self):
        theme = GLib.spawn_command_line_sync(
            "gsettings get org.gnome.desktop.interface gtk-theme")
        try:
            name = theme[1].decode().strip().strip("'")
        except Exception:
            name = "unknown"
        self.status_pill.set_label(f"ACTIVE: {name}")

    # ------------------------------------------------------------ url panel
    def _build_url_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.add_css_class("ts-panel")
        panel.add_css_class("ts-panel-glow")

        label = Gtk.Label(label="THEME SOURCE URL", xalign=0)
        label.add_css_class("ts-label")
        panel.append(label)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.url_entry = Gtk.Entry(placeholder_text="Paste a theme website URL...", hexpand=True)
        self.url_entry.add_css_class("ts-url-entry")
        self.url_entry.connect("activate", lambda *_: self._on_analyse())
        row.append(self.url_entry)

        analyse_btn = Gtk.Button(label="ANALYSE URL")
        analyse_btn.add_css_class("ts-btn")
        analyse_btn.add_css_class("ts-btn-primary")
        analyse_btn.connect("clicked", lambda *_: self._on_analyse())
        row.append(analyse_btn)
        panel.append(row)

        self.progress = Gtk.ProgressBar(visible=False)
        self.progress.add_css_class("ts-progress")
        panel.append(self.progress)

        return panel

    # --------------------------------------------------------- actions panel
    def _build_actions_panel(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.apply_btn = Gtk.Button(label="APPLY THEME")
        self.apply_btn.add_css_class("ts-btn")
        self.apply_btn.add_css_class("ts-btn-primary")
        self.apply_btn.set_sensitive(False)
        self.apply_btn.connect("clicked", lambda *_: self._on_apply())
        row.append(self.apply_btn)

        restore_btn = Gtk.Button(label="RESTORE DEFAULTS")
        restore_btn.add_css_class("ts-btn")
        restore_btn.add_css_class("ts-btn-danger")
        restore_btn.connect("clicked", lambda *_: self._on_restore())
        row.append(restore_btn)

        folder_btn = Gtk.Button(label="OPEN THEME FOLDER")
        folder_btn.add_css_class("ts-btn")
        folder_btn.connect("clicked", lambda *_: self._on_open_folder())
        row.append(folder_btn)

        log_btn = Gtk.Button(label="VIEW LOG")
        log_btn.add_css_class("ts-btn")
        log_btn.connect("clicked", lambda *_: self._on_open_log())
        row.append(log_btn)

        return row

    # --------------------------------------------------------- results panel
    def _build_results_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        panel.add_css_class("ts-panel")

        self.result_title = Gtk.Label(xalign=0)
        self.result_title.add_css_class("ts-label")
        panel.append(self.result_title)

        self.result_message = Gtk.Label(xalign=0, wrap=True)
        panel.append(self.result_message)

        self.components_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        panel.append(self.components_box)

        return panel

    # ------------------------------------------------------------- log panel
    def _build_log_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        panel.add_css_class("ts-panel")

        label = Gtk.Label(label="ACTIVITY LOG", xalign=0)
        label.add_css_class("ts-label")
        panel.append(label)

        self.log_buffer = Gtk.TextBuffer()
        log_view = Gtk.TextView(buffer=self.log_buffer, editable=False,
                                 cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        log_view.add_css_class("ts-log")
        scroller = Gtk.ScrolledWindow(min_content_height=160)
        scroller.set_child(log_view)
        panel.append(scroller)

        return panel

    def _append_log(self, msg: str):
        def do():
            end = self.log_buffer.get_end_iter()
            self.log_buffer.insert(end, msg + "\n")
            return False
        GLib.idle_add(do)

    # --------------------------------------------------------------- actions
    def _on_analyse(self):
        url = self.url_entry.get_text().strip()
        if not url:
            self._append_log("enter a URL first")
            return
        self.current_url = url
        self.status_pill.set_label("ANALYSING...")
        self.progress.set_visible(True)
        self.progress.pulse()
        self._append_log(f"analysing {url}")

        def work():
            result = analyzer.analyze(url)
            GLib.idle_add(self._on_analysis_done, result)

        threading.Thread(target=work, daemon=True).start()

    def _on_analysis_done(self, result: analyzer.AnalysisResult):
        self.progress.set_visible(False)
        self._refresh_status_pill()
        self.results_panel.set_visible(True)
        self.result_title.set_label(result.project_name or "ANALYSIS RESULT")
        self.result_message.set_label(result.message)

        for child in list(self.components_box):
            self.components_box.remove(child)

        if not result.ok or not result.candidates:
            self.apply_btn.set_sensitive(False)
            self._append_log(f"analysis: {result.message}")
            return False

        self._append_log(f"found {len(result.candidates)} candidate asset(s)")
        self._pending_candidates = result.candidates
        self.current_theme_name = result.project_name

        if len(result.candidates) > 1:
            hint = Gtk.Label(
                xalign=0, wrap=True,
                label="Multiple links found — these are often just different formats of the "
                      "SAME theme (.zip vs .tar.gz), not different themes. Only the first is "
                      "pre-selected to avoid installing duplicates; tick others only if you "
                      "actually want them too.")
            hint.add_css_class("ts-label")
            self.components_box.append(hint)

        for i, cand in enumerate(result.candidates):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row.add_css_class("ts-component-row")
            check = Gtk.CheckButton(active=(i == 0))  # only the first candidate by default
            label = Gtk.Label(label=f"{cand.filename_hint}  ({cand.kind_guess})", xalign=0, hexpand=True)
            row.append(check)
            row.append(label)
            row._cand = cand
            row._check = check
            self.components_box.append(row)

        self.apply_btn.set_sensitive(True)
        return False

    def _on_apply(self):
        candidates = getattr(self, "_pending_candidates", None)
        if not candidates:
            return
        selected_rows = [c for c in self.components_box
                          if hasattr(c, "_check") and c._check.get_active()]
        if not selected_rows:
            self._append_log("nothing selected to apply")
            return

        self.apply_btn.set_sensitive(False)
        self.status_pill.set_label("APPLYING...")
        self.progress.set_visible(True)
        self.progress.pulse()

        def work():
            all_results = []
            any_ok = False
            for row in selected_rows:
                cand = row._cand
                extracted, err = self.pipeline.download_and_extract(cand.url)
                if err:
                    all_results.append(f"{cand.filename_hint}: {err}")
                    continue
                detection = installer.detect_components(extracted)
                if not detection.components:
                    all_results.append(f"{cand.filename_hint}: {', '.join(detection.notes) or 'nothing found'}")
                    continue
                ok, results, txn_id = self.pipeline.apply(cand.url, self.current_theme_name,
                                                            detection.components)
                any_ok = any_ok or ok
                all_results.extend(results)
            GLib.idle_add(self._on_apply_done, any_ok, all_results)

        threading.Thread(target=work, daemon=True).start()

    def _on_apply_done(self, any_ok: bool, results: list[str]):
        self.progress.set_visible(False)
        self.apply_btn.set_sensitive(True)
        self._refresh_status_pill()
        self.status_pill.set_label("APPLIED" if any_ok else "APPLY FAILED")
        for r in results:
            self._append_log(r)
        return False

    def _on_restore(self):
        dialog = Gtk.AlertDialog()
        dialog.set_message("Restore your original desktop?")
        dialog.set_detail("This reapplies the desktop settings ThemeSwitch saved the first "
                           "time it ran, before any theme was applied. Files it installed "
                           "are left in place and can be removed manually if you want.")
        dialog.set_buttons(["Cancel", "Restore"])
        dialog.set_cancel_button(0)
        dialog.set_default_button(0)

        def on_response(d, res):
            try:
                choice = d.choose_finish(res)
            except GLib.Error:
                return
            if choice != 1:
                return
            self._append_log("restoring baseline desktop state...")
            ok = backup_mod.restore_baseline(log=self._append_log)
            self._refresh_status_pill()
            self.status_pill.set_label("RESTORED" if ok else "RESTORE INCOMPLETE")

        dialog.choose(self, None, on_response)

    def _on_open_folder(self):
        os.makedirs(config.APP_DATA_DIR, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", config.APP_DATA_DIR])
        except Exception as e:
            self._append_log(f"couldn't open folder: {e}")

    def _on_open_log(self):
        try:
            subprocess.Popen(["xdg-open", config.LOG_FILE if os.path.isfile(config.LOG_FILE)
                               else config.APP_DATA_DIR])
        except Exception as e:
            self._append_log(f"couldn't open log: {e}")
