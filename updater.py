"""
ClipDown updater — checks and applies updates for:
  1. yt-dlp binary (critical: keeps YouTube working)
  2. App version (informational)
"""
import os
import sys
import json
import shutil
import platform
import subprocess
import tempfile
import urllib.request

APP_VERSION = "0.2.0"

_YTDLP_LATEST_URL = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
_HTTP_TIMEOUT = 15


# ── yt-dlp version detection ────────────────────────────────────

def get_ytdlp_current_version(ytdlp_bin):
    """Return current yt-dlp version string (e.g. '2024.12.13') or None."""
    try:
        r = subprocess.run([ytdlp_bin, "--version"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None
    except Exception:
        return None


def _fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "ClipDown-Updater"})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return json.load(resp)


def get_ytdlp_latest_info():
    """Fetch latest yt-dlp release metadata from GitHub."""
    try:
        data = _fetch_json(_YTDLP_LATEST_URL)
        return {
            "version": (data.get("tag_name") or "").lstrip("v"),
            "assets": data.get("assets") or [],
            "published_at": data.get("published_at"),
            "html_url": data.get("html_url"),
        }
    except Exception:
        return None


def compare_ytdlp(current, latest):
    """Returns True if `latest` is newer than `current`. Both are date-like strings."""
    if not current or not latest:
        return False
    if current == latest:
        return False
    # yt-dlp versions are YYYY.MM.DD; string compare works.
    return latest > current


# ── yt-dlp binary update ────────────────────────────────────────

def _detect_asset_name():
    """Choose the correct yt-dlp binary for the current OS/arch."""
    s = platform.system()
    if s == "Windows":
        # 64-bit binary requires UCRT (Win 10+); 32-bit is broader compat but slower.
        return "yt-dlp.exe"
    if s == "Darwin":
        return "yt-dlp_macos"
    return "yt-dlp"  # Linux and everything else


def update_ytdlp(ytdlp_bin):
    """Download latest yt-dlp and replace the current binary.
    Returns dict with 'ok' or 'error'."""
    latest = get_ytdlp_latest_info()
    if not latest or not latest.get("version"):
        return {"error": "최신 버전 정보를 가져오지 못했습니다."}

    asset_name = _detect_asset_name()
    asset_url = None
    for a in latest["assets"]:
        if a.get("name") == asset_name:
            asset_url = a.get("browser_download_url")
            break
    if not asset_url:
        return {"error": f"이 시스템용 파일({asset_name})을 찾지 못했습니다."}

    # Bundled yt-dlp path may be inside the app bundle (read-only).
    # We only proceed if the target is writable.
    target = os.path.realpath(ytdlp_bin)
    target_dir = os.path.dirname(target)
    if not os.access(target_dir, os.W_OK):
        return {"error": f"쓰기 권한이 없어 업데이트할 수 없습니다: {target_dir}"}

    # Download to temp, then swap.
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="yt-dlp.new-", dir=target_dir)
    os.close(tmp_fd)
    try:
        req = urllib.request.Request(asset_url,
                                     headers={"User-Agent": "ClipDown-Updater"})
        with urllib.request.urlopen(req, timeout=120) as resp, \
             open(tmp_path, "wb") as out:
            shutil.copyfileobj(resp, out)

        # Backup previous binary (best-effort)
        if os.path.exists(target):
            try:
                shutil.copy2(target, target + ".bak")
            except Exception:
                pass

        # Atomic replace + make executable
        os.replace(tmp_path, target)
        try:
            os.chmod(target, 0o755)
        except Exception:
            pass

        return {"ok": True, "version": latest["version"]}
    except Exception as e:
        # Clean up temp on failure
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return {"error": str(e)}


# ── Combined status ─────────────────────────────────────────────

def get_status(ytdlp_bin):
    current = get_ytdlp_current_version(ytdlp_bin)
    latest_info = get_ytdlp_latest_info()
    latest = latest_info.get("version") if latest_info else None
    return {
        "app": {
            "version": APP_VERSION,
        },
        "ytdlp": {
            "current": current,
            "latest": latest,
            "update_available": compare_ytdlp(current, latest),
            "released_at": latest_info.get("published_at") if latest_info else None,
        },
    }
