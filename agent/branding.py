"""GameGate visual identity: the tray badge and the .exe icon.

Everything is rendered supersampled (4x) and downscaled with Lanczos so it
stays crisp at any tray/taskbar size — the fix for the 'not HD' tray dot.
Design: dark rounded badge, purple portal ring, state-colored core.
"""

SUPERSAMPLE = 4
BASE = 64  # logical size; ICO gets multiple sizes

BADGE_BG = (16, 19, 18, 255)
RING = (46, 224, 111, 255)          # Guardian signal green
STATE_COLORS = {
    "available": (140, 148, 143, 255),   # neutral: nothing to guard
    "gaming": (46, 224, 111, 255),       # green: the gate is protecting you
    "focused": (229, 72, 77, 255),       # red: do not disturb
}


def render_badge(state: str = "available", size: int = BASE):
    """Tray badge as a PIL Image. Supersampled for crisp edges."""
    from PIL import Image, ImageDraw

    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    m = big // 16                       # outer margin
    draw.rounded_rectangle(
        (m, m, big - m, big - m), radius=big // 4, fill=BADGE_BG
    )
    ring_w = big // 10
    r1 = big // 4
    draw.ellipse(
        (r1, r1, big - r1, big - r1), outline=RING, width=ring_w
    )
    core = STATE_COLORS.get(state, STATE_COLORS["available"])
    r2 = big * 41 // 100
    draw.ellipse((r2, r2, big - r2, big - r2), fill=core)

    return image.resize((size, size), Image.LANCZOS)


def write_ico(path: str = "gamegate.ico") -> str:
    """Multi-size .ico for the exe (PyInstaller --icon)."""
    image = render_badge("available", 256)
    image.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return path


if __name__ == "__main__":
    print("wrote", write_ico())
