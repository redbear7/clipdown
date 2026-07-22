// ClipDown Settings — filename template UI

const settingsState = {
  data: null,
  vars: [],
  saveTimer: null,
  previewTimer: null,
};

async function settingsLoad() {
  try {
    const res = await fetch('/api/settings');
    const j = await res.json();
    settingsState.data = j.settings;
    settingsState.vars = j.template_vars || [];
    renderSettings();
  } catch (e) {
    console.error('settingsLoad', e);
  }
}

async function schedulePreview() {
  clearTimeout(settingsState.previewTimer);
  settingsState.previewTimer = setTimeout(runPreview, 300);
}

async function runPreview() {
  const templateEl = document.getElementById('set-template');
  const sanitizeEl = document.getElementById('set-sanitize');
  const autoOrgEl = document.getElementById('set-auto-org');
  if (!templateEl) return;

  try {
    const res = await fetch('/api/settings/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        template: templateEl.value,
        sanitize: sanitizeEl?.checked !== false,
        auto_organize: autoOrgEl?.checked === true,
      }),
    });
    const j = await res.json();
    const preview = document.getElementById('set-preview');
    if (preview) preview.textContent = j.preview || j.error || '';
  } catch (e) {
    console.error('preview', e);
  }
}

async function persistSettings(patch) {
  // Accumulate patches so debounce doesn't drop intermediate values.
  settingsState.pending = settingsState.pending || {};
  Object.assign(settingsState.pending, patch);
  Object.assign(settingsState.data, patch);
  clearTimeout(settingsState.saveTimer);
  settingsState.saveTimer = setTimeout(async () => {
    const body = settingsState.pending;
    settingsState.pending = null;
    try {
      await fetch('/api/settings', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      showSettingsSaved();
    } catch (e) {
      console.error('save settings', e);
    }
  }, 400);
}

function showSettingsSaved() {
  const el = document.getElementById('set-saved-indicator');
  if (!el) return;
  el.textContent = '✓ Saved';
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 1500);
}

function renderSettings() {
  const s = settingsState.data;
  if (!s) return;

  const template = document.getElementById('set-template');
  if (template) template.value = s.filename_template || '';

  const folder = document.getElementById('set-folder');
  if (folder) folder.value = s.download_folder || '';

  const sanitize = document.getElementById('set-sanitize');
  if (sanitize) sanitize.checked = !!s.sanitize_filename;

  const autoOrg = document.getElementById('set-auto-org');
  if (autoOrg) autoOrg.checked = !!s.auto_organize_by_source;

  const id3 = document.getElementById('set-id3');
  if (id3) id3.checked = !!s.id3_tags;

  // Duplicate handling pill group
  document.querySelectorAll('.dup-pill').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.val === s.duplicate_handling);
  });

  // Default format toggle
  document.querySelectorAll('.def-fmt-pill').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.val === (s.default_format || 'video'));
  });

  runPreview();
}

function pickDupHandling(val) {
  document.querySelectorAll('.dup-pill').forEach(b => b.classList.remove('active'));
  document.querySelector(`.dup-pill[data-val="${val}"]`)?.classList.add('active');
  persistSettings({ duplicate_handling: val });
}

function pickDefaultFormat(val) {
  document.querySelectorAll('.def-fmt-pill').forEach(b => b.classList.remove('active'));
  document.querySelector(`.def-fmt-pill[data-val="${val}"]`)?.classList.add('active');
  persistSettings({ default_format: val });
}

function insertTemplateVar(v) {
  const el = document.getElementById('set-template');
  if (!el) return;
  const start = el.selectionStart || el.value.length;
  const end = el.selectionEnd || el.value.length;
  el.value = el.value.slice(0, start) + v + el.value.slice(end);
  el.focus();
  el.setSelectionRange(start + v.length, start + v.length);
  persistSettings({ filename_template: el.value });
  schedulePreview();
}

// Hook up nav click
document.addEventListener('DOMContentLoaded', () => {
  const nav = document.querySelector('[data-nav="settings"]');
  if (nav) nav.addEventListener('click', () => settingsLoad());
});
