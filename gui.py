"""Modern desktop UI for the X-Plane fallback weather server."""

from __future__ import annotations

import os
import queue
import tkinter as tk

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, EW, LEFT, RIGHT, W, X
from ttkbootstrap.dialogs import Messagebox
from ttkbootstrap.scrolled import ScrolledText

from config import WEATHER_PROXY_CERT_DIR
from log_stream import install_log_capture
from main import WeatherServer
from platform_support import WEATHER_HOST, hosts_file_path
from setup_utils import hosts_redirect_active, setup_instructions, setup_status
from user_settings import SETTINGS_PATH, UserSettings, load_settings, save_settings


def _mono_font() -> tuple[str, int]:
    import tkinter.font as tkfont

    root = tk._default_root
    if root is None:
        return ("DejaVu Sans Mono", 10)
    for name in ("JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier"):
        if name in tkfont.families(root):
            return (name, 10)
    return ("TkFixedFont", 10)

# Theme tokens (darkly base + aviation accent)
COLORS = {
    "bg": "#1a1d23",
    "surface": "#232830",
    "surface_alt": "#2c323c",
    "border": "#3a424f",
    "text": "#e8eaed",
    "text_muted": "#9aa3b2",
    "accent": "#3b82f6",
    "accent_hover": "#2563eb",
    "success": "#22c55e",
    "warning": "#f59e0b",
    "danger": "#ef4444",
    "log_bg": "#0f1117",
    "log_fg": "#c9d1d9",
}


class WeatherApp(ttk.Window):
    def __init__(self) -> None:
        super().__init__(
            title="X-Plane Weather Fallback",
            themename="darkly",
            size=(960, 640),
            minsize=(780, 520),
        )
        self.configure(bg=COLORS["bg"])

        self._settings = load_settings()
        if not os.path.isfile(SETTINGS_PATH):
            self._settings = save_settings(self._settings)
        self._log_queue: queue.Queue[str] = queue.Queue()
        self._pending_settings_apply = False

        self._setup_styles()
        self._build_widgets()
        self._load_form_from_settings()
        self._refresh_setup_banner()

        install_log_capture().add_listener(self._enqueue_log)
        self._poll_log_queue()
        self.after(5000, self._poll_setup_banner)

        self.server = WeatherServer(self._settings)
        self.server.start()
        self._set_status("running", "Running")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_styles(self) -> None:
        style = ttk.Style()
        style.configure(
            "Card.TLabelframe",
            background=COLORS["surface"],
            bordercolor=COLORS["border"],
            relief="flat",
        )
        style.configure(
            "Card.TLabelframe.Label",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "Muted.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text_muted"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "Field.TLabel",
            background=COLORS["surface"],
            foreground=COLORS["text"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "Header.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["text"],
            font=("Segoe UI", 20, "bold"),
        )
        style.configure(
            "Subheader.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["text_muted"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "Status.TLabel",
            background=COLORS["bg"],
            foreground=COLORS["text_muted"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "WarningBanner.TLabel",
            background="#3a2f1f",
            foreground="#fcd34d",
            font=("Segoe UI", 10),
            bordercolor="#f59e0b",
        )
        style.configure(
            "OkBanner.TLabel",
            background="#1f3a2a",
            foreground="#86efac",
            font=("Segoe UI", 10),
        )
        style.configure(
            "Accent.TButton",
            font=("Segoe UI", 10, "bold"),
            padding=(16, 10),
        )
        style.configure(
            "Ghost.TButton",
            font=("Segoe UI", 10),
            padding=(14, 10),
        )
        style.configure(
            "Modern.TEntry",
            fieldbackground=COLORS["surface_alt"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=8,
        )
        style.configure(
            "Modern.TSpinbox",
            fieldbackground=COLORS["surface_alt"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            arrowsize=14,
            padding=6,
        )

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self, padding=20)
        outer.pack(fill=BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=X, pady=(0, 12))

        self._setup_banner = ttk.Frame(outer)
        self._setup_banner.pack(fill=X, pady=(0, 12))
        self._setup_banner_text = tk.StringVar(value="")
        self._setup_banner_label = ttk.Label(
            self._setup_banner,
            textvariable=self._setup_banner_text,
            wraplength=860,
            style="WarningBanner.TLabel",
            padding=(12, 10),
        )

        title_block = ttk.Frame(header)
        title_block.pack(side=LEFT, fill=X, expand=True)
        ttk.Label(
            title_block,
            text="X-Plane Weather Fallback",
            style="Header.TLabel",
        ).pack(anchor=W)
        ttk.Label(
            title_block,
            text="Local METAR & GRIB server when Laminar weather is unavailable",
            style="Subheader.TLabel",
        ).pack(anchor=W, pady=(4, 0))

        self._status_frame = ttk.Frame(header)
        self._status_frame.pack(side=RIGHT, padx=(12, 0))
        self._status_dot = tk.Canvas(
            self._status_frame,
            width=10,
            height=10,
            bg=COLORS["bg"],
            highlightthickness=0,
        )
        self._status_dot.pack(side=LEFT, padx=(0, 8))
        self._status_var = tk.StringVar(value="Idle")
        ttk.Label(
            self._status_frame,
            textvariable=self._status_var,
            style="Status.TLabel",
        ).pack(side=LEFT)

        settings = ttk.Labelframe(outer, text="  Settings  ", style="Card.TLabelframe", padding=16)
        settings.pack(fill=X, pady=(0, 14))

        ttk.Label(settings, text="X-Plane root directory", style="Field.TLabel").grid(
            row=0, column=0, sticky=W, pady=(0, 6)
        )
        path_row = ttk.Frame(settings)
        path_row.grid(row=1, column=0, columnspan=2, sticky=EW, pady=(0, 14))
        self.xplane_root_var = tk.StringVar()
        self._root_entry = ttk.Entry(
            path_row,
            textvariable=self.xplane_root_var,
            style="Modern.TEntry",
        )
        self._root_entry.pack(side=LEFT, fill=X, expand=True, padx=(0, 10))
        ttk.Button(
            path_row,
            text="Browse",
            style="Ghost.TButton",
            bootstyle="secondary",
            command=self._browse_xplane_root,
        ).pack(side=RIGHT)

        ttk.Label(
            settings,
            text="Background update interval (minutes)",
            style="Field.TLabel",
        ).grid(row=2, column=0, sticky=W, pady=(0, 6))
        interval_row = ttk.Frame(settings)
        interval_row.grid(row=3, column=0, sticky=W, pady=(0, 10))
        self.interval_var = tk.StringVar()
        self._interval_spin = ttk.Spinbox(
            interval_row,
            from_=1,
            to=1440,
            textvariable=self.interval_var,
            width=8,
            style="Modern.TSpinbox",
        )
        self._interval_spin.pack(side=LEFT)
        ttk.Label(
            interval_row,
            text="  METAR every 15 min when due · GRIB every 3 h",
            style="Muted.TLabel",
        ).pack(side=LEFT, padx=(12, 0))

        self._weather_dir_label = ttk.Label(settings, text="", style="Muted.TLabel")
        self._weather_dir_label.grid(row=4, column=0, sticky=W)

        settings.columnconfigure(0, weight=1)

        actions = ttk.Frame(outer)
        actions.pack(fill=X, pady=(0, 14))

        ttk.Button(
            actions,
            text="Update weather now",
            style="Accent.TButton",
            bootstyle="primary",
            command=self._on_update_weather,
        ).pack(side=LEFT, padx=(0, 10))
        ttk.Button(
            actions,
            text="Save settings",
            style="Ghost.TButton",
            bootstyle="secondary-outline",
            command=self._apply_settings,
        ).pack(side=LEFT, padx=(0, 10))
        ttk.Button(
            actions,
            text="Setup help",
            style="Ghost.TButton",
            bootstyle="warning-outline",
            command=self._show_setup_help,
        ).pack(side=LEFT)

        log_card = ttk.Labelframe(outer, text="  Activity log  ", style="Card.TLabelframe", padding=12)
        log_card.pack(fill=BOTH, expand=True)

        self.log_text = ScrolledText(
            log_card,
            height=16,
            autohide=True,
            bootstyle="round",
            font=_mono_font(),
            padding=10,
        )
        self.log_text.text.configure(
            bg=COLORS["log_bg"],
            fg=COLORS["log_fg"],
            insertbackground=COLORS["log_fg"],
            relief="flat",
            borderwidth=0,
            state=tk.DISABLED,
            wrap=tk.WORD,
        )
        self.log_text.pack(fill=BOTH, expand=True)

        self.xplane_root_var.trace_add("write", self._on_field_changed)
        self.interval_var.trace_add("write", self._on_field_changed)

        for widget in (self._root_entry, self._interval_spin):
            widget.bind("<FocusOut>", lambda _e: self._maybe_auto_save())

    def _show_setup_help(self) -> None:
        paragraphs = setup_instructions(WEATHER_PROXY_CERT_DIR)
        Messagebox.show_info("\n\n".join(paragraphs), "X-Plane setup", parent=self)

    def _refresh_setup_banner(self) -> None:
        status = setup_status(WEATHER_PROXY_CERT_DIR)
        if status["hosts_active"] and status["tls_trusted"]:
            self._setup_banner.pack_forget()
            return

        issues: list[str] = []
        if not status["hosts_active"]:
            hosts_label = hosts_file_path()
            if status["hosts_commented"]:
                issues.append(
                    f"Hosts redirect for {WEATHER_HOST} is COMMENTED OUT in {hosts_label} — "
                    "X-Plane is using Laminar's server instead of this app."
                )
            else:
                issues.append(
                    f"Hosts redirect for {WEATHER_HOST} is missing in {hosts_label} — "
                    "X-Plane cannot reach the local proxy."
                )
        if not status["tls_trusted"]:
            issues.append(
                "HTTPS certificate is not trusted — X-Plane may reject the weather manifest."
            )

        self._setup_banner_text.set(
            "Action required: " + " ".join(issues) + " Click Setup help for fix steps."
        )
        self._setup_banner_label.configure(style="WarningBanner.TLabel")
        self._setup_banner_label.pack(fill=X)
        self._setup_banner.pack(fill=X, pady=(0, 12))

    def _poll_setup_banner(self) -> None:
        self._refresh_setup_banner()
        self.after(5000, self._poll_setup_banner)

    def _draw_status_dot(self, color: str) -> None:
        self._status_dot.delete("all")
        self._status_dot.create_oval(1, 1, 9, 9, fill=color, outline=color)

    def _set_status(self, kind: str, text: str) -> None:
        colors = {
            "running": COLORS["success"],
            "warning": COLORS["warning"],
            "idle": COLORS["text_muted"],
        }
        self._draw_status_dot(colors.get(kind, COLORS["text_muted"]))
        self._status_var.set(text)

    def _show_error(self, title: str, message: str) -> None:
        Messagebox.show_error(message, title, parent=self)

    def _show_confirm(self, title: str, message: str) -> bool:
        result = Messagebox.yesno(message, title, parent=self)
        return result == "Yes"

    def _maybe_auto_save(self) -> None:
        if not self._pending_settings_apply:
            return
        try:
            self._read_form_settings()
        except ValueError:
            return
        self._apply_settings()

    def _load_form_from_settings(self) -> None:
        self._pending_settings_apply = False
        self.xplane_root_var.set(self._settings.xplane_root)
        self.interval_var.set(str(self._settings.update_interval_minutes))
        self._update_weather_dir_label()
        self._pending_settings_apply = False

    def _update_weather_dir_label(self) -> None:
        try:
            settings = self._read_form_settings(validate=False)
            staging = settings.weather_staging_dir
            cache = settings.weather_output_dir
        except ValueError:
            staging = "(invalid path)"
            cache = "(invalid path)"
        self._weather_dir_label.configure(
            text=f"Staging → {staging}\nX-Plane cache → {cache}"
        )

    def _read_form_settings(self, *, validate: bool = True) -> UserSettings:
        root = self.xplane_root_var.get().strip()
        if validate and not root:
            raise ValueError("X-Plane root directory is required.")
        if validate and not os.path.isdir(os.path.expanduser(root)):
            raise ValueError(f"Directory does not exist:\n{root}")

        try:
            minutes = int(self.interval_var.get().strip())
        except ValueError as exc:
            raise ValueError("Update interval must be a whole number of minutes.") from exc

        return UserSettings(xplane_root=root, update_interval_minutes=minutes)

    def _on_field_changed(self, *_args) -> None:
        self._update_weather_dir_label()
        self._pending_settings_apply = True
        self._set_status("warning", "Unsaved changes")

    def _browse_xplane_root(self) -> None:
        from tkinter import filedialog

        initial = self.xplane_root_var.get().strip() or os.path.expanduser("~")
        if os.path.isdir(initial):
            start = initial
        else:
            start = os.path.dirname(initial) if os.path.dirname(initial) else os.path.expanduser("~")

        chosen = filedialog.askdirectory(
            title="Select X-Plane root directory",
            initialdir=start,
            parent=self,
        )
        if chosen:
            self.xplane_root_var.set(chosen)
            self._apply_settings()

    def _apply_settings(self) -> None:
        try:
            new_settings = save_settings(self._read_form_settings())
        except ValueError as exc:
            self._show_error("Invalid settings", str(exc))
            return

        self._settings = new_settings
        self._pending_settings_apply = False
        self._update_weather_dir_label()
        print(
            f"Settings saved: X-Plane root={self._settings.xplane_root}, "
            f"interval={self._settings.update_interval_minutes} min"
        )
        self.server.apply_settings(self._settings)
        self._set_status("running", "Running")

    def _on_update_weather(self) -> None:
        if self._pending_settings_apply:
            self._apply_settings()
        print("Manual weather update requested from UI.")
        self.server.force_weather_update()

    def _enqueue_log(self, text: str) -> None:
        self._log_queue.put(text)

    def _poll_log_queue(self) -> None:
        changed = False
        text_widget = self.log_text.text
        while True:
            try:
                chunk = self._log_queue.get_nowait()
            except queue.Empty:
                break
            text_widget.configure(state=tk.NORMAL)
            text_widget.insert(tk.END, chunk)
            text_widget.see(tk.END)
            text_widget.configure(state=tk.DISABLED)
            changed = True

        if changed and len(text_widget.get("1.0", tk.END)) > 200_000:
            text_widget.configure(state=tk.NORMAL)
            text_widget.delete("1.0", "10000.0")
            text_widget.configure(state=tk.DISABLED)

        self.after(100, self._poll_log_queue)

    def _on_close(self) -> None:
        if self._pending_settings_apply:
            if self._show_confirm(
                "Save settings?",
                "You have unsaved changes. Save before closing?",
            ):
                self._apply_settings()
        self.server.stop()
        self.destroy()


def main() -> None:
    app = WeatherApp()
    app.mainloop()


if __name__ == "__main__":
    main()
