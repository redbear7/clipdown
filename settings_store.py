"""
ClipDown user settings persisted to settings.json in the app root.
Public API:
  load(root)         → dict
  save(root, data)   → writes atomically
  update(root, **kw) → merges + saves
  render_filename(template, ctx, sanitize=True, max_bytes=200) → str
"""
import os
import json
import re
import time
import threading
from typing import Any

_SETTINGS_LOCK = threading.Lock()

DEFAULTS = {
    "filename_template": "{date}_{title}",
    "download_folder": "",              # informational (browser handles actual save)
    "sanitize_filename": True,
    "duplicate_handling": "suffix",     # skip | suffix | overwrite
    "auto_organize_by_source": False,   # if True, prefix {site}/ automatically
    "id3_tags": True,
    "default_format": "video",          # video | audio
    "default_quality": "",              # "" (best) or "1080", "720", etc.
    "id3_album": "",                    # optional album override for MP3
    "id3_artist": "",                   # optional artist override
}

# Illegal filesystem chars (macOS + Windows compatible)
_ILLEGAL_RE = re.compile(r'[\\/:*?"<>|]')
_WHITESPACE_RE = re.compile(r'\s+')


def _settings_path(root):
    return os.path.join(root, "settings.json")


def load(root):
    """Load settings from disk, merging with defaults for missing keys."""
    path = _settings_path(root)
    data = dict(DEFAULTS)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data.update(loaded)
        except Exception:
            pass
    return data


def save(root, data):
    """Atomically write settings.json (write to tmp then rename)."""
    if not isinstance(data, dict):
        raise ValueError("settings must be dict")
    path = _settings_path(root)
    tmp = path + ".tmp"
    with _SETTINGS_LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    return data


def update(root, patch):
    """Merge patch into settings and save. Returns new dict."""
    cur = load(root)
    for k, v in (patch or {}).items():
        if k in DEFAULTS:  # only allow known keys
            cur[k] = v
    return save(root, cur)


def _sanitize(name):
    if not name:
        return ""
    name = _ILLEGAL_RE.sub("", name)
    name = _WHITESPACE_RE.sub(" ", name).strip()
    return name


def _truncate_bytes(s, max_bytes):
    b = s.encode("utf-8")
    if len(b) <= max_bytes:
        return s
    return b[:max_bytes].decode("utf-8", errors="ignore").rstrip()


def _fmt_date():
    return time.strftime("%y%m%d")


def _detect_site(url):
    if not url:
        return ""
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    if "instagram.com" in u:
        return "Instagram"
    if "tiktok.com" in u:
        return "TikTok"
    if "twitter.com" in u or "x.com" in u:
        return "Twitter"
    if "reddit.com" in u:
        return "Reddit"
    if "facebook.com" in u or "fb.watch" in u:
        return "Facebook"
    if "vimeo.com" in u:
        return "Vimeo"
    if "twitch.tv" in u:
        return "Twitch"
    if "soundcloud.com" in u:
        return "SoundCloud"
    return ""


# Available variables shown in UI help + used for validation
TEMPLATE_VARS = [
    "{title}", "{channel}", "{date}", "{ext}", "{site}",
    "{resolution}", "{duration}", "{index}",
]


def render_filename(template, ctx, sanitize=True, max_bytes=200,
                    auto_organize=False):
    """
    Render a filename template with the given context. Non-string values
    are stringified. Unknown variables become empty. Path separators from
    template variables (in title etc.) are sanitized if requested.

    ctx keys:
        title (str), channel (str), ext (str), url (str), site (str),
        resolution (str/int), duration (int), index (int)
    """
    # Build safe substitution map
    site = ctx.get("site") or _detect_site(ctx.get("url", ""))
    values = {
        "title":      _sanitize(str(ctx.get("title") or "")) if sanitize else str(ctx.get("title") or ""),
        "channel":    _sanitize(str(ctx.get("channel") or "")) if sanitize else str(ctx.get("channel") or ""),
        "date":       ctx.get("date") or _fmt_date(),
        "ext":        str(ctx.get("ext") or "").lstrip("."),
        "site":       site,
        "resolution": str(ctx.get("resolution") or ""),
        "duration":   str(ctx.get("duration") or ""),
        "index":      f"{int(ctx.get('index') or 0):03d}" if ctx.get("index") is not None else "",
    }

    # Simple {key} substitution (also supports {key:03d} for index)
    def replace(m):
        expr = m.group(1)
        # Handle format specs like index:03d
        if ":" in expr:
            key, spec = expr.split(":", 1)
        else:
            key, spec = expr, None
        v = values.get(key, "")
        if spec and key == "index":
            try:
                return f"{int(ctx.get('index') or 0):{spec}}"
            except Exception:
                return v
        return v

    out = re.sub(r"\{([^\}]+)\}", replace, template or "")

    if auto_organize and site:
        out = f"{site}/{out}"

    # Ensure single .ext at end; if template didn't include one and ctx has ext
    if not out.endswith("." + values["ext"]) and values["ext"]:
        out = f"{out}.{values['ext']}"

    # Collapse repeated separators like "_ _" or "//" but keep folder slashes
    out = re.sub(r"[ ]{2,}", " ", out)
    out = re.sub(r"[_]{2,}", "_", out)
    out = out.strip(" -_.")

    # Truncate final basename to max_bytes (keep extension)
    dot = out.rfind(".")
    if dot > 0 and len(out) - dot <= 6:  # short extension
        base, ext = out[:dot], out[dot:]
        base = _truncate_bytes(base, max_bytes - len(ext))
        out = base + ext
    else:
        out = _truncate_bytes(out, max_bytes)

    return out or "download"
