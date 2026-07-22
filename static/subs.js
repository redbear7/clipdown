// ClipDown — Subscriptions view logic

const subsState = {
  items: [],
  loading: false,
};

async function subsFetch(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

async function subsLoad() {
  subsState.loading = true;
  subsRender();
  try {
    const data = await subsFetch('/api/subs');
    subsState.items = data.subs || [];
  } catch (e) {
    console.error('subsLoad', e);
    subsState.items = [];
  }
  subsState.loading = false;
  subsRender();
  updateCounts();
}

function updateCounts() {
  const el = document.getElementById('count-subs');
  if (el) el.textContent = subsState.items.length;
}

function fmtRelTime(iso) {
  if (!iso) return 'never';
  const d = new Date(iso.includes('T') ? iso : iso.replace(' ', 'T') + 'Z');
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.round(diff/60)}m ago`;
  if (diff < 86400) return `${Math.round(diff/3600)}h ago`;
  return `${Math.round(diff/86400)}d ago`;
}

function subsRender() {
  const container = document.getElementById('subs-list');
  if (!container) return;

  while (container.firstChild) container.removeChild(container.firstChild);

  if (subsState.loading) {
    container.appendChild(buildEl('div', { class: 'sub-empty' }, 'Loading...'));
    return;
  }

  if (subsState.items.length === 0) {
    const empty = buildEl('div', { class: 'sub-empty' }, [
      buildEl('div', { class: 'empty-icon' }, '◉'),
      buildEl('h3', {}, 'No subscriptions yet'),
      buildEl('p', {}, 'Add a YouTube channel URL to auto-track new uploads.'),
    ]);
    container.appendChild(empty);
    return;
  }

  subsState.items.forEach(sub => {
    container.appendChild(buildSubCard(sub));
  });
}

function buildSubCard(sub) {
  const avatarChar = (sub.avatar_emoji || sub.name?.[0] || '?').toUpperCase();
  const avatar = buildEl('div', { class: 'sub-avatar' }, avatarChar);

  const nameRow = buildEl('div', { class: 'sub-name-row' }, [
    buildEl('span', { class: 'sub-name' }, sub.name || 'Subscription'),
    sub.new_count > 0 ? buildEl('span', { class: 'sub-new-badge' }, `● ${sub.new_count} new`) : null,
  ]);

  const infoParts = [];
  infoParts.push(sub.auto_download ? 'Auto' : 'Manual');
  infoParts.push((sub.format || 'video').toUpperCase());
  if (sub.format_id) infoParts.push(`${sub.format_id}p`);
  infoParts.push(`Last: ${fmtRelTime(sub.last_checked_at)}`);
  const info = buildEl('div', { class: 'sub-info' }, infoParts.join(' · '));

  const urlLine = buildEl('div', { class: 'sub-url' }, sub.url);

  const middle = buildEl('div', { class: 'sub-middle' }, [nameRow, info, urlLine]);

  // Toggle switch
  const sw = buildEl('div', {
    class: 'sw' + (sub.auto_download ? ' on' : ''),
    onclick: () => toggleAuto(sub.id, !sub.auto_download),
    title: sub.auto_download ? 'Auto-download ON' : 'Auto-download OFF',
  });

  const checkBtn = buildEl('button', {
    class: 'icon-btn',
    title: 'Check now',
    onclick: () => checkSub(sub.id),
  }, '↻');

  const delBtn = buildEl('button', {
    class: 'icon-btn',
    title: 'Delete',
    onclick: () => deleteSub(sub.id, sub.name),
  }, '×');

  const actions = buildEl('div', { class: 'sub-actions' }, [sw, checkBtn, delBtn]);

  return buildEl('div', { class: 'sub-card' }, [avatar, middle, actions]);
}

async function addSubscription() {
  const url = prompt('Paste YouTube channel URL (or any yt-dlp-supported channel):');
  if (!url || !url.trim()) return;

  const name = prompt('Display name (leave blank to auto-detect):');
  const format = confirm('Download as MP3? (Cancel = MP4)') ? 'audio' : 'video';

  try {
    const data = await subsFetch('/api/subs', {
      method: 'POST',
      body: JSON.stringify({
        url: url.trim(),
        name: name?.trim() || null,
        format,
        auto_download: true,
      }),
    });
    subsState.items.unshift(data.sub);
    subsRender();
    updateCounts();
    setTimeout(() => subsLoad(), 3000);
  } catch (e) {
    alert('Failed to add subscription: ' + e.message);
  }
}

async function toggleAuto(subId, newVal) {
  try {
    await subsFetch(`/api/subs/${subId}`, {
      method: 'PATCH',
      body: JSON.stringify({ auto_download: newVal ? 1 : 0 }),
    });
    const sub = subsState.items.find(s => s.id === subId);
    if (sub) sub.auto_download = newVal ? 1 : 0;
    subsRender();
  } catch (e) {
    alert('Toggle failed: ' + e.message);
  }
}

async function checkSub(subId) {
  const btn = event?.currentTarget;
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  try {
    const data = await subsFetch(`/api/subs/${subId}/check`, { method: 'POST' });
    if (data.new_videos?.length) {
      const msg = `Found ${data.new_videos.length} new video(s)` +
                  (data.auto_started?.length ? ` — downloading ${data.auto_started.length}` : '');
      showToast(msg);
    } else {
      showToast('No new videos');
    }
    await subsLoad();
  } catch (e) {
    alert('Check failed: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '↻'; }
  }
}

async function checkAllSubs() {
  const btn = document.getElementById('subs-check-all');
  if (btn) { btn.disabled = true; btn.textContent = 'Checking...'; }
  try {
    const data = await subsFetch('/api/subs/check-all', { method: 'POST' });
    showToast(`Checked ${data.checked} subs — ${data.new_total} new · ${data.auto_started?.length || 0} downloading`);
    await subsLoad();
  } catch (e) {
    alert('Check failed: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Check All'; }
  }
}

async function deleteSub(subId, name) {
  if (!confirm(`Delete subscription "${name}"?`)) return;
  try {
    await subsFetch(`/api/subs/${subId}`, { method: 'DELETE' });
    subsState.items = subsState.items.filter(s => s.id !== subId);
    subsRender();
    updateCounts();
  } catch (e) {
    alert('Delete failed: ' + e.message);
  }
}

// ── Toast (simple) ─────────────────────────
function showToast(msg) {
  let t = document.getElementById('subs-toast');
  if (!t) {
    t = buildEl('div', { id: 'subs-toast', class: 'subs-toast' });
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._hideT);
  t._hideT = setTimeout(() => t.classList.remove('show'), 3500);
}

// Lazy-load when user navigates to Subscriptions
document.addEventListener('DOMContentLoaded', () => {
  const subsNav = document.querySelector('[data-nav="subscriptions"]');
  if (subsNav) {
    subsNav.addEventListener('click', () => { subsLoad(); });
  }
  // Initial count fetch
  subsLoad();
});
