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
  if (opts.body !== undefined) {
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

function errorText(reply) {
  const e = (reply && reply.error) || {};
  return [e.message || (reply && reply.message) || 'Something went wrong.', e.hint].filter(Boolean).join(' ');
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

const PERSON_STATUS = { confirmed: 'Confirmed', reserve: 'Kept for later', review: 'Needs review', rejected: 'Rejected', removed: 'Removed' };
const PERSON_TONE = { confirmed: 'ok', reserve: 'none', review: 'warn', rejected: 'bad', removed: 'none' };
const COMPANY_STATUS = { candidate: 'Candidate', approved: 'Approved', excluded: 'Excluded' };
const COMPANY_TONE = { candidate: 'info', approved: 'ok', excluded: 'none' };
const EMAIL_STATUS = {
  verified: 'Verified', risky: 'Risky', guess: 'Guess (unverified)', unknown: 'Unknown',
  invalid: 'Invalid', none: 'No email',
};
const EMAIL_TONE = { verified: 'ok', risky: 'warn', guess: 'warn', unknown: 'info', invalid: 'bad', none: 'none' };
const KNOWN = { contacted: 'Known contact', replied: 'Replied before', customer: 'Customer', partner: 'Partner' };
const ORIGIN = { api: 'Search API', ai_web_search: 'AI web search', user: 'Pasted by the user' };
const AI_VERDICT = {
  pass: 'Passed', fix: 'Needs a fix', reject: 'Rejected', pending: 'Not reviewed yet',
  no_messages: 'No messages',
};
const AI_TONE = { pass: 'ok', fix: 'warn', reject: 'bad', pending: 'info', no_messages: 'none' };
const CHANNEL = { email: 'Email', linkedin_invite: 'LinkedIn invite', linkedin_message: 'LinkedIn message' };
const MESSAGE_STATUS = {
  draft: 'Draft', qa_fail: 'Failed checks', qa_pass: 'Passed checks', approved: 'Approved',
  rejected: 'Rejected', sent: 'Sent',
};
const MESSAGE_TONE = { draft: 'none', qa_fail: 'bad', qa_pass: 'info', approved: 'ok', rejected: 'bad', sent: 'ok' };
const MARK = { ok: '✓ ', warn: '! ', bad: '✕ ', info: '', none: '' };

function badge(text, tone) {
  const t = tone || 'none';
  return h('span', { class: 'badge b-' + t, text: (MARK[t] || '') + text });
}

function label(map, key) {
  return map[key] || key || '';
}

function qaBadge(block, warn) {
  if (block) return badge(block + ' blocking', 'bad');
  if (warn) return badge(warn + ' warning' + (warn > 1 ? 's' : ''), 'warn');
  return badge('Clean', 'ok');
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
        badge(i.code, i.severity === 'block' ? 'bad' : 'warn'),
        ' ',
        h('strong', { text: i.title || '' }),
        i.detail ? ' - ' + i.detail : '',
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
  ['home', 'Home'],
  ['keys', 'API keys'],
  ['campaigns', 'Campaigns'],
  ['analytics', 'Analytics'],
  ['settings', 'Settings'],
];
const CAMPAIGN_NAV = [
  ['', 'Overview'],
  ['companies', 'Companies'],
  ['leads', 'Leads'],
  ['messages', 'Messages'],
  ['export', 'Export'],
  ['analytics', 'Analytics'],
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
      setMain(notice('bad', errorText(reply)), h('a', { href: '#/campaigns', text: 'Back to campaigns' }));
      return;
    }
    campaign = reply.campaign;
    campaign.overview = reply;
    renderNav('campaigns', campaign, sub);
  } else {
    renderNav(top, null, '');
  }
  const pages = {
    home: pageHome, keys: pageKeys, campaigns: pageCampaigns, analytics: pageAnalytics, settings: pageSettings,
  };
  const subPages = {
    '': pageOverview, companies: pageCompanies, leads: pageLeads, messages: pageMessages,
    export: pageExport, analytics: pageAnalytics,
  };
  const ctx = { token, q, campaign };
  const render = top === 'c' ? subPages[sub] : pages[top];
  if (!render) {
    setMain(h('h2', { text: 'Page not found' }), h('a', { href: '#/home', text: 'Go to the home page' }));
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

// ---- Home -------------------------------------------------------------------------------------

async function pageHome(ctx) {
  const st = await api('/api/status');
  if (ctx.token !== routeToken) return;
  if (!st.ok) return setMain(notice('bad', errorText(st)));
  const configured = st.keys.filter((k) => k.configured).length;
  const kids = [h('h2', { text: 'Home' })];
  if (st.notes && st.notes.length) kids.push(notice('warn', listOf(st.notes)));
  kids.push(
    h(
      'section',
      { class: 'card next', 'aria-labelledby': 'next-h' },
      h('h3', { id: 'next-h', text: 'What happens next' }),
      h('p', { text: st.next_step ? st.next_step.message : 'Nothing to do right now.' }),
      h('p', { class: 'muted small', text: 'Ask your AI assistant to carry on; it follows these steps.' }),
    ),
  );
  kids.push(
    h(
      'div',
      { class: 'cards' },
      h('div', { class: 'card tile' }, h('div', { class: 'num', text: String(st.campaigns.length) }), h('div', { text: 'campaigns' }),
        h('a', { href: '#/campaigns', text: 'Open campaigns' })),
      h('div', { class: 'card tile' }, h('div', { class: 'num', text: `${configured} of ${st.keys.length}` }), h('div', { text: 'providers set up' }),
        h('a', { href: '#/keys', text: 'Add or change API keys' })),
      h('div', { class: 'card tile' }, h('div', { class: 'num', text: String(st.offers.length) }), h('div', { text: 'offers learned' }),
        h('a', { href: '#/settings', text: 'Sender settings' })),
    ),
  );
  if (st.campaigns.length) {
    kids.push(h('h3', { text: 'Your campaigns' }));
    kids.push(campaignTable(st.campaigns));
  }
  kids.push(h('p', { class: 'muted small', text: `Version ${st.version}. Your data is in ${st.workspace}` }));
  setMain(...kids);
}

function sumCounts(obj) {
  return Object.values(obj || {}).reduce((a, b) => a + b, 0);
}

function campaignTable(list) {
  return h(
    'div',
    { class: 'table-wrap' },
    h(
      'table',
      null,
      h('thead', null, h('tr', null, ...['Campaign', 'Status', 'Countries', 'Channels', 'Companies', 'Leads', 'Messages'].map((t, i) =>
        h('th', { class: i >= 4 ? 'num' : null, scope: 'col', text: t })))),
      h(
        'tbody',
        null,
        ...list.map((c) =>
          h(
            'tr',
            null,
            h('td', null, h('a', { href: '#/c/' + c.id, text: c.name })),
            h('td', null, badge(c.status, c.status === 'active' ? 'ok' : 'none')),
            h('td', { text: (c.countries || []).join(', ') }),
            h('td', { text: (c.channels || []).join(', ') }),
            h('td', { class: 'num', text: fmtNum(sumCounts(c.counts && c.counts.companies)) }),
            h('td', { class: 'num', text: fmtNum(sumCounts(c.counts && c.counts.people)) }),
            h('td', { class: 'num', text: fmtNum(sumCounts(c.counts && c.counts.messages)) }),
          ),
        ),
      ),
    ),
  );
}

// ---- Keys -------------------------------------------------------------------------------------

async function pageKeys(ctx) {
  const reply = await api('/api/providers');
  if (ctx.token !== routeToken) return;
  if (!reply.ok) return setMain(notice('bad', errorText(reply)));
  const kids = [
    h('h2', { text: 'API keys' }),
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
    actions.push(field('key-' + p.key, 'API key', h('div', { class: 'row' }, input, save)));
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
  const reply = await api('/api/campaigns');
  if (ctx.token !== routeToken) return;
  if (!reply.ok) return setMain(notice('bad', errorText(reply)));
  if (!reply.campaigns.length) {
    return setMain(h('h2', { text: 'Campaigns' }), h('p', { text: 'No campaigns yet. Ask your AI assistant to start one.' }));
  }
  setMain(h('h2', { text: 'Campaigns' }), campaignTable(reply.campaigns));
}

function countLine(obj, map) {
  return Object.entries(obj || {}).map(([k, v]) => `${v} ${(map && map[k] ? map[k] : k).toLowerCase()}`).join(', ');
}

async function pageOverview(ctx) {
  const c = ctx.campaign;
  const ov = c.overview;
  const [st, qa] = await Promise.all([api('/api/status'), api(`/api/campaigns/${c.id}/qa`)]);
  if (ctx.token !== routeToken) return;
  const step = st.ok ? (st.campaigns.find((x) => x.id === c.id) || {}).next_step : null;
  const kids = [h('h2', { text: c.name })];
  if (step) {
    kids.push(h('section', { class: 'card next', 'aria-labelledby': 'next-h' },
      h('h3', { id: 'next-h', text: 'What happens next' }), h('p', { text: step.message })));
  }
  const counts = c.counts;
  kids.push(
    h('section', { class: 'card', 'aria-labelledby': 'det-h' },
      h('h3', { id: 'det-h', text: 'Details' }),
      h('dl', { class: 'kv' },
        h('dt', { text: 'Status' }), h('dd', { text: c.status }),
        h('dt', { text: 'Countries' }), h('dd', { text: (c.countries || []).join(', ') }),
        h('dt', { text: 'Sectors' }), h('dd', { text: (c.sectors || []).join(', ') }),
        h('dt', { text: 'Company size' }), h('dd', { text: c.company_size || '' }),
        h('dt', { text: 'Channels' }), h('dd', { text: (c.channels || []).join(', ') }),
        h('dt', { text: 'Job roles' }), h('dd', { text: (c.designations || []).map((d) => d.label).join('; ') }),
        h('dt', { text: 'Minimum seniority' }), h('dd', { text: c.seniority_min }),
        h('dt', { text: 'Search mode' }), h('dd', { text: (c.options || {}).search_mode || 'auto' }),
        h('dt', { text: 'Companies' }), h('dd', null, h('a', { href: `#/c/${c.id}/companies`, text: countLine(counts.companies, COMPANY_STATUS) })),
        h('dt', { text: 'Leads' }), h('dd', null, h('a', { href: `#/c/${c.id}/leads`, text: countLine(counts.people, PERSON_STATUS) })),
        h('dt', { text: 'Emails' }), h('dd', { text: countLine(counts.emails, EMAIL_STATUS) }),
        h('dt', { text: 'Messages' }), h('dd', null, h('a', { href: `#/c/${c.id}/messages`, text: countLine(counts.messages, MESSAGE_STATUS) })),
      )),
  );
  const tasks = ov.search_tasks;
  const spot = ov.spot_check;
  if (tasks.open + tasks.submitted + tasks.skipped > 0 || spot.ai_leads > 0) {
    const lines = [];
    if (tasks.open + tasks.submitted + tasks.skipped > 0) {
      lines.push(h('p', { text: `AI search tasks: ${tasks.open} open, ${tasks.submitted} done, ${tasks.skipped} skipped.` }));
    }
    if (spot.ai_leads > 0) {
      const state = { ok: 'passed', incomplete: 'not finished', failed: 'failed - needs another look', '': 'not needed' }[spot.state];
      lines.push(h('p', { text: `${spot.ai_leads} leads came from AI web search. Spot check: ${state}.` }));
    }
    kids.push(h('section', { class: 'card', 'aria-labelledby': 'ai-h' }, h('h3', { id: 'ai-h', text: 'AI search' }), ...lines));
  }
  kids.push(qaSection(c, qa));
  setMain(...kids);
}

function qaSection(c, qa) {
  const box = h('div');
  const run = h('button', { type: 'button', class: 'primary', text: 'Run the quality check' });
  run.addEventListener('click', async () => {
    run.disabled = true;
    const res = await api(`/api/campaigns/${c.id}/qa/run`, { method: 'POST' });
    run.disabled = false;
    if (res.ok) {
      toast('Quality check finished.');
      route();
    } else toast(errorText(res), 'bad');
  });
  box.appendChild(h('div', { class: 'row' }, h('h3', { class: 'grow', id: 'qa-h', text: 'Quality check' }), run));
  if (!qa.ok) {
    box.appendChild(notice('bad', errorText(qa)));
  } else if (!qa.issues.length) {
    box.appendChild(h('p', { text: qa.message || 'No issues are stored.' }));
  } else {
    box.appendChild(h('p', { class: 'muted small', text: `Showing ${qa.issues.length} of ${fmtNum(qa.total)} issues from the last run (blocking first).` }));
    box.appendChild(h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ...['Check', 'Severity', 'Who', 'Detail'].map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', null, ...qa.issues.map((i) => h('tr', null,
        h('td', null, h('strong', { text: i.code }), ' ', i.title),
        h('td', null, badge(i.severity === 'block' ? 'Blocking' : 'Warning', i.severity === 'block' ? 'bad' : 'warn')),
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
    const extra = res.unverified && res.unverified.length ? ` ${res.unverified.length} have a domain that could not be checked.` : '';
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
    const domainState = co.domain_ok === true ? badge('Domain works', 'ok')
      : co.domain_ok === false ? badge('Domain not verified', 'warn') : badge('Not checked', 'none');
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

  const filter = selectBox('f-status', [['', 'All statuses'], ...Object.entries(COMPANY_STATUS)], status, (ev) =>
    go(base, { status: ev.target.value }));
  setMain(
    h('h2', { text: 'Companies' }),
    h('div', { class: 'filters' }, h('div', null, h('label', { for: 'f-status', text: 'Show' }), filter),
      h('div', { class: 'row' },
        h('button', { type: 'button', class: 'primary', onclick: () => act('approved'), text: 'Approve selected' }),
        h('button', { type: 'button', onclick: () => act('excluded'), text: 'Exclude selected' }),
        h('button', { type: 'button', onclick: () => act('candidate'), text: 'Back to candidate' }),
        info)),
    h('p', { class: 'muted small', text: countLine(reply.counts, COMPANY_STATUS) + '.' }),
    reply.companies.length
      ? h('div', { class: 'table-wrap' }, h('table', null,
        h('thead', null, h('tr', null, h('th', { scope: 'col' }, all), ...['Company', 'Domain', 'Domain check', 'Sector', 'Status', 'Warnings'].map((t) => h('th', { scope: 'col', text: t })))),
        h('tbody', null, ...rows)))
      : h('p', { text: 'No companies here yet.' }),
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
  };
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
    h('div', null, h('label', { for: 'f-des', text: 'Job role group' }),
      selectBox('f-des', [['', 'All'], ...(filters.ok ? filters.designations : []).map((d) => [d, d])], f.designation, (e) => apply({ designation: e.target.value }))),
    h('div', null, h('label', { for: 'f-co', text: 'Company' }),
      selectBox('f-co', [['', 'All'], ...(filters.ok ? filters.companies : []).map((co) => [co.id, co.name])], f.company_id, (e) => apply({ company_id: e.target.value }))),
    h('div', null, h('label', { for: 'f-em', text: 'Email' }),
      selectBox('f-em', [['', 'All'], ...Object.entries(EMAIL_STATUS)], f.email_status, (e) => apply({ email_status: e.target.value }))),
    h('div', null, h('label', { for: 'f-qa', text: 'Quality check' }),
      selectBox('f-qa', [['', 'All'], ['block', 'Blocking issues'], ['warn', 'Warnings only'], ['clean', 'Clean']], f.qa, (e) => apply({ qa: e.target.value }))),
    h('div', null, h('label', { for: 'f-ai', text: 'AI review' }),
      selectBox('f-ai', [['', 'All'], ...Object.entries(AI_VERDICT)], f.ai_verdict, (e) => apply({ ai_verdict: e.target.value }))),
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
      h('td', null, p.email ? h('div', { class: 'small mono', text: p.email }) : null, badge(label(EMAIL_STATUS, p.email_status), EMAIL_TONE[p.email_status])),
      h('td', null, qaBadge(p.qa_block, p.qa_warn)),
      h('td', null, badge(label(AI_VERDICT, p.ai_verdict), AI_TONE[p.ai_verdict])),
      h('td', { class: 'small', text: label(ORIGIN, p.evidence_origin) })));

  setMain(
    h('h2', { text: 'Leads' }),
    form,
    reply.leads.length
      ? h('div', { class: 'table-wrap' }, h('table', null,
        h('thead', null, h('tr', null, ...['Name', 'Title', 'Company', 'Role group', 'Status', 'Email', 'Quality check', 'AI review', 'Found by'].map((t) => h('th', { scope: 'col', text: t })))),
        h('tbody', null, ...rows)))
      : h('p', { text: 'No leads match.' }),
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
    p.known_contact ? [' ', badge(label(KNOWN, p.known_contact.relationship) + ' (imported)', 'info')] : null));
  kids.push(h('dl', { class: 'kv' },
    h('dt', { text: 'Title' }), h('dd', { text: p.title }),
    h('dt', { text: 'Company' }), h('dd', { text: res.company.name + (res.company.domain ? ` (${res.company.domain})` : '') }),
    h('dt', { text: 'Role group' }), h('dd', { text: `${p.designation} - ${p.seniority}` }),
    h('dt', { text: 'Country' }), h('dd', { text: p.country }),
    h('dt', { text: 'LinkedIn' }), h('dd', null, extLink(p.linkedin_url, p.linkedin_url)),
    h('dt', { text: 'Greeting name' }), h('dd', { text: p.greeting_name })));
  kids.push(h('h3', { text: 'Evidence' }));
  kids.push(h('blockquote', null, h('strong', { text: p.evidence_title }), h('br'), p.evidence_snippet));
  if (p.source_query) kids.push(h('p', { class: 'small muted' }, 'Search used: ', h('span', { class: 'mono', text: p.source_query })));
  if (p.reasons && p.reasons.length) kids.push(h('div', null, h('h3', { text: 'Why it was confirmed or held back' }), listOf(p.reasons)));
  kids.push(h('h3', { text: 'Email' }));
  if (res.emails.length) {
    kids.push(h('ul', null, ...res.emails.map((e) => h('li', null, h('span', { class: 'mono', text: e.email }), ' ', badge(label(EMAIL_STATUS, e.status), EMAIL_TONE[e.status]),
      e.verifier ? h('span', { class: 'small muted', text: ' checked by ' + e.verifier }) : null))));
  } else kids.push(h('p', { class: 'muted', text: 'No email found yet.' }));
  if (res.issues.length) kids.push(h('div', null, h('h3', { text: 'Quality issues' }), issueList(res.issues)));
  kids.push(h('h3', { text: 'Messages' }));
  if (!res.messages.length) kids.push(h('p', { class: 'muted', text: 'No messages written yet.' }));
  for (const m of res.messages) kids.push(messageCard(m));
  const setStatus2 = async (status) => {
    let reason = 'changed in dashboard';
    if (status === 'confirmed') {
      const why = window.prompt('Why is this person right? (for example: checked the profile)');
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
    p.status !== 'confirmed' ? h('button', { type: 'button', class: 'primary', onclick: () => setStatus2('confirmed'), text: 'Mark as confirmed' }) : null,
    p.status !== 'rejected' ? h('button', { type: 'button', onclick: () => setStatus2('rejected'), text: 'Reject' }) : null,
    p.status !== 'removed' ? h('button', { type: 'button', class: 'danger', onclick: () => setStatus2('removed'), text: 'Remove' }) : null));
  openPanel(p.raw_name || 'Lead', ...kids);
}

// ---- Messages ---------------------------------------------------------------------------------

function messageCard(m, who) {
  const text = (m.subject ? 'Subject: ' + m.subject + '\n\n' : '') + m.body;
  return h('article', { class: 'card msg-card', 'aria-label': label(CHANNEL, m.channel) },
    h('div', { class: 'row' },
      h('strong', { class: 'grow', text: who ? `${who} - ${label(CHANNEL, m.channel)}` : label(CHANNEL, m.channel) }),
      badge(label(MESSAGE_STATUS, m.status), MESSAGE_TONE[m.status]),
      m.ai_review ? badge('AI: ' + label(AI_VERDICT, m.ai_review), AI_TONE[m.ai_review]) : badge('AI review pending', 'info')),
    m.subject ? h('div', { class: 'small' }, h('strong', { text: 'Subject: ' }), m.subject) : null,
    h('pre', { class: 'msg', text: m.body }),
    m.ai_review_notes ? h('p', { class: 'small' }, h('strong', { text: 'AI review notes: ' }), m.ai_review_notes) : null,
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
    h('div', null, h('label', { for: 'm-ai', text: 'AI review' }),
      selectBox('m-ai', [['', 'All'], ['pass', 'Passed'], ['fix', 'Needs a fix'], ['reject', 'Rejected'], ['none', 'Not reviewed yet']], f.ai_review, (e) => apply({ ai_review: e.target.value }))),
    h('div', { class: 'row' }, h('button', { type: 'submit', class: 'primary', text: 'Search' }),
      h('button', { type: 'button', onclick: () => go(base, {}), text: 'Clear filters' })));
  setMain(
    h('h2', { text: 'Messages' }),
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
    h('option', { value: 'approved', text: 'Only messages the AI review passed (recommended)' }),
    h('option', { value: 'qa_pass', text: 'Messages that passed the checks (AI review not required)' }),
    h('option', { value: 'all_confirmed', text: 'All confirmed leads' }));
  const run = async (format, button) => {
    button.disabled = true;
    clear(result);
    result.appendChild(h('p', { text: 'Making the file...' }));
    const res = await api(`/api/campaigns/${c.id}/exports`, { method: 'POST', body: { format, include: include.value } });
    button.disabled = false;
    clear(result);
    if (!res.ok) return result.appendChild(notice('bad', errorText(res)));
    const lines = [
      h('p', null, h('strong', { text: `${res.exported} lead(s) exported` }), `, ${res.messages_exported} message(s).`),
    ];
    if (res.path) lines.push(h('p', { class: 'small' }, 'File: ', h('span', { class: 'mono', text: res.path })));
    else if (res.message) lines.push(h('p', { text: res.message }));
    if (res.warnings && res.warnings.length) lines.push(notice('warn', listOf(res.warnings)));
    const blocked = Object.entries(res.blocked_by_code || {});
    if (blocked.length) lines.push(h('p', { class: 'small', text: 'Held back by checks: ' + blocked.map(([k, v]) => `${k} x${v}`).join(', ') }));
    if (res.next_steps && res.next_steps.length) lines.push(h('div', null, h('strong', { text: 'Next:' }), listOf(res.next_steps)));
    result.appendChild(notice(res.path ? 'ok' : 'warn', ...lines));
    refreshFiles();
  };
  const buttons = [['generic', 'Generic CSV (all fields)'], ['lemlist', 'lemlist CSV'], ['instantly', 'Instantly CSV']].map(([fmt, text]) => {
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
  const open = h('button', { type: 'button', text: 'Open folder' });
  open.addEventListener('click', async () => {
    const res = await api(`/api/campaigns/${c.id}/exports/open-folder`, { method: 'POST' });
    if (!res.ok) toast(errorText(res), 'warn');
  });
  setMain(
    h('h2', { text: 'Export' }),
    h('p', { text: 'Files are saved on this computer. Nothing is sent anywhere. Messages with blocking issues are held back and listed in the quality report.' }),
    h('div', { class: 'card stack' },
      field('x-inc', 'What to include', include),
      h('div', { class: 'row' }, ...buttons),
      result),
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
  const kids = [h('h2', { text: 'Analytics' })];
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
  kids.push(groupTable('By job role group', byDes.groups, 'Job role group'));
  if (bySector.ok) kids.push(groupTable('By sector', bySector.groups, 'Sector'));
  kids.push(groupTable('By day', byDes.timeseries.map((d) => ({ ...d, key: d.day })), 'Day'));
  const oc = byDes.other_channels;
  if (oc) kids.push(h('p', { class: 'muted small', text: `${oc.lemlist_pushed} lead(s) pushed to lemlist, ${oc.linkedin_messages_approved} approved LinkedIn message(s). ${oc.note}` }));
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
  const kids = [h('h3', { id: 'send-h', text: 'Sending' })];
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
    h('option', { value: 'domain', text: 'Domain' }), h('option', { value: 'linkedin', text: 'LinkedIn link' }));
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
    h('p', { class: 'muted small', text: 'Nobody here is ever emailed or exported. To take someone off the list, use "leadgen suppression remove" in a terminal.' }),
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
    h('fieldset', null, h('legend', { text: 'Credit budgets per campaign' }),
      h('p', { class: 'small muted', text: 'The tool asks before going over these.' }),
      text('s-b-search', 'Searches', res.budgets.search, 'number'), text('s-b-hunter', 'Hunter email lookups', res.budgets.hunter, 'number'),
      text('s-b-verify', 'Email verifications', res.budgets.verify, 'number')),
    h('fieldset', null, h('legend', { text: 'Review' }),
      h('label', { class: 'check', for: 's-strict' }, inputs.strict, 'Strict review: every message must be passed by the AI review before export or sending')),
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
  setMain(h('h2', { text: 'Settings' }), form);
}

// ---- start ------------------------------------------------------------------------------------

route();
