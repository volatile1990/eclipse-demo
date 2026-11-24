'use strict';
const $ = id => document.getElementById(id);
let demos = [], state = null, filter = '', editing = false;
let version = 0, noticeId = null, editId = null, gridKey = '';
let browseStack = [];
let cur = folderFromHash();   // aktuell geöffneter Ordner, '' = oberste Ebene

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  node.append(...children.filter(c => c != null && c !== false && c !== ''));
  return node;
}

async function api(url, body) {
  const res = await fetch(url, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || 'Das hat nicht geklappt.');
  return data;
}

let toastTimer;
function toast(text) {
  $('toast').textContent = text;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 6000);
}
const act = fn => async (...args) => { try { await fn(...args); } catch (e) { toast(e.message); } };

function fmt(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return (h ? h + ':' + String(m).padStart(2, '0') : m) + ':' + String(s).padStart(2, '0');
}
function parseTime(text, name) {
  text = text.trim();
  if (!text) return null;
  const parts = text.split(':').map(p => Number(p.replace(',', '.')));
  if (parts.length > 3 || parts.some(p => !Number.isFinite(p) || p < 0))
    throw new Error(name + ': bitte als Stunden:Minuten:Sekunden angeben, z. B. 1:12:30.');
  return parts.reduce((sum, p) => sum * 60 + p, 0);
}

// ------------------------------------------------------------- Kacheln
const inFolder = (d, f) => !f || d.folder === f || d.folder.startsWith(f + '/');
const here = () => demos.filter(d => d.folder === cur);
const categories = () => [...new Set(here().map(d => d.category))];
const visible = () => here().filter(d => !filter || d.category === filter);

// ------------------------------------------------------------- Ordner
function folderFromHash() {
  const h = location.hash.replace(/^#\/?/, '');
  return h ? h.split('/').map(decodeURIComponent).join('/') : '';
}
function openFolder(f) {
  const target = f ? '#/' + f.split('/').map(encodeURIComponent).join('/') : '#/';
  if (location.hash !== target) location.hash = target; else { cur = f; filter = ''; render(true); }
}
window.addEventListener('hashchange', () => { cur = folderFromHash(); filter = ''; render(true); window.scrollTo(0, 0); });

function subfolders() {
  const seen = new Map();
  for (const d of demos) {
    if (!inFolder(d, cur) || d.folder === cur) continue;
    const rest = cur ? d.folder.slice(cur.length + 1) : d.folder;
    const name = rest.split('/')[0];
    const path = cur ? cur + '/' + name : name;
    const entry = seen.get(path) || { name, path, count: 0 };
    entry.count++;
    seen.set(path, entry);
  }
  return [...seen.values()].sort((a, b) => a.name.localeCompare(b.name, 'de', { numeric: true }));
}

function renderCrumbs() {
  const nav = $('crumbs');
  if (!cur) { nav.hidden = true; nav.replaceChildren(); return; }
  const parts = cur.split('/');
  const items = [el('button', { class: 'back', onclick: () => openFolder(parts.slice(0, -1).join('/')) }, 'Zurück'),
                 el('button', { class: 'crumb-link', onclick: () => openFolder('') }, 'Demos')];
  parts.forEach((name, i) => {
    items.push(el('span', { class: 'sep', 'aria-hidden': 'true' }, '›'));
    const path = parts.slice(0, i + 1).join('/');
    items.push(i === parts.length - 1
      ? el('span', { class: 'here', 'aria-current': 'page' }, name)
      : el('button', { class: 'crumb-link', onclick: () => openFolder(path) }, name));
  });
  nav.replaceChildren(...items);
  nav.hidden = false;
}

const FOLDER_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.6l2 2h8.4A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z" fill="currentColor"/></svg>';
const online = () => !!(state && state.zidoo.online);

function renderFilters() {
  const cats = categories();
  if (filter && !cats.includes(filter)) filter = '';
  const chip = (label, value) => el('button', {
    'aria-pressed': String(filter === value), onclick: () => { filter = value; render(true); } }, label);
  $('filters').replaceChildren(...(cats.length > 1 ? [chip('Alle', ''), ...cats.map(c => chip(c, c))] : []));
  $('filters').hidden = cats.length < 2;
  $('categories').replaceChildren(...cats.map(c => el('option', { value: c })));
}

function span(d) {
  if (d.end) return fmt(d.start) + '–' + fmt(d.end);
  return d.start ? 'ab ' + fmt(d.start) : '';
}

function renderGrid() {
  const activeId = state && state.active ? state.active.id : '';
  const activeDemo = demos.find(d => d.id === activeId);
  const folderTiles = filter ? [] : subfolders().map(f => {
    const icon = el('span', { class: 'ficon' });
    icon.innerHTML = FOLDER_ICON;
    const playing = activeDemo && inFolder(activeDemo, f.path);
    return el('div', { class: 'tile folder' + (playing ? ' active' : '') },
      el('button', { class: 'play', 'aria-label': 'Ordner ' + f.name + ' öffnen', onclick: () => openFolder(f.path) },
        icon,
        el('span', { class: 'title' }, f.name),
        el('span', { class: 'meta' },
          el('span', {}, f.count === 1 ? '1 Demo' : f.count + ' Demos'),
          playing && el('span', { class: 'now' }, 'Läuft'))));
  });
  const tiles = visible().map(d => {
    const isActive = d.id === activeId;
    const tile = el('div', { class: 'tile' + (isActive ? ' active' : '') },
      el('button', { class: 'play', onclick: act(() => editing ? openDemo(d) : api('/api/play', { id: d.id })) },
        el('span', { class: 'title' }, d.title),
        d.subtitle && el('span', { class: 'subtitle' }, d.subtitle),
        el('span', { class: 'meta' },
          el('span', {}, d.category),
          span(d) && el('span', {}, span(d)),
          isActive && el('span', { class: 'now' }, 'Läuft'))));
    if (editing) tile.append(el('div', { class: 'tools' },
      el('button', { 'aria-label': d.title + ' früher abspielen', onclick: act(() => move(d, -1)) }, 'Früher'),
      el('button', { 'aria-label': d.title + ' später abspielen', onclick: act(() => move(d, 1)) }, 'Später'),
      el('button', { 'aria-label': d.title + ' bearbeiten', onclick: () => openDemo(d) }, 'Bearbeiten')));
    return tile;
  });
  tiles.unshift(...folderTiles);
  if (!demos.length && state) {
    const ok = state.media.ok;
    tiles.push(el('div', { class: 'empty' },
      el('h2', {}, ok ? 'Noch keine Demos' : 'Demo-Ordner nicht erreichbar'),
      el('p', {}, ok ? 'Lege Videos oder Musik in diesen Ordner. Sie erscheinen hier nach wenigen Sekunden:'
                     : 'Der Heimserver kann diesen Ordner gerade nicht lesen:'),
      el('p', { class: 'mono' }, state.media.label),
      ok && el('p', {}, 'Unterordner werden zu Kategorien, z. B. „Musik“ oder „Atmos“.')));
  }
  $('grid').replaceChildren(...tiles);
}

function render(force) {
  const key = JSON.stringify([version, cur, filter, editing, state && state.active && state.active.id, !!state]);
  if (force || key !== gridKey) { gridKey = key; renderCrumbs(); renderFilters(); renderGrid(); }
  renderStatus();
  renderBar();
}

// ----------------------------------------------------- Status und Leiste
function renderStatus() {
  const z = state ? state.zidoo : null;
  let text = '', bannerText = '', link = null, action = null;
  if (!state) {
    bannerText = 'Keine Verbindung zum Heimserver.';
  } else if (!z.configured) {
    bannerText = 'Für den Zidoo ist keine Adresse eingerichtet.';
  } else if (!z.online) {
    bannerText = 'Der Zidoo ist aus. Schalte das Kino in Home Assistant ein.';
    link = ['Home Assistant öffnen', state.haUrl];
  } else if (!z.folder) {
    bannerText = z.searching ? 'Suche den Demo-Ordner auf dem Zidoo …'
      : 'Der Zidoo kennt den Demo-Ordner noch nicht. Lege am Zidoo die Netzwerkfreigabe der UNAS an oder wähle den Ordner von Hand.';
    if (!z.searching) action = ['Ordner wählen', act(openBrowse)];
  } else {
    text = (z.model || 'Zidoo') + ' bereit';
  }
  $('status').textContent = text;
  $('banner').hidden = !bannerText;
  $('bannerText').textContent = bannerText;
  $('bannerLink').hidden = !link;
  if (link) { $('bannerLink').textContent = link[0]; $('bannerLink').href = link[1]; }
  $('bannerAction').hidden = !action;
  if (action) { $('bannerAction').textContent = action[0]; $('bannerAction').onclick = action[1]; }
  const queue = demos.filter(d => inFolder(d, cur) && (!filter || d.category === filter));
  $('playAll').disabled = !queue.length || !online() || !z.folder;
  $('playAll').textContent = filter ? filter + ' abspielen' : cur ? 'Ordner abspielen' : 'Alle abspielen';
}

function renderBar() {
  const p = online() ? state.playing : null;
  const a = state ? state.active : null;
  $('bar').hidden = !p && !a;
  if (!p && !a) return;
  const demo = a && demos.find(d => d.id === a.id);
  let title = demo ? demo.title : (p ? p.title || 'Wiedergabe am Zidoo' : '');
  if (a && a.count > 1) title = (a.index + 1) + ' von ' + a.count + ' · ' + title;
  $('barTitle').textContent = title;
  let time = 'Startet …', value = 0;
  if (p && (!a || a.phase === 'running')) {
    const from = a ? a.start * 1000 : 0;
    const to = a && a.end ? a.end * 1000 : p.duration;
    const pos = Math.min(Math.max(p.position - from, 0), Math.max(to - from, 0));
    time = fmt(pos / 1000) + ' / ' + fmt((to - from) / 1000) + (p.state === 'paused' ? ' · pausiert' : '');
    value = to > from ? pos / (to - from) : 0;
  }
  $('barTime').textContent = time;
  $('barProgress').value = value;
  $('barPause').textContent = p && p.state === 'paused' ? 'Weiter' : 'Pause';
  $('barPause').disabled = !p;
  $('barNext').hidden = !(a && a.index + 1 < a.count);
}

// ----------------------------------------------------------- Bearbeiten
async function loadDemos() { demos = await api('/api/demos'); }
async function move(d, dir) { await api('/api/demos/move', { id: d.id, dir }); await poll(); }

function openDemo(d) {
  editId = d.id;
  $('fFile').textContent = d.id;
  $('fTitle').value = d.title;
  $('fSubtitle').value = d.subtitle;
  $('fCategory').value = d.category;
  $('fStart').value = d.start ? fmt(d.start) : '';
  $('fEnd').value = d.end ? fmt(d.end) : '';
  $('fVolume').value = d.volume != null ? d.volume : '';
  $('volumeRow').hidden = !(state && state.storm.configured);
  $('demoDialog').showModal();
}

function positionOfThisFile() {
  const p = online() ? state.playing : null;
  const name = editId.split('/').pop().toLowerCase();
  if (!p || !decodeURIComponent(p.path).toLowerCase().endsWith(name))
    throw new Error('Am Zidoo läuft gerade nicht diese Datei. Tippe auf „Anspielen“ und spule zur gewünschten Stelle.');
  return fmt(p.position / 1000);
}
$('startNow').onclick = act(() => { $('fStart').value = positionOfThisFile(); });
$('endNow').onclick = act(() => { $('fEnd').value = positionOfThisFile(); });
$('demoTry').onclick = act(() => api('/api/play', { id: editId }));
$('demoCancel').onclick = () => $('demoDialog').close();
$('demoForm').onsubmit = act(async e => {
  e.preventDefault();
  const volume = $('fVolume').value.trim().replace(',', '.');
  if (volume && !Number.isFinite(Number(volume))) throw new Error('Lautstärke: bitte eine Zahl in dB angeben, z. B. -25.');
  await api('/api/demos', {
    id: editId, title: $('fTitle').value, subtitle: $('fSubtitle').value, category: $('fCategory').value,
    start: parseTime($('fStart').value, 'Start') || 0, end: parseTime($('fEnd').value, 'Ende'),
    volume: volume === '' ? null : Number(volume) });
  $('demoDialog').close();
  await poll();
});

// --------------------------------------------------------- Einstellungen
function fillSettings() {
  if (!state) return;
  const z = state.zidoo;
  $('sMedia').textContent = state.media.label;
  $('sZidoo').textContent = !z.configured ? 'Keine Adresse eingerichtet.'
    : z.online ? (z.model || 'Zidoo') + ' ist erreichbar.' : 'Der Zidoo ist gerade aus.';
  $('sFolder').textContent = z.folder || (z.searching ? 'wird gesucht …' : 'noch nicht gefunden');
  $('folderSearch').disabled = $('folderBrowse').disabled = !z.online || z.searching;
  $('sStorm').textContent = state.storm.configured
    ? 'Demos können eine Lautstärke setzen, höchstens ' + state.storm.maxVolume + ' dB.'
    : 'Nicht eingerichtet. Demos ändern die Lautstärke nicht.';
}
$('settingsOpen').onclick = () => { fillSettings(); $('settingsDialog').showModal(); };
$('settingsClose').onclick = () => $('settingsDialog').close();
$('folderSearch').onclick = act(async () => {
  $('folderSearch').disabled = true;
  $('sFolder').textContent = 'wird gesucht …';
  try {
    const { folder } = await api('/api/folder', {});
    if (folder) toast('Demo-Ordner gefunden.');
  } finally { await poll(); fillSettings(); }
});

async function browseTo(entry) {
  const q = new URLSearchParams({ path: entry.path || '', via: entry.via || 'list', type: entry.type || 0 });
  const data = await api('/api/browse?' + q);
  browseStack.push(entry);
  $('browsePath').textContent = entry.path ? entry.name + ' · ' + entry.path : 'Laufwerke und Freigaben am Zidoo';
  $('browseUp').disabled = browseStack.length < 2;
  $('browsePick').disabled = !entry.path || entry.via !== 'list';
  const items = data.entries.map(e => el('li', {}, el('button', { type: 'button', onclick: act(() => browseTo(e)) }, e.name)));
  $('browseList').replaceChildren(...(items.length ? items
    : [el('li', {}, el('button', { type: 'button', disabled: '' }, 'Keine Unterordner.'))]));
}
async function openBrowse() {
  browseStack = [];
  await browseTo({ name: '', path: '', via: 'list', type: 0 });
  if (!$('browseDialog').open) $('browseDialog').showModal();
}
$('folderBrowse').onclick = act(openBrowse);
$('browseUp').onclick = act(async () => { browseStack.pop(); await browseTo(browseStack.pop()); });
$('browseCancel').onclick = () => $('browseDialog').close();
$('browsePick').onclick = act(async () => {
  await api('/api/folder', { path: browseStack[browseStack.length - 1].path });
  $('browseDialog').close();
  toast('Demo-Ordner übernommen.');
  await poll(); fillSettings();
});

// ----------------------------------------------------------- Kopfleiste
$('editToggle').onclick = () => {
  editing = !editing;
  $('editToggle').setAttribute('aria-pressed', String(editing));
  $('editToggle').textContent = editing ? 'Fertig' : 'Bearbeiten';
  render(true);
};
$('playAll').onclick = act(() => api('/api/playall', { folder: cur, category: filter || null }));
$('barPause').onclick = act(() => api('/api/pause', {}));
$('barNext').onclick = act(() => api('/api/next', {}));
$('barStop').onclick = act(() => api('/api/stop', {}));

// ---------------------------------------------------------------- Takt
async function poll() {
  try {
    state = await api('/api/state');
    if (state.version !== version) {
      await loadDemos(); version = state.version;
      if (cur && !demos.some(d => inFolder(d, cur))) {
        let up = cur;
        while (up && !demos.some(d => inFolder(d, up))) up = up.includes('/') ? up.slice(0, up.lastIndexOf('/')) : '';
        openFolder(up);
      }
    }
    if (noticeId !== null && state.notice.id !== noticeId && state.notice.text) toast(state.notice.text);
    noticeId = state.notice.id;
  } catch (e) {
    state = null;
  }
  render();
}
poll();
setInterval(poll, 1000);
