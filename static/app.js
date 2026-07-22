// ClipDown v1.0 — main UI logic

// Navigation
document.querySelectorAll('.sidebar-item[data-nav]').forEach(item => {
  item.addEventListener('click', () => {
    const target = item.dataset.nav;
    document.querySelectorAll('.sidebar-item').forEach(el => el.classList.remove('active'));
    item.classList.add('active');
    document.querySelectorAll('.page-view').forEach(el => el.classList.remove('active'));
    document.querySelector(`.page-view[data-page="${target}"]`).classList.add('active');
  });
});

let currentFormat = 'video';
let cardData = [];

function setFormat(btn) {
  document.querySelectorAll('.format-toggle button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  currentFormat = btn.dataset.format;
}

function parseUrls(text) {
  return [...new Set(text.split(/[\s,]+/).map(u => u.trim()).filter(u => u.startsWith('http')))];
}

function fmtDur(s) {
  if (!s) return '';
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, '0')}`;
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

function friendlyError(err) {
  if (!err) return 'Something went wrong';
  if (err.includes('Unsupported URL')) return 'This URL is not supported';
  if (err.includes('Video unavailable')) return 'Video is unavailable or private';
  if (err.includes('Private video')) return 'This video is private';
  if (err.includes('HTTP Error 403')) return 'Access denied by the platform';
  if (err.includes('HTTP Error 404')) return 'Video not found';
  if (err.includes('copyright')) return 'Video blocked due to copyright';
  if (err.includes('geo')) return 'Video not available in your region';
  if (err.includes('timed out') || err.includes('Timed out')) return 'Request timed out — try again';
  return err.length > 80 ? err.slice(0, 80) + '...' : err;
}

function detectSite(url) {
  if (url.includes('youtube.com') || url.includes('youtu.be')) return 'YouTube';
  if (url.includes('instagram.com')) return 'Instagram';
  if (url.includes('tiktok.com')) return 'TikTok';
  if (url.includes('twitter.com') || url.includes('x.com')) return 'Twitter';
  if (url.includes('reddit.com')) return 'Reddit';
  if (url.includes('facebook.com') || url.includes('fb.watch')) return 'Facebook';
  if (url.includes('vimeo.com')) return 'Vimeo';
  if (url.includes('twitch.tv')) return 'Twitch';
  try { return new URL(url).hostname.replace('www.', ''); } catch { return ''; }
}

document.getElementById('urls').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); go(); }
});

document.getElementById('urls').addEventListener('input', function() {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 100) + 'px';
});

function updateQueueCount() {
  const active = cardData.filter(c => !c.removed && (
    c.status === 'loading' || c.status === 'downloading' || c.status === 'ready'
  ));
  document.getElementById('count-queue').textContent = active.length;
  const total = cardData.filter(c => !c.removed).length;
  const emptyEl = document.getElementById('empty-state');
  if (emptyEl) emptyEl.style.display = total === 0 ? 'flex' : 'none';
}

async function go() {
  const urls = parseUrls(document.getElementById('urls').value);
  if (!urls.length) return;

  const btn = document.getElementById('goBtn');
  btn.disabled = true;
  btn.textContent = 'Loading...';
  document.getElementById('urls').value = '';
  document.getElementById('urls').style.height = 'auto';

  for (let i = 0; i < urls.length; i++) {
    const url = urls[i];
    const idx = cardData.length;
    cardData.push({ url, status: 'loading', site: detectSite(url) });
    renderCard(idx);
    updateQueueCount();

    try {
      const res = await fetch('/api/info', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      });
      const data = await res.json();
      if (data.error) {
        cardData[idx] = { ...cardData[idx], status: 'info-error', error: data.error };
      } else {
        cardData[idx] = {
          ...cardData[idx],
          status: 'ready',
          title: data.title || '',
          thumbnail: data.thumbnail || '',
          duration: data.duration,
          uploader: data.uploader || '',
          formats: data.formats || [],
          selectedFormatId: data.formats?.[0]?.id || null,
        };
      }
    } catch (err) {
      cardData[idx] = { ...cardData[idx], status: 'info-error', error: err.message };
    }
    renderCard(idx);
    updateQueueCount();
  }

  btn.disabled = false;
  btn.textContent = 'Fetch';
}

// Build card DOM safely (no innerHTML with untrusted data)
function buildEl(tag, attrs, children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const k in attrs) {
      if (k === 'class') el.className = attrs[k];
      else if (k === 'style') el.setAttribute('style', attrs[k]);
      else if (k.startsWith('on')) el[k] = attrs[k];
      else el.setAttribute(k, attrs[k]);
    }
  }
  if (children) {
    for (const child of [].concat(children)) {
      if (child == null) continue;
      if (typeof child === 'string') el.appendChild(document.createTextNode(child));
      else el.appendChild(child);
    }
  }
  return el;
}

function renderCard(idx) {
  const c = cardData[idx];
  if (c.removed) return;

  let el = document.getElementById(`card-${idx}`);
  if (!el) {
    el = document.createElement('div');
    el.id = `card-${idx}`;
    document.getElementById('cards').appendChild(el);
  }

  let cls = 'card';
  if (c.status === 'downloading') cls += ' downloading';
  else if (c.status === 'done') cls += ' done';
  else if (c.status === 'error' || c.status === 'info-error') cls += ' error';
  el.className = cls;

  // Clear children safely
  while (el.firstChild) el.removeChild(el.firstChild);

  const isAudio = currentFormat === 'audio';

  // ─── Thumb ───
  let thumb;
  if (c.status === 'loading') {
    thumb = buildEl('div', { class: 'thumb loading' });
  } else if (isAudio) {
    thumb = buildEl('div', { class: 'thumb audio' }, [
      c.site ? buildEl('span', { class: 'site-tag' }, c.site) : null,
      '♫'
    ]);
  } else if (c.thumbnail) {
    thumb = buildEl('div', { class: 'thumb' }, [
      buildEl('img', { src: c.thumbnail, alt: '' }),
      c.site ? buildEl('span', { class: 'site-tag' }, c.site) : null,
      c.duration ? buildEl('span', { class: 'duration' }, fmtDur(c.duration)) : null,
    ]);
  } else {
    thumb = buildEl('div', { class: 'thumb' }, [
      c.site ? buildEl('span', { class: 'site-tag' }, c.site) : null,
    ]);
  }
  el.appendChild(thumb);

  // ─── Info column ───
  const info = buildEl('div', { class: 'card-info' });

  if (c.status === 'loading') {
    info.appendChild(buildEl('div', { class: 'card-title' }, 'Fetching metadata...'));
    const meta = buildEl('div', { class: 'card-meta' });
    const dots = buildEl('span', { class: 'dots' }, [
      buildEl('span'), buildEl('span'), buildEl('span')
    ]);
    meta.appendChild(dots);
    meta.appendChild(buildEl('span', {}, ' ' + c.url));
    info.appendChild(meta);
  } else if (c.status === 'info-error') {
    info.appendChild(buildEl('div', { class: 'card-title', style: 'color:var(--error)' }, 'Could not fetch'));
    info.appendChild(buildEl('div', { class: 'card-meta' }, friendlyError(c.error)));
    info.appendChild(buildEl('div', { class: 'card-meta', style: 'font-size:0.7rem' }, c.url));
  } else {
    info.appendChild(buildEl('div', { class: 'card-title' }, c.title || 'Untitled'));

    const meta = buildEl('div', { class: 'card-meta' });
    const parts = [];
    if (c.uploader) parts.push(buildEl('span', {}, c.uploader));
    if (c.duration) parts.push(buildEl('span', {}, fmtDur(c.duration)));
    if (c.status === 'downloading') {
      if (c.progress !== undefined) parts.push(buildEl('span', { class: 'tag' }, `${c.progress}%`));
      if (c.speed) parts.push(buildEl('span', { class: 'tag' }, c.speed));
      if (c.phase) parts.push(buildEl('span', {}, c.phase));
    }
    parts.forEach((p, i) => {
      if (i > 0) meta.appendChild(buildEl('span', { class: 'sep' }, '·'));
      meta.appendChild(p);
    });
    info.appendChild(meta);

    if (c.status === 'downloading') {
      const prog = buildEl('div', { class: 'progress' }, [
        buildEl('div', { class: 'progress-fill', style: `width:${c.progress || 0}%` })
      ]);
      info.appendChild(prog);
    }

    if (c.status === 'ready' && !isAudio && c.formats && c.formats.length > 1) {
      const chips = buildEl('div', { class: 'q-chips' });
      c.formats.forEach(f => {
        const chip = buildEl('button', {
          class: 'q-chip' + (f.id === c.selectedFormatId ? ' active' : ''),
          onclick: () => pickFormat(idx, f.id)
        }, f.label);
        chips.appendChild(chip);
      });
      info.appendChild(chips);
    }
  }
  el.appendChild(info);

  // ─── Actions ───
  const actions = buildEl('div', { class: 'card-action' });
  if (c.status === 'ready') {
    actions.appendChild(buildEl('button', { class: 'btn primary', onclick: () => dlCard(idx) }, 'Download'));
    actions.appendChild(buildEl('button', { class: 'icon-btn', title: 'Remove', onclick: () => removeCard(idx) }, '×'));
  } else if (c.status === 'downloading') {
    actions.appendChild(buildEl('span', { class: 'pct' }, `${c.progress || 0}%`));
    actions.appendChild(buildEl('button', { class: 'icon-btn', title: 'Cancel', onclick: () => removeCard(idx) }, '×'));
  } else if (c.status === 'done') {
    actions.appendChild(buildEl('span', { class: 'status-icon done' }, '✓'));
    actions.appendChild(buildEl('button', { class: 'icon-btn', title: 'Redownload', onclick: () => saveCard(idx) }, '↓'));
  } else if (c.status === 'error') {
    actions.appendChild(buildEl('button', { class: 'btn', onclick: () => dlCard(idx) }, 'Retry'));
    actions.appendChild(buildEl('button', { class: 'icon-btn', title: 'Remove', onclick: () => removeCard(idx) }, '×'));
  } else if (c.status === 'info-error' || c.status === 'loading') {
    actions.appendChild(buildEl('button', { class: 'icon-btn', onclick: () => removeCard(idx) }, '×'));
  }
  el.appendChild(actions);
}

function pickFormat(idx, formatId) {
  cardData[idx].selectedFormatId = formatId;
  renderCard(idx);
}

function removeCard(idx) {
  const el = document.getElementById(`card-${idx}`);
  if (el) el.remove();
  cardData[idx] = { removed: true };
  updateQueueCount();
}

function clearCompleted() {
  for (let i = cardData.length - 1; i >= 0; i--) {
    if (cardData[i].status === 'done' || cardData[i].status === 'info-error') {
      const el = document.getElementById(`card-${i}`);
      if (el) el.remove();
      cardData[i] = { removed: true };
    }
  }
  updateQueueCount();
}

async function dlCard(idx) {
  const c = cardData[idx];
  c.status = 'downloading';
  c.progress = 0;
  c.error = null;
  renderCard(idx);

  try {
    const res = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: c.url,
        format: currentFormat,
        format_id: c.selectedFormatId,
        title: c.title || '',
      }),
    });
    const data = await res.json();
    if (data.error) {
      c.status = 'error';
      c.error = data.error;
      renderCard(idx);
      return;
    }
    c.jobId = data.job_id;
    pollCard(idx);
  } catch (err) {
    c.status = 'error';
    c.error = err.message;
    renderCard(idx);
  }
}

function pollCard(idx) {
  const c = cardData[idx];
  const iv = setInterval(async () => {
    try {
      const res = await fetch(`/api/status/${c.jobId}`);
      const data = await res.json();
      c.progress = Math.round(data.progress || 0);
      c.phase = data.phase;
      c.speed = data.speed;
      if (data.status === 'done') {
        clearInterval(iv);
        c.status = 'done';
        c.filename = data.filename;
        c.downloadToken = data.download_token;
        renderCard(idx);
        saveCard(idx);
        updateQueueCount();
      } else if (data.status === 'error') {
        clearInterval(iv);
        c.status = 'error';
        c.error = data.error;
        renderCard(idx);
        updateQueueCount();
      } else {
        renderCard(idx);
      }
    } catch {
      clearInterval(iv);
      c.status = 'error';
      c.error = 'Lost connection to server';
      renderCard(idx);
    }
  }, 800);
}

function saveCard(idx) {
  const c = cardData[idx];
  if (!c.jobId) return;
  const a = document.createElement('a');
  const tokenQuery = c.downloadToken ? `?token=${encodeURIComponent(c.downloadToken)}` : '';
  a.href = `/api/file/${c.jobId}${tokenQuery}`;
  a.download = c.filename || '';
  a.click();
}

updateQueueCount();
