import os
import re
import sys
import uuid
import glob
import json
import time
import shutil
import secrets
import subprocess
import threading
from functools import wraps
from flask import Flask, request, jsonify, send_file, render_template, Response

import subscriptions as subs_mod
import settings_store
import history
import updater

# Detect bundled mode (PyInstaller) vs source mode
def _resource_root():
    """Return the directory that contains bundled resources/binaries."""
    if getattr(sys, "frozen", False):  # PyInstaller bundle
        p = os.path.dirname(sys.executable)
    else:
        p = os.path.dirname(os.path.abspath(__file__))
    # Tauri on Windows hands us the extended-length path form (\\?\C:\...).
    # Flask/Jinja join it with "templates" and the resulting path fails to
    # resolve — strip the prefix so downstream file ops behave normally.
    if p.startswith("\\\\?\\"):
        p = p[4:]
    return p


def _resolve_binary(name):
    """Find a binary either in bundled folder, PATH, or fallback to name."""
    root = _resource_root()
    # On Windows, .exe extension
    if os.name == "nt" and not name.endswith(".exe"):
        candidates = [name + ".exe", name]
    else:
        candidates = [name]

    for cand in candidates:
        bundled = os.path.join(root, cand)
        if os.path.isfile(bundled):
            return bundled

    found = shutil.which(name)
    return found or name


YTDLP_BIN = _resolve_binary("yt-dlp")
FFMPEG_BIN = _resolve_binary("ffmpeg")
FFPROBE_BIN = _resolve_binary("ffprobe")

# Pin templates/static to the resolved resource root so Tauri's
# extended-length launch path can't confuse Flask's default discovery.
_ROOT = _resource_root()
app = Flask(
    __name__,
    template_folder=os.path.join(_ROOT, "templates"),
    static_folder=os.path.join(_ROOT, "static"),
)


# Expose real exception details on Windows/mac desktop app so a 500 in the
# webview tells us WHAT failed instead of the generic Flask error page.
@app.errorhandler(Exception)
def _debug_error_handler(exc):
    import traceback as _tb
    tb = _tb.format_exc()
    # Log to stderr so it also lands in server.err.log
    sys.stderr.write(tb + "\n"); sys.stderr.flush()
    body = (
        "<html><body style='font-family:monospace;background:#fff;color:#222;padding:24px'>"
        "<h2 style='color:#b34e21'>ClipDown 서버 오류</h2>"
        f"<p><b>{type(exc).__name__}:</b> {exc}</p>"
        f"<pre style='background:#f8f4ec;border:1px solid #e5ddc8;padding:16px;overflow:auto'>{tb}</pre>"
        f"<p>리소스 루트: <code>{_resource_root()}</code></p>"
        f"<p>다운로드 폴더: <code>{DOWNLOAD_DIR}</code></p>"
        "</body></html>"
    )
    return body, 500


DOWNLOAD_DIR = os.path.join(_resource_root(), "downloads")
try:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
except Exception as _e:
    sys.stderr.write(f"[startup] makedirs {DOWNLOAD_DIR} failed: {_e}\n")

# === Authentication (optional) ===
# Set CLIPDOWN_PASSWORD env var or write to config.json to enable
def _load_auth():
    """Load auth credentials from env or config.json. Returns (user, password) or None."""
    pwd = os.environ.get("CLIPDOWN_PASSWORD", "").strip()
    user = os.environ.get("CLIPDOWN_USER", "clipdown").strip()
    if not pwd:
        cfg_path = os.path.join(_resource_root(), "config.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                pwd = (cfg.get("password") or "").strip()
                user = (cfg.get("username") or user).strip()
            except Exception:
                pass
    return (user, pwd) if pwd else None


AUTH_CREDS = _load_auth()


def check_auth(auth):
    """Verify HTTP Basic Auth credentials."""
    if not AUTH_CREDS:
        return True  # Auth disabled
    if not auth:
        return False
    return secrets.compare_digest(auth.username, AUTH_CREDS[0]) and \
           secrets.compare_digest(auth.password, AUTH_CREDS[1])


def _is_localhost():
    """Detect if the request originates from the same machine."""
    addr = request.remote_addr or ""
    return addr in ("127.0.0.1", "::1", "localhost")


def auth_required(view):
    """Require HTTP Basic Auth when AUTH_CREDS is set, EXCEPT for localhost."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        # Localhost (extension, web UI on same machine) bypasses auth
        if AUTH_CREDS and not _is_localhost() and not check_auth(request.authorization):
            return Response(
                "Authentication required",
                401,
                {"WWW-Authenticate": 'Basic realm="ClipDown"'},
            )
        return view(*args, **kwargs)
    return wrapped


jobs = {}
info_cache = {}  # url -> (timestamp, data)
INFO_CACHE_TTL = 600  # 10 minutes


def log(job, msg):
    """Append a timestamped log entry to the job."""
    ts = time.strftime("%H:%M:%S")
    job.setdefault("logs", []).append(f"[{ts}] {msg}")


def parse_ytdlp_progress(line):
    """Extract percentage and speed from yt-dlp output line."""
    m = re.search(r"\[download\]\s+([\d.]+)%\s+of\s+~?\s*([\d.]+\w+)\s+at\s+([\d.]+\w+/s|Unknown)", line)
    if m:
        return {"percent": float(m.group(1)), "size": m.group(2), "speed": m.group(3)}
    m = re.search(r"\[download\]\s+([\d.]+)%", line)
    if m:
        return {"percent": float(m.group(1))}
    return None


def parse_ffmpeg_progress(line, total_duration):
    """Extract encoding progress from ffmpeg stderr."""
    m = re.search(r"time=(\d+):(\d+):([\d.]+)", line)
    if m and total_duration:
        secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        return round(min(secs / total_duration * 100, 100), 1)
    return None


def get_duration(filepath):
    """Get video duration in seconds via ffprobe."""
    try:
        r = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", filepath],
            capture_output=True, text=True, timeout=10
        )
        return float(r.stdout.strip())
    except Exception:
        return None


def run_download(job_id, url, format_choice, format_id, force_h264=False,
                 fast_mode=False, start_time=None, end_time=None, sections=None,
                 subs=False):
    job = jobs[job_id]
    job["logs"] = []
    job["progress"] = 0
    job["phase"] = "starting"
    out_template = os.path.join(DOWNLOAD_DIR, f"{job_id}.%(ext)s")

    is_youtube = "youtube.com" in url or "youtu.be" in url
    cmd = [YTDLP_BIN, "--no-playlist", "--newline", "--progress",
           "--concurrent-fragments", "16",  # more aggressive parallel chunks
           "--write-info-json",  # save metadata for accurate title
           "--no-check-certificates",  # skip cert validation
           "--no-mtime",  # skip setting file mtime
           "--no-call-home",  # skip telemetry call
           "-o", out_template]

    # Normalize legacy single-segment (start_time/end_time) into sections list.
    # sections is a list of {"start": str, "end": str}; when multiple are given
    # yt-dlp downloads each and ffmpeg-concats them into one output file.
    if not sections and (start_time or end_time):
        sections = [{"start": start_time or "0", "end": end_time or "inf"}]

    if sections:
        for seg in sections:
            s = str(seg.get("start") or "0").strip()
            e = str(seg.get("end") or "inf").strip()
            cmd += ["--download-sections", f"*{s}-{e}"]
        cmd += ["--force-keyframes-at-cuts"]
        log(job, f"Trimming to {len(sections)} segment(s): "
                 + ", ".join(f"{seg.get('start','0')}-{seg.get('end','inf')}" for seg in sections))

    # Fast mode: skip cookies (works for public content).
    # Normal mode: use cookies for private/age-restricted content.
    # Prefer Edge on Windows — Chrome's cookie DB is almost always locked while
    # Chrome is running, whereas Edge is usually idle for users on other browsers.
    cookie_browser = None
    if not fast_mode:
        cookie_browser = "edge" if os.name == "nt" else "chrome"
        cmd += ["--cookies-from-browser", cookie_browser]

    # JS challenge solver only for YouTube.
    # Official yt-dlp.exe on Windows ships with ejs bundled — no remote fetch
    # needed (and the fetch was failing on Windows behind restrictive networks,
    # producing "Unsupported url scheme: ejs" at extract time).
    # On macOS / Linux we still enable the github source so Homebrew installs work.
    if is_youtube:
        if os.name != "nt":
            cmd += ["--remote-components", "ejs:github"]
        # Point yt-dlp at the bundled Deno explicitly on Windows so PATH quirks
        # can't hide it.
        if os.name == "nt":
            bundled_deno = os.path.join(_resource_root(), "bin", "deno.exe")
            if os.path.isfile(bundled_deno):
                cmd += ["--js-runtimes", f"deno:{bundled_deno}"]
        log(job, f"Fast mode: {fast_mode}")

    # Subtitles — download ko + en tracks, embed into MP4 (video mode only).
    # For audio-only, subtitles don't make sense; skip.
    if subs and format_choice != "audio":
        cmd += ["--write-subs", "--write-auto-subs",
                "--sub-langs", "ko.*,en.*",
                "--embed-subs",
                "--convert-subs", "srt"]
        log(job, "Subtitles: ko + en")

    if format_choice == "audio":
        cmd += ["-x", "--audio-format", "mp3"]
        log(job, "Format: MP3 (audio)")
    else:
        # Prefer h264+aac (universal compat, no re-encode needed)
        # Fallback to any codec if h264 not available at requested resolution
        if format_id:
            fmt = (
                f"bv*[height<={format_id}][vcodec*=avc1]+ba[acodec*=mp4a]"
                f"/bv*[height<={format_id}][vcodec*=avc1]+ba"
                f"/bv*[height<={format_id}]+ba"
                f"/b[height<={format_id}]/b"
            )
            cmd += ["-f", fmt]
            log(job, f"Format: MP4 (video, max {format_id}p, h264 preferred)")
        else:
            fmt = "bv*[vcodec*=avc1]+ba[acodec*=mp4a]/bv*[vcodec*=avc1]+ba/bv+ba/b"
            cmd += ["-f", fmt]
            log(job, "Format: MP4 (video, h264 preferred)")
        cmd += ["--merge-output-format", "mp4"]

    cmd.append(url)
    log(job, f"Starting download...")
    job["phase"] = "downloading"

    try:
        cookie_copy_failed = False
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        stream_idx = 0
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            if cookie_browser and "Could not copy" in line and "cookie" in line.lower():
                cookie_copy_failed = True

            # Track progress
            prog = parse_ytdlp_progress(line)
            if prog:
                pct = prog["percent"]
                speed = prog.get("speed", "")
                size = prog.get("size", "")
                job["progress"] = round(pct, 1)
                if speed and speed != "Unknown":
                    job["speed"] = speed
                if pct % 20 < 1 or pct >= 99:
                    detail = f" ({size}, {speed})" if size and speed and speed != "Unknown" else ""
                    log(job, f"Downloading... {pct:.1f}%{detail}")
            elif "[download] Destination:" in line:
                stream_idx += 1
                fname = line.split("Destination:")[-1].strip()
                short = os.path.basename(fname)
                label = "video" if stream_idx == 1 else "audio" if stream_idx == 2 else f"stream {stream_idx}"
                log(job, f"Downloading {label}: {short}")
            elif "[Merger]" in line:
                log(job, "Merging video + audio...")
                job["phase"] = "merging"
                job["progress"] = 100
            elif "[ExtractAudio]" in line:
                log(job, "Extracting audio...")
            elif "has already been downloaded" in line:
                log(job, "File already cached")
            elif line.startswith("ERROR"):
                log(job, f"Error: {line}")

        proc.wait(timeout=600)

        # Cookie DB was locked (browser was running) — retry once without cookies.
        # Works for public content; private/age-restricted videos would still need
        # the user to close the browser or use a cookies.txt file.
        if proc.returncode != 0 and cookie_copy_failed and cookie_browser:
            log(job, f"'{cookie_browser}' 브라우저 쿠키 복사 실패 — 쿠키 없이 재시도합니다")
            job["progress"] = 0
            retry_cmd = [a for a in cmd if a not in (cookie_browser,)]
            retry_cmd = [a for i, a in enumerate(retry_cmd)
                         if not (a == "--cookies-from-browser"
                                 or (i > 0 and retry_cmd[i-1] == "--cookies-from-browser"))]
            proc = subprocess.Popen(retry_cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
            stream_idx = 0
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                prog = parse_ytdlp_progress(line)
                if prog:
                    job["progress"] = round(prog["percent"], 1)
                    if prog.get("speed"): job["speed"] = prog["speed"]
                elif line.startswith("ERROR"):
                    log(job, f"Error: {line}")
            proc.wait(timeout=600)

        if proc.returncode != 0:
            job["status"] = "error"
            job["error"] = job["logs"][-1] if job["logs"] else "Download failed"
            log(job, "Download failed")
            return

        log(job, "Download complete")

        files = glob.glob(os.path.join(DOWNLOAD_DIR, f"{job_id}.*"))
        if not files:
            job["status"] = "error"
            job["error"] = "Download completed but no file was found"
            log(job, "Error: no output file found")
            return

        # Read title from info.json BEFORE cleanup deletes it
        title_from_meta = ""
        meta_data = {}
        info_json_path = os.path.join(DOWNLOAD_DIR, f"{job_id}.info.json")
        if os.path.exists(info_json_path):
            try:
                with open(info_json_path, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
                    title_from_meta = (meta_data.get("title") or "").strip()
            except Exception as e:
                log(job, f"Warning: could not read info.json: {e}")
        job["title_from_meta"] = title_from_meta
        job["meta_json"] = meta_data

        if format_choice == "audio":
            target = [f for f in files if f.endswith(".mp3")]
            chosen = target[0] if target else files[0]
        else:
            target = [f for f in files if f.endswith(".mp4")]
            chosen = target[0] if target else files[0]

        for f in files:
            if f != chosen:
                try:
                    os.remove(f)
                except OSError:
                    pass

        fsize = os.path.getsize(chosen)
        log(job, f"File size: {fsize / 1024 / 1024:.1f} MB")

        # Probe codec for MP4s — auto re-encode incompatible codecs (VP9, AV1)
        # to H264 so files play natively on macOS/iOS QuickTime.
        UNPLAYABLE_CODECS = {"vp9", "vp8", "av1"}
        needs_reencode = False
        vcodec = ""
        if format_choice != "audio":
            probe = subprocess.run(
                [FFPROBE_BIN, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name", "-of", "csv=p=0", chosen],
                capture_output=True, text=True, timeout=10
            )
            vcodec = probe.stdout.strip()
            log(job, f"Codec: {vcodec}")
            needs_reencode = force_h264 or (vcodec in UNPLAYABLE_CODECS)

        if needs_reencode:
            job["phase"] = "re-encoding"
            job["progress"] = 0
            reason = "forced" if force_h264 else "unplayable codec"
            log(job, f"Re-encoding {vcodec} → h264 ({reason})...")

            total_dur = get_duration(chosen)
            recoded = chosen.replace(f"{job_id}.", f"{job_id}_h264.")

            recode_cmd = [
                FFMPEG_BIN, "-y", "-i", chosen,
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", recoded
            ]

            rproc = subprocess.Popen(recode_cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1)
            for rline in rproc.stdout:
                pct = parse_ffmpeg_progress(rline, total_dur)
                if pct is not None:
                    job["progress"] = pct
                    if int(pct) % 20 == 0 and int(pct) > 0:
                        log(job, f"Re-encoding... {pct:.0f}%")
                sm = re.search(r"speed=([\d.]+)x", rline)
                if sm:
                    job["speed"] = f"{sm.group(1)}x"

            rproc.wait(timeout=600)

            if rproc.returncode == 0 and os.path.exists(recoded):
                os.remove(chosen)
                os.rename(recoded, chosen)
                new_size = os.path.getsize(chosen)
                log(job, f"Re-encoding complete ({new_size / 1024 / 1024:.1f} MB)")
            else:
                log(job, "Re-encoding failed, keeping original file")

        job["file"] = chosen
        ext = os.path.splitext(chosen)[1]

        # Title from yt-dlp's info.json (read earlier, before cleanup)
        title = job.get("title_from_meta", "").strip()
        if not title:
            title = job.get("title", "").strip()

        # Render filename using the user's template settings
        try:
            _settings = settings_store.load(_resource_root())
        except Exception:
            _settings = settings_store.DEFAULTS

        template = _settings.get("filename_template") or settings_store.DEFAULTS["filename_template"]
        sanitize = bool(_settings.get("sanitize_filename", True))
        auto_org = bool(_settings.get("auto_organize_by_source", False))

        # Extract additional context from info.json (if we still have it in job)
        meta = job.get("meta_json") or {}
        ctx = {
            "title": title,
            "channel": meta.get("uploader") or meta.get("channel") or "",
            "ext": ext.lstrip("."),
            "url": url,
            "resolution": meta.get("height") or format_id or "",
            "duration": meta.get("duration"),
        }
        try:
            job["filename"] = settings_store.render_filename(
                template, ctx, sanitize=sanitize, auto_organize=auto_org
            )
        except Exception as e:
            log(job, f"Template render failed ({e}), using fallback")
            fallback = title or os.path.splitext(os.path.basename(chosen))[0]
            job["filename"] = (fallback + ext) if fallback else os.path.basename(chosen)
        # Generate a one-time download token (mobile browsers can use ?token=...)
        job["download_token"] = secrets.token_urlsafe(16)

        log(job, f"Done → {job['filename']}")

        # Persist to history (best-effort, must not break the job on failure)
        try:
            file_size = os.path.getsize(chosen) if os.path.exists(chosen) else None
            site = ""
            if url:
                low = url.lower()
                if "youtube.com" in low or "youtu.be" in low: site = "YouTube"
                elif "instagram.com" in low: site = "Instagram"
                elif "tiktok.com" in low: site = "TikTok"
                elif "twitter.com" in low or "x.com" in low: site = "Twitter"
                elif "vimeo.com" in low: site = "Vimeo"
            hid = history.add(
                _resource_root(),
                url=url,
                title=title or job.get("filename"),
                format=format_choice,
                format_id=format_id,
                filename=job["filename"],
                file_path=chosen,
                start_time=job.get("start_time"),
                end_time=job.get("end_time"),
                file_size=file_size,
                source_site=site,
            )
            job["history_id"] = hid
        except Exception as e:
            log(job, f"History save skipped: {e}")

        # Set "done" status LAST, after filename is set
        job["phase"] = "done"
        job["progress"] = 100
        job["status"] = "done"

    except subprocess.TimeoutExpired:
        job["status"] = "error"
        job["error"] = "Download timed out (10 min limit)"
        log(job, "Error: timed out")
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        log(job, f"Error: {e}")


@app.route("/")
@auth_required
def index():
    return render_template("index.html")


@app.route("/simple")
@auth_required
def simple_index():
    """Simplified single-screen UI for beginners."""
    return render_template("simple.html")


@app.route("/api/info", methods=["POST"])
@auth_required
def get_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # Cache lookup
    cached = info_cache.get(url)
    if cached and (time.time() - cached[0]) < INFO_CACHE_TTL:
        return jsonify(cached[1])

    # YouTube needs the JS challenge solver; other sites usually don't.
    # Skip cookies+solver for non-YouTube to speed up.
    is_youtube = "youtube.com" in url or "youtu.be" in url
    base_cmd = [YTDLP_BIN, "--no-playlist", "-j",
                "--socket-timeout", "10",
                "--no-check-certificates"]
    if is_youtube:
        base_cmd += ["--remote-components", "ejs:github"]

    cookie_browser = "edge" if os.name == "nt" else "chrome"
    cmd = base_cmd + ["--cookies-from-browser", cookie_browser, url]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        # Retry without cookies when the browser DB was locked.
        if result.returncode != 0 and "Could not copy" in (result.stderr or "") and "cookie" in (result.stderr or "").lower():
            result = subprocess.run(base_cmd + [url], capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip().split("\n")[-1]}), 400

        info = json.loads(result.stdout)

        best_by_height = {}
        for f in info.get("formats", []):
            height = f.get("height")
            if height and f.get("vcodec", "none") != "none":
                tbr = f.get("tbr") or 0
                if height not in best_by_height or tbr > (best_by_height[height].get("tbr") or 0):
                    best_by_height[height] = f

        formats = []
        for height, f in best_by_height.items():
            formats.append({
                "id": str(height),
                "label": f"{height}p",
                "height": height,
            })
        formats.sort(key=lambda x: x["height"], reverse=True)

        response = {
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration"),
            "uploader": info.get("uploader", ""),
            "formats": formats,
        }

        # Cache result
        info_cache[url] = (time.time(), response)
        # Prune old cache entries
        if len(info_cache) > 100:
            now = time.time()
            for k in [k for k, v in info_cache.items() if (now - v[0]) > INFO_CACHE_TTL]:
                del info_cache[k]

        return jsonify(response)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timed out fetching video info"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/download", methods=["POST"])
@auth_required
def start_download():
    data = request.json
    url = data.get("url", "").strip()
    format_choice = data.get("format", "video")
    format_id = data.get("format_id")
    title = data.get("title", "")
    force_h264 = bool(data.get("force_h264", False))
    fast_mode = bool(data.get("fast_mode", False))
    start_time = (data.get("start_time") or "").strip() or None
    end_time = (data.get("end_time") or "").strip() or None

    subs = bool(data.get("subs", False))

    # Multi-segment support: sections = [{"start": "0:10", "end": "0:30"}, ...]
    raw_sections = data.get("sections") or []
    sections = []
    for seg in raw_sections:
        s = (str(seg.get("start", "")).strip() if isinstance(seg, dict) else "")
        e = (str(seg.get("end", "")).strip() if isinstance(seg, dict) else "")
        if s or e:
            sections.append({"start": s or "0", "end": e or "inf"})
    sections = sections or None

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "downloading", "url": url, "title": title,
                    "logs": [], "progress": 0, "phase": "queued",
                    "start_time": start_time, "end_time": end_time,
                    "sections": sections}

    thread = threading.Thread(target=run_download,
                              args=(job_id, url, format_choice, format_id,
                                    force_h264, fast_mode, start_time, end_time,
                                    sections, subs))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
@auth_required
def check_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404

    # Only return new logs since last_log index
    last_log = request.args.get("last_log", 0, type=int)
    logs = job.get("logs", [])

    return jsonify({
        "status": job["status"],
        "error": job.get("error"),
        "filename": job.get("filename"),
        "progress": job.get("progress", 0),
        "phase": job.get("phase", ""),
        "speed": job.get("speed", ""),
        "logs": logs[last_log:],
        "total_logs": len(logs),
        "download_token": job.get("download_token"),
    })


@app.route("/api/file/<job_id>")
def download_file(job_id):
    """File download — accepts either Basic Auth or ?token=... query param.
    Token-based access is needed for mobile browsers that don't pass auth headers
    to download manager."""
    job = jobs.get(job_id)
    if not job or job["status"] != "done":
        return jsonify({"error": "File not ready"}), 404

    # Auth: localhost bypasses; otherwise require token OR Basic Auth
    if AUTH_CREDS and not _is_localhost():
        token = request.args.get("token", "")
        valid_token = (token and job.get("download_token") and
                       secrets.compare_digest(token, job["download_token"]))
        if not valid_token and not check_auth(request.authorization):
            return Response(
                "Authentication required",
                401,
                {"WWW-Authenticate": 'Basic realm="ClipDown"'},
            )
    return send_file(job["file"], as_attachment=True, download_name=job["filename"])


# ═══════════════════════════════════════════════════════════════════
#  Channel Subscriptions API
# ═══════════════════════════════════════════════════════════════════

# Initialize DB on startup
try:
    subs_mod.init_db(_resource_root())
except Exception as _e:
    sys.stderr.write(f"[startup] subs.init_db failed: {_e}\n")
try:
    history.init_db(_resource_root())
except Exception as _e:
    sys.stderr.write(f"[startup] history.init_db failed: {_e}\n")


def _download_subscription_video(sub, video):
    """Kick off a background download for a specific subscription video."""
    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {
        "status": "downloading", "url": video["video_url"],
        "title": video["title"] or "", "logs": [], "progress": 0, "phase": "queued",
        "subscription_id": sub["id"], "sub_video_id": video["id"],
    }
    fmt = sub.get("format") or "video"
    fmt_id = sub.get("format_id")
    thread = threading.Thread(
        target=run_download,
        args=(job_id, video["video_url"], fmt, fmt_id, False, False),
    )
    thread.daemon = True
    thread.start()
    return job_id


@app.route("/api/subs", methods=["GET"])
@auth_required
def api_list_subs():
    return jsonify({"subs": subs_mod.list_subs(_resource_root())})


@app.route("/api/subs", methods=["POST"])
@auth_required
def api_create_sub():
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400

    sub_id = subs_mod.create_sub(
        _resource_root(),
        url=url,
        name=data.get("name"),
        format=data.get("format", "video"),
        format_id=data.get("format_id"),
        output_folder=data.get("output_folder"),
        filename_template=data.get("filename_template"),
        auto_download=1 if data.get("auto_download", True) else 0,
        check_interval_minutes=data.get("check_interval_minutes", 60),
    )
    if not sub_id:
        return jsonify({"error": "Could not create subscription"}), 500

    sub = subs_mod.get_sub(_resource_root(), sub_id)

    # Kick off initial channel scan in background so response is fast
    def _initial_scan():
        subs_mod.check_subscription(_resource_root(), sub_id, ytdlp_bin=YTDLP_BIN)

    threading.Thread(target=_initial_scan, daemon=True).start()
    return jsonify({"sub": sub})


@app.route("/api/subs/<int:sub_id>", methods=["PATCH"])
@auth_required
def api_update_sub(sub_id):
    data = request.json or {}
    subs_mod.update_sub(_resource_root(), sub_id, **data)
    return jsonify({"sub": subs_mod.get_sub(_resource_root(), sub_id)})


@app.route("/api/subs/<int:sub_id>", methods=["DELETE"])
@auth_required
def api_delete_sub(sub_id):
    subs_mod.delete_sub(_resource_root(), sub_id)
    return jsonify({"ok": True})


@app.route("/api/subs/<int:sub_id>/videos", methods=["GET"])
@auth_required
def api_sub_videos(sub_id):
    only_new = request.args.get("new") == "1"
    return jsonify({"videos": subs_mod.get_videos(_resource_root(), sub_id, only_new=only_new)})


@app.route("/api/subs/<int:sub_id>/check", methods=["POST"])
@auth_required
def api_check_sub(sub_id):
    """Manual check for new videos on a single subscription."""
    new_vids, err = subs_mod.check_subscription(_resource_root(), sub_id, ytdlp_bin=YTDLP_BIN)
    sub = subs_mod.get_sub(_resource_root(), sub_id)
    if err:
        return jsonify({"error": err, "sub": sub}), 400

    # Auto-download if enabled
    auto_started = []
    if sub and sub.get("auto_download"):
        for v in new_vids:
            job_id = _download_subscription_video(sub, v)
            auto_started.append({"job_id": job_id, "title": v["title"]})

    return jsonify({
        "sub": sub,
        "new_videos": new_vids,
        "auto_started": auto_started,
    })


@app.route("/api/subs/check-all", methods=["POST"])
@auth_required
def api_check_all_subs():
    result = subs_mod.check_all(_resource_root(), ytdlp_bin=YTDLP_BIN)
    # Trigger auto-downloads for subs with auto_download=1
    started = []
    for sub in subs_mod.list_subs(_resource_root()):
        if not sub.get("auto_download"):
            continue
        new_vids = subs_mod.get_videos(_resource_root(), sub["id"], only_new=True)
        for v in new_vids:
            job_id = _download_subscription_video(sub, v)
            started.append({"job_id": job_id, "title": v["title"], "sub_id": sub["id"]})
    result["auto_started"] = started
    return jsonify(result)


# ═══════════════════════════════════════════════════════════════════
#  Settings API
# ═══════════════════════════════════════════════════════════════════

@app.route("/api/settings", methods=["GET"])
@auth_required
def api_get_settings():
    return jsonify({
        "settings": settings_store.load(_resource_root()),
        "defaults": settings_store.DEFAULTS,
        "template_vars": settings_store.TEMPLATE_VARS,
    })


@app.route("/api/settings", methods=["PATCH"])
@auth_required
def api_update_settings():
    patch = request.json or {}
    updated = settings_store.update(_resource_root(), patch)
    return jsonify({"settings": updated})


@app.route("/api/settings/preview", methods=["POST"])
@auth_required
def api_preview_filename():
    """Preview a filename template with sample data."""
    data = request.json or {}
    template = data.get("template") or settings_store.DEFAULTS["filename_template"]
    sanitize = bool(data.get("sanitize", True))
    auto_org = bool(data.get("auto_organize", False))
    sample = data.get("sample", {}) or {}
    ctx = {
        "title":      sample.get("title", "Rick Astley - Never Gonna Give You Up"),
        "channel":    sample.get("channel", "Rick Astley"),
        "ext":        sample.get("ext", "mp4"),
        "url":        sample.get("url", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        "resolution": sample.get("resolution", "1080"),
        "duration":   sample.get("duration", 213),
        "index":      sample.get("index", 1),
    }
    try:
        result = settings_store.render_filename(
            template, ctx, sanitize=sanitize, auto_organize=auto_org
        )
        return jsonify({"preview": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ═══════════════════════════════════════════════════════════════════
#  Video trim API (post-download clipping of an existing job's file)
# ═══════════════════════════════════════════════════════════════════

def _parse_time_to_seconds(t):
    """Accept '30', '1:30', '0:01:30' → seconds (float). Returns None on bad input."""
    if t is None:
        return None
    s = str(t).strip()
    if not s or s.lower() in ("inf", "end"):
        return None
    try:
        parts = s.split(":")
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (ValueError, IndexError):
        pass
    return None


def _trim_worker(job_id, source_path, start, end, out_ext, want_title):
    """Background ffmpeg trim job."""
    job = jobs[job_id]
    log(job, f"Trimming {os.path.basename(source_path)} from {start} to {end or 'end'}")
    job["phase"] = "trimming"
    job["progress"] = 0

    out_name = f"{job_id}.{out_ext.lstrip('.')}"
    out_path = os.path.join(DOWNLOAD_DIR, out_name)

    cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "info", "-progress", "pipe:1"]
    if start:
        cmd += ["-ss", str(start)]
    if end and end.lower() not in ("inf", "end"):
        cmd += ["-to", str(end)]
    cmd += ["-i", source_path, "-c", "copy", "-avoid_negative_ts", "make_zero", out_path]

    total_dur = None
    start_s = _parse_time_to_seconds(start) or 0
    end_s = _parse_time_to_seconds(end)
    if end_s is not None and end_s > start_s:
        total_dur = end_s - start_s

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        for line in proc.stdout:
            m = re.search(r"out_time_ms=(\d+)", line)
            if m and total_dur:
                secs = int(m.group(1)) / 1_000_000
                pct = round(min(secs / total_dur * 100, 100), 1)
                job["progress"] = pct
            if line.startswith("progress=end"):
                job["progress"] = 100

        proc.wait(timeout=300)
        if proc.returncode != 0 or not os.path.exists(out_path):
            job["status"] = "error"
            job["error"] = "Trim failed"
            log(job, "Trim failed")
            return

        job["file"] = out_path
        job["download_token"] = secrets.token_urlsafe(16)

        # Build filename honoring template if possible
        base_title = (want_title or os.path.splitext(os.path.basename(source_path))[0]).strip()
        # Append segment suffix so user knows it's a clip
        seg_suffix = f"_{start.replace(':', '')}-{(end or 'end').replace(':', '')}"
        try:
            s = settings_store.load(_resource_root())
            tpl = s.get("filename_template") or settings_store.DEFAULTS["filename_template"]
            sanitize = bool(s.get("sanitize_filename", True))
            ctx = {"title": base_title + seg_suffix, "ext": out_ext.lstrip("."),
                   "url": ""}
            job["filename"] = settings_store.render_filename(tpl, ctx, sanitize=sanitize)
        except Exception:
            safe = re.sub(r'[\\/:*?"<>|]', "", base_title + seg_suffix)
            job["filename"] = f"{safe}.{out_ext.lstrip('.')}"

        log(job, f"Done → {job['filename']}")
        job["phase"] = "done"
        job["progress"] = 100
        job["status"] = "done"
    except subprocess.TimeoutExpired:
        job["status"] = "error"
        job["error"] = "Trim timed out"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


@app.route("/api/trim", methods=["POST"])
@auth_required
def api_trim():
    """Trim an already-completed job's file to [start, end].
    Body: {source_job_id: "abc", start: "0:15", end: "1:30", title?: "..."}"""
    data = request.json or {}
    src_id = (data.get("source_job_id") or "").strip()
    start = (data.get("start") or "0").strip()
    end = (data.get("end") or "").strip() or None

    src_job = jobs.get(src_id)
    if not src_job or not src_job.get("file") or not os.path.exists(src_job["file"]):
        return jsonify({"error": "Source file not available"}), 400

    if not start and not end:
        return jsonify({"error": "Provide at least a start time"}), 400

    src_path = src_job["file"]
    ext = os.path.splitext(src_path)[1] or ".mp4"
    trim_id = uuid.uuid4().hex[:10]
    jobs[trim_id] = {
        "status": "trimming", "url": src_job.get("url", ""), "title": src_job.get("title", ""),
        "logs": [], "progress": 0, "phase": "trimming",
        "start_time": start, "end_time": end,
    }
    t = threading.Thread(target=_trim_worker,
                         args=(trim_id, src_path, start, end, ext, data.get("title") or src_job.get("title", "")))
    t.daemon = True
    t.start()
    return jsonify({"job_id": trim_id})


@app.route("/api/open_folder", methods=["POST"])
@auth_required
def api_open_folder():
    """Open the OS file browser at the user's downloads folder.
    Localhost-only so we don't let remote clients trigger OS commands."""
    if not _is_localhost():
        return jsonify({"error": "Local access only"}), 403

    # Prefer configured folder, fallback to user's Downloads
    try:
        settings = settings_store.load(_resource_root())
    except Exception:
        settings = {}
    folder = (settings.get("download_folder") or "").strip()
    folder = os.path.expanduser(folder) if folder else os.path.expanduser("~/Downloads")

    if not os.path.isdir(folder):
        return jsonify({"error": f"Folder not found: {folder}"}), 404

    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        elif os.name == "nt":  # Windows
            subprocess.Popen(["explorer", folder])
        else:  # Linux and others
            subprocess.Popen(["xdg-open", folder])
        return jsonify({"ok": True, "folder": folder})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════
#  Download history API
# ═══════════════════════════════════════════════════════════════════

@app.route("/api/history", methods=["GET"])
@auth_required
def api_history_list():
    limit = int(request.args.get("limit", 100))
    return jsonify({
        "history": history.list_all(_resource_root(), limit=limit),
        "count": history.count(_resource_root()),
    })


@app.route("/api/history/<int:hid>", methods=["DELETE"])
@auth_required
def api_history_delete(hid):
    history.delete(_resource_root(), hid)
    return jsonify({"ok": True})


@app.route("/api/history/clear", methods=["POST"])
@auth_required
def api_history_clear():
    history.clear_all(_resource_root())
    return jsonify({"ok": True})


@app.route("/api/history/<int:hid>/redownload", methods=["POST"])
@auth_required
def api_history_redownload(hid):
    """Kick off a fresh download using the parameters saved in the history row."""
    row = history.get(_resource_root(), hid)
    if not row:
        return jsonify({"error": "Not found"}), 404

    job_id = uuid.uuid4().hex[:10]
    jobs[job_id] = {
        "status": "downloading", "url": row["url"],
        "title": row.get("title") or "",
        "logs": [], "progress": 0, "phase": "queued",
        "start_time": row.get("start_time"),
        "end_time": row.get("end_time"),
    }
    t = threading.Thread(
        target=run_download,
        args=(job_id, row["url"], row.get("format", "video"), row.get("format_id"),
              False, False, row.get("start_time"), row.get("end_time")),
    )
    t.daemon = True
    t.start()
    return jsonify({"job_id": job_id})


# ═══════════════════════════════════════════════════════════════════
#  Updates API — yt-dlp binary + app version status
# ═══════════════════════════════════════════════════════════════════

_ytdlp_update_lock = threading.Lock()
_ytdlp_update_result = {"status": "idle"}  # idle | running | done | error


@app.route("/api/updates/status", methods=["GET"])
@auth_required
def api_updates_status():
    return jsonify(updater.get_status(YTDLP_BIN))


@app.route("/api/updates/ytdlp", methods=["POST"])
@auth_required
def api_updates_ytdlp():
    """Kick off yt-dlp update in a background thread. Poll /api/updates/status."""
    global _ytdlp_update_result
    with _ytdlp_update_lock:
        if _ytdlp_update_result.get("status") == "running":
            return jsonify({"status": "running"})
        _ytdlp_update_result = {"status": "running"}

    def _worker():
        global _ytdlp_update_result
        try:
            result = updater.update_ytdlp(YTDLP_BIN)
            if result.get("ok"):
                _ytdlp_update_result = {
                    "status": "done",
                    "version": result.get("version"),
                }
            else:
                _ytdlp_update_result = {
                    "status": "error",
                    "error": result.get("error", "알 수 없는 오류"),
                }
        except Exception as e:
            _ytdlp_update_result = {"status": "error", "error": str(e)}

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return jsonify({"status": "running"})


@app.route("/api/updates/ytdlp/status", methods=["GET"])
@auth_required
def api_updates_ytdlp_status():
    return jsonify(_ytdlp_update_result)


# ═══════════════════════════════════════════════════════════════════
#  Background subscription scheduler
# ═══════════════════════════════════════════════════════════════════

_scheduler_stop = threading.Event()


def _scheduler_loop():
    """Every 5 minutes, check subscriptions whose interval has elapsed."""
    while not _scheduler_stop.wait(300):
        try:
            root = _resource_root()
            for sub in subs_mod.list_subs(root):
                # Simple policy: honor check_interval_minutes.
                last = sub.get("last_checked_at")
                interval = sub.get("check_interval_minutes", 60)
                if last:
                    try:
                        from datetime import datetime, timedelta
                        last_dt = datetime.fromisoformat(last.replace(" ", "T"))
                        if datetime.utcnow() - last_dt < timedelta(minutes=interval):
                            continue
                    except Exception:
                        pass
                new_vids, err = subs_mod.check_subscription(root, sub["id"], ytdlp_bin=YTDLP_BIN)
                if err or not sub.get("auto_download"):
                    continue
                for v in new_vids:
                    _download_subscription_video(sub, v)
        except Exception as e:
            print(f"scheduler error: {e}", file=sys.stderr)


def _start_scheduler():
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8899))
    host = os.environ.get("HOST", "127.0.0.1")

    # Print startup info
    print(f"\n  ClipDown server running at http://{host}:{port}")
    if AUTH_CREDS:
        print(f"  Auth: enabled (user: {AUTH_CREDS[0]})")
    else:
        print(f"  Auth: disabled (recommended for public exposure)")
    if host == "0.0.0.0":
        print(f"  Accessible from LAN at http://<this-computer-ip>:{port}\n")
    else:
        print()

    _start_scheduler()
    app.run(host=host, port=port, threaded=True)
