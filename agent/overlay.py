"""GameGate overlay notifier — designed card, not a gray box.

Layout (Jules' second design round, 2026-08-23):
┌▌──────────────────────────────┐
│▌ [badge] GAMEGATE        now ✕ │   header row
│▌ Title in bold                 │
│▌ Body text, wrapped            │
│▌▂▂▂▂▂▂▂▂▂▂▂▂ (countdown line)  │
└────────────────────────────────┘
- top-right corner, content-hugging height (15% of screen is the MAX, not
  the fixed size — no more giant empty slab)
- fades in, plays a sound, auto-dismisses as the countdown line runs out,
  click anywhere (or ✕) to dismiss early
- always-on-top, never steals focus
- per-monitor DPI aware (the fix for blurry rendering on modern screens)

Why not native Windows toasts: Focus Assist suppresses them during
fullscreen gaming — exactly when GameGate's break-through matters most.
"""
import logging

log = logging.getLogger("gamegate.overlay")

WIDTH_FRACTION = 0.26
MAX_HEIGHT_FRACTION = 0.15
MARGIN_PX = 16
DEFAULT_DURATION_S = 8
CORNER_RADIUS = 16
TARGET_ALPHA = 0.97

BG = "#16171d"
EDGE = "#2c2e3a"
ACCENT = "#7c3aed"
FG_TITLE = "#ffffff"
FG_BODY = "#c3c6d4"
FG_MUTED = "#71748a"
TRANSPARENT_KEY = "#010203"

HEADER_H = 34
TITLE_H = 24
LINE_H = 19
V_PAD = 12
TEXT_X = 24
MIN_HEIGHT = 92


def enable_dpi_awareness() -> None:
    """Per-monitor DPI awareness — must run once, before any window exists."""
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001 — non-Windows or already set; fine
        log.debug("DPI awareness not applied (non-Windows or already set)")


def compute_card_height(body_lines: int, screen_h: int) -> int:
    """Content-hugging height, clamped to [MIN_HEIGHT, 15% of screen]."""
    wanted = V_PAD + HEADER_H + TITLE_H + body_lines * LINE_H + V_PAD + 4
    return max(MIN_HEIGHT, min(wanted, int(screen_h * MAX_HEIGHT_FRACTION)))


def compute_geometry(
    screen_w: int, screen_h: int, height: int | None = None, margin: int = MARGIN_PX
) -> tuple[int, int, int, int]:
    """(width, height, x, y) for a top-right card. Pure — unit-tested."""
    width = int(screen_w * WIDTH_FRACTION)
    card_height = height if height is not None else int(screen_h * MAX_HEIGHT_FRACTION)
    return width, card_height, screen_w - width - margin, margin


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


def _badge_photo(size: int = 22):
    """The GameGate badge as a Tk image, or None if PIL is unavailable."""
    try:
        from branding import render_badge
        from PIL import ImageTk

        return ImageTk.PhotoImage(render_badge("gaming", size))
    except Exception:  # noqa: BLE001 — icon is decoration, never a blocker
        return None


def show_overlay(title: str, body: str, duration_s: int = DEFAULT_DURATION_S) -> bool:
    """Display the overlay card. Returns False on any failure so the pump
    retries instead of acking. Sequential by design — cards never stack."""
    try:
        import tkinter as tk
        import tkinter.font as tkfont

        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        transparent_ok = True
        try:
            root.attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            transparent_ok = False

        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        width = int(screen_w * WIDTH_FRACTION)
        body_font = tkfont.Font(family="Segoe UI", size=10)
        text_width = width - TEXT_X - 28

        # Estimate wrapped body lines so the card hugs its content.
        body_lines = 0
        for paragraph in (body or " ").splitlines() or [" "]:
            body_lines += max(1, -(-body_font.measure(paragraph) // text_width))
        body_lines = min(body_lines, 6)

        height = compute_card_height(body_lines, screen_h)
        width, height, x, y = compute_geometry(screen_w, screen_h, height)
        root.geometry(f"{width}x{height}+{x}+{y}")

        canvas = tk.Canvas(
            root, bg=TRANSPARENT_KEY if transparent_ok else BG,
            highlightthickness=0, width=width, height=height,
        )
        canvas.pack(fill="both", expand=True)

        # Card, edge, accent bar (inset so it never fights the corners).
        canvas.create_polygon(
            rounded_rect_points(0, 0, width - 1, height - 1, CORNER_RADIUS),
            smooth=True, fill=BG, outline=EDGE, width=1,
        )
        canvas.create_rectangle(
            0, CORNER_RADIUS, 7, height - CORNER_RADIUS, fill=ACCENT, outline=""
        )

        # Header row: badge · GAMEGATE · now · ✕
        badge = _badge_photo()
        header_y = V_PAD + 10
        if badge is not None:
            canvas.create_image(TEXT_X, header_y, image=badge, anchor="w")
            root._badge_ref = badge  # keep a reference or Tk garbage-collects it
            name_x = TEXT_X + 30
        else:
            name_x = TEXT_X
        canvas.create_text(
            name_x, header_y, text="GAMEGATE", anchor="w", fill=FG_MUTED,
            font=("Segoe UI", 8, "bold"),
        )
        canvas.create_text(
            width - 44, header_y, text="now", anchor="e", fill=FG_MUTED,
            font=("Segoe UI", 8),
        )
        close = canvas.create_text(
            width - 24, header_y, text="✕", anchor="center", fill=FG_MUTED,
            font=("Segoe UI", 10),
        )
        canvas.tag_bind(close, "<Button-1>", lambda _e: root.destroy())

        # Title + body.
        title_y = V_PAD + HEADER_H
        canvas.create_text(
            TEXT_X, title_y, text=title, anchor="nw", fill=FG_TITLE,
            font=("Segoe UI", 12, "bold"), width=text_width,
        )
        canvas.create_text(
            TEXT_X, title_y + TITLE_H, text=body, anchor="nw", fill=FG_BODY,
            font=body_font, width=text_width,
        )

        # Countdown line along the bottom — shrinks as time runs out.
        track_x1, track_x2 = TEXT_X, width - 20
        track_y = height - 10
        canvas.create_line(track_x1, track_y, track_x2, track_y, fill=EDGE, width=3)
        countdown = canvas.create_line(
            track_x1, track_y, track_x2, track_y, fill=ACCENT, width=3
        )
        steps = duration_s * 20

        def tick(step: int = 0) -> None:
            try:
                if step >= steps:
                    root.destroy()
                    return
                remaining = track_x1 + (track_x2 - track_x1) * (1 - step / steps)
                canvas.coords(countdown, track_x1, track_y, remaining, track_y)
                root.after(50, tick, step + 1)
            except tk.TclError:
                pass  # dismissed early

        root.bind("<Button-1>", lambda _e: root.destroy())

        root.attributes("-alpha", 0.0)

        def fade(step: int = 0) -> None:
            alpha = min(TARGET_ALPHA, (step + 1) * TARGET_ALPHA / 8)
            try:
                root.attributes("-alpha", alpha)
                if alpha < TARGET_ALPHA:
                    root.after(22, fade, step + 1)
            except tk.TclError:
                pass

        fade()
        tick()
        play_sound()
        root.mainloop()
        return True
    except Exception:
        log.exception("Overlay failed")
        return False
