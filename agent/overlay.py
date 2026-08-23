"""Custom on-screen overlay notifier (Jules' spec, 2026-08-23):

- box in the TOP-RIGHT corner, ~15% of screen height
- plays a sound
- always-on-top but never steals focus — the game keeps keyboard/mouse
- auto-dismisses after a few seconds; click to dismiss early

Why not native Windows toasts: Focus Assist suppresses them during
fullscreen gaming — exactly when GameGate's break-through matters most. A
topmost overlay window is not subject to Focus Assist. (Fullscreen-EXCLUSIVE
games hide any overlay, Discord's included; borderless/windowed — the modern
default — shows it fine.)

Pure stdlib (tkinter + winsound) so PyInstaller packaging stays simple.
"""
import logging

log = logging.getLogger("gamegate.overlay")

WIDTH_FRACTION = 0.30
HEIGHT_FRACTION = 0.15
MARGIN_PX = 16
DEFAULT_DURATION_S = 8

BG = "#1e1f22"
ACCENT = "#7c3aed"
FG_TITLE = "#ffffff"
FG_BODY = "#c8c9cf"


def compute_geometry(
    screen_w: int,
    screen_h: int,
    width_fraction: float = WIDTH_FRACTION,
    height_fraction: float = HEIGHT_FRACTION,
    margin: int = MARGIN_PX,
) -> tuple[int, int, int, int]:
    """(width, height, x, y) for a top-right box. Pure — unit-tested."""
    width = int(screen_w * width_fraction)
    height = int(screen_h * height_fraction)
    x = screen_w - width - margin
    y = margin
    return width, height, x, y


def play_sound() -> None:
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:  # noqa: BLE001 — sound is best-effort, never fatal
        log.debug("Sound unavailable on this platform")


def show_overlay(title: str, body: str, duration_s: int = DEFAULT_DURATION_S) -> bool:
    """Display the overlay. Returns False on any failure so the pump
    retries instead of acking. Blocks for at most duration_s (sequential
    notifications by design — they never stack over each other)."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.overrideredirect(True)          # borderless
        root.attributes("-topmost", True)    # above the game
        try:
            root.attributes("-alpha", 0.94)
        except tk.TclError:
            pass

        width, height, x, y = compute_geometry(
            root.winfo_screenwidth(), root.winfo_screenheight()
        )
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.configure(bg=BG)

        tk.Frame(root, bg=ACCENT, width=6).pack(side="left", fill="y")
        content = tk.Frame(root, bg=BG)
        content.pack(side="left", fill="both", expand=True, padx=12, pady=10)
        tk.Label(
            content, text=title, bg=BG, fg=FG_TITLE, anchor="w",
            font=("Segoe UI", 12, "bold"), justify="left",
        ).pack(fill="x")
        tk.Label(
            content, text=body, bg=BG, fg=FG_BODY, anchor="nw",
            font=("Segoe UI", 10), justify="left",
            wraplength=max(100, width - 60),
        ).pack(fill="both", expand=True)

        root.bind("<Button-1>", lambda _e: root.destroy())
        root.after(duration_s * 1000, root.destroy)

        play_sound()
        root.mainloop()
        return True
    except Exception:
        log.exception("Overlay failed")
        return False
