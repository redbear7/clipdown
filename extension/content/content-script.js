(() => {
  const { normalizeEmbedUrl, isMediaUrl } = window.__reclipPatterns;

  function stripYoutubeNoise(url) {
    try {
      const u = new URL(url);
      // Shorts: keep as-is
      if (u.pathname.startsWith("/shorts/")) return u.origin + u.pathname;
      // youtu.be short links
      if (u.hostname === "youtu.be") return `https://www.youtube.com/watch?v=${u.pathname.slice(1)}`;
      // /watch: keep only `v` parameter
      if (u.pathname === "/watch") {
        const v = u.searchParams.get("v");
        return v ? `https://www.youtube.com/watch?v=${v}` : url;
      }
      return url;
    } catch {
      return url;
    }
  }

  function scanPage() {
    const found = new Map(); // url -> { url, source, label }

    function add(url, source, label) {
      if (!url || found.has(url)) return;
      try { new URL(url); } catch { return; }
      if (url.startsWith("blob:") || url.startsWith("data:")) return;
      found.set(url, { url, source, label: label || url });
    }

    // 1. Current page URL
    const loc = window.location.href;
    add(loc, "PAGE", document.title || loc);

    // YouTube — only return the main video (strip playlist/related noise)
    if (/youtube\.com\/(watch|shorts)|youtu\.be\//.test(loc)) {
      // Normalize to clean watch URL (drop list/index/start_radio params)
      const cleaned = stripYoutubeNoise(loc);
      if (cleaned !== loc) {
        found.clear();
        add(cleaned, "PAGE", document.title || cleaned);
      }
      return Array.from(found.values());
    }

    // 2. Meta tags
    const metaSelectors = [
      'meta[property="og:video"]',
      'meta[property="og:video:url"]',
      'meta[property="og:video:secure_url"]',
      'meta[property="og:audio"]',
      'meta[property="og:audio:url"]',
      'meta[name="twitter:player:stream"]',
    ];
    for (const sel of metaSelectors) {
      const el = document.querySelector(sel);
      if (el) add(el.content, "META", el.getAttribute("property") || el.getAttribute("name"));
    }

    // 3. <video> elements
    document.querySelectorAll("video").forEach((v) => {
      if (v.src) add(v.src, "VIDEO", "video element");
      v.querySelectorAll("source").forEach((s) => {
        if (s.src) add(s.src, "VIDEO", "video source");
      });
    });

    // 4. <audio> elements
    document.querySelectorAll("audio").forEach((a) => {
      if (a.src) add(a.src, "AUDIO", "audio element");
      a.querySelectorAll("source").forEach((s) => {
        if (s.src) add(s.src, "AUDIO", "audio source");
      });
    });

    // 5. <iframe> embeds
    document.querySelectorAll("iframe").forEach((iframe) => {
      if (!iframe.src) return;
      const normalized = normalizeEmbedUrl(iframe.src);
      if (normalized !== iframe.src || isMediaUrl(iframe.src)) {
        add(normalized, "EMBED", "embedded media");
      }
    });

    // 6. Links with media URLs
    document.querySelectorAll("a[href]").forEach((a) => {
      if (a.href && isMediaUrl(a.href) && a.href !== window.location.href) {
        add(a.href, "LINK", a.textContent.trim().slice(0, 60) || a.href);
      }
    });

    return Array.from(found.values());
  }

  // Listen for messages from popup
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.action === "getMediaUrls") {
      sendResponse({ urls: scanPage() });
    }
    return true;
  });
})();
