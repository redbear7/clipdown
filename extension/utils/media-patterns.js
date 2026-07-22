const MEDIA_EXTENSIONS = new Set([
  "mp4", "mp3", "webm", "m4a", "ogg", "wav", "flac", "avi", "mkv", "m4v", "mov",
]);

const PLATFORM_PATTERNS = [
  /youtube\.com\/watch/,
  /youtu\.be\//,
  /tiktok\.com\/.+\/video/,
  /instagram\.com\/(p|reel|tv)\//,
  /twitter\.com\/.+\/status/,
  /x\.com\/.+\/status/,
  /reddit\.com\/r\/.+\/comments/,
  /facebook\.com\/.*\/videos/,
  /fb\.watch\//,
  /vimeo\.com\/\d+/,
  /twitch\.tv\//,
  /dailymotion\.com\/video/,
  /soundcloud\.com\//,
  /streamable\.com\//,
  /pinterest\.com\/pin\//,
  /threads\.net\/.+\/post/,
  /linkedin\.com\/.*\/posts/,
  /bilibili\.com\/video/,
  /nicovideo\.jp\/watch/,
  /v\.redd\.it\//,
];

const EMBED_TRANSFORMS = [
  {
    regex: /(?:youtube\.com|youtube-nocookie\.com)\/embed\/([^?&#]+)/,
    transform: (_, id) => `https://www.youtube.com/watch?v=${id}`,
  },
  {
    regex: /player\.vimeo\.com\/video\/(\d+)/,
    transform: (_, id) => `https://vimeo.com/${id}`,
  },
  {
    regex: /dailymotion\.com\/embed\/video\/([^?&#]+)/,
    transform: (_, id) => `https://www.dailymotion.com/video/${id}`,
  },
  {
    regex: /facebook\.com\/plugins\/video\.php\?.*href=([^&]+)/,
    transform: (_, href) => decodeURIComponent(href),
  },
];

function normalizeEmbedUrl(url) {
  for (const { regex, transform } of EMBED_TRANSFORMS) {
    const match = url.match(regex);
    if (match) return transform(...match);
  }
  return url;
}

function isMediaUrl(url) {
  try {
    const u = new URL(url);
    const ext = u.pathname.split(".").pop().toLowerCase();
    if (MEDIA_EXTENSIONS.has(ext)) return true;
  } catch {}
  return PLATFORM_PATTERNS.some((p) => p.test(url));
}

// Expose for content script (not a module context)
window.__reclipPatterns = { normalizeEmbedUrl, isMediaUrl, MEDIA_EXTENSIONS };
