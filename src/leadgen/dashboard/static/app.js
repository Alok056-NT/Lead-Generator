/* Lead Generator dashboard. Plain JavaScript, no libraries, no network except this computer.
   Everything that comes from the server is put on the page as text (never as HTML). */
'use strict';

// ---- small helpers ----------------------------------------------------------------------------

const main = document.getElementById('main');
const panel = document.getElementById('panel');
const toastBox = document.getElementById('toast');

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [key, val] of Object.entries(props || {})) {
    if (val === null || val === undefined || val === false) continue;
    if (key === 'class') el.className = val;
    else if (key === 'text') el.textContent = val;
    else if (key.startsWith('on') && typeof val === 'function') el.addEventListener(key.slice(2), val);
    else if (key === 'value' || key === 'checked' || key === 'selected') el[key] = val;
    else if (val === true) el.setAttribute(key, '');
    else el.setAttribute(key, String(val));
  }
  addKids(el, kids);
  return el;
}

function addKids(el, kids) {
  for (const kid of kids) {
    if (kid === null || kid === undefined || kid === false) continue;
    if (Array.isArray(kid)) addKids(el, kid);
    else if (kid instanceof Node) el.appendChild(kid);
    else el.appendChild(document.createTextNode(String(kid)));
  }
}

function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
}

function safeUrl(url) {
  return typeof url === 'string' && /^https?:\/\//i.test(url) ? url : '';
}

function extLink(url, label) {
  const href = safeUrl(url);
  if (!href) return h('span', { text: label || url || '' });
  return h('a', { href, target: '_blank', rel: 'noopener noreferrer', text: label || url });
}

function fmtNum(n) {
  return typeof n === 'number' ? n.toLocaleString() : String(n ?? '');
}

function fmtPct(n) {
  return n === null || n === undefined ? '-' : (n * 100).toFixed(1) + '%';
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
}

function fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

async function api(path, options) {
  const opts = options || {};
  const method = opts.method || 'GET';
  const init = { method, headers: {} };
  if (method !== 'GET') init.headers['X-LeadGen'] = '1';
  if (opts.form !== undefined) {
    init.body = opts.form; // the browser sets the multipart header itself
  } else if (opts.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  try {
    const res = await fetch(path, init);
    return await res.json();
  } catch (err) {
    return {
      ok: false,
      error: {
        code: 'unreachable',
        message: 'The dashboard is not answering. Was it closed?',
        hint: 'Start it again with "leadgen dashboard", or ask the AI to open the dashboard.',
      },
    };
  }
}

// Hints that name a tool or a code (they are written for the AI) are not shown to people.
function plainHint(hint) {
  return hint && !/[_()=]/.test(hint) ? hint : '';
}

function errorText(reply) {
  const e = (reply && reply.error) || {};
  return [e.message || (reply && reply.message) || 'Something went wrong.', plainHint(e.hint)].filter(Boolean).join(' ');
}

let toastTimer = null;
function toast(message, tone) {
  clear(toastBox);
  toastBox.appendChild(h('div', { class: 'notice ' + (tone || 'ok'), text: message }));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => clear(toastBox), 6000);
}

function query(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== '') q.set(k, v);
  }
  const s = q.toString();
  return s ? '?' + s : '';
}

// ---- labels -----------------------------------------------------------------------------------

const PERSON_STATUS = {
  confirmed: 'Ready', reserve: 'Saved for a later campaign', review: 'Needs a look', rejected: 'Rejected', removed: 'Removed',
};
const PERSON_TONE = { confirmed: 'ok', reserve: 'info', review: 'warn', rejected: 'bad', removed: 'none' };
const COMPANY_STATUS = { candidate: 'Waiting for approval', approved: 'Approved', excluded: 'Left out' };
const COMPANY_TONE = { candidate: 'info', approved: 'ok', excluded: 'none' };
const NOT_VERIFIED = "Not verified (won't be used)";
const EMAIL_STATUS = {
  verified: 'Verified', risky: NOT_VERIFIED, guess: NOT_VERIFIED, unknown: NOT_VERIFIED,
  invalid: 'Not a working address', none: 'No email found',
};
const EMAIL_TONE = { verified: 'ok', risky: 'warn', guess: 'warn', unknown: 'warn', invalid: 'bad', none: 'none' };
const KNOWN = { contacted: 'Known contact', replied: 'Replied before', customer: 'Customer', partner: 'Partner' };
const ORIGIN = { api: 'Found by a search service', ai_web_search: "Found by the AI's web search", user: 'Added by you' };
const AI_VERDICT = {
  pass: 'Passed', fix: 'Needs a fix', reject: 'Rejected', pending: 'Not checked yet',
  no_messages: 'No messages',
};
const AI_TONE = { pass: 'ok', fix: 'warn', reject: 'bad', pending: 'info', no_messages: 'none' };
const CHANNEL = { email: 'Email', linkedin_invite: 'LinkedIn invitation', linkedin_message: 'LinkedIn message' };
const MESSAGE_STATUS = {
  draft: 'Draft', qa_fail: 'Did not pass the checks', qa_pass: 'Passed the checks', approved: 'Approved',
  rejected: 'Rejected', sent: 'Sent',
};
const MESSAGE_TONE = { draft: 'none', qa_fail: 'bad', qa_pass: 'info', approved: 'ok', rejected: 'bad', sent: 'ok' };
const SENIORITY = {
  individual: 'Anyone', manager: 'Managers and above', director: 'Directors and above',
  vp: 'Vice presidents and above', c_suite: 'Top leaders only',
};
const CAMPAIGN_STATUS = { draft: 'Not started', active: 'Running', archived: 'Archived' };
const MARK = { ok: '✓ ', warn: '! ', bad: '✕ ', info: '', none: '' };

// What the AI does next, in plain words (the server's own text is written for the AI).
const NEXT_STEP = {
  project: 'Create your first project: choose "New project" below.',
  offer: 'Attach your product or service to the project (open the project and use "Replace the source").',
  offer_profile: 'Open your AI assistant and ask it to set up the project. It will write down what you offer, together with you.',
  campaign: 'Create a campaign: open the project and choose "New campaign".',
  approve_companies: 'Companies were added. Look at them and approve the ones you want.',
  companies: 'Ask your AI assistant to suggest companies for this campaign.',
  people_ai: 'Ask your AI assistant to look for the people at the approved companies.',
  keys: 'Add a free search key on the Keys page, or ask your AI assistant to use its own web search.',
  people: 'Ask your AI assistant to look for the people at the approved companies.',
  people_reserve: 'Everyone found is saved for a later campaign because of the size limits. Raise the limits or add more companies.',
  people_review: 'Nobody is ready yet. Look at the people who need a look, or search more companies.',
  emails: 'Ask your AI assistant to find and verify email addresses.',
  drafting: 'Ask your AI assistant to write the messages.',
  fix_drafts: 'Some messages did not pass the checks. Ask your AI assistant to fix them.',
  review: 'Messages are waiting to be checked by the AI.',
  spot_check: "Some people were found by the AI's web search. Please spot-check a few of them before you export.",
  deliver: 'Everything is checked. Export the people, or send from your mailbox (optional).',
  analytics: 'Emails were sent. Look at the results on the Results page.',
  run_qa: 'Run the checks to see where the campaign stands.',
};

function nextStepText(step) {
  if (!step) return 'Nothing to do right now.';
  return NEXT_STEP[step.step] || 'Ask your AI assistant to carry on.';
}

function emailLabel(status, allowRisky) {
  if (status === 'risky' && allowRisky) return 'Risky address (this campaign allows it)';
  return label(EMAIL_STATUS, status);
}

function badge(text, tone) {
  const t = tone || 'none';
  return h('span', { class: 'badge b-' + t, text: (MARK[t] || '') + text });
}

function label(map, key) {
  return map[key] || key || '';
}

function qaBadge(block, warn) {
  if (block) return badge(block + ' to fix', 'bad');
  if (warn) return badge(warn + ' to look at', 'warn');
  return badge('No problems', 'ok');
}

// A line the server wrote with a code in front ("L04:company is not shown ...") becomes the plain
// sentence first and the code in small grey text after it.
function reasonLine(text) {
  const raw = String(text || '');
  const manual = raw.startsWith('MANUAL:');
  const m = /^(L\d\d):\s*(.*)$/.exec(raw);
  if (manual) return h('li', { text: 'Changed by hand: ' + raw.slice(7).trim() });
  if (!m) return h('li', { text: raw });
  return h('li', null, m[2], ' ', h('span', { class: 'code', text: '(' + m[1] + ')' }));
}

function reasonList(reasons) {
  return h('ul', null, ...(reasons || []).map(reasonLine));
}

// ---- pieces used on several pages -------------------------------------------------------------

function notice(tone, ...kids) {
  return h('div', { class: 'notice ' + tone, role: tone === 'bad' ? 'alert' : null }, ...kids);
}

function listOf(items) {
  return h('ul', null, ...items.map((t) => h('li', { text: t })));
}

function pager(data, goto) {
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  return h(
    'div',
    { class: 'pager' },
    h('button', { type: 'button', disabled: data.page <= 1, onclick: () => goto(data.page - 1), text: 'Previous' }),
    h('span', { text: `Page ${data.page} of ${pages} (${fmtNum(data.total)} in all)` }),
    h('button', { type: 'button', disabled: !data.has_more, onclick: () => goto(data.page + 1), text: 'Next' }),
  );
}

function field(id, labelText, input) {
  return h('div', { class: 'field' }, h('label', { for: id, text: labelText }), input);
}

function whatsThis(text) {
  return h('p', { class: 'whats' }, h('strong', { text: "What's this? " }), text);
}

function checkField(id, labelText, input, help) {
  return h('div', { class: 'field' }, h('label', { class: 'check', for: id }, input, labelText),
    help ? h('p', { class: 'small muted', text: help }) : null);
}

function selectBox(id, options, current, onchange) {
  return h(
    'select',
    { id, onchange },
    ...options.map(([val, text]) => h('option', { value: val, selected: String(val) === String(current ?? ''), text })),
  );
}

function bar(value, max, cls) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('class', 'bar');
  svg.setAttribute('viewBox', '0 0 100 10');
  svg.setAttribute('preserveAspectRatio', 'none');
  svg.setAttribute('aria-hidden', 'true');
  const track = document.createElementNS(ns, 'rect');
  track.setAttribute('class', 'track');
  track.setAttribute('width', '100');
  track.setAttribute('height', '10');
  const fill = document.createElementNS(ns, 'rect');
  fill.setAttribute('class', cls || 'fill');
  fill.setAttribute('width', String(pct));
  fill.setAttribute('height', '10');
  svg.append(track, fill);
  return svg;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (err) {
    const area = h('textarea', { 'aria-hidden': 'true' });
    area.value = text;
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand('copy');
    } finally {
      document.body.removeChild(area);
    }
  }
  toast('Copied.');
}

function issueList(issues) {
  if (!issues || !issues.length) return null;
  return h(
    'ul',
    { class: 'small' },
    ...issues.map((i) =>
      h(
        'li',
        null,
        badge(i.severity === 'block' ? 'To fix' : 'To look at', i.severity === 'block' ? 'bad' : 'warn'),
        ' ',
        h('strong', { text: i.title || '' }),
        i.detail ? ' - ' + i.detail : '',
        ' ',
        h('span', { class: 'code', text: '(' + i.code + ')' }),
      ),
    ),
  );
}

// ---- routing ----------------------------------------------------------------------------------

let routeToken = 0;
let lastFocusBeforePanel = null;

function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/home';
  const [path, qs] = raw.split('?');
  return { parts: path.split('/').filter(Boolean), q: new URLSearchParams(qs || '') };
}

function go(path, params) {
  location.hash = '#' + path + query(params || {});
}

const TOP_NAV = [
  ['home', 'Projects'],
  ['campaigns', 'Campaigns'],
  ['analytics', 'Results'],
  ['keys', 'Keys'],
  ['settings', 'Settings'],
];
const CAMPAIGN_NAV = [
  ['', 'Overview'],
  ['companies', '1 Companies'],
  ['leads', '2 People'],
  ['messages', '4 Messages'],
  ['export', '6 Export or send'],
  ['analytics', 'Results'],
];

function renderNav(topKey, campaign, subKey) {
  const nav = document.getElementById('nav');
  clear(nav);
  for (const [key, text] of TOP_NAV) {
    nav.appendChild(h('a', { href: '#/' + key, 'aria-current': key === topKey ? 'page' : null, text }));
  }
  const sub = document.getElementById('subnav');
  clear(sub);
  if (!campaign) {
    sub.hidden = true;
    return;
  }
  sub.hidden = false;
  if (campaign.project) {
    sub.appendChild(h('a', { class: 'crumb-link', href: '#/p/' + campaign.project.id, text: campaign.project.name }));
    sub.appendChild(h('span', { class: 'crumb', 'aria-hidden': 'true', text: '/' }));
  }
  sub.appendChild(h('span', { class: 'crumb', text: campaign.name }));
  for (const [key, text] of CAMPAIGN_NAV) {
    const href = '#/c/' + campaign.id + (key ? '/' + key : '');
    sub.appendChild(h('a', { href, 'aria-current': key === subKey ? 'page' : null, text }));
  }
}

function setMain(...kids) {
  clear(main);
  addKids(main, kids);
}

function loading() {
  setMain(h('p', { role: 'status', text: 'Loading...' }));
}

async function route() {
  const token = ++routeToken;
  closePanel(false);
  const { parts, q } = parseHash();
  const top = parts[0] || 'home';
  loading();
  let campaign = null;
  let sub = '';
  if (top === 'c') {
    const id = Number(parts[1]);
    sub = parts[2] || '';
    const reply = await api('/api/campaigns/' + id);
    if (token !== routeToken) return;
    if (!reply.ok) {
      renderNav('campaigns', null, '');
      setMain(notice('bad', errorText(reply)), h('a', { href: '#/campaigns', text: 'Back to your campaigns' }));
      return;
    }
    campaign = reply.campaign;
    campaign.overview = reply;
    campaign.project = reply.project;
    renderNav('campaigns', campaign, sub);
  } else {
    renderNav(top === 'p' || top === 'new-project' ? 'home' : top, null, '');
  }
  const pages = {
    home: pageHome, keys: pageKeys, campaigns: pageCampaigns, analytics: pageAnalytics, settings: pageSettings,
    'new-project': pageNewProject, mailbox: pageMailbox,
    p: parts[2] === 'new-campaign' ? pageNewCampaign : parts[2] === 'contacts' ? pageContacts : pageProject,
  };
  const subPages = {
    '': pageOverview, companies: pageCompanies, leads: pageLeads, messages: pageMessages,
    export: pageExport, analytics: pageAnalytics,
  };
  const ctx = { token, q, campaign, projectId: top === 'p' ? Number(parts[1]) : null };
  const render = top === 'c' ? subPages[sub] : pages[top];
  if (!render) {
    setMain(h('h2', { text: 'Page not found' }), h('a', { href: '#/home', text: 'Go to your projects' }));
    return;
  }
  try {
    await render(ctx);
  } catch (err) {
    if (token === routeToken) setMain(notice('bad', 'This page could not be shown. Reload and try again.'));
    console.error(err);
  }
  if (token === routeToken) {
    document.title = 'Lead Generator';
    main.focus({ preventScroll: true });
  }
}

window.addEventListener('hashchange', route);
document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && !panel.hidden) closePanel(true);
});

// ---- side panel -------------------------------------------------------------------------------

function openPanel(title, ...kids) {
  if (panel.hidden) lastFocusBeforePanel = document.activeElement;
  clear(panel);
  const heading = h('h2', { id: 'panel-title', tabindex: '-1', text: title });
  panel.appendChild(h('button', { type: 'button', class: 'close', onclick: () => closePanel(true), text: 'Close' }));
  panel.appendChild(heading);
  addKids(panel, kids);
  panel.hidden = false;
  heading.focus();
}

function closePanel(restore) {
  if (panel.hidden) return;
  panel.hidden = true;
  clear(panel);
  if (restore && lastFocusBeforePanel && document.contains(lastFocusBeforePanel)) lastFocusBeforePanel.focus();
}

// ---- Home: projects ---------------------------------------------------------------------------

function fmtDay(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString();
}

function sumCounts(obj) {
  return Object.values(obj || {}).reduce((a, b) => a + b, 0);
}

function nextCard(step, idText) {
  return h('section', { class: 'card next', 'aria-labelledby': idText },
    h('h3', { id: idText, text: 'What happens next' }),
    h('p', { text: nextStepText(step) }),
    h('p', { class: 'muted small', text: 'Ask your AI assistant to carry on; it follows these steps.' }));
}

async function pageHome(ctx) {
  const [list, st] = await Promise.all([api('/api/projects'), api('/api/status')]);
  if (ctx.token !== routeToken) return;
  if (!list.ok) return setMain(notice('bad', errorText(list)));
  const kids = [
    h('div', { class: 'row' },
      h('h2', { class: 'grow', text: 'Your projects' }),
      h('a', { class: 'button primary', href: '#/new-project', text: 'New project' })),
    whatsThis('A project is one product, service or website you want to find customers for. '
      + 'It has its own campaigns, and it remembers who you already contacted so nobody gets a message twice.'),
  ];
  if (st.ok && st.notes && st.notes.length) kids.push(notice('warn', listOf(st.notes)));
  if (st.ok) kids.push(nextCard(st.next_step, 'next-h'));
  if (!list.projects.length) {
    kids.push(h('p', { text: 'You have no projects yet. Choose "New project" to start.' }));
  } else {
    kids.push(h('div', { class: 'cards' }, ...list.projects.map(projectCard)));
  }
  if (st.ok) kids.push(h('p', { class: 'muted small', text: `Version ${st.version}. Your data is in ${st.workspace}` }));
  setMain(...kids);
}

function projectCard(p) {
  const id = 'pc-' + p.id;
  return h('article', { class: 'card project', 'aria-labelledby': id },
    h('h3', null, h('a', { id, href: '#/p/' + p.id, text: p.name })),
    h('p', { class: p.about ? null : 'muted', text: p.about || 'Not set up yet' }),
    h('dl', { class: 'kv' },
      h('dt', { text: 'Campaigns' }), h('dd', { text: fmtNum(p.campaigns) }),
      h('dt', { text: 'People ready' }), h('dd', { text: fmtNum(p.leads) }),
      h('dt', { text: 'Verified emails' }), h('dd', { text: fmtNum(p.verified_emails) }),
      h('dt', { text: 'Last activity' }), h('dd', { text: p.last_activity ? fmtDay(p.last_activity) : 'None yet' })));
}

function campaignTable(list, showProject) {
  const heads = ['Campaign', ...(showProject ? ['Project'] : []), 'Status', 'Countries', 'Channels', 'Companies', 'People', 'Messages'];
  const firstNum = heads.indexOf('Companies');
  return h(
    'div',
    { class: 'table-wrap' },
    h(
      'table',
      null,
      h('thead', null, h('tr', null, ...heads.map((t, i) =>
        h('th', { class: i >= firstNum ? 'num' : null, scope: 'col', text: t })))),
      h(
        'tbody',
        null,
        ...list.map((c) =>
          h(
            'tr',
            null,
            h('td', null, h('a', { href: '#/c/' + c.id, text: c.name })),
            showProject ? h('td', null, h('a', { href: '#/p/' + c.project_id, text: c.project || '' })) : null,
            h('td', null, badge(label(CAMPAIGN_STATUS, c.status), c.status === 'active' ? 'ok' : 'none')),
            h('td', { text: (c.countries || []).join(', ') }),
            h('td', { text: (c.channels || []).map((x) => (x === 'linkedin' ? 'LinkedIn' : 'Email')).join(', ') }),
            h('td', { class: 'num', text: fmtNum(sumCounts(c.counts && c.counts.companies)) }),
            h('td', { class: 'num', text: fmtNum(sumCounts(c.counts && c.counts.people)) }),
            h('td', { class: 'num', text: fmtNum(sumCounts(c.counts && c.counts.messages)) }),
          ),
        ),
      ),
    ),
  );
}

// ---- Attaching a product or service (website, file or pasted text) ----------------------------

// The fields to choose a source. `read()` gives `{kind, ...}`, null when the user chose to do it
// later, or an `{error}` object.
function sourceFields(prefix, allowLater) {
  const kinds = [['url', 'A website address'], ['file', 'A PDF, Word or text file'], ['text', 'Paste some text']];
  if (allowLater) kinds.push(['later', 'I will do this later']);
  const radios = kinds.map(([val, text], i) => {
    const input = h('input', { type: 'radio', name: prefix + '-kind', id: `${prefix}-kind-${val}`, value: val, checked: i === 0 });
    input.addEventListener('change', show);
    return h('div', null, h('label', { class: 'check', for: `${prefix}-kind-${val}` }, input, text));
  });
  const url = h('input', { type: 'text', id: prefix + '-url', placeholder: 'https://www.example.com', autocomplete: 'off' });
  const scopePage = h('input', { type: 'radio', name: prefix + '-scope', id: prefix + '-scope-page', value: 'page' });
  const scopeSite = h('input', { type: 'radio', name: prefix + '-scope', id: prefix + '-scope-site', value: 'site', checked: true });
  const file = h('input', { type: 'file', id: prefix + '-file', accept: '.pdf,.docx,.txt,.md' });
  const text = h('textarea', { id: prefix + '-text', rows: 6, placeholder: 'Paste what you offer here' });
  const urlBox = h('div', { class: 'indent' },
    field(prefix + '-url', 'Website address', url),
    h('div', { class: 'field' },
      h('div', null, h('label', { class: 'check', for: prefix + '-scope-page' }, scopePage, 'Just this page')),
      h('div', null, h('label', { class: 'check', for: prefix + '-scope-site' }, scopeSite, 'Whole website (up to 15 pages)'))));
  const fileBox = h('div', { class: 'indent' }, field(prefix + '-file', 'File (up to 20 MB)', file));
  const textBox = h('div', { class: 'indent' }, field(prefix + '-text', 'Text', text));
  const node = h('fieldset', null, h('legend', { text: 'Attach your product or service' }), ...radios, urlBox, fileBox, textBox);
  function chosen() {
    const el = node.querySelector(`input[name="${prefix}-kind"]:checked`);
    return el ? el.value : 'url';
  }
  function show() {
    const kind = chosen();
    urlBox.hidden = kind !== 'url';
    fileBox.hidden = kind !== 'file';
    textBox.hidden = kind !== 'text';
  }
  show();
  return {
    node,
    read() {
      const kind = chosen();
      if (kind === 'later') return null;
      if (kind === 'url') {
        const value = url.value.trim();
        if (!value) return { error: 'Type the website address first.' };
        return { kind, url: value, scope: scopePage.checked ? 'page' : 'site' };
      }
      if (kind === 'file') {
        if (!file.files || !file.files.length) return { error: 'Choose a file first.' };
        return { kind, file: file.files[0] };
      }
      if (!text.value.trim()) return { error: 'Paste some text first.' };
      return { kind, text: text.value };
    },
  };
}

// Attach the source to the project. A website is read in steps; `box` shows how far it got.
async function attachSource(projectId, spec, box) {
  const started = Date.now();
  let line = 'Working...';
  const draw = () => {
    const secs = Math.floor((Date.now() - started) / 1000);
    setStatus(box, 'info', `${line} (${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')})`);
  };
  draw();
  const timer = setInterval(draw, 1000);
  const base = `/api/projects/${projectId}/source`;
  try {
    if (spec.kind === 'file') {
      line = 'Reading the file...';
      const form = new FormData();
      form.append('file', spec.file);
      return await api(base + '-file', { method: 'POST', form });
    }
    if (spec.kind === 'text') {
      line = 'Reading the text...';
      return await api(base, { method: 'POST', body: { text: spec.text } });
    }
    line = spec.scope === 'page' ? 'Reading the page...' : 'Reading the website...';
    let reply = await api(base, {
      method: 'POST', body: { url: spec.url, scope: spec.scope, max_pages: spec.scope === 'page' ? 1 : 15 },
    });
    while (reply.ok && reply.done === false) {
      line = `Reading the website... ${reply.pages_read || 0} pages read so far. A big site can take a few minutes: please keep this page open`;
      draw();
      reply = await api(base, { method: 'POST', body: { offer_id: reply.offer_id, cursor: reply.cursor } });
    }
    return reply;
  } finally {
    clearInterval(timer);
    clear(box);
  }
}

function setupSentence(name) {
  return 'Set up project ' + name;
}

// ---- New project ------------------------------------------------------------------------------

async function pageNewProject() {
  const name = h('input', { type: 'text', id: 'np-name', autocomplete: 'off', maxlength: '200' });
  const days = h('input', { type: 'number', id: 'np-days', min: '0', max: '3650', value: '90' });
  const source = sourceFields('np', true);
  const status = h('div', { role: 'status' });
  const save = h('button', { type: 'submit', class: 'primary', text: 'Create the project' });
  const form = h('form', { class: 'stack' },
    field('np-name', 'Project name', name),
    source.node,
    h('div', { class: 'field' }, h('label', { for: 'np-days', text: "Don't contact the same person again for this many days" }), days,
      h('p', { class: 'small muted', text: 'This stops the same person getting a second message too soon. Use 0 to turn it off.' })),
    h('div', { class: 'row' }, save), status);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const title = name.value.trim();
    if (!title) return setStatus(status, 'bad', 'Give the project a name.');
    const spec = source.read();
    if (spec && spec.error) return setStatus(status, 'bad', spec.error);
    save.disabled = true;
    const made = await api('/api/projects', {
      method: 'POST', body: { name: title, cooldown_days: Number(days.value || 0) },
    });
    if (!made.ok) {
      save.disabled = false;
      return setStatus(status, 'bad', errorText(made));
    }
    const pid = made.project.id;
    let read = null;
    if (spec) read = await attachSource(pid, spec, status);
    save.disabled = false;
    const kids = [h('h3', { text: 'Your project is created' })];
    if (read && !read.ok) {
      kids.push(notice('bad', 'The project was created, but the product or service could not be read. ' + errorText(read)));
      kids.push(h('p', { text: 'Open the project and use "Replace the source" to try again.' }));
    } else {
      if (read) kids.push(notice('ok', read.summary || 'Your product or service was read.'));
      const say = setupSentence(title);
      kids.push(h('p', { text: 'Next: open your AI assistant (for example Claude) and say:' }));
      kids.push(h('blockquote', { text: say }));
      kids.push(h('p', { class: 'muted small', text: 'It will write down what you offer, together with you. Then you can create campaigns.' }));
      kids.push(h('div', { class: 'row' }, h('button', { type: 'button', onclick: () => copyText(say), text: 'Copy this sentence' })));
    }
    kids.push(h('p', null, h('a', { class: 'button', href: '#/p/' + pid, text: 'Open the project' })));
    setMain(h('h2', { text: 'New project' }), h('section', { class: 'card stack' }, ...kids));
  });
  setMain(
    h('h2', { text: 'New project' }),
    whatsThis('Give it a name and tell us what you are promoting. We read it so the messages can be about the right thing.'),
    form,
  );
}

// ---- Project page -----------------------------------------------------------------------------

function sourceKindText(src) {
  if (!src) return '';
  const size = `${fmtNum(src.characters)} characters`;
  if (src.kind === 'url') return `A website: ${src.label} (${size} read)`;
  if (src.kind === 'file') return `A file: ${src.label} (${size} read)`;
  return `Pasted text (${size})`;
}

async function pageProject(ctx) {
  const res = await api('/api/projects/' + ctx.projectId);
  if (ctx.token !== routeToken) return;
  if (!res.ok) return setMain(notice('bad', errorText(res)), h('a', { href: '#/home', text: 'Back to your projects' }));
  const pr = res.project;
  const src = res.source;
  const kids = [
    h('div', { class: 'row' }, h('h2', { class: 'grow', text: pr.name }), h('a', { href: '#/home', text: 'All projects' })),
    whatsThis('This is one product or service. Below you can see what it is about, its campaigns, the contacts you already have, and who we will not contact again yet.'),
  ];

  // About this project
  const aboutBox = h('div', { class: 'stack' });
  if (src) {
    aboutBox.appendChild(h('dl', { class: 'kv' },
      h('dt', { text: 'What it is about' }),
      h('dd', { text: src.about || 'Not written down yet. Open your AI assistant and say: ' + setupSentence(pr.name) }),
      h('dt', { text: 'What we read' }), h('dd', { text: sourceKindText(src) })));
  } else {
    aboutBox.appendChild(notice('warn', 'No product or service is attached yet. Attach one below.'));
  }
  const source = sourceFields('ps', false);
  const srcStatus = h('div', { role: 'status' });
  const attach = h('button', { type: 'button', class: 'primary', text: src ? 'Replace the source' : 'Attach it' });
  attach.addEventListener('click', async () => {
    const spec = source.read();
    if (spec && spec.error) return setStatus(srcStatus, 'bad', spec.error);
    if (src && !window.confirm('Replace what we read before with this? Campaigns you already made keep what they used.')) return;
    attach.disabled = true;
    const out = await attachSource(pr.id, spec, srcStatus);
    attach.disabled = false;
    if (out.ok) {
      toast(out.summary || 'Done.');
      route();
    } else setStatus(srcStatus, 'bad', errorText(out));
  });
  aboutBox.appendChild(source.node);
  aboutBox.appendChild(h('div', { class: 'row' }, attach));
  aboutBox.appendChild(srcStatus);
  kids.push(h('section', { class: 'card stack', 'aria-labelledby': 'about-h' },
    h('h3', { id: 'about-h', text: 'About this project' }), aboutBox, emailPolicyBox(pr)));

  // Campaigns
  const camps = res.campaigns;
  const campBody = camps.length
    ? h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ...['Campaign', 'Status', 'People ready', 'Saved for later', 'Verified emails'].map((t, i) => h('th', { class: i >= 2 ? 'num' : null, scope: 'col', text: t })))),
      h('tbody', null, ...camps.map((c) => h('tr', null,
        h('td', null, h('a', { href: '#/c/' + c.id, text: c.name })),
        h('td', null, badge(label(CAMPAIGN_STATUS, c.status), c.status === 'active' ? 'ok' : 'none')),
        h('td', { class: 'num', text: fmtNum(c.counts.people.confirmed || 0) }),
        h('td', { class: 'num', text: fmtNum(c.counts.people.reserve || 0) }),
        h('td', { class: 'num', text: fmtNum((c.email_summary || {}).verified_emails || 0) }))))))
    : h('p', { class: 'muted', text: 'No campaigns yet.' });
  kids.push(h('section', { class: 'card stack', 'aria-labelledby': 'camp-h' },
    h('div', { class: 'row' }, h('h3', { class: 'grow', id: 'camp-h', text: 'Campaigns' }),
      h('a', { class: 'button primary', href: `#/p/${pr.id}/new-campaign`, text: 'New campaign' })),
    campBody));

  // Contacts you already have
  const known = res.known;
  const knownText = known.total
    ? `You have added ${fmtNum(known.total)} known contact${known.total === 1 ? '' : 's'}: ` + Object.entries(known.by_relationship).map(([k, v]) => `${v} ${label(KNOWN, k).toLowerCase()}`).join(', ') + '.'
    : 'You have not added any yet.';
  kids.push(h('section', { class: 'card stack', 'aria-labelledby': 'known-h' },
    h('h3', { id: 'known-h', text: 'Contacts you already have' }),
    h('p', { text: 'Upload a list of people you have already spoken to (an Excel or CSV file, or your LinkedIn contacts file). You can stop us contacting them again, use them as warm leads, or both.' }),
    h('p', { class: 'muted', text: knownText }),
    h('div', { class: 'row' },
      h('a', { class: 'button', href: '/api/contacts-template/xlsx', download: '', text: 'Download the Excel template' }),
      h('a', { class: 'button', href: '/api/contacts-template/csv', download: '', text: 'Download the CSV template' }),
      h('a', { class: 'button primary', href: `#/p/${pr.id}/contacts`, text: 'Upload your contacts' }))));

  kids.push(memoryBox(pr, res.memory));
  setMain(...kids);
}

function emailPolicyBox(pr) {
  const choices = [
    ['verified_only', 'Only email addresses we could verify (recommended)'],
    ['allow_risky', 'Also use addresses on mail servers that accept everything (we cannot be sure they work)'],
  ];
  const status = h('div', { role: 'status' });
  const radios = choices.map(([val, text]) => h('div', null,
    h('label', { class: 'check', for: 'ep-' + val },
      h('input', { type: 'radio', name: 'ep', id: 'ep-' + val, value: val, checked: pr.email_policy === val }), text)));
  const save = h('button', { type: 'button', text: 'Save this choice' });
  const box = h('fieldset', null, h('legend', { text: 'Which email addresses may be used' }),
    ...radios,
    h('p', { class: 'small muted', text: 'A change applies to campaigns you create afterwards. Campaigns you already made keep their own setting.' }),
    h('div', { class: 'row' }, save), status);
  save.addEventListener('click', async () => {
    const el = box.querySelector('input[name="ep"]:checked');
    if (!el) return;
    save.disabled = true;
    const out = await api('/api/projects/' + pr.id, { method: 'PATCH', body: { email_policy: el.value } });
    save.disabled = false;
    if (out.ok) setStatus(status, 'ok', 'Saved. It applies to campaigns you create from now on.');
    else setStatus(status, 'bad', errorText(out));
  });
  return box;
}

function memoryBox(pr, mem) {
  const days = h('input', { type: 'number', id: 'cm-days', min: '0', max: '3650', value: String(mem.cooldown_days) });
  const status = h('div', { role: 'status' });
  const save = h('button', { type: 'button', text: 'Save' });
  save.addEventListener('click', async () => {
    save.disabled = true;
    const out = await api('/api/projects/' + pr.id, { method: 'PATCH', body: { cooldown_days: Number(days.value || 0) } });
    save.disabled = false;
    if (out.ok) { toast('Saved.'); route(); } else setStatus(status, 'bad', errorText(out));
  });
  let held = 'Nobody is being held back right now.';
  if (mem.blocked) {
    held = `${fmtNum(mem.blocked)} ${mem.blocked === 1 ? 'person is' : 'people are'} being held back right now. `
      + (mem.first_free === mem.last_free
        ? `They can be contacted again on ${fmtDay(mem.first_free)}.`
        : `The first can be contacted again on ${fmtDay(mem.first_free)}, the last on ${fmtDay(mem.last_free)}.`);
  } else if (!mem.cooldown_days) held = 'This is turned off, so nobody is held back.';
  return h('section', { class: 'card stack', 'aria-labelledby': 'mem-h' },
    h('h3', { id: 'mem-h', text: 'Who we will not contact again yet' }),
    h('p', { text: 'We remember everyone who was found, exported or contacted in this project, so nobody gets a second message too soon.' }),
    h('div', { class: 'field' }, h('label', { for: 'cm-days', text: "Don't contact the same person again for this many days" }),
      h('div', { class: 'row' }, days, save)),
    h('p', { text: held }), status);
}

// ---- Contacts you already have: upload, check, add -------------------------------------------

const IMPORT_MODES = [
  ['exclude', "Don't contact these people again", 'They are left out of new campaigns of this project for the number of days you set.'],
  ['signal', 'Use them as warm leads', 'Their companies get a "Warm" badge and the people get a "Known contact" badge, so messages can be friendlier.'],
  ['both', 'Both', 'Leave them out and also mark them as known contacts.'],
];

function previewLines(res, applied) {
  const lines = [];
  const people = `${fmtNum(res.contacts)} ${res.contacts === 1 ? 'person' : 'people'} found in ${fmtNum(res.rows_read)} rows`;
  lines.push(people + (res.duplicates_merged ? ` (${res.duplicates_merged} repeated rows were joined)` : '') + '.');
  if (res.source === 'linkedin_connections') lines.push('This looks like the contacts file from LinkedIn. We read it as it is.');
  if (res.rows_skipped) lines.push(`${fmtNum(res.rows_skipped)} ${res.rows_skipped === 1 ? 'row cannot' : 'rows cannot'} be used (see the list below).`);
  const verb = applied ? 'were' : 'will be';
  const held = applied ? res.excluded : res.would_exclude;
  if (held !== undefined) {
    lines.push(`${fmtNum(held)} ${held === 1 ? 'person' : 'people'} ${verb} left out of new campaigns for the number of days you set.`);
    if (res.already_logged) lines.push(`${fmtNum(res.already_logged)} of them were already on the list.`);
    if (res.outside_cooldown) lines.push(`${fmtNum(res.outside_cooldown)} were contacted longer ago than that, so they will not hold anyone back now.`);
  }
  const warm = applied ? res.signalled : res.would_signal;
  if (warm !== undefined) lines.push(`${fmtNum(warm)} ${warm === 1 ? 'person' : 'people'} ${verb} saved as known contacts (their companies count as warm).`);
  if (res.do_not_contact) lines.push(`${fmtNum(res.do_not_contact)} ${res.do_not_contact === 1 ? 'person is' : 'people are'} marked "never contact" in your file: their email addresses and LinkedIn links ${verb} added to your do-not-contact list.`);
  return lines;
}

function problemTable(items, title) {
  if (!items || !items.length) return null;
  return h('div', null, h('h4', { text: title }),
    h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ...['Row', 'Column', 'What is wrong'].map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', null, ...items.map((e) => h('tr', null, h('td', { text: String(e.row) }), h('td', { class: 'small', text: e.column || '' }), h('td', { text: e.problem })))))));
}

async function pageContacts(ctx) {
  const res = await api('/api/projects/' + ctx.projectId);
  if (ctx.token !== routeToken) return;
  if (!res.ok) return setMain(notice('bad', errorText(res)), h('a', { href: '#/home', text: 'Back to your projects' }));
  const pr = res.project;
  const file = h('input', { type: 'file', id: 'ct-file', accept: '.csv,.xlsx' });
  const out = h('div', { class: 'stack' });
  const status = h('div', { role: 'status' });
  const check = h('button', { type: 'button', class: 'primary', text: 'Check the file' });
  const modeRadios = IMPORT_MODES.map(([val, text, help], i) => h('div', { class: 'field' },
    h('label', { class: 'check', for: 'ct-mode-' + val },
      h('input', { type: 'radio', name: 'ct-mode', id: 'ct-mode-' + val, value: val, checked: i === 0 }), text),
    h('p', { class: 'small muted indent', text: help })));
  const modeBox = h('section', { class: 'card stack' }, h('h3', { text: '3 What should we do with these people?' }), ...modeRadios);
  const mode = () => (modeBox.querySelector('input[name="ct-mode"]:checked') || {}).value || 'exclude';
  const send = async (dry) => {
    const form = new FormData();
    form.append('file', file.files[0]);
    form.append('mode', mode());
    form.append('dry_run', dry ? 'true' : 'false');
    return api(`/api/projects/${pr.id}/contacts`, { method: 'POST', form });
  };
  const resetOut = () => { clear(out); clear(status); };
  file.addEventListener('change', resetOut);
  modeBox.addEventListener('change', resetOut);
  check.addEventListener('click', async () => {
    resetOut();
    if (!file.files || !file.files.length) return setStatus(status, 'bad', 'Choose a file first.');
    check.disabled = true;
    setStatus(status, 'info', 'Checking the file...');
    const prev = await send(true);
    check.disabled = false;
    clear(status);
    if (!prev.ok) return setStatus(status, 'bad', errorText(prev));
    const add = h('button', { type: 'button', class: 'primary', text: 'Add these contacts' });
    add.addEventListener('click', async () => {
      add.disabled = true;
      setStatus(status, 'info', 'Adding the contacts...');
      const done = await send(false);
      clear(status);
      if (!done.ok) { add.disabled = false; return setStatus(status, 'bad', errorText(done)); }
      clear(out);
      out.appendChild(notice('ok', h('strong', { text: 'Done. ' }), ...previewLines(done, true).map((t) => h('div', { text: t }))));
      out.appendChild(h('p', null, h('a', { class: 'button', href: '#/p/' + pr.id, text: 'Back to the project' })));
    });
    out.appendChild(h('section', { class: 'card stack', 'aria-labelledby': 'pv-h' },
      h('h3', { id: 'pv-h', text: 'What we found' }),
      h('p', { class: 'small muted', text: 'Nothing has been saved yet.' }),
      h('ul', null, ...previewLines(prev, false).map((t) => h('li', { text: t }))),
      problemTable(prev.errors, `Rows we cannot use (${fmtNum(prev.error_count)})`),
      problemTable(prev.warnings, `Things to look at (${fmtNum(prev.warning_count)})`),
      prev.unknown_columns && prev.unknown_columns.length ? h('p', { class: 'small muted', text: 'Columns we did not recognise and ignored: ' + prev.unknown_columns.join(', ') }) : null,
      h('div', { class: 'row' }, add)));
  });
  setMain(
    h('div', { class: 'row' }, h('h2', { class: 'grow', text: 'Contacts you already have' }), h('a', { href: '#/p/' + pr.id, text: 'Back to ' + pr.name })),
    whatsThis('Upload the people you have already spoken to. First we check the file and show you what we found. Nothing is saved until you press "Add these contacts".'),
    h('section', { class: 'card stack' },
      h('h3', { text: '1 Get the template (optional)' }),
      h('p', { text: 'You can also upload the contacts file you download from LinkedIn, as it is.' }),
      h('div', { class: 'row' },
        h('a', { class: 'button', href: '/api/contacts-template/xlsx', download: '', text: 'Download the Excel template' }),
        h('a', { class: 'button', href: '/api/contacts-template/csv', download: '', text: 'Download the CSV template' }))),
    h('section', { class: 'card stack' },
      h('h3', { text: '2 Choose your file' }),
      field('ct-file', 'Excel (.xlsx) or CSV file, up to 20 MB', file)),
    modeBox,
    h('div', { class: 'row' }, check),
    status, out,
  );
}

// ---- New campaign -----------------------------------------------------------------------------

async function pageNewCampaign(ctx) {
  const [res, opts] = await Promise.all([api('/api/projects/' + ctx.projectId), api('/api/campaign-options')]);
  if (ctx.token !== routeToken) return;
  if (!res.ok) return setMain(notice('bad', errorText(res)), h('a', { href: '#/home', text: 'Back to your projects' }));
  if (!opts.ok) return setMain(notice('bad', errorText(opts)));
  const pr = res.project;
  const name = h('input', { type: 'text', id: 'nc-name', autocomplete: 'off', maxlength: '200' });
  const countries = h('select', { id: 'nc-countries', multiple: true, size: '8' },
    ...opts.countries.map((c) => h('option', { value: c.code, text: c.name })));
  const sectors = h('input', { type: 'text', id: 'nc-sectors', placeholder: 'for example: logistics, retail', autocomplete: 'off' });
  const email = h('input', { type: 'checkbox', id: 'nc-email', checked: true });
  const linkedin = h('input', { type: 'checkbox', id: 'nc-linkedin', checked: true });
  const roles = opts.roles.map((r) => ({ key: r.key, box: h('input', { type: 'checkbox', id: 'nc-role-' + r.key, value: r.key }), label: r.label }));
  const seniority = selectBox('nc-seniority', opts.seniority.map((v) => [v, label(SENIORITY, v)]), 'manager');
  const num = (id) => h('input', { type: 'number', id, min: '1', max: '10000', placeholder: 'No limit' });
  const n = num('nc-n');
  const c = num('nc-c');
  const p = num('nc-p');
  const donors = res.campaigns.filter((x) => (x.counts.people.reserve || 0) > 0);
  const take = selectBox('nc-take', [['', 'No, start empty'], ...donors.map((x) => [x.id, `${x.name} (${x.counts.people.reserve} saved)`])], '');
  const status = h('div', { role: 'status' });
  const save = h('button', { type: 'submit', class: 'primary', text: 'Create the campaign' });
  if (!res.source) save.disabled = true;
  const form = h('form', { class: 'stack' },
    !res.source ? notice('warn', 'Attach your product or service to the project first. ', h('a', { href: '#/p/' + pr.id, text: 'Open the project' })) : null,
    field('nc-name', 'Campaign name', name),
    h('div', { class: 'field' }, h('label', { for: 'nc-countries', text: 'Countries' }), countries,
      h('p', { class: 'small muted', text: 'Hold Ctrl (or Cmd on a Mac) to choose more than one.' })),
    field('nc-sectors', 'Sectors (optional, separate with commas)', sectors),
    h('fieldset', null, h('legend', { text: 'Where do you want to reach them?' }),
      checkField('nc-email', 'Email', email),
      checkField('nc-linkedin', 'LinkedIn', linkedin, 'A campaign plans both. Sending is optional and comes later.')),
    h('fieldset', null, h('legend', { text: 'Job roles (choose 1 to 8)' }),
      ...roles.map((r) => checkField('nc-role-' + r.key, r.label, r.box))),
    field('nc-seniority', 'Lowest level to contact', seniority),
    h('fieldset', null, h('legend', { text: 'How big should it be?' }),
      field('nc-n', 'How many people in total (n)', n),
      field('nc-c', 'How many companies (c)', c),
      field('nc-p', 'Most people per company (p)', p),
      h('p', { class: 'small muted', text: 'People who do not fit are not lost: they are saved for a later campaign.' })),
    donors.length ? field('nc-take', 'Take people saved by another campaign?', take) : null,
    h('div', { class: 'row' }, save), status);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const channels = [email.checked ? 'email' : null, linkedin.checked ? 'linkedin' : null].filter(Boolean);
    const chosenRoles = roles.filter((r) => r.box.checked).map((r) => r.key);
    const chosenCountries = [...countries.selectedOptions].map((o) => o.value);
    const limit = (el) => (el.value ? Number(el.value) : null);
    if (!name.value.trim()) return setStatus(status, 'bad', 'Give the campaign a name.');
    if (!chosenCountries.length) return setStatus(status, 'bad', 'Choose at least one country.');
    if (!channels.length) return setStatus(status, 'bad', 'Choose Email, LinkedIn or both.');
    if (!chosenRoles.length || chosenRoles.length > 8) return setStatus(status, 'bad', 'Choose between 1 and 8 job roles.');
    save.disabled = true;
    const body = {
      name: name.value.trim(), project_id: pr.id, countries: chosenCountries,
      sectors: sectors.value.split(',').map((x) => x.trim()).filter(Boolean),
      channels, designations: chosenRoles, seniority_min: seniority.value,
      max_people: limit(n), max_companies: limit(c), max_per_company: limit(p),
      take_reserve_from: take.value ? Number(take.value) : null,
    };
    const out = await api('/api/campaigns', { method: 'POST', body });
    save.disabled = false;
    if (!out.ok) return setStatus(status, 'bad', errorText(out));
    toast(out.reserve ? `Campaign created. ${out.reserve.moved} people moved in from the other campaign.` : 'Campaign created.');
    go('/c/' + out.campaign.id);
  });
  setMain(
    h('div', { class: 'row' }, h('h2', { class: 'grow', text: 'New campaign' }), h('a', { href: '#/p/' + pr.id, text: 'Back to ' + pr.name })),
    whatsThis('A campaign is one round of outreach: who you want to reach, and how many. Your AI assistant does the searching and writing; you stay in charge.'),
    form,
  );
}

// ---- Connect your mailbox (the step-by-step help is added in the next update) ----------------

async function pageMailbox() {
  setMain(
    h('h2', { text: 'Connect your mailbox (optional)' }),
    whatsThis('You only need this if you want Lead Generator to send the emails for you. You can always export a file and send from your own email program instead.'),
    h('section', { class: 'card stack' },
      h('p', { text: 'Step-by-step help for connecting your mailbox is coming soon. Until then, ask your AI assistant to help you connect it.' }),
      h('p', null, h('a', { href: '#/home', text: 'Back to your projects' }))),
  );
}

// ---- Keys -------------------------------------------------------------------------------------

async function pageKeys(ctx) {
  const reply = await api('/api/providers');
  if (ctx.token !== routeToken) return;
  if (!reply.ok) return setMain(notice('bad', errorText(reply)));
  const kids = [
    h('h2', { text: 'Keys' }),
    whatsThis('A key is like a password that lets Lead Generator use a service for you, for example a search service. Most have a free allowance.'),
    h('p', null,
      'Paste a key here instead of in the chat. It is saved on this computer only, tested once, and never shown again ',
      '(only the last four characters).'),
  ];
  for (const p of reply.providers) kids.push(providerCard(p));
  setMain(...kids);
}

function providerCard(p) {
  const status = h('div', { role: 'status', class: 'small' });
  const single = p.env_keys.length === 1 && p.implemented;
  const head = h('div', { class: 'row' },
    h('h3', { class: 'grow', text: p.name }),
    p.configured ? badge('Set ' + (p.masked_key || ''), 'ok') : badge('Not set', 'none'),
    p.paid ? badge('Paid plan', 'warn') : null,
  );
  const lines = [
    h('p', { text: p.purpose }),
    h('p', { class: 'small muted' },
      `Free allowance: ${p.free_tier || 'none'}. Used this month: ${fmtNum(p.usage_month.credits)} credits`,
      p.remaining_free !== null ? `, about ${fmtNum(p.remaining_free)} free left.` : '.'),
  ];
  const actions = [];
  if (single) {
    const input = h('input', {
      type: 'password', id: 'key-' + p.key, autocomplete: 'off', spellcheck: 'false',
      placeholder: 'Paste the key here', name: 'key-' + p.key,
    });
    const save = h('button', { type: 'button', class: 'primary', text: 'Save and test' });
    save.addEventListener('click', async () => {
      const value = input.value.trim();
      if (!value) return setStatus(status, 'bad', 'Paste a key first.');
      save.disabled = true;
      setStatus(status, 'info', 'Testing the key...');
      const res = await api('/api/providers/' + p.key + '/key', { method: 'POST', body: { key: value } });
      input.value = '';
      save.disabled = false;
      if (res.ok) {
        toast(`${p.name} key saved ${res.masked_key || ''}. ${res.message || ''}`);
        route();
      } else {
        setStatus(status, 'bad', errorText(res));
      }
    });
    actions.push(field('key-' + p.key, 'Key', h('div', { class: 'row' }, input, save)));
    if (p.configured) {
      const test = h('button', { type: 'button', text: 'Test the saved key (may use 1 credit)' });
      test.addEventListener('click', async () => {
        test.disabled = true;
        setStatus(status, 'info', 'Testing...');
        const res = await api('/api/providers/' + p.key + '/test', { method: 'POST' });
        test.disabled = false;
        setStatus(status, res.ok ? 'ok' : 'bad', errorText(res));
      });
      const remove = h('button', { type: 'button', class: 'danger', text: 'Remove the key' });
      remove.addEventListener('click', async () => {
        if (!window.confirm('Remove the saved ' + p.name + ' key from this computer?')) return;
        const res = await api('/api/providers/' + p.key + '/key', { method: 'DELETE' });
        if (res.ok) {
          toast(res.message);
          route();
        } else setStatus(status, 'bad', errorText(res));
      });
      actions.push(h('div', { class: 'row' }, test, remove));
    }
  } else if (p.key === 'smtp') {
    lines.push(h('p', { class: 'small' }, 'The mailbox password is set in a terminal so it is never shown: run ',
      h('code', { text: 'leadgen mailbox set' }), '.'));
  } else if (!p.env_keys.length) {
    lines.push(h('p', { class: 'small', text: 'No key needed. It is off unless you turn it on in the settings file.' }));
  } else if (!p.implemented) {
    lines.push(h('p', { class: 'small', text: 'Not available in this version yet.' }));
  }
  if (p.signup_url) lines.push(h('p', { class: 'small' }, extLink(p.signup_url, 'Get a key (' + p.name + ' sign-up page)')));
  return h('section', { class: 'card', 'aria-label': p.name }, head, ...lines, ...actions, status);
}

function setStatus(box, tone, text) {
  clear(box);
  if (text) box.appendChild(notice(tone === 'info' ? 'ok' : tone, text));
}

// ---- Campaigns --------------------------------------------------------------------------------

async function pageCampaigns(ctx) {
  const project = ctx.q.get('project') || '';
  const [reply, projs] = await Promise.all([api('/api/campaigns' + query({ project_id: project })), api('/api/projects')]);
  if (ctx.token !== routeToken) return;
  if (!reply.ok) return setMain(notice('bad', errorText(reply)));
  const kids = [
    h('h2', { text: 'Campaigns' }),
    whatsThis('A campaign is one round of outreach inside a project. To start one, open the project and choose "New campaign".'),
  ];
  if (projs.ok && projs.projects.length > 1) {
    kids.push(h('div', { class: 'filters' }, h('div', null, h('label', { for: 'c-project', text: 'Show campaigns of' }),
      selectBox('c-project', [['', 'All projects'], ...projs.projects.map((x) => [x.id, x.name])], project, (e) => go('/campaigns', { project: e.target.value })))));
  }
  kids.push(reply.campaigns.length
    ? campaignTable(reply.campaigns, true)
    : h('p', { text: 'No campaigns yet. Open a project and choose "New campaign".' }));
  setMain(...kids);
}

function countLine(obj, map) {
  return Object.entries(obj || {}).map(([k, v]) => `${v} ${(map && map[k] ? map[k] : k).toLowerCase()}`).join(', ');
}

function limitsLine(options) {
  const o = options || {};
  const parts = [];
  if (o.max_people) parts.push(`${fmtNum(o.max_people)} people in all`);
  if (o.max_companies) parts.push(`${fmtNum(o.max_companies)} companies`);
  if (o.max_per_company) parts.push(`at most ${fmtNum(o.max_per_company)} per company`);
  return parts.length ? parts.join(', ') : 'No size limits';
}

function flowItem(title, ...body) {
  return h('li', { class: 'flow-item' }, h('strong', { text: title }), h('div', null, ...body));
}

async function pageOverview(ctx) {
  const c = ctx.campaign;
  const ov = c.overview;
  const [st, qa, sibs] = await Promise.all([
    api('/api/status'), api(`/api/campaigns/${c.id}/qa`), api('/api/campaigns' + query({ project_id: c.project_id })),
  ]);
  if (ctx.token !== routeToken) return;
  const step = st.ok ? (st.campaigns.find((x) => x.id === c.id) || {}).next_step : null;
  const counts = c.counts;
  const allowRisky = !!(c.options || {}).allow_risky_emails;
  const emails = c.email_summary || {};
  const kids = [
    h('div', { class: 'row' }, h('h2', { class: 'grow', text: c.name }), badge(label(CAMPAIGN_STATUS, c.status), c.status === 'active' ? 'ok' : 'none')),
    whatsThis('A campaign goes through six steps, from choosing companies to sending or exporting. Your AI assistant does most of the work. This page shows where you are.'),
  ];
  if (step) kids.push(nextCard(step, 'next-h'));
  kids.push(h('section', { class: 'card', 'aria-labelledby': 'flow-h' },
    h('h3', { id: 'flow-h', text: 'Where you are' }),
    h('ol', { class: 'flow' },
      flowItem('1 Companies', h('a', { href: `#/c/${c.id}/companies`, text: countLine(counts.companies, COMPANY_STATUS) || 'None yet' })),
      flowItem('2 People',
        h('a', { href: `#/c/${c.id}/leads`, text: countLine(counts.people, PERSON_STATUS) || 'None yet' }),
        h('div', { class: 'small muted', text: 'Size plan: ' + limitsLine(c.options) })),
      flowItem(allowRisky ? '3 Emails' : '3 Emails (verified only)',
        `${emails.verified_emails || 0} people have a verified email address. `,
        `${emails.found_not_verified || 0} have an address we could not verify` + (allowRisky ? '.' : ' (it will not be used).')),
      flowItem('4 Messages', h('a', { href: `#/c/${c.id}/messages`, text: countLine(counts.messages, MESSAGE_STATUS) || 'None yet' })),
      flowItem('5 Checks', qa.ok && qa.total ? `${fmtNum(qa.total)} things were found in the last check. See below.` : 'Run the checks below before you export or send.'),
      flowItem('6 Export / Send (optional)', h('a', { href: `#/c/${c.id}/export`, text: 'Download your people and messages, or send from your mailbox' })))));
  kids.push(
    h('section', { class: 'card', 'aria-labelledby': 'det-h' },
      h('h3', { id: 'det-h', text: 'About this campaign' }),
      h('dl', { class: 'kv' },
        h('dt', { text: 'Project' }), h('dd', null, c.project ? h('a', { href: '#/p/' + c.project.id, text: c.project.name }) : ''),
        h('dt', { text: 'Countries' }), h('dd', { text: (c.countries || []).join(', ') }),
        h('dt', { text: 'Sectors' }), h('dd', { text: (c.sectors || []).join(', ') || 'Any' }),
        h('dt', { text: 'Reaching them by' }), h('dd', { text: (c.channels || []).map((x) => (x === 'linkedin' ? 'LinkedIn' : 'Email')).join(' and ') }),
        h('dt', { text: 'Job roles' }), h('dd', { text: (c.designations || []).map((d) => d.label).join('; ') }),
        h('dt', { text: 'Lowest level' }), h('dd', { text: label(SENIORITY, c.seniority_min) }),
        h('dt', { text: 'Email addresses used' }), h('dd', { text: allowRisky ? 'Verified, and risky ones on catch-all servers' : 'Only verified addresses' }))));
  kids.push(reserveSection(c, ov, sibs.ok ? sibs.campaigns : []));
  const tasks = ov.search_tasks;
  const spot = ov.spot_check;
  if (tasks.open + tasks.submitted + tasks.skipped > 0 || spot.ai_leads > 0) {
    const lines = [];
    if (tasks.open + tasks.submitted + tasks.skipped > 0) {
      lines.push(h('p', { text: `Searches done by your AI assistant: ${tasks.open} waiting, ${tasks.submitted} done, ${tasks.skipped} skipped.` }));
    }
    if (spot.ai_leads > 0) {
      const state = { ok: 'passed', incomplete: 'not finished', failed: 'failed, so it needs another look', '': 'not needed' }[spot.state];
      lines.push(h('p', { text: `${spot.ai_leads} people were found by the AI's web search. Spot check: ${state}.` }));
    }
    kids.push(h('section', { class: 'card', 'aria-labelledby': 'ai-h' }, h('h3', { id: 'ai-h', text: "The AI's web search" }), ...lines));
  }
  kids.push(qaSection(c, qa));
  setMain(...kids);
}

const RESERVE_WHY = {
  limit_reached: "did not fit the size limits",
  not_matching: "do not match this campaign's countries or job roles",
  duplicates: 'were already in the campaign',
  already_contacted: 'were found or contacted recently',
};

function moveText(res) {
  const why = Object.entries(RESERVE_WHY).filter(([k]) => res[k]).map(([k, t]) => `${res[k]} ${t}`);
  return `Moved ${res.moved} people. ${res.left_in_reserve} are still saved for later` + (why.length ? ` (${why.join('; ')}).` : '.');
}

function reserveSection(c, ov, siblings) {
  const own = (c.counts.people || {}).reserve || 0;
  const donors = ov.reserve_sources || [];
  const others = siblings.filter((x) => x.id !== c.id && x.status !== 'archived');
  if (!own && !donors.length) return null;
  const status = h('div', { role: 'status' });
  const kids = [h('h3', { id: 'res-h', text: 'People saved for a later campaign' })];
  kids.push(h('p', { text: 'Some people did not fit a campaign\'s size limits. We keep them here so you can use them in another campaign of the same project.' }));
  if (own) {
    kids.push(h('p', null, `${fmtNum(own)} ${own === 1 ? 'person is' : 'people are'} saved in this campaign. `,
      h('a', { href: `#/c/${c.id}/leads?status=reserve`, text: 'See them' })));
  }
  const run = async (button, targetId, fromId) => {
    button.disabled = true;
    const out = await api(`/api/campaigns/${targetId}/move-reserve`, { method: 'POST', body: { from_campaign_id: fromId } });
    button.disabled = false;
    if (!out.ok) return setStatus(status, 'bad', errorText(out));
    toast(moveText(out), out.moved ? 'ok' : 'warn');
    route();
  };
  if (donors.length) {
    const pick = selectBox('rs-from', donors.map((d) => [d.id, `${d.name} (${d.reserve} saved)`]), donors[0].id);
    const go1 = h('button', { type: 'button', class: 'primary', text: 'Move them into this campaign' });
    go1.addEventListener('click', () => run(go1, c.id, Number(pick.value)));
    kids.push(h('div', { class: 'field' }, h('label', { for: 'rs-from', text: 'Take saved people from' }), h('div', { class: 'row' }, pick, go1)));
  }
  if (own && others.length) {
    const pick = selectBox('rs-to', others.map((d) => [d.id, d.name]), others[0].id);
    const go2 = h('button', { type: 'button', text: 'Move them there' });
    go2.addEventListener('click', () => run(go2, Number(pick.value), c.id));
    kids.push(h('div', { class: 'field' }, h('label', { for: 'rs-to', text: 'Send this campaign\'s saved people to' }), h('div', { class: 'row' }, pick, go2)));
  }
  kids.push(status);
  return h('section', { class: 'card stack', 'aria-labelledby': 'res-h' }, ...kids);
}

function qaSection(c, qa) {
  const box = h('div');
  const run = h('button', { type: 'button', class: 'primary', text: 'Run the checks' });
  run.addEventListener('click', async () => {
    run.disabled = true;
    const res = await api(`/api/campaigns/${c.id}/qa/run`, { method: 'POST' });
    run.disabled = false;
    if (res.ok) {
      toast('The checks are finished.');
      route();
    } else toast(errorText(res), 'bad');
  });
  box.appendChild(h('div', { class: 'row' }, h('h3', { class: 'grow', id: 'qa-h', text: '5 Checks' }), run));
  box.appendChild(h('p', { class: 'small muted', text: 'We look for mistakes in the people and messages before anything is exported or sent.' }));
  if (!qa.ok) {
    box.appendChild(notice('bad', errorText(qa)));
  } else if (!qa.issues.length) {
    box.appendChild(h('p', { text: 'Nothing has been found yet. Run the checks to see.' }));
  } else {
    box.appendChild(h('p', { class: 'muted small', text: `Showing ${qa.issues.length} of ${fmtNum(qa.total)} things from the last check (the ones to fix first).` }));
    box.appendChild(h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ...['What was found', 'How serious', 'Who', 'Details'].map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', null, ...qa.issues.map((i) => h('tr', null,
        h('td', null, i.title, ' ', h('span', { class: 'code', text: '(' + i.code + ')' })),
        h('td', null, badge(i.severity === 'block' ? 'To fix' : 'To look at', i.severity === 'block' ? 'bad' : 'warn')),
        h('td', null, h('button', { type: 'button', class: 'link', text: `${i.person} (${i.company})`, onclick: () => openLead(i.person_id) }),
          i.channel ? h('div', { class: 'small muted', text: label(CHANNEL, i.channel) }) : null),
        h('td', { text: i.detail })))))));
  }
  return h('section', { class: 'card', 'aria-labelledby': 'qa-h' }, box);
}

// ---- Companies --------------------------------------------------------------------------------

async function pageCompanies(ctx) {
  const c = ctx.campaign;
  const status = ctx.q.get('status') || '';
  const page = Number(ctx.q.get('page') || 1);
  const reply = await api(`/api/campaigns/${c.id}/companies${query({ status, page, page_size: 50 })}`);
  if (ctx.token !== routeToken) return;
  if (!reply.ok) return setMain(notice('bad', errorText(reply)));
  const base = `/c/${c.id}/companies`;
  const selected = new Set();
  const info = h('span', { class: 'muted small', role: 'status' });
  const boxes = [];

  const act = async (newStatus) => {
    if (!selected.size) return toast('Tick at least one company first.', 'warn');
    const res = await api(`/api/campaigns/${c.id}/companies/status`, {
      method: 'POST', body: { ids: [...selected], status: newStatus, reason: 'changed in dashboard' },
    });
    if (!res.ok) return toast(errorText(res), 'bad');
    const extra = res.unverified && res.unverified.length ? ` ${res.unverified.length} have a website address that could not be checked.` : '';
    toast(`${res.updated} updated.${extra}`, res.unverified && res.unverified.length ? 'warn' : 'ok');
    route();
  };

  const all = h('input', { type: 'checkbox', id: 'sel-all', 'aria-label': 'Select all companies on this page' });
  all.addEventListener('change', () => {
    boxes.forEach((b) => { b.checked = all.checked; b.dispatchEvent(new Event('change')); });
  });
  const rows = reply.companies.map((co) => {
    const box = h('input', { type: 'checkbox', 'aria-label': 'Select ' + co.name });
    box.addEventListener('change', () => {
      if (box.checked) selected.add(co.id); else selected.delete(co.id);
      info.textContent = selected.size ? `${selected.size} selected` : '';
    });
    boxes.push(box);
    const domainState = co.domain_ok === true ? badge('Website works', 'ok')
      : co.domain_ok === false ? badge('Website not verified', 'warn') : badge('Not checked', 'none');
    return h('tr', null,
      h('td', null, box),
      h('td', null, h('strong', { text: co.name }),
        co.warm_contacts ? [' ', badge(`Warm: ${co.warm_contacts} known contact${co.warm_contacts === 1 ? '' : 's'}`, 'info')] : null,
        co.aliases && co.aliases.length ? h('div', { class: 'small muted', text: 'Also: ' + co.aliases.join(', ') }) : null),
      h('td', { class: 'mono', text: co.domain || '-' }),
      h('td', null, domainState),
      h('td', { text: co.sector || '' }),
      h('td', null, badge(label(COMPANY_STATUS, co.status), COMPANY_TONE[co.status])),
      h('td', { class: 'small', text: (co.warnings || []).join(' ') }));
  });

  const filter = selectBox('f-status', [['', 'All'], ...Object.entries(COMPANY_STATUS)], status, (ev) =>
    go(base, { status: ev.target.value }));
  setMain(
    h('h2', { text: '1 Companies' }),
    whatsThis('These are the companies we will look for people at. Tick the ones you want and choose "Approve selected". Only approved companies are searched.'),
    h('div', { class: 'filters' }, h('div', null, h('label', { for: 'f-status', text: 'Show' }), filter),
      h('div', { class: 'row' },
        h('button', { type: 'button', class: 'primary', onclick: () => act('approved'), text: 'Approve selected' }),
        h('button', { type: 'button', onclick: () => act('excluded'), text: 'Leave out selected' }),
        h('button', { type: 'button', onclick: () => act('candidate'), text: 'Put back to waiting' }),
        info)),
    h('p', { class: 'muted small', text: countLine(reply.counts, COMPANY_STATUS) + '.' }),
    reply.companies.length
      ? h('div', { class: 'table-wrap' }, h('table', null,
        h('thead', null, h('tr', null, h('th', { scope: 'col' }, all), ...['Company', 'Website', 'Website check', 'Sector', 'Status', 'Warnings'].map((t) => h('th', { scope: 'col', text: t })))),
        h('tbody', null, ...rows)))
      : h('p', { text: 'No companies here yet. Ask your AI assistant to suggest some.' }),
    pager(reply, (p) => go(base, { status, page: p })),
  );
}

// ---- Leads ------------------------------------------------------------------------------------

async function pageLeads(ctx) {
  const c = ctx.campaign;
  const f = {
    status: ctx.q.get('status') || '', designation: ctx.q.get('designation') || '',
    company_id: ctx.q.get('company_id') || '', email_status: ctx.q.get('email_status') || '',
    qa: ctx.q.get('qa') || '', ai_verdict: ctx.q.get('ai_verdict') || '', search: ctx.q.get('search') || '',
    known: ctx.q.get('known') || '',
  };
  const allowRisky = !!(c.options || {}).allow_risky_emails;
  const page = Number(ctx.q.get('page') || 1);
  const [reply, filters] = await Promise.all([
    api(`/api/campaigns/${c.id}/leads${query({ ...f, page, page_size: 25 })}`),
    api(`/api/campaigns/${c.id}/lead-filters`),
  ]);
  if (ctx.token !== routeToken) return;
  if (!reply.ok) return setMain(notice('bad', errorText(reply)));
  const base = `/c/${c.id}/leads`;
  const apply = (extra) => go(base, { ...f, page: 1, ...extra });

  const searchBox = h('input', { type: 'search', id: 'f-search', value: f.search, placeholder: 'Name, title, company' });
  const form = h('form', { class: 'filters', role: 'search', onsubmit: (ev) => { ev.preventDefault(); apply({ search: searchBox.value.trim() }); } },
    h('div', null, h('label', { for: 'f-search', text: 'Search' }), searchBox),
    h('div', null, h('label', { for: 'f-status', text: 'Status' }),
      selectBox('f-status', [['', 'All'], ...Object.entries(PERSON_STATUS)], f.status, (e) => apply({ status: e.target.value }))),
    h('div', null, h('label', { for: 'f-des', text: 'Job role' }),
      selectBox('f-des', [['', 'All'], ...(filters.ok ? filters.designations : []).map((d) => [d, d])], f.designation, (e) => apply({ designation: e.target.value }))),
    h('div', null, h('label', { for: 'f-co', text: 'Company' }),
      selectBox('f-co', [['', 'All'], ...(filters.ok ? filters.companies : []).map((co) => [co.id, co.name])], f.company_id, (e) => apply({ company_id: e.target.value }))),
    h('div', null, h('label', { for: 'f-em', text: 'Email' }),
      selectBox('f-em', [['', 'All'], ['verified', 'Verified'], ['risky', 'Not verified: catch-all server'], ['guess', 'Not verified: guessed address'], ['unknown', 'Not verified: not checked'], ['invalid', 'Not a working address'], ['none', 'No email found']], f.email_status, (e) => apply({ email_status: e.target.value }))),
    h('div', null, h('label', { for: 'f-qa', text: 'Checks' }),
      selectBox('f-qa', [['', 'All'], ['block', 'Things to fix'], ['warn', 'Things to look at'], ['clean', 'No problems']], f.qa, (e) => apply({ qa: e.target.value }))),
    h('div', null, h('label', { for: 'f-ai', text: 'Checked by AI' }),
      selectBox('f-ai', [['', 'All'], ...Object.entries(AI_VERDICT)], f.ai_verdict, (e) => apply({ ai_verdict: e.target.value }))),
    h('div', null, h('label', { class: 'check', for: 'f-known' },
      h('input', { type: 'checkbox', id: 'f-known', checked: f.known === '1', onchange: (e) => apply({ known: e.target.checked ? '1' : '' }) }),
      'Only people I already know')),
    h('div', { class: 'row' },
      h('button', { type: 'submit', class: 'primary', text: 'Search' }),
      h('button', { type: 'button', onclick: () => go(base, {}), text: 'Clear filters' })),
  );

  const rows = reply.leads.map((p) =>
    h('tr', { class: 'clickable', onclick: (ev) => { if (ev.target.closest('a,button')) return; openLead(p.id); } },
      h('td', null, h('button', { type: 'button', class: 'link', text: p.raw_name || '(no name)', onclick: () => openLead(p.id) }),
        p.known_contact ? [' ', badge(label(KNOWN, p.known_contact.relationship), 'info')] : null),
      h('td', { text: p.title }),
      h('td', { text: p.company }),
      h('td', { text: p.designation }),
      h('td', null, badge(label(PERSON_STATUS, p.status), PERSON_TONE[p.status])),
      h('td', null, p.email ? h('div', { class: 'small mono', text: p.email }) : null, badge(emailLabel(p.email_status, allowRisky), EMAIL_TONE[p.email_status])),
      h('td', null, qaBadge(p.qa_block, p.qa_warn)),
      h('td', null, badge(label(AI_VERDICT, p.ai_verdict), AI_TONE[p.ai_verdict])),
      h('td', { class: 'small', text: label(ORIGIN, p.evidence_origin) })));

  setMain(
    h('h2', { text: '2 People' }),
    whatsThis('These are the people we found at your companies. "Ready" people can get messages. "Needs a look" people are held back until you decide. "Saved for a later campaign" people did not fit the size limits.'),
    form,
    reply.leads.length
      ? h('div', { class: 'table-wrap' }, h('table', null,
        h('thead', null, h('tr', null, ...['Name', 'Job title', 'Company', 'Job role', 'Status', 'Email', 'Checks', 'Checked by AI', 'How we found them'].map((t) => h('th', { scope: 'col', text: t })))),
        h('tbody', null, ...rows)))
      : h('p', { text: 'No people match.' }),
    pager(reply, (p) => go(base, { ...f, page: p })),
  );
}

async function openLead(personId) {
  const res = await api('/api/leads/' + personId);
  if (!res.ok) return toast(errorText(res), 'bad');
  const p = res.person;
  const kids = [];
  kids.push(h('p', null, badge(label(PERSON_STATUS, p.status), PERSON_TONE[p.status]), ' ',
    badge(label(ORIGIN, p.evidence_origin), p.evidence_origin === 'ai_web_search' ? 'warn' : 'info'),
    p.spot_check ? [' ', badge('Spot check: ' + p.spot_check, p.spot_check === 'ok' ? 'ok' : p.spot_check === 'wrong' ? 'bad' : 'warn')] : null,
    p.known_contact ? [' ', badge(label(KNOWN, p.known_contact.relationship) + ' (from your list)', 'info')] : null));
  kids.push(h('dl', { class: 'kv' },
    h('dt', { text: 'Job title' }), h('dd', { text: p.title }),
    h('dt', { text: 'Company' }), h('dd', { text: res.company.name + (res.company.domain ? ` (${res.company.domain})` : '') }),
    h('dt', { text: 'Job role' }), h('dd', { text: `${p.designation} - ${label(SENIORITY, p.seniority)}` }),
    h('dt', { text: 'Country' }), h('dd', { text: p.country }),
    h('dt', { text: 'LinkedIn' }), h('dd', null, extLink(p.linkedin_url, p.linkedin_url)),
    h('dt', { text: 'Name used in messages' }), h('dd', { text: p.greeting_name })));
  kids.push(h('h3', { text: 'Where we found them' }));
  kids.push(h('blockquote', null, h('strong', { text: p.evidence_title }), h('br'), p.evidence_snippet));
  if (p.source_query) kids.push(h('p', { class: 'small muted' }, 'Search used: ', h('span', { class: 'mono', text: p.source_query })));
  if (p.reasons && p.reasons.length) kids.push(h('div', null, h('h3', { text: 'Why this person is held back' }), reasonList(p.reasons)));
  kids.push(h('h3', { text: 'Email' }));
  if (res.emails.length) {
    kids.push(h('ul', null, ...res.emails.map((e) => h('li', null, h('span', { class: 'mono', text: e.email }), ' ', badge(label(EMAIL_STATUS, e.status), EMAIL_TONE[e.status]),
      e.verifier ? h('span', { class: 'small muted', text: ' checked by ' + e.verifier }) : null))));
  } else kids.push(h('p', { class: 'muted', text: 'No email found yet.' }));
  if (res.issues.length) kids.push(h('div', null, h('h3', { text: 'Things the checks found' }), issueList(res.issues)));
  kids.push(h('h3', { text: 'Messages' }));
  if (!res.messages.length) kids.push(h('p', { class: 'muted', text: 'No messages written yet.' }));
  for (const m of res.messages) kids.push(messageCard(m));
  const setStatus2 = async (status) => {
    let reason = 'changed in dashboard';
    if (status === 'confirmed') {
      const why = window.prompt('Why is this person right? (for example: I checked their profile)');
      if (!why || !why.trim()) return;
      reason = why.trim();
    }
    const out = await api(`/api/campaigns/${res.campaign_id}/leads/status`, {
      method: 'POST', body: { ids: [p.id], status, reason },
    });
    if (!out.ok) return toast(errorText(out), 'bad');
    if (out.refused && out.refused.length) return toast('Not changed: ' + out.refused[0].why, 'warn');
    toast('Status changed.');
    route();
  };
  kids.push(h('h3', { text: 'Change status' }));
  kids.push(h('div', { class: 'row' },
    p.status !== 'confirmed' ? h('button', { type: 'button', class: 'primary', onclick: () => setStatus2('confirmed'), text: 'Mark as ready' }) : null,
    p.status !== 'rejected' ? h('button', { type: 'button', onclick: () => setStatus2('rejected'), text: 'Reject' }) : null,
    p.status !== 'removed' ? h('button', { type: 'button', class: 'danger', onclick: () => setStatus2('removed'), text: 'Remove' }) : null));
  openPanel(p.raw_name || 'Person', ...kids);
}

// ---- Messages ---------------------------------------------------------------------------------

function messageCard(m, who) {
  const text = (m.subject ? 'Subject: ' + m.subject + '\n\n' : '') + m.body;
  return h('article', { class: 'card msg-card', 'aria-label': label(CHANNEL, m.channel) },
    h('div', { class: 'row' },
      h('strong', { class: 'grow', text: who ? `${who} - ${label(CHANNEL, m.channel)}` : label(CHANNEL, m.channel) }),
      badge(label(MESSAGE_STATUS, m.status), MESSAGE_TONE[m.status]),
      m.ai_review ? badge('Checked by AI: ' + label(AI_VERDICT, m.ai_review), AI_TONE[m.ai_review]) : badge('Not checked by AI yet', 'info')),
    m.subject ? h('div', { class: 'small' }, h('strong', { text: 'Subject: ' }), m.subject) : null,
    h('pre', { class: 'msg', text: m.body }),
    m.ai_review_notes ? h('p', { class: 'small' }, h('strong', { text: 'Notes from the AI check: ' }), m.ai_review_notes) : null,
    issueList(m.issues),
    h('button', { type: 'button', onclick: () => copyText(text), text: 'Copy' }));
}

async function pageMessages(ctx) {
  const c = ctx.campaign;
  const f = {
    channel: ctx.q.get('channel') || '', ai_review: ctx.q.get('ai_review') || '',
    status: ctx.q.get('status') || '', search: ctx.q.get('search') || '',
  };
  const page = Number(ctx.q.get('page') || 1);
  const reply = await api(`/api/campaigns/${c.id}/messages${query({ ...f, page, page_size: 20 })}`);
  if (ctx.token !== routeToken) return;
  if (!reply.ok) return setMain(notice('bad', errorText(reply)));
  const base = `/c/${c.id}/messages`;
  const apply = (extra) => go(base, { ...f, page: 1, ...extra });
  const searchBox = h('input', { type: 'search', id: 'm-search', value: f.search, placeholder: 'Name, company, words' });
  const form = h('form', { class: 'filters', role: 'search', onsubmit: (ev) => { ev.preventDefault(); apply({ search: searchBox.value.trim() }); } },
    h('div', null, h('label', { for: 'm-search', text: 'Search' }), searchBox),
    h('div', null, h('label', { for: 'm-ch', text: 'Channel' }),
      selectBox('m-ch', [['', 'All'], ...Object.entries(CHANNEL)], f.channel, (e) => apply({ channel: e.target.value }))),
    h('div', null, h('label', { for: 'm-st', text: 'Status' }),
      selectBox('m-st', [['', 'All'], ...Object.entries(MESSAGE_STATUS)], f.status, (e) => apply({ status: e.target.value }))),
    h('div', null, h('label', { for: 'm-ai', text: 'Checked by AI' }),
      selectBox('m-ai', [['', 'All'], ['pass', 'Passed'], ['fix', 'Needs a fix'], ['reject', 'Rejected'], ['none', 'Not checked yet']], f.ai_review, (e) => apply({ ai_review: e.target.value }))),
    h('div', { class: 'row' }, h('button', { type: 'submit', class: 'primary', text: 'Search' }),
      h('button', { type: 'button', onclick: () => go(base, {}), text: 'Clear filters' })));
  setMain(
    h('h2', { text: '4 Messages' }),
    whatsThis('These are the messages written for each person. Each one is checked for mistakes and then by the AI before you export or send it.'),
    h('p', { class: 'muted small', text: Object.entries(reply.counts).map(([k, v]) => `${v} ${label(CHANNEL, k).toLowerCase()}`).join(', ') || 'No messages yet.' }),
    form,
    reply.messages.length ? reply.messages.map((m) => messageCard(m, `${m.person}, ${m.company}`)) : h('p', { text: 'No messages match.' }),
    pager(reply, (p) => go(base, { ...f, page: p })),
  );
}

// ---- Export -----------------------------------------------------------------------------------

async function pageExport(ctx) {
  const c = ctx.campaign;
  const files = await api(`/api/campaigns/${c.id}/exports`);
  if (ctx.token !== routeToken) return;
  const result = h('div', { role: 'status' });
  const include = h('select', { id: 'x-inc' },
    h('option', { value: 'approved', text: 'Only messages the AI check passed (recommended)' }),
    h('option', { value: 'qa_pass', text: 'Messages that passed the checks (the AI check is not needed)' }),
    h('option', { value: 'all_confirmed', text: 'Everyone who is ready' }));
  const run = async (format, button) => {
    button.disabled = true;
    clear(result);
    result.appendChild(h('p', { text: 'Making the file...' }));
    const res = await api(`/api/campaigns/${c.id}/exports`, { method: 'POST', body: { format, include: include.value } });
    button.disabled = false;
    clear(result);
    if (!res.ok) return result.appendChild(notice('bad', errorText(res)));
    const lines = [
      h('p', null, h('strong', { text: `${res.exported} ${res.exported === 1 ? 'person' : 'people'} exported` }), `, ${res.messages_exported} message(s).`),
    ];
    if (res.path) lines.push(h('p', { class: 'small' }, 'File: ', h('span', { class: 'mono', text: res.path })));
    else if (res.message) lines.push(h('p', { text: res.message }));
    if (res.warnings && res.warnings.length) lines.push(notice('warn', listOf(res.warnings)));
    const blocked = Object.entries(res.blocked_by_code || {});
    if (blocked.length) {
      const total = blocked.reduce((sum, [, v]) => sum + v, 0);
      lines.push(h('p', { class: 'small' }, `${total} held back by the checks. `,
        h('span', { class: 'code', text: '(' + blocked.map(([k, v]) => `${k} x${v}`).join(', ') + ')' })));
    }
    result.appendChild(notice(res.path ? 'ok' : 'warn', ...lines));
    refreshFiles();
  };
  const buttons = [['generic', 'Download a CSV file (all details)'], ['lemlist', 'Download a file for lemlist'], ['instantly', 'Download a file for Instantly']].map(([fmt, text]) => {
    const b = h('button', { type: 'button', class: 'primary', text });
    b.addEventListener('click', () => run(fmt, b));
    return b;
  });
  const fileBox = h('div');
  const renderFiles = (data) => {
    clear(fileBox);
    if (!data.ok) return fileBox.appendChild(notice('bad', errorText(data)));
    fileBox.appendChild(h('p', { class: 'small' }, 'Folder: ', h('span', { class: 'mono', text: data.folder })));
    if (!data.files.length) return fileBox.appendChild(h('p', { class: 'muted', text: 'No files yet.' }));
    fileBox.appendChild(h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ...['File', 'Size', 'Made'].map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', null, ...data.files.map((fi) => h('tr', null, h('td', { class: 'mono', text: fi.name }), h('td', { text: fmtSize(fi.size) }), h('td', { text: fmtDate(fi.modified) })))))));
  };
  const refreshFiles = async () => renderFiles(await api(`/api/campaigns/${c.id}/exports`));
  renderFiles(files);
  const open = h('button', { type: 'button', text: 'Open the folder' });
  open.addEventListener('click', async () => {
    const res = await api(`/api/campaigns/${c.id}/exports/open-folder`, { method: 'POST' });
    if (!res.ok) toast(errorText(res), 'warn');
  });
  const wantsEmail = (c.channels || []).includes('email');
  const wantsLinkedin = (c.channels || []).includes('linkedin');
  setMain(
    h('h2', { text: '6 Export / Send (optional)' }),
    whatsThis('This is the last step. You can download your people and their messages and use them anywhere, or let Lead Generator send the emails from your own mailbox. Sending is always your choice.'),
    h('section', { class: 'card stack', 'aria-labelledby': 'dl-h' },
      h('h3', { id: 'dl-h', text: 'Download your people and messages' }),
      h('p', { text: 'Files are saved on this computer. Nothing is sent anywhere. Messages with problems are held back and listed in a report.' }),
      field('x-inc', 'What to include', include),
      h('div', { class: 'row' }, ...buttons),
      result),
    wantsEmail ? h('section', { class: 'card stack', 'aria-labelledby': 'send-mail-h' },
      h('h3', { id: 'send-mail-h', text: 'Send emails from your mailbox (optional)' }),
      h('p', { text: 'Lead Generator can send your approved emails from your own mailbox, a few at a time. Only people with a verified email address get one. You do not have to use this.' }),
      h('div', { class: 'row' },
        h('a', { class: 'button primary', href: '#/mailbox', text: 'Connect your mailbox' }),
        h('a', { class: 'button', href: `#/c/${c.id}/analytics`, text: 'See sending and replies' }))) : null,
    wantsLinkedin ? h('section', { class: 'card stack', 'aria-labelledby': 'li-h' },
      h('h3', { id: 'li-h', text: 'LinkedIn: export for lemlist or push to lemlist (optional)' }),
      h('p', { text: 'Download the lemlist file above and upload it to lemlist yourself, or ask your AI assistant to send the people to lemlist for you.' })) : null,
    h('section', { 'aria-labelledby': 'files-h', class: 'stack' },
      h('div', { class: 'row' }, h('h3', { class: 'grow', id: 'files-h', text: 'Latest files' }), open),
      fileBox),
  );
}

// ---- Analytics --------------------------------------------------------------------------------

async function pageAnalytics(ctx) {
  const c = ctx.campaign;
  const campaigns = c ? null : await api('/api/campaigns');
  const scopeId = c ? c.id : ctx.q.get('campaign_id') || '';
  const scopeQ = scopeId ? { campaign_id: scopeId } : {};
  const [byDes, bySector, sendingQ, replies, supp] = await Promise.all([
    api('/api/analytics' + query({ ...scopeQ, group_by: 'designation' })),
    api('/api/analytics' + query({ ...scopeQ, group_by: 'sector' })),
    api('/api/sending' + query(scopeQ)),
    api('/api/replies' + query(scopeQ)),
    api('/api/suppression'),
  ]);
  if (ctx.token !== routeToken) return;
  if (!byDes.ok) return setMain(notice('bad', errorText(byDes)));
  const kids = [h('h2', { text: 'Results' }), whatsThis('How your emails are doing: how many were sent, replied to, or bounced. LinkedIn results are tracked in lemlist.')];
  if (campaigns && campaigns.ok) {
    kids.push(h('div', { class: 'filters' }, h('div', null, h('label', { for: 'a-scope', text: 'Show' }),
      selectBox('a-scope', [['', 'All campaigns'], ...campaigns.campaigns.map((x) => [x.id, x.name])], scopeId, (e) => go('/analytics', { campaign_id: e.target.value })))));
  }
  const t = byDes.totals;
  kids.push(h('div', { class: 'cards' },
    tile('Sent', fmtNum(t.sent)), tile('Delivered', fmtNum(t.delivered)),
    tile('Bounced', fmtNum(t.bounced), fmtPct(t.bounce_rate) + ' of sent'),
    tile('Replied', fmtNum(t.replied), fmtPct(t.reply_rate) + ' of delivered'),
    tile('Unsubscribed', fmtNum(t.unsubscribed), fmtPct(t.unsubscribe_rate) + ' of delivered')));
  if (t.sent && t.small_sample) kids.push(notice('warn', 'Fewer than 20 emails were delivered, so these rates are too small to draw conclusions from.'));
  if (!t.sent) kids.push(h('p', { class: 'muted', text: 'No emails have been sent from this tool yet. LinkedIn and lemlist results are tracked in lemlist.' }));
  kids.push(groupTable('By job role', byDes.groups, 'Job role'));
  if (bySector.ok) kids.push(groupTable('By sector', bySector.groups, 'Sector'));
  kids.push(groupTable('By day', byDes.timeseries.map((d) => ({ ...d, key: d.day })), 'Day'));
  const oc = byDes.other_channels;
  if (oc) kids.push(h('p', { class: 'muted small', text: `${oc.lemlist_pushed} people sent to lemlist, ${oc.linkedin_messages_approved} approved LinkedIn message(s). ${oc.note}` }));
  kids.push(sendingSection(sendingQ, byDes.last_sync));
  kids.push(repliesSection(replies));
  kids.push(suppressionSection(supp));
  setMain(...kids);
}

function tile(titleText, value, sub) {
  return h('div', { class: 'card tile' }, h('div', { class: 'num', text: value }), h('div', { text: titleText }),
    sub ? h('div', { class: 'small muted', text: sub }) : null);
}

function groupTable(title, groups, keyLabel) {
  const id = 'g-' + title.replace(/\W+/g, '-').toLowerCase();
  if (!groups || !groups.length) {
    return h('section', { 'aria-labelledby': id }, h('h3', { id, text: title }), h('p', { class: 'muted', text: 'Nothing to show yet.' }));
  }
  const max = Math.max(...groups.map((g) => g.sent), 1);
  return h('section', { 'aria-labelledby': id },
    h('h3', { id, text: title }),
    h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null,
        h('th', { scope: 'col', text: keyLabel }), h('th', { scope: 'col', class: 'bar-cell', text: 'Sent (bar) and replies' }),
        ...['Sent', 'Bounced', 'Replied', 'Reply rate', 'Unsub.'].map((x) => h('th', { scope: 'col', class: 'num', text: x })))),
      h('tbody', null, ...groups.map((g) => h('tr', null,
        h('th', { scope: 'row', text: g.key }),
        h('td', { class: 'bar-cell' }, bar(g.sent, max, 'fill'), bar(g.replied, max, 'fill2')),
        h('td', { class: 'num', text: fmtNum(g.sent) }), h('td', { class: 'num', text: fmtNum(g.bounced) }),
        h('td', { class: 'num', text: fmtNum(g.replied) }),
        h('td', { class: 'num', text: g.small_sample ? fmtPct(g.reply_rate) + ' *' : fmtPct(g.reply_rate) }),
        h('td', { class: 'num', text: fmtNum(g.unsubscribed) })))))),
    groups.some((g) => g.small_sample) ? h('p', { class: 'muted small', text: '* fewer than 20 delivered: too few to judge.' }) : null);
}

function sendingSection(q, lastSync) {
  const sync = h('button', { type: 'button', text: 'Check the mailbox now' });
  const out = h('div', { role: 'status' });
  sync.addEventListener('click', async () => {
    sync.disabled = true;
    setStatus(out, 'info', 'Reading the mailbox (read-only)...');
    const res = await api('/api/mailbox/sync', { method: 'POST' });
    sync.disabled = false;
    if (res.ok) {
      setStatus(out, 'ok', (res.message || 'Mailbox checked.') + (res.done === false ? ' More mail is waiting: press the button again.' : ''));
      if (res.done !== false) route();
    } else setStatus(out, 'bad', errorText(res));
  });
  const kids = [h('h3', { id: 'send-h', text: 'Sending emails' })];
  if (q.ok) {
    kids.push(h('p', { text: `Queue: ${q.queued} waiting, ${q.sent} sent, ${q.failed} failed, ${q.refused} refused, ${q.skipped} skipped.` }));
    const worker = q.worker && q.worker.running ? 'The sender is running.' : 'The sender is not running.';
    kids.push(h('p', { text: `${q.paused ? 'Sending is PAUSED. ' : ''}${worker}` }));
    const toggle = h('button', { type: 'button', class: q.paused ? 'primary' : 'danger', text: q.paused ? 'Resume sending' : 'Pause sending' });
    toggle.addEventListener('click', async () => {
      const res = await api(q.paused ? '/api/sending/resume' : '/api/sending/pause', { method: 'POST' });
      if (res.ok) { toast(q.paused ? 'Sending can go on.' : 'Sending is paused.'); route(); } else toast(errorText(res), 'bad');
    });
    kids.push(h('div', { class: 'row' }, toggle, sync));
  } else kids.push(notice('bad', errorText(q)), sync);
  kids.push(h('p', { class: 'muted small', text: lastSync ? 'Mailbox last checked ' + fmtDate(lastSync) : 'The mailbox has not been checked yet.' }), out);
  return h('section', { class: 'card', 'aria-labelledby': 'send-h' }, ...kids);
}

function repliesSection(res) {
  const kids = [h('h3', { id: 'rep-h', text: 'Latest replies' })];
  if (!res.ok) kids.push(notice('bad', errorText(res)));
  else if (!res.events.length) kids.push(h('p', { class: 'muted', text: 'No replies, unsubscribes or bounces yet.' }));
  else {
    const kind = { reply: ['Replied', 'ok'], unsubscribe: ['Unsubscribed', 'warn'], bounce: ['Bounced', 'bad'] };
    kids.push(h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ...['When', 'Who', 'What', 'Text'].map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', null, ...res.events.map((e) => h('tr', null,
        h('td', { text: fmtDate(e.at) }), h('td', { text: `${e.person} (${e.company})` }),
        h('td', null, badge(kind[e.type][0], kind[e.type][1])), h('td', { text: e.detail })))))));
  }
  return h('section', { 'aria-labelledby': 'rep-h' }, ...kids);
}

function suppressionSection(res) {
  const kind = h('select', { id: 's-kind' }, h('option', { value: 'email', text: 'Email address' }),
    h('option', { value: 'domain', text: 'Website (everyone at it)' }), h('option', { value: 'linkedin', text: 'LinkedIn link' }));
  const values = h('textarea', { id: 's-vals', placeholder: 'One per line' });
  const add = h('button', { type: 'button', text: 'Add to the list' });
  add.addEventListener('click', async () => {
    const list = values.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    if (!list.length) return toast('Type at least one value.', 'warn');
    const out = await api('/api/suppression', { method: 'POST', body: { kind: kind.value, values: list, reason: 'added in dashboard' } });
    if (!out.ok) return toast(errorText(out), 'bad');
    const bad = out.invalid && out.invalid.length ? ` ${out.invalid.length} were not valid.` : '';
    toast(`Added ${out.added.length}.${bad}`, bad ? 'warn' : 'ok');
    route();
  });
  const entries = res.ok ? res.entries : [];
  return h('section', { class: 'card', 'aria-labelledby': 'sup-h' },
    h('h3', { id: 'sup-h', text: 'Do-not-contact list' }),
    h('p', { class: 'muted small', text: 'Nobody on this list is ever emailed or exported. To take someone off the list, ask whoever set this up for you, or use the command below.' }),
    h('p', { class: 'small' }, h('code', { text: 'leadgen suppression remove' })),
    entries.length
      ? h('p', { class: 'small', text: `${entries.length} entr${entries.length === 1 ? 'y' : 'ies'}: ` + entries.slice(0, 15).map((e) => e.value).join(', ') + (entries.length > 15 ? ', ...' : '') })
      : h('p', { class: 'muted', text: 'The list is empty.' }),
    field('s-kind', 'Add', kind), field('s-vals', 'Values', values), add);
}

// ---- Settings ---------------------------------------------------------------------------------

async function pageSettings(ctx) {
  const res = await api('/api/settings');
  if (ctx.token !== routeToken) return;
  if (!res.ok) return setMain(h('h2', { text: 'Settings' }), notice('bad', errorText(res)));
  const inputs = {};
  const text = (id, labelText, value, type) => {
    inputs[id] = h('input', { type: type || 'text', id, value: value ?? '', autocomplete: 'off' });
    return field(id, labelText, inputs[id]);
  };
  inputs.postal = h('textarea', { id: 's-postal', rows: 3 });
  inputs.postal.value = res.postal_address;
  inputs.strict = h('input', { type: 'checkbox', id: 's-strict', checked: res.strict_review });
  const status = h('div', { role: 'status' });
  const save = h('button', { type: 'submit', class: 'primary', text: 'Save settings' });
  const form = h('form', { class: 'stack' },
    h('fieldset', null, h('legend', { text: 'Sender (shown in emails)' }),
      text('s-name', 'Name', res.sender.name), text('s-company', 'Company', res.sender.company),
      text('s-role', 'Role', res.sender.role), text('s-email', 'Email address', res.sender.email, 'email'),
      text('s-website', 'Website', res.sender.website)),
    h('fieldset', null, h('legend', { text: 'Postal address' }),
      h('p', { class: 'small muted', text: 'Needed in the footer of every email you send (anti-spam rules in many countries).' }),
      field('s-postal', 'Address', inputs.postal)),
    h('fieldset', null, h('legend', { text: 'Language' }),
      text('s-locale', 'Spelling (for example en-GB, en-US, en-AU)', res.locale)),
    h('fieldset', null, h('legend', { text: 'Spending limits per campaign (in credits)' }),
      h('p', { class: 'small muted', text: 'The tool asks before going over these.' }),
      text('s-b-search', 'Searches', res.budgets.search, 'number'), text('s-b-hunter', 'Hunter email lookups', res.budgets.hunter, 'number'),
      text('s-b-verify', 'Email verifications', res.budgets.verify, 'number')),
    h('fieldset', null, h('legend', { text: 'Checking messages' }),
      h('label', { class: 'check', for: 's-strict' }, inputs.strict, 'Strict: every message must be passed by the AI check before it is exported or sent')),
    h('div', { class: 'row' }, save), status);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    save.disabled = true;
    const body = {
      sender: {
        name: inputs['s-name'].value, company: inputs['s-company'].value, role: inputs['s-role'].value,
        email: inputs['s-email'].value, website: inputs['s-website'].value,
      },
      postal_address: inputs.postal.value, locale: inputs['s-locale'].value,
      budgets: {
        search: Number(inputs['s-b-search'].value), hunter: Number(inputs['s-b-hunter'].value),
        verify: Number(inputs['s-b-verify'].value),
      },
      strict_review: inputs.strict.checked,
    };
    const out = await api('/api/settings', { method: 'POST', body });
    save.disabled = false;
    if (out.ok) { setStatus(status, 'ok', 'Settings saved.'); toast('Settings saved.'); } else setStatus(status, 'bad', errorText(out));
  });
  setMain(h('h2', { text: 'Settings' }), whatsThis('Who your messages come from, your postal address, your spelling, and how much a campaign may spend.'), form);
}

// ---- start ------------------------------------------------------------------------------------

route();
