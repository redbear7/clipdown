"""
ClipDown Channel Subscriptions
──────────────────────────────
Persistent subscription list + video ledger backed by SQLite.
Extracts video list from a channel URL via yt-dlp (extract_flat=True),
then diffs against seen videos to identify new ones for download.
"""
import os
import re
import sqlite3
import threading
import time
import subprocess
import json
from typing import Optional

DB_LOCK = threading.Lock()


def db_path(root):
    return os.path.join(root, "subs.db")


def get_conn(root):
    """Return a thread-local connection with row_factory set."""
    conn = sqlite3.connect(db_path(root), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(root):
    """Create tables if missing."""
    with get_conn(root) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL UNIQUE,
            name TEXT,
            format TEXT DEFAULT 'video',       -- video | audio
            format_id TEXT,                     -- height (e.g. '1080')
            output_folder TEXT,
            filename_template TEXT,             -- optional
            auto_download INTEGER DEFAULT 1,
            check_interval_minutes INTEGER DEFAULT 60,
            last_checked_at TEXT,
            new_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            avatar_emoji TEXT
        );

        CREATE TABLE IF NOT EXISTS subscription_videos (
            id INTEGER PRIMARY KEY,
            subscription_id INTEGER NOT NULL,
            video_id TEXT NOT NULL,
            video_url TEXT NOT NULL,
            title TEXT,
            duration REAL,
            downloaded INTEGER DEFAULT 0,
            downloaded_at TEXT,
            file_path TEXT,
            seen_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (subscription_id) REFERENCES subscriptions(id) ON DELETE CASCADE,
            UNIQUE(subscription_id, video_id)
        );

        CREATE INDEX IF NOT EXISTS idx_sub_videos_sub ON subscription_videos(subscription_id);
        CREATE INDEX IF NOT EXISTS idx_sub_videos_downloaded ON subscription_videos(downloaded);
        """)
        c.commit()


def as_dict(row):
    return dict(row) if row else None


# ── CRUD ─────────────────────────────────────────────

def list_subs(root):
    with get_conn(root) as c:
        rows = c.execute("SELECT * FROM subscriptions ORDER BY created_at DESC").fetchall()
    return [as_dict(r) for r in rows]


def get_sub(root, sub_id):
    with get_conn(root) as c:
        row = c.execute("SELECT * FROM subscriptions WHERE id=?", (sub_id,)).fetchone()
    return as_dict(row)


def create_sub(root, url, name=None, format="video", format_id=None,
               output_folder=None, filename_template=None,
               auto_download=1, check_interval_minutes=60):
    url = normalize_channel_url(url)
    if not name:
        name = derive_name_from_url(url)
    avatar = name[0].upper() if name else "?"
    with get_conn(root) as c:
        try:
            cur = c.execute("""
                INSERT INTO subscriptions
                (url, name, format, format_id, output_folder, filename_template,
                 auto_download, check_interval_minutes, avatar_emoji)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (url, name, format, format_id, output_folder, filename_template,
                  int(auto_download), int(check_interval_minutes), avatar))
            c.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            row = c.execute("SELECT id FROM subscriptions WHERE url=?", (url,)).fetchone()
            return row["id"] if row else None


def update_sub(root, sub_id, **fields):
    if not fields:
        return
    allowed = {"name", "format", "format_id", "output_folder", "filename_template",
               "auto_download", "check_interval_minutes"}
    sets = []
    values = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            values.append(v)
    if not sets:
        return
    values.append(sub_id)
    with get_conn(root) as c:
        c.execute(f"UPDATE subscriptions SET {','.join(sets)} WHERE id=?", values)
        c.commit()


def delete_sub(root, sub_id):
    with get_conn(root) as c:
        c.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
        c.commit()


# ── Video ledger ─────────────────────────────────────

def get_videos(root, sub_id, limit=50, only_new=False):
    with get_conn(root) as c:
        q = "SELECT * FROM subscription_videos WHERE subscription_id=?"
        params = [sub_id]
        if only_new:
            q += " AND downloaded=0"
        q += " ORDER BY seen_at DESC LIMIT ?"
        params.append(limit)
        return [as_dict(r) for r in c.execute(q, params).fetchall()]


def mark_downloaded(root, video_row_id, file_path):
    with get_conn(root) as c:
        c.execute(
            "UPDATE subscription_videos SET downloaded=1, downloaded_at=datetime('now'), file_path=? WHERE id=?",
            (file_path, video_row_id))
        c.commit()


# ── Channel scanning ─────────────────────────────────

def normalize_channel_url(url):
    """Ensure the URL points to the channel's video listing (yt-dlp friendly)."""
    url = url.strip()
    # YouTube @handles need /videos suffix for yt-dlp to list uploads
    if "youtube.com/@" in url and "/videos" not in url and "/playlist" not in url and "/streams" not in url:
        return url.rstrip("/") + "/videos"
    if "youtube.com/channel/" in url and "/videos" not in url and "/playlist" not in url:
        return url.rstrip("/") + "/videos"
    return url


def derive_name_from_url(url):
    m = re.search(r"@([\w.-]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"channel/([\w-]+)", url)
    if m:
        return "Channel " + m.group(1)[:8]
    try:
        from urllib.parse import urlparse
        return urlparse(url).hostname or "Subscription"
    except Exception:
        return "Subscription"


def scan_channel(url, ytdlp_bin="yt-dlp", max_videos=50, cookies_from_browser=None):
    """Return a list of {video_id, url, title, duration} for videos on the channel.
    Uses --flat-playlist so it's fast (no per-video fetch)."""
    cmd = [
        ytdlp_bin, "--flat-playlist", "--skip-download",
        "--no-warnings", "--playlist-end", str(max_videos),
        "--print", '%(id)s|%(title)s|%(duration)s|%(url)s',
    ]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    cmd.append(normalize_channel_url(url))

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return [], "Channel scan timed out"
    except Exception as e:
        return [], str(e)

    if r.returncode != 0:
        err = (r.stderr or "").strip().split("\n")[-1] if r.stderr else "scan failed"
        return [], err

    results = []
    for line in r.stdout.splitlines():
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        vid, title, dur, vurl = parts
        if not vid:
            continue
        try:
            duration = float(dur) if dur and dur != "NA" else None
        except ValueError:
            duration = None
        results.append({
            "video_id": vid.strip(),
            "title": title.strip(),
            "duration": duration,
            "video_url": vurl.strip(),
        })
    return results, None


def check_subscription(root, sub_id, ytdlp_bin="yt-dlp", cookies_from_browser=None):
    """Scan the channel, insert unseen videos, update last_checked_at + new_count.
    Returns (list_of_new_videos, error_or_None).
    """
    sub = get_sub(root, sub_id)
    if not sub:
        return [], "Subscription not found"

    videos, err = scan_channel(sub["url"], ytdlp_bin=ytdlp_bin,
                                cookies_from_browser=cookies_from_browser)
    if err:
        with get_conn(root) as c:
            c.execute("UPDATE subscriptions SET last_checked_at=datetime('now') WHERE id=?", (sub_id,))
            c.commit()
        return [], err

    new_videos = []
    with get_conn(root) as c:
        for v in videos:
            try:
                cur = c.execute("""
                    INSERT INTO subscription_videos
                    (subscription_id, video_id, video_url, title, duration)
                    VALUES (?,?,?,?,?)
                """, (sub_id, v["video_id"], v["video_url"], v["title"], v["duration"]))
                v["id"] = cur.lastrowid
                new_videos.append(v)
            except sqlite3.IntegrityError:
                # Already seen — skip
                pass

        # Count still-not-downloaded new videos
        cnt = c.execute(
            "SELECT COUNT(*) FROM subscription_videos WHERE subscription_id=? AND downloaded=0",
            (sub_id,)
        ).fetchone()[0]
        c.execute(
            "UPDATE subscriptions SET last_checked_at=datetime('now'), new_count=? WHERE id=?",
            (cnt, sub_id)
        )
        c.commit()

    return new_videos, None


def check_all(root, ytdlp_bin="yt-dlp", cookies_from_browser=None):
    """Run check_subscription for every active subscription. Returns summary dict."""
    subs = list_subs(root)
    summary = {"checked": 0, "new_total": 0, "errors": []}
    for s in subs:
        new_vids, err = check_subscription(root, s["id"], ytdlp_bin, cookies_from_browser)
        summary["checked"] += 1
        if err:
            summary["errors"].append({"sub_id": s["id"], "name": s["name"], "error": err})
        else:
            summary["new_total"] += len(new_vids)
    return summary
