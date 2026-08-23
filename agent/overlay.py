"""Custom on-screen overlay notifier (Jules' spec, 2026-08-23):

- rounded card in the TOP-RIGHT corner, ~15% of screen height
- plays a sound, fades in
- always-on-top but never steals focus — the game keeps keyboard/mouse
- auto-dismisses after a few seconds; click to dismiss early

Rendering quality: the process is made per-monitor DPI aware (without this,
Windows bitmap-scales the window and everything looks blurry on modern
screens), and corners are rounded via the -transparentcolor trick.

Why not native Windows toasts: Focus Assist suppresses them during
fullscreen gaming — exactly when GameGate's break-through matters most.
Pure stdlib (tkinter + winsound + ctypes) so PyInstaller packaging stays simple.
"""
import logging

log = logging.getLogger("gamegate.overlay")

WIDTH_FRACTION = 0.30
HEIGHT_FRACTION = 0.15
MARGIN_PX = 16
DEFAULT_DURATION_S = 8
CORNER_RADIUS = 18
TARGET_ALPHA = 0.96

BG = "#16171d"
EDGE = "#2c2e3a"
ACCENT = "#7c3aed"
FG_TITLE = "#ffffff"
FG_BODY = "#c3c6d4"
TRANSPARENT_KEY = "#010203"  # magic color rendered as fully transparent


def enable_dpi_awareness() -> None:
    """Per-monitor DPI awareness — must run once, before any window exists.
    Without it, Windows scales the app bitmap and it looks 'not HD'."""
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 — non-Windows or already set; fine
        log.debug("DPI awareness not applied (non-Windows or already set)")


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


def rounded_rect_points(x1: int, y1: int, x2: int, y2: int, r: int) -> list[int]:
    """Polygon points for a rounded rectangle (smooth=True). Pure — tested."""
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def play_sound() -> None:
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:  # noqa: BLE001 — sound is best-effort, never fatal
        log.debug("Sound unavailable on this platform")


def show_overlay(title: str, body: str, duration_s: int = DEFAULT_DURATION_S) -> bool:
    """Display the overlay card. Returns False on any failure so the pump
    retries instead of acking. Blocks for at most duration_s (sequential
    notifications by design — they never stack over each other)."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)

        transparent_ok = True
        try:
            root.attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            transparent_ok = False

        width, height, x, y = compute_geometry(
            root.winfo_screenwidth(), root.winfo_screenheight()
        )
        root.geometry(f"{width}x{height}+{x}+{y}")

        canvas = tk.Canvas(
            root,
            bg=TRANSPARENT_KEY if transparent_ok else BG,
            highlightthickness=0,
            width=width,
            height=height,
        )
        canvas.pack(fill="both", expand=True)

        # Card with a subtle edge, then the accent bar, then text.
        canvas.create_polygon(
            rounded_rect_points(0, 0, width - 1, height - 1, CORNER_RADIUS),
            smooth=True, fill=BG, outline=EDGE, width=1,
        )
        canvas.create_polygon(
            rounded_rect_points(0, 0, 10, height - 1, CORNER_RADIUS // 2),
            smooth=True, fill=ACCENT, outline="",
        )

        pad = 22
        canvas.create_text(
            pad + 6, 18, text=title, anchor="nw", fill=FG_TITLE,
            font=("Segoe UI", 12, "bold"), width=width - pad - 40,
        )
        canvas.create_text(
            pad + 6, 48, text=body, anchor="nw", fill=FG_BODY,
            font=("Segoe UI", 10), width=width - pad - 40,
        )

        root.bind("<Button-1>", lambda _e: root.destroy())
        root.after(duration_s * 1000, root.destroy)

        # Fade in — reads as intentional design instead of a popup blink.
        root.attributes("-alpha", 0.0)

        def fade(step: int = 0) -> None:
            alpha = min(TARGET_ALPHA, (step + 1) * TARGET_ALPHA / 8)
            try:
                root.attributes("-alpha", alpha)
                if alpha < TARGET_ALPHA:
                    root.after(22, fade, step + 1)
            except tk.TclError:
                pass  # dismissed mid-fade

        fade()
        play_sound()
        root.mainloop()
        return True
    except Exception:
        log.exception("Overlay failed")
        return False
