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
import threading

log = logging.getLogger("gamegate.overlay")

# Serialize ALL Tk rendering. show_overlay (pump thread) and show_update_prompt
# (update-check thread) each create their own tk.Tk() + mainloop(); Tk is not
# thread-safe and two live roots in two threads can crash. This lock guarantees
# at most ONE root exists at a time — a notification arriving during an update
# prompt simply waits for it to close, then renders (review MAJOR: concurrent Tk
# interpreters). Blocking (not try-acquire) so a busy UI never DROPS a card — the
# pump's poison-guard must not mistake "UI busy" for "card failed".
_ui_lock = threading.Lock()

WIDTH_FRACTION = 0.26
MAX_HEIGHT_FRACTION = 0.15
MARGIN_PX = 16
DEFAULT_DURATION_S = 8
CORNER_RADIUS = 16
TARGET_ALPHA = 0.97

BG = "#101312"
EDGE = "#222824"
ACCENT = "#2ee06f"
FG_TITLE = "#ffffff"
FG_BODY = "#c2c8c4"
FG_MUTED = "#79817c"
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


def show_overlay(
    title: str, body: str, duration_s: int = DEFAULT_DURATION_S, sound: bool = True
) -> bool:
    """Public entry: serialize rendering so only one Tk root is ever alive."""
    with _ui_lock:
        return _show_overlay(title, body, duration_s, sound)


def _show_overlay(
    title: str, body: str, duration_s: int = DEFAULT_DURATION_S, sound: bool = True
) -> bool:
    """Display the overlay card. Returns False on any failure so the pump
    retries instead of acking. Sequential by design — cards never stack."""
    # Canvas.bbox() returns None for empty text -> TypeError -> the card would
    # fail and be retried forever. A Discord message that is only an attachment
    # has empty content, and a digest whose text starts with a newline yields an
    # empty title line, so guard BOTH (M7, N9).
    body = body or " "
    title = title or " "
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
        # DPI scale factor: 1.0 at 100% Windows scaling, 1.5 at 150%, etc.
        # Fonts use negative (pixel) sizes derived from the same factor, so
        # text and layout can never disagree again.
        scale = max(1.0, root.winfo_fpixels("1i") / 96.0)

        def px(value: float) -> int:
            return int(value * scale)

        width = int(screen_w * WIDTH_FRACTION)
        title_font = tkfont.Font(family="Segoe UI", size=-px(17), weight="bold")
        body_font = tkfont.Font(family="Segoe UI", size=-px(13))
        muted_font = tkfont.Font(family="Segoe UI", size=-px(11))
        muted_bold = tkfont.Font(family="Segoe UI", size=-px(11), weight="bold")
        text_x = px(TEXT_X)
        text_width = width - text_x - px(28)

        canvas = tk.Canvas(
            root, bg=TRANSPARENT_KEY if transparent_ok else BG, highlightthickness=0
        )
        canvas.pack(fill="both", expand=True)

        # Measure-then-draw (live find: word-wrap estimates undercount and
        # clipped a 3-line Gmail subject). Render the real text items, read
        # their true bounding boxes, then size the card around them.
        probe_title = canvas.create_text(
            text_x, 0, text=title, anchor="nw", font=title_font, width=text_width
        )
        title_h = canvas.bbox(probe_title)[3] - canvas.bbox(probe_title)[1]
        probe_body = canvas.create_text(
            text_x, 0, text=body, anchor="nw", font=body_font, width=text_width
        )
        body_h = canvas.bbox(probe_body)[3] - canvas.bbox(probe_body)[1]
        canvas.delete(probe_title, probe_body)

        wanted = px(V_PAD + HEADER_H) + title_h + px(6) + body_h + px(26)
        height = max(px(MIN_HEIGHT), min(wanted, int(screen_h * MAX_HEIGHT_FRACTION)))
        width, height, x, y = compute_geometry(screen_w, screen_h, height)
        root.geometry(f"{width}x{height}+{x}+{y}")
        canvas.configure(width=width, height=height)

        # Card, edge, accent bar (inset so it never fights the corners).
        canvas.create_polygon(
            rounded_rect_points(0, 0, width - 1, height - 1, CORNER_RADIUS),
            smooth=True, fill=BG, outline=EDGE, width=1,
        )
        canvas.create_rectangle(
            0, CORNER_RADIUS, 7, height - CORNER_RADIUS, fill=ACCENT, outline=""
        )

        # Header row: badge · GAMEGATE · now · ✕
        badge = _badge_photo(px(22))
        header_y = px(V_PAD + 10)
        if badge is not None:
            canvas.create_image(text_x, header_y, image=badge, anchor="w")
            root._badge_ref = badge  # keep a reference or Tk garbage-collects it
            name_x = text_x + px(30)
        else:
            name_x = text_x
        canvas.create_text(
            name_x, header_y, text="GAMEGATE", anchor="w", fill=FG_MUTED,
            font=muted_bold,
        )
        canvas.create_text(
            width - px(44), header_y, text="now", anchor="e", fill=FG_MUTED,
            font=muted_font,
        )
        close = canvas.create_text(
            width - px(24), header_y, text="✕", anchor="center", fill=FG_MUTED,
            font=muted_font,
        )
        canvas.tag_bind(close, "<Button-1>", lambda _e: root.destroy())

        # Title + body (positions from the measured title height).
        title_y = px(V_PAD + HEADER_H)
        canvas.create_text(
            text_x, title_y, text=title, anchor="nw", fill=FG_TITLE,
            font=title_font, width=text_width,
        )
        body_item = canvas.create_text(
            text_x, title_y + title_h + px(6), text=body, anchor="nw", fill=FG_BODY,
            font=body_font, width=text_width,
        )
        # Cap case (live find): when even the max-height card can't fit the
        # text, truncate with an ellipsis ABOVE the countdown lane — text and
        # bar must never collide. Full content always lives in the digest.
        lane_top = height - px(26)
        shown_text = body
        while shown_text and canvas.bbox(body_item)[3] > lane_top:
            cut = max(10, len(shown_text) // 10)
            shown_text = shown_text[: len(shown_text) - cut].rstrip()
            canvas.itemconfigure(body_item, text=shown_text + " …")

        # Countdown line along the bottom — shrinks as time runs out.
        track_x1, track_x2 = text_x, width - px(20)
        track_y = height - px(10)
        bar_w = max(2, px(3))
        canvas.create_line(track_x1, track_y, track_x2, track_y, fill=EDGE, width=bar_w)
        countdown = canvas.create_line(
            track_x1, track_y, track_x2, track_y, fill=ACCENT, width=bar_w
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
        if sound:
            play_sound()
        root.mainloop()
        return True
    except Exception:
        log.exception("Overlay failed")
        return False


def show_update_prompt(change_count: int) -> bool:
    """Public entry: serialize rendering so it never coexists with an overlay."""
    with _ui_lock:
        return _show_update_prompt(change_count)


def _show_update_prompt(change_count: int) -> bool:
    """'Update available' card with real buttons (Jules' spec). Returns True
    for Update now, False for Later/dismiss. Blocking; call from a worker."""
    try:
        import tkinter as tk

        result = {"update": False}
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        scale = max(1.0, root.winfo_fpixels("1i") / 96.0)

        def px(value: float) -> int:
            return int(value * scale)

        width, height = px(360), px(150)
        screen_w = root.winfo_screenwidth()
        root.geometry(f"{width}x{height}+{screen_w - width - px(16)}+{px(16)}")
        root.configure(bg=BG, highlightthickness=1, highlightbackground=EDGE)

        tk.Label(
            root, text="Update available", bg=BG, fg=FG_TITLE,
            font=("Segoe UI", -px(16), "bold"), anchor="w",
        ).pack(fill="x", padx=px(18), pady=(px(16), px(2)))
        tk.Label(
            root,
            text=f"{change_count} new change{'s' if change_count != 1 else ''} ready to install.",
            bg=BG, fg=FG_BODY, font=("Segoe UI", -px(12)), anchor="w",
        ).pack(fill="x", padx=px(18))

        row = tk.Frame(root, bg=BG)
        row.pack(fill="x", padx=px(18), pady=px(14))

        def choose(update: bool) -> None:
            result["update"] = update
            root.destroy()

        update_btn = tk.Button(
            row, text="Update now", command=lambda: choose(True),
            bg=ACCENT, fg="#06130b", activebackground="#4ae584",
            activeforeground="#06130b", relief="flat", bd=0,
            font=("Segoe UI", -px(12), "bold"), padx=px(14), pady=px(6), cursor="hand2",
        )
        update_btn.pack(side="left")
        tk.Button(
            row, text="Later", command=lambda: choose(False),
            bg=BG, fg=FG_MUTED, activebackground=EDGE, activeforeground=FG_BODY,
            relief="flat", bd=0, font=("Segoe UI", -px(12)),
            padx=px(14), pady=px(6), cursor="hand2",
        ).pack(side="left", padx=(px(8), 0))

        root.after(60_000, root.destroy)  # auto-Later after a minute
        play_sound()
        root.mainloop()
        return result["update"]
    except Exception:
        log.exception("Update prompt failed")
        return False


def show_consent_prompt(title: str, message: str,
                        yes_label: str = "Yes", no_label: str = "Not now") -> bool:
    """Public entry: a Yes/No consent card. Serialized like the other prompts."""
    with _ui_lock:
        return _show_consent_prompt(title, message, yes_label, no_label)


def _show_consent_prompt(title: str, message: str, yes_label: str, no_label: str) -> bool:
    """A centered Yes/No dialog (first-run opt-in). Returns True on Yes, False on
    No / dismiss / timeout. Best run on the main thread at startup."""
    try:
        import tkinter as tk

        result = {"yes": False}
        root = tk.Tk()
        # Frameless like the overlay/update cards — no white OS title bar or
        # default Tk icon clashing with the dark theme (owner: "white bar").
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        scale = max(1.0, root.winfo_fpixels("1i") / 96.0)

        def px(value: float) -> int:
            return int(value * scale)

        width, height = px(420), px(250)
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(
            f"{width}x{height}+{(screen_w - width) // 2}+{(screen_h - height) // 3}"
        )
        root.configure(bg=BG, highlightthickness=1, highlightbackground=EDGE)

        tk.Label(root, text=title, bg=BG, fg=FG_TITLE,
                 font=("Segoe UI", -px(16), "bold"), anchor="w",
                 ).pack(fill="x", padx=px(20), pady=(px(18), px(4)))
        tk.Label(root, text=message, bg=BG, fg=FG_BODY, font=("Segoe UI", -px(12)),
                 anchor="w", justify="left", wraplength=width - px(40),
                 ).pack(fill="x", padx=px(20))

        row = tk.Frame(root, bg=BG)
        row.pack(side="bottom", fill="x", padx=px(20), pady=px(16))

        def choose(yes: bool) -> None:
            result["yes"] = yes
            root.destroy()

        tk.Button(row, text=yes_label, command=lambda: choose(True),
                  bg=ACCENT, fg="#06130b", activebackground="#4ae584",
                  activeforeground="#06130b", relief="flat", bd=0,
                  font=("Segoe UI", -px(12), "bold"), padx=px(16), pady=px(6),
                  cursor="hand2").pack(side="right")
        tk.Button(row, text=no_label, command=lambda: choose(False),
                  bg=BG, fg=FG_MUTED, activebackground=EDGE, activeforeground=FG_BODY,
                  relief="flat", bd=0, font=("Segoe UI", -px(12)),
                  padx=px(16), pady=px(6), cursor="hand2").pack(side="right", padx=(0, px(8)))

        root.after(120_000, root.destroy)  # auto-decline after two minutes
        root.after(50, root.focus_force)   # frameless window still grabs focus
        root.mainloop()
        return result["yes"]
    except Exception:
        log.exception("Consent prompt failed")
        return False
