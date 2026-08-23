"""Overlay notifier — the OS-independent parts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from overlay import (
    MIN_HEIGHT,
    compute_card_height,
    compute_geometry,
    rounded_rect_points,
)
from tray_app import pick_notifier, windows_toast


def test_card_hugs_content_but_caps_at_15_percent():
    short = compute_card_height(body_lines=1, screen_h=1080)
    tall = compute_card_height(body_lines=50, screen_h=1080)
    assert short >= MIN_HEIGHT
    assert short < tall
    assert tall == int(1080 * 0.15)  # Jules' 15% is the MAX, not the fixed size


def test_geometry_is_top_right():
    width, height, x, y = compute_geometry(1920, 1080, height=120)
    assert height == 120
    assert x + width == 1920 - 16  # flush right with margin
    assert y == 16                 # top corner


def test_geometry_scales_with_resolution():
    w1, *_ = compute_geometry(1920, 1080)
    w2, *_ = compute_geometry(3840, 2160)
    assert w2 == w1 * 2


def test_rounded_rect_points_shape():
    points = rounded_rect_points(0, 0, 100, 50, 10)
    assert len(points) == 24  # 12 (x,y) pairs for the smooth polygon
    assert max(points[::2]) == 100 and max(points[1::2]) == 50


def test_overlay_is_default_notifier():
    from overlay import show_overlay

    assert pick_notifier({}) is show_overlay
    assert pick_notifier({"notifier": "overlay"}) is show_overlay


def test_toast_available_via_config():
    assert pick_notifier({"notifier": "toast"}) is windows_toast
