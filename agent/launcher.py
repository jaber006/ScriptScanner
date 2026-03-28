"""
ScriptScanner Launcher v3.0
============================
GUI launcher for the ScriptScanner Vision Agent.

- Green START button to begin polling
- Status display
- Dry Run checkbox
- Settings dialog (API keys, Supabase, initials)
- Live log area (last 10 lines)
- System tray when minimized
- Stop button for graceful shutdown
- Minimize-to-tray on close (X button)
"""

import os
import sys
import json
import queue
import logging
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import datetime

# ── Tray support (optional) ───────────────────────────────────────────────────
try:
    import pystray
    from PIL import Image as PILImage, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

# ── Local imports ─────────────────────────────────────────────────────────────
# Add agent/ dir to sys.path so we can import config_manager
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from config_manager import ConfigManager, REQUIRED_KEYS

VERSION = "3.0"
APP_TITLE = f"ScriptScanner v{VERSION}"
LOG_FILE = os.path.join(_HERE, 'scriptscanner.log')
MAX_LOG_LINES = 10  # Lines shown in GUI

# ─────────────────────────────────────────────────────────────────────────────
# Logging setup: file + queue (for GUI)
# ─────────────────────────────────────────────────────────────────────────────

log_queue: queue.Queue = queue.Queue()


class QueueHandler(logging.Handler):
    def emit(self, record):
        try:
            log_queue.put_nowait(self.format(record))
        except Exception:
            pass


def setup_logging():
    fmt = '%(asctime)s [%(levelname)s] %(message)s'
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # File handler
    fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
    fh.setFormatter(logging.Formatter(fmt))
    root.addHandler(fh)
    # Queue handler (→ GUI)
    qh = QueueHandler()
    qh.setFormatter(logging.Formatter(fmt))
    root.addHandler(qh)
    # Console
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(fmt))
    root.addHandler(ch)


log = logging.getLogger('launcher')

# ─────────────────────────────────────────────────────────────────────────────
# Settings Dialog
# ─────────────────────────────────────────────────────────────────────────────

class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, cfg: ConfigManager):
        super().__init__(parent)
        self.cfg = cfg
        self.result = None

        self.title("Settings")
        self.resizable(False, False)
        self.grab_set()

        pad = dict(padx=10, pady=5)

        tk.Label(self, text="Anthropic API Key:").grid(row=0, column=0, sticky='e', **pad)
        self.anthropic_var = tk.StringVar(value=cfg.get('ANTHROPIC_API_KEY'))
        tk.Entry(self, textvariable=self.anthropic_var, width=50, show='*').grid(row=0, column=1, **pad)

        tk.Label(self, text="Supabase URL:").grid(row=1, column=0, sticky='e', **pad)
        self.supa_url_var = tk.StringVar(value=cfg.get('SUPABASE_URL'))
        tk.Entry(self, textvariable=self.supa_url_var, width=50).grid(row=1, column=1, **pad)

        tk.Label(self, text="Supabase Key:").grid(row=2, column=0, sticky='e', **pad)
        self.supa_key_var = tk.StringVar(value=cfg.get('SUPABASE_KEY'))
        tk.Entry(self, textvariable=self.supa_key_var, width=50, show='*').grid(row=2, column=1, **pad)

        tk.Label(self, text="Pharmacist Initials:").grid(row=3, column=0, sticky='e', **pad)
        self.initials_var = tk.StringVar(value=cfg.get('PHARMACIST_INITIALS'))
        tk.Entry(self, textvariable=self.initials_var, width=10).grid(row=3, column=1, sticky='w', **pad)

        btn_frame = tk.Frame(self)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=10)
        tk.Button(btn_frame, text="Save", width=10, command=self._save).pack(side='left', padx=5)
        tk.Button(btn_frame, text="Cancel", width=10, command=self.destroy).pack(side='left', padx=5)

        self._center(parent)

    def _center(self, parent):
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")

    def _save(self):
        self.cfg.set('ANTHROPIC_API_KEY', self.anthropic_var.get().strip())
        self.cfg.set('SUPABASE_URL', self.supa_url_var.get().strip())
        self.cfg.set('SUPABASE_KEY', self.supa_key_var.get().strip())
        self.cfg.set('PHARMACIST_INITIALS', self.initials_var.get().strip())
        self.cfg.save()
        self.result = True
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Agent runner (subprocess)
# ─────────────────────────────────────────────────────────────────────────────

class AgentRunner:
    """Runs vision_agent.py in a subprocess and streams stdout/stderr to log."""

    def __init__(self, cfg: ConfigManager, dry_run: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.on_status_change = None  # callback(str)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self):
        if self.running:
            return

        # Build env for subprocess
        env = os.environ.copy()
        env['ANTHROPIC_API_KEY'] = self.cfg.get('ANTHROPIC_API_KEY')
        env['SUPABASE_URL'] = self.cfg.get('SUPABASE_URL')
        env['NEXT_PUBLIC_SUPABASE_URL'] = self.cfg.get('SUPABASE_URL')
        env['SUPABASE_KEY'] = self.cfg.get('SUPABASE_KEY')
        env['NEXT_PUBLIC_SUPABASE_ANON_KEY'] = self.cfg.get('SUPABASE_KEY')
        if self.cfg.get('PHARMACIST_INITIALS'):
            env['PHARMACIST_INITIALS'] = self.cfg.get('PHARMACIST_INITIALS')

        # Locate vision_agent.py
        if getattr(sys, 'frozen', False):
            # Bundled: vision_agent.py is in _MEIPASS temp dir
            agent_path = os.path.join(sys._MEIPASS, 'vision_agent.py')  # type: ignore[attr-defined]
        else:
            agent_path = os.path.join(_HERE, 'vision_agent.py')

        cmd = [sys.executable, agent_path]
        if self.dry_run:
            cmd.append('--dry-run')

        self._stop_event.clear()
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        self._thread = threading.Thread(target=self._stream, daemon=True)
        self._thread.start()
        self._notify_status("Waiting for scripts...")
        log.info(f"Agent started (PID {self._proc.pid}) {'[DRY RUN]' if self.dry_run else ''}")

    def _stream(self):
        try:
            for line in self._proc.stdout:  # type: ignore[union-attr]
                line = line.rstrip()
                if not line:
                    continue
                log.info(f"[agent] {line}")
                # Update status from agent output
                low = line.lower()
                if 'job:' in low or 'patient:' in low:
                    self._notify_status("Dispensing...")
                elif 'waiting' in low or 'no pending' in low or 'polling' in low:
                    self._notify_status("Waiting for scripts...")
                elif 'error' in low or 'failed' in low:
                    self._notify_status("Error — check logs")
                elif 'completed' in low or 'dispensing complete' in low:
                    self._notify_status("Done ✅ — Waiting for scripts...")
        except Exception as e:
            log.debug(f"Stream error: {e}")
        finally:
            rc = self._proc.wait() if self._proc else -1
            log.info(f"Agent exited (rc={rc})")
            self._notify_status("Stopped")

    def stop(self):
        if self._proc and self._proc.poll() is None:
            log.info("Stopping agent...")
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._stop_event.set()

    def _notify_status(self, status: str):
        if callable(self.on_status_change):
            self.on_status_change(status)


# ─────────────────────────────────────────────────────────────────────────────
# Tray icon
# ─────────────────────────────────────────────────────────────────────────────

def _make_tray_icon_image() -> "PILImage.Image":
    """Generate a simple green pill icon for the tray."""
    img = PILImage.new('RGB', (64, 64), color=(40, 40, 40))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 20, 56, 44], fill=(34, 197, 94))
    return img


# ─────────────────────────────────────────────────────────────────────────────
# Main Application Window
# ─────────────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()

        setup_logging()

        self.cfg = ConfigManager()
        self.cfg.load()

        self._runner: AgentRunner | None = None
        self._tray: "pystray.Icon | None" = None
        self._tray_thread: threading.Thread | None = None
        self._log_lines: list[str] = []

        self.title(APP_TITLE)
        self.resizable(False, False)
        self._build_ui()
        self._center()

        # Poll log queue every 200ms
        self._poll_logs()

        # Check config on first run
        self.after(100, self._check_first_run)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI Build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        self.configure(bg='#1a1a2e')
        pad = dict(padx=12, pady=6)

        # Title
        tk.Label(
            self, text="💊 ScriptScanner Vision Agent",
            font=('Segoe UI', 14, 'bold'),
            bg='#1a1a2e', fg='#e0e0e0'
        ).pack(pady=(14, 4))

        tk.Label(
            self, text=f"v{VERSION}",
            font=('Segoe UI', 9),
            bg='#1a1a2e', fg='#888'
        ).pack()

        # Status bar
        self._status_var = tk.StringVar(value="Ready")
        status_frame = tk.Frame(self, bg='#16213e', bd=1, relief='sunken')
        status_frame.pack(fill='x', padx=12, pady=(10, 4))
        tk.Label(
            status_frame, textvariable=self._status_var,
            font=('Segoe UI', 11),
            bg='#16213e', fg='#22c55e',
            anchor='w', padx=8, pady=6
        ).pack(fill='x')

        # Control buttons row
        ctrl = tk.Frame(self, bg='#1a1a2e')
        ctrl.pack(pady=8)

        self._start_btn = tk.Button(
            ctrl, text="▶  START",
            font=('Segoe UI', 13, 'bold'),
            bg='#22c55e', fg='#000',
            activebackground='#16a34a',
            width=12, height=1,
            relief='flat', cursor='hand2',
            command=self._on_start,
        )
        self._start_btn.pack(side='left', padx=6)

        self._stop_btn = tk.Button(
            ctrl, text="⏹  STOP",
            font=('Segoe UI', 13, 'bold'),
            bg='#ef4444', fg='#fff',
            activebackground='#b91c1c',
            width=12, height=1,
            relief='flat', cursor='hand2',
            state='disabled',
            command=self._on_stop,
        )
        self._stop_btn.pack(side='left', padx=6)

        # Options row
        opts = tk.Frame(self, bg='#1a1a2e')
        opts.pack(pady=4)

        self._dry_run_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            opts, text="Dry Run (no actions)",
            variable=self._dry_run_var,
            font=('Segoe UI', 10),
            bg='#1a1a2e', fg='#ccc',
            activebackground='#1a1a2e', activeforeground='#fff',
            selectcolor='#333',
        ).pack(side='left', padx=8)

        tk.Button(
            opts, text="⚙ Settings",
            font=('Segoe UI', 10),
            bg='#374151', fg='#e0e0e0',
            activebackground='#4b5563',
            relief='flat', cursor='hand2',
            command=self._on_settings,
        ).pack(side='left', padx=8)

        # Log area
        log_frame = tk.Frame(self, bg='#1a1a2e')
        log_frame.pack(fill='both', expand=True, padx=12, pady=(6, 12))

        tk.Label(
            log_frame, text="Log (last 10 lines):",
            font=('Segoe UI', 9), bg='#1a1a2e', fg='#888',
            anchor='w'
        ).pack(fill='x')

        self._log_text = tk.Text(
            log_frame,
            height=10, width=72,
            font=('Consolas', 9),
            bg='#0f172a', fg='#94a3b8',
            insertbackground='white',
            state='disabled',
            wrap='word',
        )
        scroll = tk.Scrollbar(log_frame, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=scroll.set)
        self._log_text.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')

    def _center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _check_first_run(self):
        if not self.cfg.is_configured():
            messagebox.showinfo(
                "Setup Required",
                "No config found.\nPlease enter your API keys in Settings.",
                parent=self,
            )
            self._on_settings()

    def _on_settings(self):
        dlg = SettingsDialog(self, self.cfg)
        self.wait_window(dlg)
        if dlg.result:
            self._set_status("Settings saved ✓")

    def _on_start(self):
        if not self.cfg.is_configured():
            messagebox.showwarning(
                "Config Missing",
                "Please complete Settings before starting.",
                parent=self,
            )
            self._on_settings()
            return

        dry = self._dry_run_var.get()
        self._runner = AgentRunner(self.cfg, dry_run=dry)
        self._runner.on_status_change = self._set_status_threadsafe
        self._runner.start()

        self._start_btn.config(state='disabled')
        self._stop_btn.config(state='normal')
        self._set_status("Waiting for scripts...")

    def _on_stop(self):
        if self._runner:
            self._runner.stop()
        self._start_btn.config(state='normal')
        self._stop_btn.config(state='disabled')
        self._set_status("Stopped")

    def _on_close(self):
        """Minimize to tray instead of exiting."""
        if TRAY_AVAILABLE:
            self.withdraw()
            self._start_tray()
        else:
            # No tray — just iconify
            self.iconify()

    # ── Status ────────────────────────────────────────────────────────────────

    def _set_status(self, text: str):
        self._status_var.set(text)
        # Colour coding
        colour = '#22c55e'  # green
        if 'error' in text.lower() or 'fail' in text.lower():
            colour = '#ef4444'
        elif 'dispens' in text.lower():
            colour = '#facc15'
        elif 'stopped' in text.lower() or 'ready' in text.lower():
            colour = '#94a3b8'
        # Update label colour (find the status label child)
        try:
            for w in self.winfo_children():
                for ww in w.winfo_children():
                    if hasattr(ww, 'cget') and ww.cget('textvariable'):
                        ww.config(fg=colour)
        except Exception:
            pass

    def _set_status_threadsafe(self, text: str):
        self.after(0, lambda: self._set_status(text))

    # ── Log area ──────────────────────────────────────────────────────────────

    def _poll_logs(self):
        try:
            while True:
                line = log_queue.get_nowait()
                self._log_lines.append(line)
                if len(self._log_lines) > MAX_LOG_LINES:
                    self._log_lines = self._log_lines[-MAX_LOG_LINES:]
                self._refresh_log()
        except queue.Empty:
            pass
        self.after(200, self._poll_logs)

    def _refresh_log(self):
        self._log_text.config(state='normal')
        self._log_text.delete('1.0', 'end')
        self._log_text.insert('end', '\n'.join(self._log_lines))
        self._log_text.see('end')
        self._log_text.config(state='disabled')

    # ── System Tray ───────────────────────────────────────────────────────────

    def _start_tray(self):
        if not TRAY_AVAILABLE or self._tray:
            return

        icon_img = _make_tray_icon_image()

        menu = pystray.Menu(
            pystray.MenuItem("Show", self._tray_show, default=True),
            pystray.MenuItem("Stop Agent", self._tray_stop),
            pystray.MenuItem("Quit", self._tray_quit),
        )

        self._tray = pystray.Icon(
            name="ScriptScanner",
            icon=icon_img,
            title=APP_TITLE,
            menu=menu,
        )

        self._tray_thread = threading.Thread(target=self._tray.run, daemon=True)
        self._tray_thread.start()

    def _tray_show(self, icon=None, item=None):
        self.after(0, self._show_window)

    def _show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        if self._tray:
            self._tray.stop()
            self._tray = None

    def _tray_stop(self, icon=None, item=None):
        self.after(0, self._on_stop)
        self.after(0, self._show_window)

    def _tray_quit(self, icon=None, item=None):
        self.after(0, self._quit)

    def _quit(self):
        if self._runner:
            self._runner.stop()
        if self._tray:
            self._tray.stop()
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────

def main():
    app = App()
    app.mainloop()


if __name__ == '__main__':
    main()
