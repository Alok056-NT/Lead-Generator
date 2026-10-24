/* Lead Generator Studio, part 2: the project page (chat, progress, ready contacts, export and send).
   Plain JavaScript, no libraries. Loaded BEFORE app.js (see studio.js); it only defines functions and
   uses app.js's helpers (h, api, clear, setMain, toast, notice, errorText, badge, extLink, field,
   selectBox, pager, copyText, query, fmtNum, setStatus, route) and its variable routeToken.
   Everything from the server is put on the page as text, never as HTML. */
(function () {
  'use strict';

  const STATE_WORD = { done: 'Done', active: 'In progress', todo: 'Not started', skipped: 'Skipped' };
  const SIGN_IN_CODES = ['not_signed_in', 'bad_api_key', 'missing_cli'];
  let live = null; // the page now shown: { close() } stops its stream and timers

  function leave() {
    if (live) { live.close(); live = null; }
  }
  window.addEventListener('hashchange', leave);

  // ---- progress -----------------------------------------------------------------------------------

  function stepper(steps) {
    return h('ol', { class: 'stepper', 'aria-label': 'Progress' }, ...steps.map((s) =>
      h('li', { class: 'step ' + s.state, 'aria-current': s.state === 'active' ? 'step' : null },
        h('span', { class: 'step-mark', 'aria-hidden': 'true' }),
        h('div', null,
          h('div', { class: 'step-title' }, s.label, h('span', { class: 'sr-only', text: ': ' + (STATE_WORD[s.state] || '') })),
          h('div', { class: 'step-detail muted small', text: s.detail || '' })))));
  }

  // ---- ready contacts -----------------------------------------------------------------------------

  function viewDialog(lead) {
    const body = [h('p', { class: 'muted small' }, lead.title, lead.title && lead.company ? ' at ' : '', lead.company),
      h('p', null, badge(lead.checked ? 'Checked' : 'Not checked yet', lead.checked ? 'ok' : 'none'))];
    const actions = [];
    const close = h('button', { type: 'button', text: 'Close' });
    if (lead.linkedin_message) {
      body.push(h('h3', { text: 'LinkedIn message' }), h('pre', { class: 'msg', text: lead.linkedin_message }));
      const copy = h('button', { type: 'button', text: 'Copy message' });
      copy.addEventListener('click', () => copyText(lead.linkedin_message));
      actions.push(copy);
    }
    if (lead.email_body) {
      body.push(h('h3', { text: 'Email' }), h('p', { class: 'small' }, h('strong', { text: 'Subject: ' }), lead.email_subject), h('pre', { class: 'msg', text: lead.email_body }));
      const copy = h('button', { type: 'button', text: 'Copy email' });
      copy.addEventListener('click', () => copyText(lead.email_subject + '\n\n' + lead.email_body));
      actions.push(copy);
    }
    if (!actions.length) body.push(h('p', { class: 'muted', text: 'No messages have been written for this person yet.' }));
    const dlg = Studio.dialog(lead.name, body, [...actions, close]);
    close.addEventListener('click', () => dlg.close());
  }

  function resultsBody(r, view, again) {
    const kids = [];
    if (r.campaigns.length > 1) {
      const pick = selectBox('res-search', r.campaigns.map((c) => [c.id, c.name]), r.campaign_id, (ev) => {
        view.campaignId = Number(ev.target.value);
        view.page = 1;
        again();
      });
      kids.push(field('res-search', 'Search', pick));
    }
    if (!r.leads.length) {
      kids.push(h('p', { class: 'muted', text: 'Contacts will appear here as the AI finds and checks them.' }));
      return kids;
    }
    const heads = ['Name', 'Job', 'Company', 'LinkedIn', 'Email (verified)', 'Messages'];
    kids.push(h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ...heads.map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', null, ...r.leads.map((lead) => {
        const view1 = h('button', { type: 'button', text: 'View' });
        view1.addEventListener('click', () => viewDialog(lead));
        return h('tr', null,
          h('td', { text: lead.name }), h('td', { text: lead.title }), h('td', { text: lead.company }),
          h('td', null, lead.linkedin_url ? extLink(lead.linkedin_url, 'Open profile') : ''),
          h('td', { text: lead.email }),
          h('td', null, view1));
      })))));
    kids.push(pager(r, (page) => { view.page = page; again(); }));
    return kids;
  }

  // ---- export and send ----------------------------------------------------------------------------

  function actionsCard(state) {
    const note = h('div', { role: 'status' });
    const folderRow = h('div', { class: 'row' });
    const exportButtons = [['generic', 'Download for Excel (CSV)'], ['lemlist', 'Download for lemlist (CSV)']].map(([format, text]) => {
      const button = h('button', { type: 'button', text });
      button.addEventListener('click', async () => {
        const id = state.campaignId();
        if (!id) return setStatus(note, 'warn', 'There is nothing to export yet.');
        button.disabled = true;
        const res = await api('/api/campaigns/' + id + '/exports', { method: 'POST', body: { format, include: 'approved' } });
        button.disabled = false;
        if (!res.ok) return setStatus(note, 'bad', errorText(res));
        if (!res.exported) return setStatus(note, 'warn', 'Nobody is ready to export yet. Ask the AI to finish the checks first.');
        setStatus(note, 'ok', 'Saved ' + fmtNum(res.exported) + ' contacts to your exports folder.');
        clear(folderRow);
        const folder = h('button', { type: 'button', text: 'Open the folder' });
        folder.addEventListener('click', () => api('/api/campaigns/' + id + '/exports/open-folder', { method: 'POST' }));
        folderRow.appendChild(folder);
      });
      return button;
    });

    async function sendEmail() {
      const id = state.campaignId();
      if (!id) return toast('There is nothing to send yet.', 'warn');
      const dry = await api('/api/campaigns/' + id + '/send', { method: 'POST', body: { limit: 50, dry_run: true, confirm: false } });
      if (!dry.ok) return toast(errorText(dry), 'bad');
      const count = dry.count || 0;
      const missing = dry.would_be_blocked_by || [];
      const body = [h('p', { text: 'This will send ' + count + (count === 1 ? ' email' : ' emails') + ' from your own mailbox, one at a time over the next hours. (Up to 50 at a time.)' })];
      const first = (dry.emails || [])[0];
      if (first) {
        body.push(h('p', { class: 'small muted', text: 'Here is the first one:' }),
          h('pre', { class: 'msg', text: 'To: ' + first.to + '\nSubject: ' + first.subject + '\n\n' + first.body }));
      }
      if (missing.length) {
        body.push(notice('bad', h('strong', { text: 'Not ready yet:' }),
          h('ul', null, ...missing.map((m) => h('li', { text: [m.detail, m.fix].filter(Boolean).join(' ') })))));
      }
      const yes = await Studio.confirmDialog({
        title: 'Send emails?', body, yes: 'Yes, send ' + count, no: 'Not now', disabled: count === 0 || missing.length > 0,
      });
      if (!yes) return;
      const res = await api('/api/campaigns/' + id + '/send', { method: 'POST', body: { limit: Math.min(count, 50), dry_run: false, confirm: true } });
      if (!res.ok) return toast(errorText(res), 'bad');
      toast(res.message || 'The emails are queued.');
      state.refresh();
    }

    async function sendLemlist() {
      const id = state.campaignId();
      if (!id) return toast('There is nothing to send yet.', 'warn');
      const list = await api('/api/lemlist/campaigns');
      if (!list.ok) return toast(errorText(list), 'bad');
      if (!list.campaigns.length) return toast('You have no campaigns in lemlist yet. Make one there first.', 'warn');
      const text = h('p', { role: 'status', text: 'Checking...' });
      const yes = h('button', { type: 'button', class: 'primary', text: 'Yes, send', disabled: true });
      const no = h('button', { type: 'button', text: 'Not now' });
      const pick = selectBox('lm-campaign', list.campaigns.map((c) => [c.id, c.name]), list.campaigns[0].id, () => check());
      const dlg = Studio.dialog('Send to lemlist?', [field('lm-campaign', 'lemlist campaign', pick), text], [no, yes]);
      const push = (dryRun, cursor) => api('/api/campaigns/' + id + '/lemlist', {
        method: 'POST', body: { lemlist_campaign_id: pick.value, dry_run: dryRun, confirm: !dryRun, cursor },
      });
      async function check() {
        yes.disabled = true;
        text.textContent = 'Checking...';
        const dry = await push(true, null);
        if (!dry.ok) { text.textContent = errorText(dry); return; }
        const n = dry.to_push || 0;
        const skipped = dry.no_email_for_lemlist_api || 0;
        text.textContent = 'This will send ' + n + (n === 1 ? ' contact' : ' contacts') + ' to the lemlist campaign you chose.'
          + (skipped ? ' ' + skipped + ' are left out because they have no verified email.' : '');
        yes.textContent = 'Yes, send ' + n;
        yes.disabled = n === 0;
      }
      no.addEventListener('click', () => dlg.close());
      yes.addEventListener('click', async () => {
        yes.disabled = true;
        let cursor = null;
        let sent = 0;
        for (;;) {
          const res = await push(false, cursor);
          if (!res.ok) { text.textContent = errorText(res); yes.disabled = false; return; }
          const done = res.pushed || {};
          sent += (done.created || 0) + (done.duplicate || 0);
          if (res.done || !res.next_cursor) break;
          cursor = res.next_cursor;
          text.textContent = 'Sent ' + sent + ' so far...';
        }
        dlg.close();
        toast('Sent ' + sent + (sent === 1 ? ' contact' : ' contacts') + ' to lemlist.');
        state.refresh();
      });
      check();
    }

    const sendMail = h('button', { type: 'button', class: 'primary', text: 'Send by email...' });
    const sendLem = h('button', { type: 'button', class: 'primary', text: 'Send to lemlist...' });
    sendMail.addEventListener('click', sendEmail);
    sendLem.addEventListener('click', sendLemlist);

    const lemBox = h('div', { class: 'stack', hidden: true });
    const lemKey = h('input', { type: 'password', id: 'lm-key', autocomplete: 'off', spellcheck: 'false' });
    const lemSave = h('button', { type: 'button', class: 'primary', text: 'Save and check' });
    const lemStatus = h('div', { role: 'status' });
    lemSave.addEventListener('click', async () => {
      if (!lemKey.value.trim()) return setStatus(lemStatus, 'bad', 'Paste your key first.');
      lemSave.disabled = true;
      const res = await api('/api/providers/lemlist/key', { method: 'POST', body: { key: lemKey.value.trim() } });
      lemSave.disabled = false;
      lemKey.value = '';
      setStatus(lemStatus, res.ok ? 'ok' : 'bad', res.ok ? 'Your lemlist key is saved.' : errorText(res));
    });
    lemBox.append(field('lm-key', 'lemlist key', h('div', { class: 'row' }, lemKey, lemSave)), lemStatus);
    const connectLem = h('button', { type: 'button', class: 'link', text: 'Connect lemlist' });
    connectLem.addEventListener('click', () => { lemBox.hidden = !lemBox.hidden; });

    return h('section', { class: 'card stack', 'aria-labelledby': 'act-h' },
      h('h3', { id: 'act-h', text: 'Export and send' }),
      h('p', { class: 'muted small', text: 'Nothing is sent until you click and confirm.' }),
      h('div', { class: 'row' }, ...exportButtons), note, folderRow,
      h('div', { class: 'row' }, sendMail, sendLem),
      h('p', { class: 'small' }, h('a', { href: '#/mailbox', text: 'Connect your email' }), ' · ', connectLem),
      lemBox);
  }

  // ---- the chat -----------------------------------------------------------------------------------

  function chatPane(projectId, first, hooks) {
    const log = h('div', { class: 'chat-log', role: 'log', 'aria-live': 'polite', 'aria-label': 'Conversation' });
    const chips = h('div', { class: 'chips' });
    const pills = h('div', { class: 'file-pills' });
    const input = h('textarea', { class: 'chat-input', rows: '2', placeholder: 'Type your message...', 'aria-label': 'Your message' });
    const send = h('button', { type: 'button', class: 'primary', text: 'Send' });
    const attach = h('button', { type: 'button', text: 'Attach a file' });
    const picker = h('input', { type: 'file', class: 'sr-only', tabindex: '-1', 'aria-hidden': 'true', accept: '.pdf,.docx,.txt,.md,.csv,.xlsx', multiple: true });
    const typing = h('div', { class: 'tool-line running', hidden: true }, h('span', { class: 'dot' }), 'Thinking...');
    log.appendChild(typing); // always the last thing in the list; everything else goes in before it
    const files = [];
    let toolLines = {};
    let stream = null;
    let bubble = null;
    let running = false;

    function toBottom() { log.scrollTop = log.scrollHeight; }
    function put(el) { log.insertBefore(el, typing); toBottom(); return el; }
    function addBubble(role, text) { return put(h('div', { class: 'bubble ' + role, text })); }
    function addNote(text, tone) { put(notice(tone || 'ok', text)); }
    function showError(err) {
      const kids = [err.message || 'Something went wrong.', ' ', err.hint || ''];
      if (SIGN_IN_CODES.includes(err.code)) kids.push(' ', h('a', { href: '#/connect', text: 'Open Your AI' }));
      put(notice('bad', ...kids));
    }
    function toolLine(label, state) {
      return h('div', { class: 'tool-line ' + state }, h('span', { class: 'dot', 'aria-hidden': 'true' }), label);
    }
    function showChips(d) {
      clear(chips);
      for (const text of d.options || []) {
        const b = h('button', { type: 'button', class: 'chip', text });
        b.addEventListener('click', () => { if (!running) sendMessage(text); });
        chips.appendChild(b);
      }
      for (const btn of d.buttons || []) {
        if (typeof btn.href === 'string' && btn.href.startsWith('#/')) chips.appendChild(h('a', { class: 'button chip', href: btn.href, text: btn.label }));
      }
    }
    function setRunning(on) {
      running = on;
      send.textContent = on ? 'Stop' : 'Send';
      send.className = on ? 'danger solid' : 'primary';
      typing.hidden = !on;
      if (on) clear(chips); else toolLines = {};
    }
    function renderFiles() {
      clear(pills);
      files.forEach((name, i) => {
        const x = h('button', { type: 'button', class: 'pill-x', 'aria-label': 'Remove ' + name, text: '×' });
        x.addEventListener('click', () => { files.splice(i, 1); renderFiles(); });
        pills.appendChild(h('span', { class: 'file-pill' }, name, x));
      });
    }
    function closeStream() {
      if (stream) { stream.close(); stream = null; }
    }

    function follow(turnId) {
      closeStream();
      setRunning(true);
      bubble = null;
      stream = new EventSource('/api/projects/' + projectId + '/chat/stream?turn=' + encodeURIComponent(turnId));
      const on = (name, fn) => stream && stream.addEventListener(name, (ev) => {
        let data = {};
        try { data = JSON.parse(ev.data); } catch (err) { return; }
        fn(data);
      });
      on('text', (d) => {
        if (!bubble) bubble = addBubble('assistant', '');
        bubble.textContent += d.delta;
        toBottom();
      });
      on('tool', (d) => {
        bubble = null;
        if (d.state === 'started') {
          toolLines[d.id] = toolLine(d.label, 'running');
          put(toolLines[d.id]);
        } else if (toolLines[d.id]) {
          toolLines[d.id].className = 'tool-line ' + (d.ok ? 'ok' : 'bad');
        }
      });
      on('refresh', () => hooks.refresh(false));
      on('chips', showChips);
      on('error', showError);
      on('done', (d) => {
        closeStream();
        setRunning(false);
        if (d.cancelled) addNote('Stopped.');
        hooks.refresh(true);
        input.focus();
      });
      if (stream) {
        // While the connection is only interrupted the browser retries by itself (it sends the id of the
        // last event, so nothing is repeated). A closed stream means the server refused it.
        stream.onerror = () => {
          if (stream && stream.readyState === EventSource.CLOSED) {
            closeStream();
            setRunning(false);
            addNote('The connection to the AI was lost. Reload the page to see what happened.', 'warn');
          }
        };
      }
    }

    async function sendMessage(text) {
      const attachments = files.splice(0);
      clear(chips);
      renderFiles();
      addBubble('user', text || 'I attached a file.');
      if (attachments.length) addNote('Attached: ' + attachments.join(', '));
      const res = await api('/api/projects/' + projectId + '/chat', { method: 'POST', body: { message: text, attachments } });
      if (!res.ok) { showError(res.error || {}); return; }
      follow(res.turn_id);
    }

    send.addEventListener('click', async () => {
      if (running) { await api('/api/projects/' + projectId + '/chat/stop', { method: 'POST' }); return; }
      const text = input.value.trim();
      if (!text && !files.length) return;
      input.value = '';
      await sendMessage(text);
    });
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); send.click(); }
    });
    attach.addEventListener('click', () => picker.click());
    picker.addEventListener('change', async () => {
      for (const file of Array.from(picker.files)) {
        const form = new FormData();
        form.append('file', file);
        const res = await api('/api/projects/' + projectId + '/chat/upload', { method: 'POST', form });
        if (res.ok) { files.push(res.file.name); renderFiles(); } else toast(errorText(res), 'bad');
      }
      picker.value = '';
    });

    // the history, then the live turn if one is running
    if (!first.messages.length) addBubble('assistant', 'Hi! Tell me what you want to promote. Paste your website address, attach a brochure, or just describe it in a few words.');
    for (const m of first.messages) {
      if (m.role === 'user') addBubble('user', m.text);
      else if (m.role === 'assistant') addBubble('assistant', m.text);
      else put(toolLine(m.text, m.ok === false ? 'bad' : 'ok'));
    }
    const last = first.messages[first.messages.length - 1];
    if (last && last.role === 'assistant') showChips({ options: last.chips, buttons: last.buttons });
    if (first.turn.running) follow(first.turn.turn_id);
    toBottom();

    const ready = first.ai.ready;
    if (!ready) { input.disabled = true; send.disabled = true; attach.disabled = true; }
    const node = h('section', { class: 'card chat-card', 'aria-labelledby': 'chat-h' },
      h('h3', { id: 'chat-h', text: 'Chat with your AI' }),
      ready ? null : h('div', { class: 'notice warn' }, 'Connect your AI to start chatting. ', h('a', { class: 'button', href: '#/connect', text: 'Connect your AI' })),
      log, chips, pills,
      h('div', { class: 'chat-box' }, input, h('div', { class: 'row' }, attach, picker, h('span', { class: 'grow' }), send)));
    return { node, close: closeStream };
  }

  // ---- the page -----------------------------------------------------------------------------------

  async function pageProject(ctx) {
    leave();
    const id = ctx.projectId;
    const [studio, chat] = await Promise.all([api('/api/projects/' + id + '/studio'), api('/api/projects/' + id + '/chat')]);
    if (ctx.token !== routeToken) return;
    if (!studio.ok) return setMain(notice('bad', errorText(studio)), h('a', { href: '#/home', text: 'Back to your projects' }));
    if (!chat.ok) return setMain(notice('bad', errorText(chat)));

    const view = { campaignId: null, page: 1 };
    const progress = h('section', { class: 'card stack', 'aria-labelledby': 'prog-h' });
    const results = h('section', { class: 'card stack', 'aria-labelledby': 'res-h' });
    let timer = null;
    let dead = false;

    function drawProgress(s) {
      clear(progress);
      progress.append(h('h3', { id: 'prog-h', text: 'Progress' }), stepper(s.steps));
    }
    function drawResults(r) {
      view.campaignId = r.campaign_id;
      clear(results);
      results.append(h('div', { class: 'row' }, h('h3', { id: 'res-h', class: 'grow', text: 'Ready contacts' }), h('span', { class: 'muted', text: fmtNum(r.total) })),
        ...resultsBody(r, view, () => refresh(true)));
    }
    async function refresh(now) {
      clearTimeout(timer);
      const run = async () => {
        if (dead || ctx.token !== routeToken) return;
        const [s, r] = await Promise.all([
          api('/api/projects/' + id + '/studio'),
          api('/api/projects/' + id + '/results' + query({ campaign_id: view.campaignId, page: view.page })),
        ]);
        if (dead || ctx.token !== routeToken) return;
        if (s.ok) drawProgress(s);
        if (r.ok) drawResults(r);
      };
      if (now) await run(); else timer = setTimeout(run, 1200); // many tool events in a row make one refresh
    }

    const pane = chatPane(id, chat, { refresh });
    const actions = actionsCard({ campaignId: () => view.campaignId, refresh: () => refresh(true) });
    drawProgress(studio);
    const edit = h('button', { type: 'button', text: 'Edit project' });
    edit.addEventListener('click', () => Studio.openEditDialog(studio.project, () => route()));
    const right = h('div', { class: 'right stack' }, progress, results, actions);
    const grid = h('div', { class: 'studio-grid', 'data-pane': 'chat' }, pane.node, right);
    const tab = (key, text) => {
      const b = h('button', { type: 'button', role: 'tab', class: 'tab', 'aria-selected': key === 'chat' ? 'true' : 'false', text });
      b.addEventListener('click', () => {
        grid.dataset.pane = key;
        for (const other of b.parentNode.children) other.setAttribute('aria-selected', other === b ? 'true' : 'false');
      });
      return b;
    };
    setMain(
      h('div', { class: 'row page-head' }, h('a', { href: '#/home', text: 'All projects' }), h('h1', { class: 'grow', text: studio.project.name }), edit),
      h('div', { class: 'studio-tabs', role: 'tablist' }, tab('chat', 'Chat'), tab('results', 'Progress and results')),
      grid);
    live = { close() { dead = true; clearTimeout(timer); pane.close(); } };
    refresh(true);
  }

  window.Studio = Object.assign(window.Studio || {}, { pageProject });
})();
