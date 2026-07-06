/**
 * Docker Backup Manager — Frontend
 * Vanilla JS, no build step required.
 */

const API = {
  containers:  () => fetch('/api/containers').then(r => r.json()),
  container:   (name) => fetch(`/api/containers/${encodeURIComponent(name)}`).then(r => r.json()),
  backups:     (name) => fetch(`/api/backups/${encodeURIComponent(name)}`).then(r => r.json()),
  snapshots:   (name) => fetch(`/api/restore/${encodeURIComponent(name)}/snapshots`).then(r => r.json()),
  triggerBackup:  (name, opts) => fetch(`/api/backups/${encodeURIComponent(name)}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  }).then(r => r.json()),
  triggerRestore: (name, opts) => fetch(`/api/restore/${encodeURIComponent(name)}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(opts),
  }).then(r => r.json()),
  job:          (id) => fetch(`/api/jobs/${id}`).then(r => r.json()),
  config:       () => fetch('/api/config').then(r => r.json()),
  scheduleStatus: () => fetch('/api/config/schedule').then(r => r.json()),
  reloadConfig: () => fetch('/api/config/reload', { method: 'POST' }).then(r => r.json()),
};

// ── State ──────────────────────────────────────────────────────────────────────
let state = {
  containers: [],
  selected: null,
  backups: [],
  snapshots: [],
  filter: '',
  activeJobs: new Map(),   // jobId → intervalId
};

// ── Boot ───────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadContainers();
  loadSchedulerStatus();
  setInterval(refreshContainerStatuses, 15000);
  setInterval(loadSchedulerStatus, 60000);

  document.getElementById('search').addEventListener('input', e => {
    state.filter = e.target.value.toLowerCase();
    renderSidebar();
  });

  document.getElementById('btn-backup-all').addEventListener('click', () => {
    if (confirm('Back up ALL containers now?')) triggerBackupAll();
  });

  document.getElementById('btn-refresh').addEventListener('click', loadContainers);
});

// ── Container loading ──────────────────────────────────────────────────────────
async function loadContainers() {
  try {
    state.containers = await API.containers();
    renderSidebar();
    if (state.selected) {
      const found = state.containers.find(c => c.name === state.selected);
      if (found) selectContainer(found);
    }
    updateHeaderMeta();
  } catch (e) {
    showToast('error', 'Load failed', 'Could not reach the API. Is the server running?');
  }
}

async function refreshContainerStatuses() {
  try {
    const updated = await API.containers();
    state.containers = updated;
    renderSidebar();
    updateHeaderMeta();
    if (state.selected) {
      const c = updated.find(c => c.name === state.selected);
      if (c) updateDetailHeader(c);
    }
  } catch (_) {}
}

function updateHeaderMeta() {
  const running = state.containers.filter(c => c.status === 'running').length;
  const schedEl = document.getElementById('sched-meta');
  const schedText = schedEl ? schedEl.textContent : '';
  document.getElementById('header-meta').textContent =
    `${state.containers.length} containers · ${running} running${schedText ? '  ·  ' + schedText : ''}`;
}

async function loadSchedulerStatus() {
  try {
    const s = await API.scheduleStatus();
    const el = document.getElementById('sched-meta');
    if (!el) return;
    if (!s.enabled) {
      el.textContent = '';
      return;
    }
    const next = s.next_run ? relativeTime(s.next_run) : 'unknown';
    el.textContent = `⏱ next backup ${next}`;
    updateHeaderMeta();
  } catch (_) {}
}

// ── Sidebar rendering ──────────────────────────────────────────────────────────
function renderSidebar() {
  const list = document.getElementById('container-list');
  const filtered = state.containers.filter(c =>
    c.name.toLowerCase().includes(state.filter)
  );

  document.getElementById('sidebar-count').textContent = `(${filtered.length})`;

  list.innerHTML = filtered.map(c => `
    <div
      class="container-card ${c.name === state.selected ? 'active' : ''} ${c.is_self ? 'is-self' : ''}"
      data-name="${esc(c.name)}"
      onclick="${c.is_self ? '' : `selectByName('${esc(c.name)}')`}"
      title="${c.is_self ? 'Backup manager — excluded' : c.image}"
    >
      <div class="status-led ${c.status}"></div>
      <div class="card-body">
        <div class="card-name">${esc(c.name)}${c.is_self ? ' <span style="opacity:.5;font-size:10px">(self)</span>' : ''}</div>
        <div class="card-meta">
          ${c.compose?.project_name
            ? `<span class="card-compose-tag" title="${esc(c.compose.project_name)}">${esc(c.compose.project_name)}</span>`
            : ''}
          <span class="card-last-backup">${c.last_backup ? relativeTime(c.last_backup) : 'never backed up'}</span>
        </div>
      </div>
    </div>
  `).join('');
}

// ── Container selection ────────────────────────────────────────────────────────
function selectByName(name) {
  const c = state.containers.find(c => c.name === name);
  if (c) selectContainer(c);
}

async function selectContainer(c) {
  state.selected = c.name;
  renderSidebar();
  renderDetailLoading(c);

  const [backups, snapshots] = await Promise.all([
    API.backups(c.name).catch(() => []),
    API.snapshots(c.name).catch(() => []),
  ]);

  state.backups = backups;
  state.snapshots = snapshots;
  renderDetail(c, backups, snapshots);
}

// ── Detail panel ───────────────────────────────────────────────────────────────
function renderDetailLoading(c) {
  document.getElementById('detail').innerHTML = `
    <div class="detail-header">
      <div class="status-led detail-status-led ${c.status}"></div>
      <div>
        <div class="detail-name">${esc(c.name)}</div>
        <div class="detail-image">${esc(c.image)}</div>
      </div>
    </div>
    <div class="detail-empty" style="flex:1">
      <div class="spinner" style="width:24px;height:24px;border-width:3px"></div>
      <div>Loading backup history…</div>
    </div>
  `;
}

function updateDetailHeader(c) {
  const led = document.querySelector('.detail-status-led');
  if (led) {
    led.className = `status-led detail-status-led ${c.status}`;
  }
}

function renderDetail(c, backups, snapshots) {
  const dataBackups     = backups.filter(b => b.backup_type === 'data');
  const composeBackups  = backups.filter(b => b.backup_type === 'compose');
  const internalBackups = backups.filter(b => b.backup_type === 'internal');

  const statusTag = {
    running:  '<span class="tag tag-green">running</span>',
    stopped:  '<span class="tag tag-amber">stopped</span>',
    exited:   '<span class="tag tag-red">exited</span>',
    paused:   '<span class="tag tag-blue">paused</span>',
  }[c.status] || `<span class="tag">${c.status}</span>`;

  const composeTag = c.compose?.project_name
    ? `<span class="tag tag-purple">${esc(c.compose.project_name)}</span>`
    : '';

  const volTag = c.has_internal_volumes
    ? '<span class="tag tag-amber">named volumes</span>'
    : '';

  document.getElementById('detail').innerHTML = `
    <!-- Header -->
    <div class="detail-header">
      <div class="status-led detail-status-led ${c.status}" style="width:10px;height:10px;margin-top:6px"></div>
      <div style="flex:1;min-width:0">
        <div class="detail-name">${esc(c.name)}</div>
        <div class="detail-image">${esc(c.image)}</div>
        <div class="detail-tags" style="margin-top:6px">
          ${statusTag}${composeTag}${volTag}
          ${c.backup_count > 0 ? `<span class="tag tag-blue">${c.backup_count} backup${c.backup_count !== 1 ? 's' : ''}</span>` : ''}
        </div>
      </div>
      <div class="detail-actions">
        <button class="btn btn-primary btn-sm" onclick="openBackupModal()">↑ Backup</button>
        <button class="btn btn-danger btn-sm" onclick="openRestoreModal()" ${backups.length === 0 ? 'disabled' : ''}>↺ Restore</button>
      </div>
    </div>

    <!-- Metadata -->
    <div class="detail-meta">
      ${metaCell('Status', c.status)}
      ${metaCell('Data Directory', c.data_dir || '—')}
      ${metaCell('Compose Project', c.compose?.project_name || '—')}
      ${metaCell('Compose Siblings', c.compose?.shared_containers?.length
        ? c.compose.shared_containers.join(', ')
        : '—')}
      ${metaCell('External Mounts', c.has_external_mounts ? 'yes' : 'no')}
      ${metaCell('Internal Volumes', c.has_internal_volumes ? 'yes' : 'no')}
      ${metaCell('Last Backup', c.last_backup ? formatDate(c.last_backup) : 'never')}
      ${metaCell('Image', c.image)}
    </div>

    <!-- Backup history -->
    <div class="history-section">
      <div class="section-header">
        <span class="section-title">Backup History</span>
        <button class="btn btn-ghost btn-sm" onclick="selectContainer(state.containers.find(c=>c.name==='${esc(c.name)}'))">↻ Refresh</button>
      </div>

      ${backups.length === 0
        ? '<div class="backup-empty">No backups yet — click Backup to create one</div>'
        : `
          ${renderBackupGroup('Data', dataBackups)}
          ${renderBackupGroup('Compose', composeBackups)}
          ${renderBackupGroup('Internal', internalBackups)}
        `
      }

      ${snapshots.length > 0 ? `
        <div class="section-header" style="margin-top:24px">
          <span class="section-title" style="color:var(--amber)">⚠ Safety Snapshots</span>
        </div>
        ${snapshots.map(b => renderBackupRow(b, true)).join('')}
      ` : ''}
    </div>
  `;
}

function metaCell(label, value) {
  return `
    <div class="meta-cell">
      <div class="meta-label">${esc(label)}</div>
      <div class="meta-value">${esc(String(value))}</div>
    </div>
  `;
}

function renderBackupGroup(title, records) {
  if (records.length === 0) return '';
  return `
    <div class="backup-group">
      <div class="backup-group-label">${esc(title)}</div>
      ${records.map(b => renderBackupRow(b, false)).join('')}
    </div>
  `;
}

function renderBackupRow(b, isSnapshot) {
  return `
    <div class="backup-row ${isSnapshot ? 'snapshot' : ''}">
      <span class="backup-type-badge ${b.backup_type}">${b.backup_type}</span>
      <span class="backup-ts">${formatDate(b.timestamp)}</span>
      <span class="backup-size">${b.size_human}</span>
      <div class="backup-actions">
        <button class="btn-icon btn-sm" title="Restore from this backup"
          onclick="openRestoreModalWithBackup(${JSON.stringify(b).replace(/"/g, '&quot;')})">↺</button>
      </div>
    </div>
  `;
}

// ── Backup modal ───────────────────────────────────────────────────────────────
function openBackupModal() {
  const c = state.containers.find(c => c.name === state.selected);
  if (!c) return;

  const hasSiblings = c.compose?.shared_containers?.length > 0;
  const siblingsNote = hasSiblings ? `
    <div class="alert alert-warning">
      ⚠ This container shares the <strong>${esc(c.compose.project_name)}</strong> compose stack with
      ${c.compose.shared_containers.length} other container(s):
      ${esc(c.compose.shared_containers.join(', '))}.
    </div>
    <div class="form-group">
      <label class="form-label">Backup Scope</label>
      <label class="form-check">
        <input type="radio" name="scope" value="single" checked>
        <span class="form-check-label">This container only</span>
      </label>
      <label class="form-check">
        <input type="radio" name="scope" value="all">
        <span class="form-check-label">All containers in compose group</span>
      </label>
    </div>
  ` : '';

  showModal('Backup: ' + c.name, `
    <div class="form-group">
      <label class="form-label">What to back up</label>
      <label class="form-check">
        <input type="checkbox" id="bk-data" checked ${!c.data_dir ? 'disabled' : ''}>
        <span class="form-check-label">Data directory${!c.data_dir ? ' <span style="color:var(--text-dim)">(not found)</span>' : ''}</span>
      </label>
      <label class="form-check">
        <input type="checkbox" id="bk-compose" checked ${!c.compose?.config_files?.length ? 'disabled' : ''}>
        <span class="form-check-label">Compose file(s)${!c.compose?.config_files?.length ? ' <span style="color:var(--text-dim)">(not discovered)</span>' : ''}</span>
      </label>
      <label class="form-check">
        <input type="checkbox" id="bk-internal" ${!c.has_internal_volumes ? 'disabled' : ''}>
        <span class="form-check-label">Internal volumes${!c.has_internal_volumes ? ' <span style="color:var(--text-dim)">(none detected)</span>' : ''}</span>
      </label>
    </div>
    ${siblingsNote}
  `, [
    { label: 'Cancel', cls: 'btn-ghost', action: closeModal },
    { label: '↑ Start Backup', cls: 'btn-primary', action: () => submitBackup(c, hasSiblings) },
  ]);
}

async function submitBackup(c, hasSiblings) {
  const opts = {
    backup_data:    document.getElementById('bk-data')?.checked ?? false,
    backup_compose: document.getElementById('bk-compose')?.checked ?? false,
    backup_internal: document.getElementById('bk-internal')?.checked ?? false,
    include_compose_siblings: hasSiblings
      ? document.querySelector('input[name="scope"]:checked')?.value === 'all'
      : false,
  };

  closeModal();
  try {
    const job = await API.triggerBackup(c.name, opts);
    trackJob(job, c.name, 'backup');
  } catch (e) {
    showToast('error', 'Backup failed', e.message);
  }
}

async function triggerBackupAll() {
  for (const c of state.containers) {
    if (c.is_self || c.status !== 'running') continue;
    try {
      const job = await API.triggerBackup(c.name, {
        backup_data: true, backup_compose: true, backup_internal: false,
      });
      trackJob(job, c.name, 'backup');
    } catch (e) {
      showToast('error', `Backup failed: ${c.name}`, e.message);
    }
  }
}

// ── Restore modal ──────────────────────────────────────────────────────────────
function openRestoreModal() {
  _openRestoreModalInternal(null);
}

function openRestoreModalWithBackup(b) {
  _openRestoreModalInternal(b);
}

function _openRestoreModalInternal(preSelected) {
  const c = state.containers.find(c => c.name === state.selected);
  if (!c) return;

  const dataBackups     = state.backups.filter(b => b.backup_type === 'data');
  const composeBackups  = state.backups.filter(b => b.backup_type === 'compose');
  const internalBackups = state.backups.filter(b => b.backup_type === 'internal');

  const backupList = (items, prefix) => items.length === 0
    ? '<div style="color:var(--text-dim);font-size:12px;padding:6px 0">No backups of this type</div>'
    : `<div class="backup-select-list">
        ${items.map((b, i) => `
          <label class="backup-select-item ${preSelected?.filename === b.filename ? 'selected' : ''}">
            <input type="radio" name="${prefix}" value="${esc(b.filename)}" ${(preSelected?.filename === b.filename || i === 0) ? 'checked' : ''}>
            <span class="backup-type-badge ${b.backup_type}">${b.backup_type}</span>
            <span style="flex:1;font-family:var(--font-mono);font-size:12px">${formatDate(b.timestamp)}</span>
            <span style="color:var(--text-muted);font-size:11px">${b.size_human}</span>
          </label>
        `).join('')}
      </div>`;

  showModal('Restore: ' + c.name, `
    <div class="alert alert-warning">
      ⚠ A safety snapshot of the current state will be taken before restore begins.
      The container will be stopped during restore.
    </div>

    <div class="form-group">
      <label class="form-check" style="margin-bottom:8px">
        <input type="checkbox" id="rs-data" checked>
        <span class="form-check-label" style="font-weight:600">Restore Data Directory</span>
      </label>
      ${backupList(dataBackups, 'rs-data-id')}
    </div>

    <div class="form-group">
      <label class="form-check" style="margin-bottom:8px">
        <input type="checkbox" id="rs-compose" ${composeBackups.length === 0 ? 'disabled' : 'checked'}>
        <span class="form-check-label" style="font-weight:600">Restore Compose File(s)</span>
      </label>
      ${backupList(composeBackups, 'rs-compose-id')}
    </div>

    <div class="form-group">
      <label class="form-check" style="margin-bottom:8px">
        <input type="checkbox" id="rs-internal" ${internalBackups.length === 0 ? 'disabled' : ''}>
        <span class="form-check-label" style="font-weight:600">Restore Internal Data</span>
      </label>
      ${backupList(internalBackups, 'rs-internal-id')}
    </div>
  `, [
    { label: 'Cancel', cls: 'btn-ghost', action: closeModal },
    { label: '↺ Start Restore', cls: 'btn-danger', action: () => submitRestore(c) },
  ]);
}

async function submitRestore(c) {
  const opts = {
    restore_data:       document.getElementById('rs-data')?.checked ?? false,
    restore_compose:    document.getElementById('rs-compose')?.checked ?? false,
    restore_internal:   document.getElementById('rs-internal')?.checked ?? false,
    backup_id_data:     document.querySelector('input[name="rs-data-id"]:checked')?.value || null,
    backup_id_compose:  document.querySelector('input[name="rs-compose-id"]:checked')?.value || null,
    backup_id_internal: document.querySelector('input[name="rs-internal-id"]:checked')?.value || null,
  };

  closeModal();
  try {
    const job = await API.triggerRestore(c.name, opts);
    trackJob(job, c.name, 'restore');
  } catch (e) {
    showToast('error', 'Restore failed', e.message);
  }
}

// ── Job tracking ───────────────────────────────────────────────────────────────
function trackJob(job, containerName, type) {
  showToast('running', `${type === 'backup' ? '↑ Backing up' : '↺ Restoring'}: ${containerName}`, 'Job started…', job.id);

  const interval = setInterval(async () => {
    try {
      const updated = await API.job(job.id);
      updateToast(job.id, updated, containerName, type);
      if (['success', 'failed', 'cancelled'].includes(updated.status)) {
        clearInterval(interval);
        state.activeJobs.delete(job.id);
        // Refresh container list and backup history after job completes
        setTimeout(() => {
          loadContainers();
          if (state.selected === containerName) selectByName(containerName);
        }, 800);
      }
    } catch (_) {}
  }, 2000);

  state.activeJobs.set(job.id, interval);
}

// ── Modal helper ───────────────────────────────────────────────────────────────
function showModal(title, bodyHtml, buttons) {
  const overlay = document.getElementById('modal-overlay');
  overlay.querySelector('.modal-title').textContent = title;
  overlay.querySelector('.modal-body').innerHTML = bodyHtml;

  const footer = overlay.querySelector('.modal-footer');
  footer.innerHTML = buttons.map(b =>
    `<button class="btn ${b.cls}">${b.label}</button>`
  ).join('');

  buttons.forEach((b, i) => {
    footer.querySelectorAll('button')[i].addEventListener('click', b.action);
  });

  overlay.classList.remove('hidden');
}

function closeModal() {
  document.getElementById('modal-overlay').classList.add('hidden');
}

// ── Toast helper ───────────────────────────────────────────────────────────────
function showToast(type, title, msg, jobId = null) {
  const container = document.getElementById('toast-container');
  const id = jobId || `toast-${Date.now()}`;
  const icon = { success: '✓', error: '✗', running: '' }[type] || 'ℹ';
  const spinnerHtml = type === 'running' ? '<div class="spinner"></div>' : '';

  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.id = `toast-${id}`;
  el.innerHTML = `
    <div class="toast-icon">${spinnerHtml || icon}</div>
    <div class="toast-body">
      <div class="toast-title">${esc(title)}</div>
      <div class="toast-msg">${esc(msg)}</div>
    </div>
  `;
  container.appendChild(el);

  if (type !== 'running') {
    setTimeout(() => el.remove(), 5000);
  }
}

function updateToast(jobId, job, containerName, type) {
  const el = document.getElementById(`toast-${jobId}`);
  if (!el) return;

  const lastLog = job.log?.slice(-1)[0]?.message || '';

  if (job.status === 'success') {
    el.className = 'toast success';
    el.innerHTML = `
      <div class="toast-icon">✓</div>
      <div class="toast-body">
        <div class="toast-title">${type === 'backup' ? 'Backup' : 'Restore'} complete: ${esc(containerName)}</div>
        <div class="toast-msg">${esc(lastLog)}</div>
      </div>
    `;
    setTimeout(() => el.remove(), 6000);
  } else if (job.status === 'failed') {
    el.className = 'toast error';
    el.innerHTML = `
      <div class="toast-icon">✗</div>
      <div class="toast-body">
        <div class="toast-title">${type === 'backup' ? 'Backup' : 'Restore'} failed: ${esc(containerName)}</div>
        <div class="toast-msg">${esc(job.error || lastLog)}</div>
      </div>
    `;
    setTimeout(() => el.remove(), 10000);
  } else {
    const msgEl = el.querySelector('.toast-msg');
    if (msgEl && lastLog) msgEl.textContent = lastLog;
  }
}

// ── Utilities ──────────────────────────────────────────────────────────────────
function esc(str) {
  return String(str ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString(undefined, {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function relativeTime(iso) {
  if (!iso) return 'never';
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1)   return 'just now';
  if (m < 60)  return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24)  return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

// Close modal on overlay click
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('modal-overlay').addEventListener('click', e => {
    if (e.target === e.currentTarget) closeModal();
  });
});
