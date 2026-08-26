"""In-app auto-updater for the downloaded/installed GameGate.exe.

The old updater (update.ps1 / git pull) only works for a source checkout. A
downloaded exe has no git repo, so this checks the GitHub Releases API for a
newer version, downloads the new GameGate.exe, and swaps it in — the way Discord
et al. update themselves.

Self-replace trick: a running .exe can't overwrite itself. So we launch the
freshly-downloaded exe in "--apply-update" mode; it waits for this process to
exit, copies itself over the installed exe, and relaunches it. Per-user install
(%LOCALAPPDATA%\\Programs\\GameGate) means no admin is needed to replace the file.
"""
import contextlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("gamegate.updater")

# Bumped every release; the tag on GitHub (vX.Y.Z) is compared against this.
AGENT_VERSION = "0.5.12"

REPO = "JPDevelops/gamegate"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
_ASSET_NAME = "GameGate.exe"
_UA = {"User-Agent": "GameGate-Updater"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def parse_version(text: str) -> tuple:
    """'v0.3.10' -> (0, 3, 10). Non-numeric parts become 0; missing -> (0,)."""
    core = (text or "").strip().lstrip("vV").split("-")[0].split("+")[0]
    out = []
    for part in core.split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out) or (0,)


def latest_release() -> tuple[str, str] | None:
    """(tag, GameGate.exe download URL) for the newest release, or None."""
    headers = {**_UA, "Accept": "application/vnd.github+json"}
    with urllib.request.urlopen(
        urllib.request.Request(LATEST_API, headers=headers), timeout=15
    ) as resp:
        data = json.loads(resp.read())
    tag = data.get("tag_name") or ""
    url = next(
        (a.get("browser_download_url") for a in data.get("assets", [])
         if a.get("name") == _ASSET_NAME),
        None,
    )
    return (tag, url) if tag and url else None


# How long to wait before retrying the SAME target version after an attempt that
# didn't take. This is the loop-breaker: if a swap fails to "stick" (the relaunch
# is still the old version), we won't immediately try again — at most one attempt
# per version per window, so the app can never enter a restart loop.
_RETRY_COOLDOWN_S = 1800  # 30 minutes


def _marker_path() -> Path:
    base = os.environ.get("LOCALAPPDATA", str(Path.home()))
    return Path(base) / "GameGate" / "update_attempt.json"


def _attempted_recently(tag: str) -> bool:
    try:
        data = json.loads(_marker_path().read_text())
        age = time.time() - float(data.get("at", 0))
        return data.get("tag") == tag and age < _RETRY_COOLDOWN_S
    except Exception:  # noqa: BLE001 — no/absent marker → treat as not attempted
        return False


def _record_attempt(tag: str) -> None:
    with contextlib.suppress(Exception):
        _marker_path().parent.mkdir(parents=True, exist_ok=True)
        _marker_path().write_text(json.dumps({"tag": tag, "at": time.time()}))


def _clear_marker() -> None:
    with contextlib.suppress(Exception):
        _marker_path().unlink(missing_ok=True)


def available_update(current: str = AGENT_VERSION) -> tuple[str, str] | None:
    """Return (tag, url) for a newer release worth OFFERING the user, or None.
    Does NOT download or apply anything — it only checks. Non-frozen runs return
    None (dev uses the git updater). Loop-proof: won't re-offer a tag we recently
    tried that didn't stick (so a bad swap can't spin a re-prompt loop); clears
    the marker when we're already current."""
    if not getattr(sys, "frozen", False):
        return None
    try:
        info = latest_release()
        if not info:
            return None
        tag, url = info
        if parse_version(tag) <= parse_version(current):
            log.info("GameGate is up to date (have %s, latest %s)", current, tag)
            _clear_marker()  # we're current — a prior attempt succeeded/moot
            return None
        if _attempted_recently(tag):
            log.warning(
                "Update to %s was attempted recently but we're still on %s — not "
                "re-offering yet (avoids a restart/re-prompt loop).", tag, current)
            return None
        return tag, url
    except Exception:  # noqa: BLE001 — a check failure must never crash the app
        log.exception("Update check failed")
        return None


def apply_update(tag: str, url: str) -> bool:
    """Download the update and launch the self-replace swap. Records the attempt
    FIRST (loop-proof marker) so a swap that fails to stick can't loop. Returns
    True if the swap was launched (the caller should then quit)."""
    log.info("Applying update -> %s", tag)
    _record_attempt(tag)  # mark BEFORE applying, so a failed swap can't loop
    try:
        return _download_and_launch_swap(url)
    except Exception:  # noqa: BLE001 — an update failure must never crash the app
        log.exception("Update apply failed")
        return False


def check_and_update(current: str = AGENT_VERSION) -> bool:
    """Back-compat: check + auto-apply with no prompt. Returns True if an update
    is being applied (caller should quit). The tray now prompts the user instead
    (available_update + apply_update), but this stays for any silent-update path."""
    info = available_update(current)
    if not info:
        return False
    return apply_update(*info)


def _download_and_launch_swap(url: str) -> bool:
    target = Path(sys.executable)          # the installed GameGate.exe to replace
    tmp = Path(tempfile.mkdtemp(prefix="gg_update_")) / "GameGate-new.exe"
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=300) as resp, open(tmp, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    if tmp.stat().st_size < 1_000_000:     # sanity: a real build is tens of MB
        log.error("Downloaded update is implausibly small (%d bytes); aborting", tmp.stat().st_size)
        return False
    log.info("Update downloaded; launching swap into %s", target)
    subprocess.Popen(
        [str(tmp), "--apply-update", str(target), "--pid", str(os.getpid())],
        creationflags=_NO_WINDOW, close_fds=True,
    )
    return True


def _kill_processes_using(target: str) -> None:
    """Kill any process running the target exe image so the file can be replaced
    (a running .exe is locked on Windows). We run from a DIFFERENT image
    (GameGate-new.exe in temp), so we never kill ourselves."""
    try:
        import psutil
        me = os.getpid()
        target_norm = os.path.normcase(os.path.abspath(target))
        for proc in psutil.process_iter(["pid", "exe"]):
            if proc.info["pid"] == me:
                continue
            exe = proc.info.get("exe")
            if exe and os.path.normcase(os.path.abspath(exe)) == target_norm:
                with contextlib.suppress(Exception):
                    proc.kill()
    except Exception:  # noqa: BLE001 — best effort; the copy retry loop still guards
        log.debug("pre-copy process cleanup skipped", exc_info=True)


def apply_update_mode() -> None:
    """Entry point when GameGate is launched with --apply-update <target>.
    Runs from the freshly-downloaded exe: wait for the old process to exit, copy
    self over the installed exe, relaunch it, then exit."""
    argv = sys.argv
    target = argv[argv.index("--apply-update") + 1]
    time.sleep(2)  # give the old process a moment to fully exit and release the file
    _kill_processes_using(target)  # make sure nothing from the old image locks it
    src = sys.executable
    for _ in range(60):  # up to ~30s, retry while the old exe is still locked
        try:
            shutil.copyfile(src, target)
            break
        except OSError:
            time.sleep(0.5)
    else:
        log.error("Could not replace %s (still locked)", target)
        return
    log.info("Update applied; relaunching %s", target)
    with contextlib.suppress(OSError):
        # --show so the app reopens its WINDOW after updating, instead of quietly
        # relaunching into the tray (which read as "it just closed").
        subprocess.Popen([target, "--show"], creationflags=_NO_WINDOW, close_fds=True)
