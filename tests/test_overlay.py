"""Overlay notifier — the OS-independent parts."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from overlay import (
    compute_geometry,
    rounded_rect_points,
)
from tray_app import pick_notifier, windows_toast


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
    r = 10
    points = rounded_rect_points(0, 0, 100, 50, r)
    assert len(points) == 24  # 12 (x,y) pairs for the smooth polygon
    xs, ys = points[::2], points[1::2]
    assert max(xs) == 100 and max(ys) == 50
    pts = set(zip(xs, ys, strict=True))
    # The corner points are present as SPLINE control points (Tkinter smooth=True
    # rounds them at render); what actually produces the rounding is the pair of
    # inset points a radius in along each edge from every corner. Assert those
    # exist for all four corners (review NITPICK: rounding was never checked).
    for cx, cy in [(0, 0), (100, 0), (0, 50), (100, 50)]:
        inset_x = (cx + r if cx == 0 else cx - r, cy)   # r in along the top/bottom edge
        inset_y = (cx, cy + r if cy == 0 else cy - r)   # r in along the side edge
        assert inset_x in pts and inset_y in pts


def test_overlay_is_default_notifier():
    from overlay import show_overlay

    assert pick_notifier({}) is show_overlay
    assert pick_notifier({"notifier": "overlay"}) is show_overlay


def test_toast_available_via_config():
    assert pick_notifier({"notifier": "toast"}) is windows_toast
