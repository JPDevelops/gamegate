"""Overlay notifier — the OS-independent parts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from overlay import compute_geometry
from tray_app import pick_notifier, windows_toast


def test_geometry_is_top_right_at_15_percent():
    width, height, x, y = compute_geometry(1920, 1080)
    assert height == int(1080 * 0.15)          # Jules' spec: ~15% of screen
    assert x + width == 1920 - 16              # flush right with margin
    assert y == 16                             # top corner


def test_geometry_scales_with_resolution():
    w1, h1, *_ = compute_geometry(1920, 1080)
    w2, h2, *_ = compute_geometry(3840, 2160)
    assert (w2, h2) == (w1 * 2, h1 * 2)


def test_overlay_is_default_notifier():
    from overlay import show_overlay

    assert pick_notifier({}) is show_overlay
    assert pick_notifier({"notifier": "overlay"}) is show_overlay


def test_toast_available_via_config():
    assert pick_notifier({"notifier": "toast"}) is windows_toast


def test_rounded_rect_points_shape():
    from overlay import rounded_rect_points

    points = rounded_rect_points(0, 0, 100, 50, 10)
    assert len(points) == 24  # 12 (x,y) pairs for the smooth polygon
    assert max(points[::2]) == 100 and max(points[1::2]) == 50
