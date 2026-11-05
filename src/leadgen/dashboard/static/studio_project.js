/* Lead Generator Studio, part 2: the project page (chat with the live timeline, progress, ready contacts,
   searches and message style, message templates, sign-off, product, contacts, export and send).
   Plain JavaScript, no libraries. Loaded BEFORE app.js (see studio.js); it only defines functions and
   uses app.js's helpers (h, api, clear, setMain, toast, notice, errorText, badge, extLink, field,
   selectBox, pager, copyText, query, fmtNum, setStatus, route, label, KNOWN, CAMPAIGN_STATUS, memoryBox,
   sourceKindText, setupSentence) and its variable routeToken. Card and layout memory comes from ui.js.
   Everything from the server is put on the page as text, never as HTML. */
(function () {
  'use strict';

  const STATE_WORD = { done: 'Done', active: 'In progress', todo: 'Not started', skipped: 'Skipped' };
  const SIGN_IN_CODES = ['not_signed_in', 'bad_api_key', 'missing_cli'];
  const LEVEL_NAME = { csuite: 'Top leaders', director: 'Directors and heads', manager: 'Managers', any: 'Anyone' };
  const DEPTH_NAME = { short: 'Short', standard: 'Standard', detailed: 'Detailed' };
  const EMAIL_WORD = { verified: 'Verified', risky: 'Risky', guess: 'A guess', invalid: 'Not valid', unknown: 'Not checked', none: 'None found' };
  const MESSAGE_WORD = { '': 'Not written yet', written: 'Written', approved: 'Approved', skipped: 'Skipped', sent: 'Sent' };
  const CHANNEL_WORD = { email: 'Email', linkedin_message: 'LinkedIn message', linkedin_invite: 'LinkedIn invitation' };
  const TAB_KEY = 'lg.tab.';
  let live = null; // the page now shown: { close() } stops its stream and timers
  let drawer = null; // the open review drawer: { close() }

  function leave() {
    if (live) { live.close(); live = null; }
    if (drawer) drawer.close();
  }
  window.addEventListener('hashchange', leave);

  // ---- progress -----------------------------------------------------------------------------------

  // Each step is a button that takes the person to the tab and card that step needs (onStep(key)).
  function stepper(steps, onStep) {
    return h('ol', { class: 'stepper', 'aria-label': 'Progress' }, ...steps.map((s) => {
      const go = h('button', { type: 'button', class: 'step-go', title: 'Show me where' },
        h('span', { class: 'step-mark', 'aria-hidden': 'true' }),
        h('span', null,
          h('span', { class: 'step-title' }, s.label, h('span', { class: 'sr-only', text: ': ' + (STATE_WORD[s.state] || '') })),
          h('span', { class: 'step-detail muted small', text: s.detail || '' })));
      go.addEventListener('click', () => onStep(s.key));
      return h('li', { class: 'step ' + s.state, 'aria-current': s.state === 'active' ? 'step' : null }, go);
    }));
  }

  // The one-sentence "Next:" line from the steps the page already has. Returns { text, key } or null.
  function nextStepLine(studio) {
    const step = studio.steps.find((x) => x.state === 'active') || studio.steps.find((x) => x.state === 'todo');
    if (!step) return null;
    const sum = studio.summary || {};
    const toCheck = Math.max(0, (sum.ready_contacts || 0) - (sum.verified_emails || 0));
    const plural = (n, word) => n + ' ' + word + (n === 1 ? '' : 's');
    const text = {
      product: 'Next: add your product',
      reach: 'Next: tell the AI who you want to reach',
      companies: 'Next: approve the companies',
      people: 'Next: find the people',
      emails: toCheck ? 'Next: check ' + plural(toCheck, 'email') : 'Next: check the emails',
      messages: sum.messages ? 'Next: review ' + plural(sum.messages, 'message') : 'Next: let the AI write the messages',
      checks: sum.messages ? 'Next: finish the checks on ' + plural(sum.messages, 'message') : 'Next: finish the checks',
      export: 'Next: export or send your contacts',
    }[step.key];
    return { text: text || 'Next: ' + step.label.toLowerCase(), key: step.key };
  }

  // ---- the review drawer ----------------------------------------------------------------------------

  // One word for a person's messages (the same rule as the server's): sent, skipped, approved, written, or ''.
  function stateOf(messages) {
    if (!messages.length) return '';
    if (messages.some((m) => m.status === 'sent')) return 'sent';
    const choices = new Set(messages.map((m) => m.owner_choice));
    return choices.size === 1 && !choices.has('') ? Array.from(choices)[0] : 'written';
  }

  function typing(el) {
    return !!el && (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) || el.isContentEditable);
  }

  // A side drawer with one person's messages. leads: the people on the page, index: who to open,
  // onChange(): runs after something was saved so the tables can redraw.
  function reviewDrawer(leads, index, onChange) {
    if (drawer) drawer.close();
    const opener = document.activeElement;
    let at = index;
    const body = h('div', { class: 'drawer-body' });
    const where = h('span', { class: 'muted small', 'aria-live': 'polite' });
    const closeButton = h('button', { type: 'button', class: 'ghost', text: 'Close', 'aria-label': 'Close the review (Esc)' });
    const prev = h('button', { type: 'button', text: 'Previous' });
    const next = h('button', { type: 'button', text: 'Next' });
    const node = h('aside', { class: 'drawer', role: 'dialog', 'aria-label': 'Review messages', tabindex: '-1' },
      h('header', { class: 'drawer-head' }, h('h2', { text: 'Review messages' }), closeButton),
      body,
      h('footer', { class: 'drawer-foot' }, prev, where, next,
        h('span', { class: 'muted small', text: 'Keys: J next, K previous, Esc close' })));

    function close() {
      document.removeEventListener('keydown', onKey);
      node.remove();
      drawer = null;
      if (opener && opener.isConnected) opener.focus();
    }
    function onKey(ev) {
      if (ev.key === 'Escape') { ev.preventDefault(); close(); return; }
      if (ev.ctrlKey || ev.metaKey || ev.altKey || typing(ev.target)) return;
      if (ev.key === 'j' || ev.key === 'J') move(1);
      else if (ev.key === 'k' || ev.key === 'K') move(-1);
    }
    function move(step) {
      const to = at + step;
      if (to >= 0 && to < leads.length) { at = to; show(); }
    }

    function messageBlock(lead, m) {
      const sent = m.status === 'sent';
      const subject = m.channel === 'email' ? h('input', { type: 'text', value: m.subject || '', maxlength: '300', 'aria-label': 'Subject', id: 'ds-subject', readOnly: sent }) : null;
      const text = h('textarea', { rows: '9', maxlength: '5000', value: m.body || '', 'aria-label': CHANNEL_WORD[m.channel] || 'Message', readOnly: sent });
      const status = h('div', { role: 'status', class: 'small' });
      const mark = h('span', { class: 'muted small' });
      const save = h('button', { type: 'button', text: 'Save changes', disabled: true });
      const approve = h('button', { type: 'button', class: 'primary', text: 'Approve', disabled: sent });
      const skip = h('button', { type: 'button', text: 'Skip', disabled: sent });
      const copy = h('button', { type: 'button', text: 'Copy' });
      const edited = () => text.value !== (m.body || '') || (subject !== null && subject.value !== (m.subject || ''));
      const paint = () => {
        mark.textContent = sent ? 'Sent' : m.owner_choice === 'approved' ? 'Approved' : m.owner_choice === 'skipped' ? 'Skipped' : 'Not reviewed yet';
        approve.setAttribute('aria-pressed', String(m.owner_choice === 'approved'));
        skip.setAttribute('aria-pressed', String(m.owner_choice === 'skipped'));
      };
      const changed = () => { lead.message_state = stateOf(lead.messages); onChange(); };
      async function saveText() {
        save.disabled = true;
        const res = await api('/api/messages/' + m.id, { method: 'PATCH', body: { body: text.value, subject: subject ? subject.value : null } });
        if (!res.ok) { setStatus(status, 'bad', errorText(res)); save.disabled = false; return false; }
        Object.assign(m, res.message);
        text.value = m.body;
        if (subject) subject.value = m.subject;
        const blocks = (res.issues || []).filter((i) => i.severity === 'block');
        if (blocks.length) setStatus(status, 'warn', 'Saved, but it needs another look: ' + blocks.map((i) => i.detail).join(' '));
        else setStatus(status, 'ok', 'Saved. The AI will look at the new text again.');
        paint();
        changed();
        return true;
      }
      async function choose(choice) {
        if (edited() && !(await saveText())) return;
        const pick = m.owner_choice === choice ? '' : choice;
        const res = await api('/api/messages/' + m.id + '/choice', { method: 'POST', body: { choice: pick } });
        if (!res.ok) return setStatus(status, 'bad', errorText(res));
        Object.assign(m, res.message);
        clear(status);
        paint();
        changed();
      }
      for (const input of [text, subject]) if (input) input.addEventListener('input', () => { save.disabled = sent || !edited(); });
      save.addEventListener('click', saveText);
      approve.addEventListener('click', () => choose('approved'));
      skip.addEventListener('click', () => choose('skipped'));
      copy.addEventListener('click', () => copyText((subject ? subject.value + '\n\n' : '') + text.value));
      paint();
      return h('section', { class: 'drawer-msg' },
        h('div', { class: 'row' }, h('h3', { class: 'grow', text: CHANNEL_WORD[m.channel] || 'Message' }), mark),
        subject ? field('ds-subject', 'Subject', subject) : null, text,
        h('div', { class: 'row' }, approve, skip, save, copy), status);
    }

    function show() {
      const lead = leads[at];
      clear(body);
      body.append(
        h('h3', { class: 'drawer-name', text: lead.name }),
        h('p', { class: 'muted' }, lead.title, lead.title && lead.company ? ' at ' : '', lead.company),
        h('p', { class: 'row small' },
          badge('Email: ' + (EMAIL_WORD[lead.email_status] || lead.email_status) + (lead.email ? ' (' + lead.email + ')' : ''), lead.email_status === 'verified' ? 'ok' : 'none'),
          lead.linkedin_url ? extLink(lead.linkedin_url, 'Open LinkedIn profile') : badge('No LinkedIn profile', 'none')),
        ...(lead.messages.length ? lead.messages.map((m) => messageBlock(lead, m))
          : [h('p', { class: 'muted', text: 'No messages have been written for this person yet.' })]));
      where.textContent = (at + 1) + ' of ' + leads.length;
      prev.disabled = at === 0;
      next.disabled = at === leads.length - 1;
      body.scrollTop = 0;
    }

    closeButton.addEventListener('click', close);
    prev.addEventListener('click', () => move(-1));
    next.addEventListener('click', () => move(1));
    document.addEventListener('keydown', onKey);
    document.body.appendChild(node);
    drawer = { close };
    show();
    node.focus();
  }

  // ---- ready contacts and the wave table -------------------------------------------------------------

  // The people table. columns: [[heading, (lead, open) => cell content]]; openAt(i) shows person i in the drawer.
  function leadTable(leads, columns, openAt) {
    return h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ...columns.map(([t]) => h('th', { scope: 'col', text: t })))),
      h('tbody', null, ...leads.map((lead, i) => {
        const open = () => openAt(i);
        const row = h('tr', { class: 'clickable' }, ...columns.map(([, cell]) => h('td', null, cell(lead, open))));
        row.addEventListener('click', (ev) => { if (!ev.target.closest('a, button')) open(); });
        return row;
      }))));
  }

  const nameButton = (lead, open) => {
    const b = h('button', { type: 'button', class: 'link', text: lead.name });
    b.addEventListener('click', open);
    return b;
  };
  const linkedinCell = (lead) => (lead.linkedin_url ? extLink(lead.linkedin_url, 'Open profile') : '');

  function resultsBody(r, view, again, openAt) {
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
    const review = (lead, open) => {
      const b = h('button', { type: 'button', text: lead.messages.length ? 'Review' : 'Open' });
      b.addEventListener('click', open);
      return h('span', { class: 'row' }, b, h('span', { class: 'muted small', text: MESSAGE_WORD[lead.message_state] || '' }));
    };
    kids.push(leadTable(r.leads, [
      ['Name', nameButton], ['Job', (l) => l.title], ['Company', (l) => l.company], ['LinkedIn', linkedinCell],
      ['Email (verified)', (l) => l.email],
      ['Phone (found, not checked)', (l) => (l.phone_status === 'waiting' ? 'Requested, waiting' : l.phone)],
      ['Messages', review],
    ], openAt));
    kids.push(pager(r, (page) => { view.page = page; again(); }));
    return kids;
  }

  // ---- waves ------------------------------------------------------------------------------------------

  const plainDay = (iso) => (iso ? String(iso).slice(0, 10) : '');

  // The waves list. handlers: { open(id), exportWave(id), launch() }.
  function wavesBody(waves, handlers) {
    const launch = h('button', { type: 'button', class: 'primary', text: 'Launch a new wave' });
    launch.addEventListener('click', handlers.launch);
    const kids = [h('div', { class: 'row' }, h('p', { class: 'muted small grow', text: 'Each wave is one search: its own companies, people and messages. Newest first.' }), launch)];
    if (!waves.length) {
      kids.push(h('p', { class: 'muted', text: 'No waves yet. Tell the AI who you want to reach, or launch one here.' }));
      return kids;
    }
    const heads = ['Wave', 'Started', 'Status', 'Companies', 'People', 'Verified emails', 'Messages written', 'Sent', 'Message style', ''];
    kids.push(h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ...heads.map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', null, ...waves.map((w) => {
        const open = h('button', { type: 'button', class: 'link', text: w.name, title: 'Show everyone in this wave' });
        open.addEventListener('click', () => handlers.open(w.id));
        const exp = h('button', { type: 'button', text: 'Export', 'aria-label': 'Export ' + w.name });
        exp.addEventListener('click', () => handlers.exportWave(w.id));
        return h('tr', null, h('td', null, open), h('td', { text: plainDay(w.started) }), h('td', null, badge(w.status, w.status === 'Planning' ? 'none' : 'ok')),
          ...[w.companies, w.people, w.verified_emails, w.messages, w.sent].map((n) => h('td', { class: 'num', text: fmtNum(n) })),
          h('td', { text: w.style }), h('td', null, exp));
      })))));
    return kids;
  }

  // One wave's whole table with paging; the person's row opens the review drawer.
  function waveTable(wave, r, handlers) {
    const back = h('button', { type: 'button', text: 'Back to all waves' });
    back.addEventListener('click', handlers.back);
    const kids = [h('div', { class: 'row' }, back, h('strong', { class: 'grow', text: wave ? wave.name : 'Wave' }))];
    if (!r.leads.length) {
      kids.push(h('p', { class: 'muted', text: 'Nobody is ready in this wave yet.' }));
      return kids;
    }
    const emailCell = (l) => [l.email, l.email ? ' · ' : '', EMAIL_WORD[l.email_status] || l.email_status].join('');
    kids.push(leadTable(r.leads, [
      ['Name', nameButton], ['Job', (l) => l.title], ['Company', (l) => l.company],
      ['Email (status)', emailCell], ['LinkedIn', linkedinCell],
      ['Message status', (l) => MESSAGE_WORD[l.message_state] || ''],
    ], handlers.openAt));
    kids.push(pager(r, handlers.page));
    return kids;
  }

  // "Launch a new wave": a few plain fields, then ONE chat message for the AI (it follows the usual estimate and yes flow).
  function launchDialog(waves, sendToAi) {
    const numbers = waves.map((w) => /^Wave (\d+)$/i.exec(w.name)).filter(Boolean).map((m) => Number(m[1]));
    const nextNumber = numbers.length ? Math.max(...numbers) + 1 : waves.length + 1;
    const name = h('input', { type: 'text', id: 'lw-name', value: 'Wave ' + nextNumber, maxlength: '80', autocomplete: 'off' });
    const same = selectBox('lw-same', [['', 'Start fresh'], ...waves.map((w) => [w.id, w.name])], waves.length ? String(waves[0].id) : '');
    const change = h('textarea', { id: 'lw-change', rows: '3', maxlength: '500', placeholder: 'For example: aim at hotels instead of airlines' });
    const companies = h('input', { type: 'number', id: 'lw-companies', value: '30', min: '1', max: '500' });
    const per = h('input', { type: 'number', id: 'lw-per', value: '2', min: '1', max: '10' });
    const status = h('div', { role: 'status' });
    const cancel = h('button', { type: 'button', text: 'Cancel' });
    const go = h('button', { type: 'button', class: 'primary', text: 'Launch' });
    const dlg = Studio.dialog('Launch a new wave', [
      field('lw-name', 'Wave name', name),
      field('lw-same', 'Keep the same targeting as', same),
      field('lw-change', 'What should change? (optional)', change),
      h('div', { class: 'row' }, field('lw-companies', 'Companies', companies), field('lw-per', 'People per company', per)),
      h('p', { class: 'small muted', text: 'The AI will show you what it plans and what it costs, and waits for your yes before anything is spent.' }),
      status], [cancel, go]);
    cancel.addEventListener('click', () => dlg.close());
    go.addEventListener('click', () => {
      const title = name.value.trim();
      const nCompanies = Number(companies.value);
      const nPer = Number(per.value);
      if (!title) return setStatus(status, 'bad', 'Give the wave a name.');
      if (!(nCompanies >= 1) || !(nPer >= 1)) return setStatus(status, 'bad', 'Enter how many companies and how many people per company.');
      const from = waves.find((w) => String(w.id) === same.value);
      const lines = ['Start a new wave called "' + title + '".',
        from ? 'Keep the same targeting as "' + from.name + '" (its countries, kinds of company, job roles and message style).' : 'Start fresh: work out the targeting from my product.',
        change.value.trim() ? 'What should change: ' + change.value.trim() : 'Nothing should change.',
        'Size: up to ' + nCompanies + ' companies and ' + nPer + ' people per company.',
        'Show me the plan and the cost first, and wait for my yes before anything is spent.'];
      if (sendToAi(lines.join('\n'))) dlg.close();
    });
  }

  // ---- searches and message style -----------------------------------------------------------------

  function searchesBody(projectId, campaigns, choices) {
    const link = h('p', { class: 'row' }, h('a', { class: 'button', href: '#/p/' + projectId + '/new-campaign', text: 'New search (form)' }));
    const intro = h('p', { class: 'muted small', text: 'Or tell the AI who you want to reach.' });
    if (!campaigns.length) return [h('p', { class: 'muted', text: 'No searches yet. Tell the AI who you want to reach.' }), link];
    const rows = campaigns.map((c) => {
      const pick = selectBox('ms-' + c.id, choices.map((x) => [x.key, x.label]), c.template_key, async (ev) => {
        const res = await api('/api/campaigns/' + c.id + '/template', { method: 'POST', body: { template_key: ev.target.value } });
        if (res.ok) toast('Saved. New messages for this search follow it.');
        else { toast(errorText(res), 'bad'); ev.target.value = c.template_key; }
      });
      pick.setAttribute('aria-label', 'Message style for ' + c.name);
      return h('tr', null, h('td', { text: c.name }),
        h('td', null, badge(label(CAMPAIGN_STATUS, c.status), c.status === 'active' ? 'ok' : 'none')),
        h('td', { class: 'num', text: fmtNum(c.counts.people.confirmed || 0) }),
        h('td', null, pick));
    });
    return [intro, h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, ...['Search', 'Status', 'Ready', 'Message style'].map((t) => h('th', { scope: 'col', text: t })))),
      h('tbody', null, ...rows))), link];
  }

  // ---- message templates --------------------------------------------------------------------------

  // The editor: initial is a template (with id to update it) or null for a new one.
  function templateEditor(projectId, initial, onSaved, placeholders) {
    const t = initial || {};
    const name = h('input', { type: 'text', id: 'tp-name', value: t.name || '', autocomplete: 'off', maxlength: '80' });
    const level = selectBox('tp-level', Object.entries(LEVEL_NAME), t.level || 'any');
    const depth = selectBox('tp-depth', Object.entries(DEPTH_NAME), t.depth || 'standard');
    const useCase = h('input', { type: 'text', id: 'tp-use', value: t.use_case || '', autocomplete: 'off', maxlength: '200' });
    const subject = h('input', { type: 'text', id: 'tp-subject', value: t.subject || '', autocomplete: 'off', maxlength: '120' });
    const body = h('textarea', { id: 'tp-body', rows: '12', maxlength: '3000', value: t.body || '' });
    const insertRow = h('div', { class: 'row small' }, 'Insert:', ...Object.entries(placeholders).map(([name, meaning]) => {
      const b = h('button', { type: 'button', class: 'ghost', title: meaning, text: '{' + name + '}' });
      b.addEventListener('click', () => {
        body.setRangeText('{' + name + '}', body.selectionStart, body.selectionEnd, 'end');
        body.focus();
      });
      return b;
    }));
    const help = h('p', { class: 'small muted', text: 'The AI fills in every {placeholder} for each person and words the rest naturally. Your sign-off is added at the end if you leave {sign_off} out.' });
    const status = h('div', { role: 'status' });
    const cancel = h('button', { type: 'button', text: 'Cancel' });
    const save = h('button', { type: 'button', class: 'primary', text: 'Save' });
    const dlg = Studio.dialog('Your template', [
      field('tp-name', 'Name', name), field('tp-level', 'Who it is for', level), field('tp-depth', 'Length', depth),
      field('tp-use', 'When to use it', useCase), field('tp-subject', 'Subject (email only)', subject),
      field('tp-body', 'Message', body), insertRow, help, status], [cancel, save]);
    cancel.addEventListener('click', () => dlg.close());
    save.addEventListener('click', async () => {
      save.disabled = true;
      const payload = { name: name.value, level: level.value, depth: depth.value, use_case: useCase.value, subject: subject.value, body: body.value };
      const res = t.id
        ? await api('/api/projects/' + projectId + '/templates/' + t.id, { method: 'PUT', body: payload })
        : await api('/api/projects/' + projectId + '/templates', { method: 'POST', body: payload });
      save.disabled = false;
      if (res.ok) { dlg.close(); toast('Saved.'); if (onSaved) onSaved(); } else setStatus(status, 'bad', errorText(res));
    });
  }

  // A built-in template in full; its copy button opens the editor with the same text.
  function templateView(t, onCopy) {
    const copy = h('button', { type: 'button', text: 'Copy into my templates' });
    const close = h('button', { type: 'button', text: 'Close' });
    const dlg = Studio.dialog(t.name, [
      h('p', { class: 'muted small', text: t.use_case || '' }),
      t.subject ? h('p', { class: 'small', text: 'Subject: ' + t.subject }) : null,
      h('pre', { class: 'msg', text: t.body })], [copy, close]);
    close.addEventListener('click', () => dlg.close());
    copy.addEventListener('click', () => { dlg.close(); onCopy(); });
  }

  // data: the reply of GET /api/projects/<id>/templates; reload runs after a save or a delete.
  function templatesBody(projectId, data, reload) {
    const placeholders = data.placeholders || {};
    const view = (t) => {
      const b = h('button', { type: 'button', text: 'View' });
      b.addEventListener('click', () => templateView(t, () => templateEditor(projectId, {
        name: t.name + ' (my copy)', level: t.level, depth: t.depth, use_case: t.use_case, subject: t.subject, body: t.body,
      }, reload, placeholders)));
      return b;
    };
    const builtin = h('div', { class: 'table-wrap' }, h('table', null,
      h('thead', null, h('tr', null, h('th', { scope: 'col', text: 'Template' }), h('th', { scope: 'col', text: 'When to use it' }), h('th', { scope: 'col', text: '' }))),
      h('tbody', null, ...data.builtin.map((t) => h('tr', null, h('td', { text: t.name }), h('td', { text: t.use_case }), h('td', null, view(t)))))));

    const edit = (t) => {
      const b = h('button', { type: 'button', text: 'Edit' });
      b.addEventListener('click', () => templateEditor(projectId, t, reload, placeholders));
      return b;
    };
    const remove = (t) => {
      const b = h('button', { type: 'button', text: 'Delete' });
      b.addEventListener('click', async () => {
        const yes = await Studio.confirmDialog({
          title: 'Delete this template?',
          body: [h('p', { text: "Searches that use it go back to matching each person's level." })],
          yes: 'Delete', no: 'Keep it',
        });
        if (!yes) return;
        const res = await api('/api/projects/' + projectId + '/templates/' + t.id, { method: 'DELETE' });
        if (res.ok) { toast('Deleted.'); reload(); } else toast(errorText(res), 'bad');
      });
      return b;
    };
    const newButton = h('button', { type: 'button', class: 'primary', text: 'New template' });
    newButton.addEventListener('click', () => templateEditor(projectId, null, reload, placeholders));
    const yours = data.custom.length
      ? h('div', { class: 'table-wrap' }, h('table', null,
        h('thead', null, h('tr', null, ...['Template', 'For', 'Length', 'When to use it', ''].map((x) => h('th', { scope: 'col', text: x })))),
        h('tbody', null, ...data.custom.map((t) => h('tr', null, h('td', { text: t.name }), h('td', { text: LEVEL_NAME[t.level] || '' }),
          h('td', { text: DEPTH_NAME[t.depth] || '' }), h('td', { text: t.use_case }), h('td', null, h('div', { class: 'row' }, edit(t), remove(t))))))))
      : h('p', { class: 'muted', text: 'You have not written your own yet.' });
    return [
      h('p', { class: 'muted small', text: 'Messages follow the style chosen for each search (see Searches and message style). You can read the built-in templates and write your own.' }),
      h('h4', { text: 'Built-in' }), builtin,
      h('div', { class: 'row' }, h('h4', { class: 'grow', text: 'Yours' }), newButton),
      yours,
    ];
  }

  // ---- your sign-off, product, contacts ----------------------------------------------------------

  function signOffBody(project, onSaved) {
    const name = h('input', { type: 'text', id: 'so-name', value: project.sender_name || '', autocomplete: 'off', maxlength: '120' });
    const title = h('input', { type: 'text', id: 'so-title', value: project.sender_title || '', autocomplete: 'off', maxlength: '120' });
    const company = h('input', { type: 'text', id: 'so-company', value: project.sender_company || '', autocomplete: 'off', maxlength: '120' });
    const preview = h('pre', { class: 'msg' });
    const status = h('div', { role: 'status' });
    const save = h('button', { type: 'button', class: 'primary', text: 'Save' });
    const show = () => {
      const lines = [name, title, company].map((i) => i.value.trim()).filter(Boolean);
      preview.textContent = ['Kind regards,', ...lines].join('\n');
    };
    for (const input of [name, title, company]) input.addEventListener('input', show);
    save.addEventListener('click', async () => {
      save.disabled = true;
      const res = await api('/api/projects/' + project.id, { method: 'PATCH', body: { sender_name: name.value, sender_title: title.value, sender_company: company.value } });
      save.disabled = false;
      if (res.ok) { toast('Saved. New messages will end with this.'); if (onSaved) onSaved(res.project); } else setStatus(status, 'bad', errorText(res));
    });
    show();
    return [
      h('p', { class: 'muted small', text: 'Every email and LinkedIn message ends with these lines.' }),
      field('so-name', 'Your name', name), field('so-title', 'Your job title', title), field('so-company', 'Your company', company),
      preview,
      h('p', { class: 'small muted', text: 'Messages already written keep their old ending. Ask the AI to rewrite them if you want.' }),
      h('div', { class: 'row' }, save), status,
    ];
  }

  function productBody(page) {
    const pr = page.project;
    const src = page.source;
    const more = h('p', { class: 'small' }, h('a', { href: '#/p/' + pr.id + '/details', text: 'Change it or other project settings' }));
    if (!src) {
      return [notice('warn', 'No product is attached yet. Send your website address or a file in the chat.'), more];
    }
    return [h('dl', { class: 'kv' },
      h('dt', { text: 'What it is about' }),
      h('dd', { text: src.about || 'Not written down yet. Open your AI assistant and say: ' + setupSentence(pr.name) }),
      h('dt', { text: 'What we read' }), h('dd', { text: sourceKindText(src) })), more];
  }

  function contactsBody(page) {
    const known = page.known;
    const knownText = known.total
      ? 'You have added ' + fmtNum(known.total) + ' known contact' + (known.total === 1 ? '' : 's') + ': '
        + Object.entries(known.by_relationship).map(([k, v]) => v + ' ' + label(KNOWN, k).toLowerCase()).join(', ') + '.'
      : 'You have not added any yet.';
    return [
      h('p', { text: 'Upload people you have already spoken to, so the AI can skip them or treat them as warm leads.' }),
      h('p', { class: 'muted', text: knownText }),
      h('div', { class: 'row' },
        h('a', { class: 'button', href: '/api/contacts-template/xlsx', download: '', text: 'Download the Excel template' }),
        h('a', { class: 'button', href: '/api/contacts-template/csv', download: '', text: 'Download the CSV template' }),
        h('a', { class: 'button primary', href: '#/p/' + page.project.id + '/contacts', text: 'Upload your contacts' })),
    ];
  }

  // ---- export and send ----------------------------------------------------------------------------

  function actionsCard(state) {
    const note = h('div', { role: 'status' });
    const folderRow = h('div', { class: 'row' });
    // One export: a single wave (campaign id) or every wave of the project (id null). Shows the result in this card.
    async function runExport(id, format, button) {
      const all = id === null;
      if (!all && !id) return setStatus(note, 'warn', 'There is nothing to export yet.');
      const base = all ? '/api/projects/' + state.projectId : '/api/campaigns/' + id;
      if (button) button.disabled = true;
      const res = await api(base + '/exports', { method: 'POST', body: { format, include: 'approved' } });
      if (button) button.disabled = false;
      if (!res.ok) return setStatus(note, 'bad', errorText(res));
      if (!res.exported) return setStatus(note, 'warn', 'Nobody is ready to export yet. Ask the AI to finish the checks first.');
      setStatus(note, 'ok', 'Saved ' + fmtNum(res.exported) + ' contacts to your exports folder' + (all ? ', all waves in one file.' : '.'));
      clear(folderRow);
      const folder = h('button', { type: 'button', text: 'Open the folder' });
      folder.addEventListener('click', () => api(base + '/exports/open-folder', { method: 'POST' }));
      folderRow.appendChild(folder);
    }
    const exportRow = (formats, id) => h('div', { class: 'row' }, ...formats.map(([format, text]) => {
      const button = h('button', { type: 'button', text });
      button.addEventListener('click', () => runExport(id(), format, button));
      return button;
    }));
    const exportButtons = exportRow([['generic', 'Download for Excel (CSV)'], ['lemlist', 'Download for lemlist (CSV)']], state.campaignId);
    const exportAll = exportRow([['generic', 'Export all waves (Excel CSV)'], ['lemlist', 'Export all waves (lemlist CSV)']], () => null);

    const MISSING = {
      email_channel: 'This search is not set up for email.',
      mailbox_configured: 'Your email is not connected yet.',
      sender_identity: 'Your sender name and company are missing.',
      postal_address: 'Your postal address is missing (Settings).',
      approved_messages: 'There are no checked emails ready to send.',
      not_paused: 'Sending is paused.',
      bounce_rate: 'Too many recent emails bounced.',
      daily_cap: 'Your sending limit for today is used up.',
    };
    // the service lists missing items as short ids (or, in older replies, {detail, fix})
    const missingLine = (m) => (typeof m === 'string'
      ? MISSING[m] || (m.startsWith('compliance') ? 'The sending rules for this country are not met yet (Settings).' : 'Something is missing.')
      : [m.detail, m.fix].filter(Boolean).join(' '));

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
          h('ul', null, ...missing.map((m) => h('li', { text: missingLine(m) })))));
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

    const node = h('div', { class: 'stack' },
      h('p', { class: 'muted small', text: 'Nothing is sent until you click and confirm.' }),
      exportButtons, exportAll,
      h('p', { class: 'small muted', text: 'The first row saves the wave chosen in Ready contacts. The second puts every wave in one file, with a Wave column.' }),
      note, folderRow,
      h('div', { class: 'row' }, sendMail, sendLem),
      h('p', { class: 'small' }, h('a', { href: '#/mailbox', text: 'Connect your email' }), ' · ', connectLem),
      lemBox);
    node.runExport = runExport;
    return node;
  }

  // ---- the chat -----------------------------------------------------------------------------------

  // hooks: { refresh(now), timeline } where timeline is UI.timeline() (the agent strip and its steps).
  function chatPane(projectId, first, hooks) {
    const log = h('div', { class: 'chat-log', role: 'log', 'aria-live': 'polite', 'aria-label': 'Conversation' });
    const chips = h('div', { class: 'chips' });
    const pills = h('div', { class: 'file-pills' });
    const input = h('textarea', { class: 'chat-input', rows: '2', placeholder: 'Type your message...', 'aria-label': 'Your message' });
    const send = h('button', { type: 'button', class: 'primary', text: 'Send' });
    const attach = h('button', { type: 'button', text: 'Attach a file' });
    const picker = h('input', { type: 'file', class: 'sr-only', tabindex: '-1', 'aria-hidden': 'true', accept: '.pdf,.docx,.txt,.md,.csv,.xlsx', multiple: true });
    const down = h('button', { type: 'button', class: 'round to-bottom', 'aria-label': 'Scroll to the latest message', text: '↓', hidden: true });
    const files = [];
    let stream = null;
    let bubble = null;
    let running = false;

    function toBottom() { log.scrollTop = log.scrollHeight; }
    function put(el) { log.appendChild(el); toBottom(); return el; }
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
      if (on) clear(chips);
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
      hooks.timeline.start();
      stream = new EventSource('/api/projects/' + projectId + '/chat/stream?turn=' + encodeURIComponent(turnId));
      const on = (name, fn) => stream && stream.addEventListener(name, (ev) => {
        let data = {};
        try { data = JSON.parse(ev.data); } catch (err) { return; }
        fn(data);
      });
      on('text', (d) => {
        hooks.timeline.text();
        if (!bubble) bubble = addBubble('assistant', '');
        bubble.textContent += d.delta;
        toBottom();
      });
      on('tool', (d) => {
        bubble = null;
        hooks.timeline.tool(d);
      });
      on('refresh', () => { hooks.timeline.refreshed(); hooks.refresh(false); });
      on('chips', showChips);
      on('error', (d) => { showError(d); hooks.timeline.error(d); });
      on('done', (d) => {
        hooks.timeline.done(d);
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
      input.style.height = '';
      await sendMessage(text);
    });
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); send.click(); }
    });
    input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 160) + 'px'; });
    log.addEventListener('scroll', () => { down.hidden = log.scrollHeight - log.scrollTop - log.clientHeight < 40; });
    down.addEventListener('click', toBottom);
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
    if (first.turn.running) follow(first.turn.turn_id); else hooks.timeline.idle();
    toBottom();

    const ready = first.ai.ready;
    if (!ready) { input.disabled = true; send.disabled = true; attach.disabled = true; }
    const node = h('section', { class: 'card chat-card', 'aria-labelledby': 'chat-h' },
      h('h3', { id: 'chat-h', text: 'Chat with your AI' }),
      ready ? null : h('div', { class: 'notice warn' }, 'Connect your AI to start chatting. ', h('a', { class: 'button', href: '#/connect', text: 'Connect your AI' })),
      log, chips, pills,
      h('div', { class: 'chat-box' }, input, h('div', { class: 'row' }, attach, picker, h('span', { class: 'grow' }), send)),
      down);
    // Sends a message on the person's behalf (the launch-a-wave dialog). False when it cannot go now.
    function sendFromPage(text) {
      if (!ready) { toast('Connect your AI first.', 'warn'); return false; }
      if (running) { toast('The AI is still working. Try again when it has finished.', 'warn'); return false; }
      sendMessage(text);
      return true;
    }
    return { node, close: closeStream, send: sendFromPage };
  }

  // ---- the page -----------------------------------------------------------------------------------

  // Where each progress step leads: [tab, card].
  const STEP_TARGET = {
    product: ['settings', 'product'], reach: ['settings', 'searches'], companies: ['results', 'waves'],
    people: ['results', 'waves'], emails: ['results', 'results'], checks: ['results', 'results'],
    messages: ['results', 'results'], export: ['results', 'send'],
  };
  const TABS = [['activity', 'Activity'], ['results', 'Results'], ['settings', 'Settings']];

  function rememberedTab(projectId) {
    const key = TAB_KEY + projectId;
    return { get: () => UI.load(key, ''), set: (tab) => UI.save(key, tab) };
  }

  async function pageProject(ctx) {
    leave();
    const id = ctx.projectId;
    const [studio, chat, page, list, tpl, camps] = await Promise.all([
      api('/api/projects/' + id + '/studio'), api('/api/projects/' + id + '/chat'), api('/api/projects/' + id),
      api('/api/projects'), api('/api/projects/' + id + '/templates'), api('/api/campaigns' + query({ project_id: id })),
    ]);
    if (ctx.token !== routeToken) return;
    if (!studio.ok) return setMain(notice('bad', errorText(studio)), h('a', { href: '#/home', text: 'Back to your projects' }));
    if (!chat.ok || !page.ok) return setMain(notice('bad', errorText(chat.ok ? page : chat)));

    // wave: the id of the wave whose table is open (null: the list); lastLeads: the people last loaded for Ready contacts
    const view = { campaignId: null, page: 1, wave: null, wavePage: 1, waves: [], waveData: null, lastLeads: [] };
    const tl = UI.timeline();
    let timer = null;
    let dead = false;
    const memory = memoryBox(page.project, page.memory);
    memory.querySelector('h3').hidden = true; // the card's own title says it
    const sendCard = actionsCard({ projectId: id, campaignId: () => view.campaignId, refresh: () => refresh(true) });
    const cards = {
      activity: UI.card('p.activity', 'What the AI is doing', { open: true }, tl.node),
      progress: UI.card('p.progress', 'Progress', { open: true }),
      waves: UI.card('p.waves', 'Waves', { open: true }),
      results: UI.card('p.results', 'Ready contacts', { open: true }),
      send: UI.card('p.send', 'Export and send', {}, sendCard),
      product: UI.card('p.product', 'Your product', {}, ...productBody(page)),
      signoff: UI.card('p.signoff', 'Your sign-off', {}, signOffBody(studio.project, () => route())),
      templates: UI.card('p.templates', 'Message templates', {}),
      searches: UI.card('p.searches', 'Searches and message style', {}),
      memory: UI.card('p.memory', "Don't contact again", {}, memory),
      contacts: UI.card('p.contacts', 'Contacts you already have', {}, ...contactsBody(page)),
    };
    const inTab = {
      activity: ['activity', 'progress'], results: ['waves', 'results', 'send'],
      settings: ['product', 'signoff', 'templates', 'searches', 'memory', 'contacts'],
    };
    const drawSearches = (rows, choices) => { clear(cards.searches.inner); cards.searches.inner.append(...searchesBody(id, rows, choices)); };
    const drawTemplates = (data) => { clear(cards.templates.inner); cards.templates.inner.append(...templatesBody(id, data, reloadTemplates)); };
    async function reloadTemplates() {
      const [t, c] = await Promise.all([api('/api/projects/' + id + '/templates'), api('/api/campaigns' + query({ project_id: id }))]);
      if (t.ok) drawTemplates(t);
      if (t.ok && c.ok) drawSearches(c.campaigns, t.choices);
    }

    // ---- the tabs, the "Next:" line and where a step leads
    const memoryOfTab = rememberedTab(id);
    const tabButtons = {};
    const panels = {};
    for (const [key] of TABS) panels[key] = h('div', { class: 'tab-panel', role: 'tabpanel', id: 'rp-' + key, 'aria-labelledby': 'rt-' + key, hidden: true }, ...inTab[key].map((c) => cards[c]));
    function selectTab(key, remember) {
      for (const [k] of TABS) {
        tabButtons[k].setAttribute('aria-selected', String(k === key));
        tabButtons[k].tabIndex = k === key ? 0 : -1;
        panels[k].hidden = k !== key;
      }
      if (remember !== false) memoryOfTab.set(key);
    }
    const tabList = h('div', { class: 'right-tabs', role: 'tablist', 'aria-label': 'Project panel' });
    TABS.forEach(([key, text], i) => {
      const b = h('button', { type: 'button', role: 'tab', class: 'tab', id: 'rt-' + key, 'aria-controls': 'rp-' + key, text });
      b.addEventListener('click', () => selectTab(key));
      b.addEventListener('keydown', (ev) => {
        const step = ev.key === 'ArrowRight' ? 1 : ev.key === 'ArrowLeft' ? -1 : 0;
        if (!step) return;
        ev.preventDefault();
        const to = TABS[(i + step + TABS.length) % TABS.length][0];
        selectTab(to);
        tabButtons[to].focus();
      });
      tabButtons[key] = b;
      tabList.appendChild(b);
    });
    const nextText = h('span', { class: 'grow' });
    const nextButton = h('button', { type: 'button', class: 'primary', text: 'Show me' });
    const nextBox = h('div', { class: 'next-step', hidden: true }, nextText, nextButton);
    let nextKey = null;
    nextButton.addEventListener('click', () => { if (nextKey) goTo(nextKey); });

    function showPane(which) { // on a narrow screen only one of chat and panel is shown
      grid.dataset.pane = which;
      for (const t of topTabs.children) t.setAttribute('aria-selected', String(t.dataset.pane === which));
    }
    function openFirstToReview() {
      const leads = view.lastLeads;
      let at = leads.findIndex((l) => l.message_state === 'written');
      if (at < 0) at = leads.findIndex((l) => l.messages.length);
      if (at >= 0) reviewDrawer(leads, at, () => refresh(true));
    }
    function goTo(key) {
      const [tab, target] = STEP_TARGET[key] || ['results', 'results'];
      showPane('results');
      selectTab(tab);
      cards[target].setOpen(true);
      cards[target].scrollIntoView({ behavior: 'smooth', block: 'start' });
      if (key === 'messages') openFirstToReview();
    }
    function drawProgress(s) {
      clear(cards.progress.inner);
      cards.progress.inner.append(stepper(s.steps, goTo));
      const next = nextStepLine(s);
      nextBox.hidden = !next;
      nextKey = next ? next.key : null;
      nextText.textContent = next ? next.text : '';
    }

    // ---- results, waves
    const openReady = (i) => reviewDrawer(view.lastLeads, i, () => refresh(true));
    function drawResults(r) {
      view.campaignId = r.campaign_id;
      view.lastLeads = r.leads;
      clear(cards.results.inner);
      cards.results.setCount(fmtNum(r.total));
      cards.results.inner.append(...resultsBody(r, view, () => refresh(true), openReady));
    }
    function drawWaves() {
      clear(cards.waves.inner);
      cards.waves.setCount(String(view.waves.length));
      if (view.wave === null) {
        cards.waves.inner.append(...wavesBody(view.waves, {
          open: (waveId) => { view.wave = waveId; view.wavePage = 1; view.waveData = null; refresh(true); },
          exportWave: (waveId) => {
            selectTab('results');
            cards.send.setOpen(true);
            cards.send.scrollIntoView({ behavior: 'smooth', block: 'start' });
            sendCard.runExport(waveId, 'generic', null);
          },
          launch: () => launchDialog(view.waves, (text) => {
            const sent = pane.send(text);
            if (sent) showPane('chat');
            return sent;
          }),
        }));
      } else if (view.waveData) {
        const wave = view.waves.find((w) => w.id === view.wave);
        cards.waves.inner.append(...waveTable(wave, view.waveData, {
          back: () => { view.wave = null; drawWaves(); },
          page: (n) => { view.wavePage = n; refresh(true); },
          openAt: (i) => reviewDrawer(view.waveData.leads, i, () => refresh(true)),
        }));
      }
    }
    async function refresh(now) {
      clearTimeout(timer);
      const run = async () => {
        if (dead || ctx.token !== routeToken) return;
        const [s, r, w, wr] = await Promise.all([
          api('/api/projects/' + id + '/studio'),
          api('/api/projects/' + id + '/results' + query({ campaign_id: view.campaignId, page: view.page })),
          api('/api/projects/' + id + '/waves'),
          view.wave === null ? null : api('/api/projects/' + id + '/results' + query({ campaign_id: view.wave, page: view.wavePage, page_size: 25 })),
        ]);
        if (dead || ctx.token !== routeToken) return;
        if (s.ok) drawProgress(s);
        if (r.ok) drawResults(r);
        if (w.ok) view.waves = w.waves;
        if (wr && wr.ok) view.waveData = wr;
        if (w.ok || (wr && wr.ok)) drawWaves();
      };
      if (now) await run(); else timer = setTimeout(run, 1200); // many tool events in a row make one refresh
    }

    const pane = chatPane(id, chat, { refresh, timeline: tl });
    const right = h('div', { class: 'right' }, nextBox, tabList, ...Object.values(panels));
    const grid = h('div', { class: 'studio-grid', 'data-pane': 'chat' }, pane.node, right);
    const layout = UI.layoutSwitch(grid, 'project', [['chat', 'Chat wide'], ['split', 'Side by side'], ['results', 'Results wide']], 'split');
    const switcher = selectBox('p-switch', (list.ok ? list.projects : []).map((p) => [p.id, p.name]), id, (ev) => go('/p/' + ev.target.value));
    const signs = studio.project.sender_name ? 'Signs as ' + studio.project.sender_name : 'Sign-off not set yet';
    const topTabs = h('div', { class: 'studio-tabs', role: 'tablist' });
    for (const [key, text] of [['chat', 'Chat'], ['results', 'Progress and results']]) {
      const b = h('button', { type: 'button', role: 'tab', class: 'tab', 'data-pane': key, 'aria-selected': key === 'chat' ? 'true' : 'false', text });
      b.addEventListener('click', () => showPane(key));
      topTabs.appendChild(b);
    }
    setMain(
      h('div', { class: 'row page-head' }, h('a', { href: '#/home', text: 'All projects' }), h('h1', { text: studio.project.name }),
        h('label', { class: 'sr-only', for: 'p-switch', text: 'Switch project' }), switcher,
        h('span', { class: 'chip-static', text: signs }),
        layout, UI.collapseAllButton(right)),
      topTabs,
      grid);
    drawProgress(studio);
    if (tpl.ok) drawTemplates(tpl);
    if (tpl.ok && camps.ok) drawSearches(camps.campaigns, tpl.choices);
    // the tab last used for this project; else Results once there are contacts, else Activity
    const saved = memoryOfTab.get();
    selectTab(TABS.some(([k]) => k === saved) ? saved : studio.summary.ready_contacts > 0 ? 'results' : 'activity', false);
    live = { close() { dead = true; clearTimeout(timer); pane.close(); } };
    refresh(true);
  }

  window.Studio = Object.assign(window.Studio || {}, { pageProject });
})();
