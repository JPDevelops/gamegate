"""Silence the native pop-up banner for the messaging apps GameGate surfaces, so
the user isn't pinged twice — once by the app, once by GameGate.

The key fact (verified): setting the per-app `ShowBanner=0` DWORD hides the toast
banner, but the notification is STILL filed in the Windows notification center —
i.e. still written to wpndatabase.db, which is exactly where GameGate reads from.
So muting the banner does NOT blind GameGate: it keeps capturing, and it becomes
the single surface for those apps.

Per-user registry (no admin needed):
  HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Notifications\\Settings\\<AUMID>

Everything here is win32-guarded: off Windows every entry point is a no-op that
returns [], so the server package stays import-safe and unit-testable on any OS.
Only ShowBanner is touched (0 = muted, 1 = restored) — we never disable the app
or its Action Center entry, so the change is minimal and fully reversible.
"""
import logging
import sys

log = logging.getLogger("gamegate.banners")

_SETTINGS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"

# When silencing, we mute EVERY app's banner (so nothing pops — the user asked
# for zero pop-ups, incl. phone-mirrored apps like Blink that come via Phone
# Link) except GameGate's own, so its overlay/toasts are never muted by itself.
_NEVER_MUTE = ("gamegate",)


def should_mute(aumid: str) -> bool:
    low = (aumid or "").lower()
    return bool(low) and not any(marker in low for marker in _NEVER_MUTE)


def _iter_registered_apps():
    """AUMIDs that have a notification-settings entry (i.e. have notified before)."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _SETTINGS_KEY) as key:
        index = 0
        while True:
            try:
                yield winreg.EnumKey(key, index)
            except OSError:
                return
            index += 1


def _set_banner(aumid: str, show: bool) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _SETTINGS_KEY + "\\" + aumid) as sub:
        winreg.SetValueEx(sub, "ShowBanner", 0, winreg.REG_DWORD, 1 if show else 0)


def apply(enabled: bool) -> list[str]:
    """Mute (enabled=True) or restore (False) the pop-up banner for EVERY
    registered app (except GameGate) — so the user gets zero native pop-ups and
    GameGate is the single surface. Returns the AUMIDs touched. No-op returning
    [] off Windows or if the registry can't be read/written. NOTE: Windows only
    reloads notification settings at sign-in, so a change takes effect after the
    next restart (and a brand-new app that first pops mid-session is caught on
    the following restart)."""
    if sys.platform != "win32":
        return []
    try:
        apps = [a for a in _iter_registered_apps() if should_mute(a)]
    except OSError as exc:  # key missing / access — nothing to do
        log.warning("Couldn't enumerate notification apps: %s", exc)
        return []
    touched: list[str] = []
    for aumid in apps:
        try:
            _set_banner(aumid, show=not enabled)
            touched.append(aumid)
        except OSError as exc:
            log.warning("Couldn't set banner for %s: %s", aumid, exc)
    log.info("Source-banner suppression %s for %d app(s): %s",
             "ON" if enabled else "off", len(touched), ", ".join(touched) or "(none)")
    return touched
