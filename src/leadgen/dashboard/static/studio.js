/* Lead Generator Studio, part 1: top-bar status, Connect your AI, Projects home, dialogs, Advanced.
   Plain JavaScript, no libraries. This file is loaded BEFORE app.js, so it only defines functions;
   app.js's router calls them later. They use app.js's helpers (h, api, clear, setMain, toast, notice,
   errorText, badge, extLink, field, copyText, fmtNum, fmtDay, go, setStatus, route) and its variable routeToken.
   Everything that comes from the server is put on the page as text, never as HTML. */
(function () {
  'use strict';

  const SKIP_KEY = 'leadgen.skipConnect';
  function skipped() {
    try { return window.localStorage.getItem(SKIP_KEY) === '1'; } catch (err) { return false; }
  }
  function rememberSkip() {
    try { window.localStorage.setItem(SKIP_KEY, '1'); } catch (err) { /* a private window: fine */ }
  }

  // ---- the little status pill in the top bar ----------------------------------------------------

  async function pill() {
    const box = document.getElementById('ai-pill');
    if (!box) return;
    const ai = await api('/api/ai');
    clear(box);
    if (!ai.ok) return;
    const text = !ai.ready ? 'AI not connected'
      : ai.mode === 'api_key' ? 'AI connected (API key)' : 'AI connected (Claude plan)';
    box.appendChild(h('a', { href: '#/connect', class: 'pill-link ' + (ai.ready ? 'on' : 'off') },
      h('span', { class: 'pill-dot', 'aria-hidden': 'true' }), text));
  }

  // ---- dialogs (the browser's own <dialog>: it traps the keyboard focus and closes on Escape) ----

  function dialog(title, bodyKids, actionKids) {
    const dlg = h('dialog', { class: 'dialog', 'aria-labelledby': 'dlg-title' },
      h('h2', { id: 'dlg-title', text: title }),
      ...bodyKids,
      h('div', { class: 'dialog-actions' }, ...actionKids));
    dlg.addEventListener('close', () => dlg.remove());
    dlg.addEventListener('click', (ev) => { if (ev.target === dlg) dlg.close(); });
    document.body.appendChild(dlg);
    dlg.showModal();
    return dlg;
  }

  // Resolves true when the person presses the yes button, false otherwise.
  function confirmDialog(opts) {
    return new Promise((resolve) => {
      const yes = h('button', { type: 'button', class: 'primary', text: opts.yes, disabled: opts.disabled || null });
      const no = h('button', { type: 'button', text: opts.no || 'Not now' });
      let answer = false;
      const dlg = dialog(opts.title, opts.body || [], [no, yes]);
      yes.addEventListener('click', () => { answer = true; dlg.close(); });
      no.addEventListener('click', () => dlg.close());
      dlg.addEventListener('close', () => resolve(answer));
    });
  }

  // ---- Connect your AI ---------------------------------------------------------------------------

  async function pageConnect(ctx) {
    let ai = await api('/api/ai');
    if (ctx.token !== routeToken) return;
    if (!ai.ok) return setMain(notice('bad', errorText(ai)));
    const root = h('div', { class: 'connect stack' });
    setMain(root);
    let timer = null;
    let showInstall = false;
    let tested = false;

    function stopPolling() {
      if (timer) { clearInterval(timer); timer = null; }
    }
    async function refresh() {
      const next = await api('/api/ai?refresh=1');
      if (ctx.token !== routeToken) { stopPolling(); return; }
      if (next.ok) { ai = next; render(); }
      if (!(ai.login && ai.login.state === 'waiting')) stopPolling();
    }
    function startPolling() {
      stopPolling();
      timer = setInterval(refresh, 2000);
    }

    function installCard() {
      const kids = [h('h3', { text: '1. Install Claude Code' }),
        h('p', { class: 'muted', text: 'Claude Code is the free program that does the thinking for Lead Generator.' })];
      if (ai.claude.installed) {
        kids.push(h('p', null, badge('Installed', 'ok'), ' ', h('span', { class: 'muted small', text: ai.claude.version ? 'Version ' + ai.claude.version : '' })));
        if (ai.claude.outdated) kids.push(notice('warn', 'Your copy of Claude Code is out of date. In a terminal, run: claude update'));
        return h('section', { class: 'card stack' }, ...kids);
      }
      kids.push(h('p', null, badge('Not installed yet', 'none')));
      const how = h('button', { type: 'button', text: 'Show me how to install it' });
      how.addEventListener('click', () => { showInstall = true; render(); });
      kids.push(h('div', { class: 'row' }, how));
      if (showInstall) {
        const copy = h('button', { type: 'button', text: 'Copy the command' });
        copy.addEventListener('click', () => copyText(ai.install.command));
        const again = h('button', { type: 'button', class: 'primary', text: 'Check again' });
        again.addEventListener('click', async () => { again.disabled = true; await refresh(); again.disabled = false; });
        kids.push(h('pre', { class: 'command' }, ai.install.command));
        kids.push(h('p', { class: 'small', text: 'Open PowerShell (Windows) or Terminal (Mac), paste the command, press Enter, then come back and press Check again.' }));
        kids.push(h('div', { class: 'row' }, copy, again, extLink(ai.install.help_url, 'Open the install page')));
      }
      return h('section', { class: 'card stack' }, ...kids);
    }

    function choiceCards() {
      const options = [
        ['claude_plan', 'Use my Claude plan', 'For people with a Claude Pro or Max subscription. No extra cost per chat.'],
        ['api_key', 'Use an API key', 'Pay as you go with an Anthropic API key. Anthropic charges you for each chat.'],
      ];
      const group = h('div', { class: 'choices', role: 'radiogroup', 'aria-label': 'How do you want to use the AI?' });
      for (const [value, title, text] of options) {
        const on = ai.mode === value;
        const card = h('button', { type: 'button', role: 'radio', class: 'choice' + (on ? ' on' : ''), 'aria-checked': on ? 'true' : 'false', tabindex: on || (!ai.mode && value === 'claude_plan') ? '0' : '-1' },
          h('span', { class: 'choice-title', text: title }), h('span', { class: 'choice-text', text }));
        card.addEventListener('click', async () => {
          const res = await api('/api/ai/mode', { method: 'POST', body: { mode: value } });
          if (res.ok) { ai = res; tested = false; render(); } else toast(errorText(res), 'bad');
        });
        card.addEventListener('keydown', (ev) => {
          if (ev.key !== 'ArrowRight' && ev.key !== 'ArrowLeft' && ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
          ev.preventDefault();
          const buttons = Array.from(group.querySelectorAll('button.choice'));
          const next = buttons[(buttons.indexOf(card) + 1) % buttons.length];
          next.focus();
          next.click();
        });
        group.appendChild(card);
      }
      return h('fieldset', { class: 'choose' }, h('legend', { text: 'How do you want to use the AI?' }), group);
    }

    function planCard() {
      const kids = [h('h3', { text: '2. Sign in to Claude' })];
      const login = ai.login || {};
      if (ai.plan.signed_in) {
        kids.push(notice('ok', 'Signed in' + (ai.plan.email ? ' as ' + ai.plan.email : '') + (ai.plan.plan ? ' (' + ai.plan.plan + ' plan)' : '') + '.'));
        return h('section', { class: 'card stack' }, ...kids);
      }
      const button = h('button', { type: 'button', class: 'primary big', text: 'Sign in to Claude', disabled: !ai.claude.installed || login.state === 'waiting' || null });
      button.addEventListener('click', async () => {
        button.disabled = true;
        const res = await api('/api/ai/login', { method: 'POST' });
        if (!res.ok) { toast(errorText(res), 'bad'); button.disabled = false; return; }
        await refresh();
        startPolling();
      });
      kids.push(h('div', { class: 'row' }, button));
      if (login.state === 'waiting') {
        kids.push(h('p', { role: 'status', text: 'A browser window opened. Finish signing in there, then come back here. Waiting...' }));
        if (login.url) kids.push(h('p', { class: 'small' }, extLink(login.url, 'Open the sign-in page again')));
      } else if (login.state === 'failed') {
        kids.push(notice('bad', 'Signing in did not finish. Press the button to try again.'));
      }
      return h('section', { class: 'card stack' }, ...kids);
    }

    function keyCard() {
      const kids = [h('h3', { text: '2. Your API key' }),
        h('p', { class: 'muted', text: 'Paste a key from console.anthropic.com. It stays on this computer and only its last four characters are ever shown.' })];
      if (ai.api_key.configured) kids.push(h('p', null, badge('Saved key ' + ai.api_key.masked, 'ok')));
      const input = h('input', { type: 'password', id: 'ai-key', autocomplete: 'off', spellcheck: 'false', name: 'ai-key' });
      const save = h('button', { type: 'button', class: 'primary', text: 'Save and check' });
      const status = h('div', { role: 'status' });
      save.addEventListener('click', async () => {
        const value = input.value.trim();
        if (!value) return setStatus(status, 'bad', 'Paste your key first.');
        save.disabled = true;
        setStatus(status, 'info', 'Checking the key...');
        const res = await api('/api/ai/api-key', { method: 'POST', body: { key: value } });
        input.value = '';
        save.disabled = false;
        if (res.ok) { toast('Key saved ' + (res.masked || '') + '.'); await refresh(); } else setStatus(status, 'bad', errorText(res));
      });
      kids.push(field('ai-key', 'Anthropic API key', h('div', { class: 'row' }, input, save)), status);
      return h('section', { class: 'card stack' }, ...kids);
    }

    function testCard() {
      const status = h('div', { role: 'status' });
      const button = h('button', { type: 'button', class: 'primary', text: 'Test the connection' });
      button.addEventListener('click', async () => {
        button.disabled = true;
        setStatus(status, 'info', 'Testing...');
        const res = await api('/api/ai/test', { method: 'POST' });
        button.disabled = false;
        if (res.ok) { tested = true; render(); } else setStatus(status, 'bad', errorText(res));
      });
      const kids = [h('h3', { text: '3. Test it' }), h('div', { class: 'row' }, button), status];
      if (tested) {
        clear(status);
        status.appendChild(notice('ok', 'Connected. You are ready to go.'));
        kids.push(h('div', { class: 'row' }, h('a', { class: 'button primary big', href: '#/home', text: 'Go to my projects' })));
      }
      return h('section', { class: 'card stack' }, ...kids);
    }

    function render() {
      clear(root);
      root.appendChild(h('h1', { text: 'Connect your AI' }));
      root.appendChild(h('p', { class: 'lead', text: 'Lead Generator works by chatting with an AI assistant that runs on this computer. Choose how you want to pay for it, then sign in.' }));
      root.appendChild(installCard());
      root.appendChild(choiceCards());
      if (ai.mode === 'claude_plan') root.appendChild(planCard());
      if (ai.mode === 'api_key') root.appendChild(keyCard());
      if (ai.mode) root.appendChild(testCard());
      const skip = h('button', { type: 'button', class: 'link', text: 'Skip for now' });
      skip.addEventListener('click', () => { rememberSkip(); go('/home'); });
      root.appendChild(h('p', { class: 'center' }, skip));
      if (ai.login && ai.login.state === 'waiting' && !timer) startPolling();
    }
    render();
  }

  // ---- Projects home -----------------------------------------------------------------------------

  function openNewDialog() {
    const name = h('input', { type: 'text', id: 'np-name', autocomplete: 'off', placeholder: 'For example: Spring training course', maxlength: '120' });
    const status = h('div', { role: 'status' });
    const create = h('button', { type: 'button', class: 'primary', text: 'Create and open' });
    const cancel = h('button', { type: 'button', text: 'Cancel' });
    const dlg = dialog('Name your project', [field('np-name', 'Project name', name), status], [cancel, create]);
    cancel.addEventListener('click', () => dlg.close());
    async function submit() {
      if (!name.value.trim()) return setStatus(status, 'bad', 'Give your project a name.');
      create.disabled = true;
      const res = await api('/api/projects', { method: 'POST', body: { name: name.value } });
      create.disabled = false;
      if (res.ok) { dlg.close(); go('/p/' + res.project.id); } else setStatus(status, 'bad', errorText(res));
    }
    create.addEventListener('click', submit);
    name.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') submit(); });
    name.focus();
  }

  // project: {id, name, sender_name, sender_title, sender_company, cooldown_days}; onSaved runs after a successful save.
  function openEditDialog(project, onSaved) {
    const name = h('input', { type: 'text', id: 'ep-name', value: project.name, autocomplete: 'off', maxlength: '120' });
    const sender = h('input', { type: 'text', id: 'ep-sender', value: project.sender_name || '', autocomplete: 'off', maxlength: '120' });
    const title = h('input', { type: 'text', id: 'ep-title', value: project.sender_title || '', autocomplete: 'off', maxlength: '120' });
    const company = h('input', { type: 'text', id: 'ep-company', value: project.sender_company || '', autocomplete: 'off', maxlength: '120' });
    const days = h('input', { type: 'number', id: 'ep-days', value: String(project.cooldown_days), min: '0', max: '3650' });
    const status = h('div', { role: 'status' });
    const save = h('button', { type: 'button', class: 'primary', text: 'Save' });
    const cancel = h('button', { type: 'button', text: 'Cancel' });
    const dlg = dialog('Edit project', [
      field('ep-name', 'Project name', name),
      field('ep-sender', 'Your name for the sign-off', sender),
      field('ep-title', 'Your job title', title),
      field('ep-company', 'Your company', company),
      h('p', { class: 'small muted', text: 'Messages end with your name, job title and company, each on its own line.' }),
      field('ep-days', 'Do not contact the same person again for (days)', days),
      h('p', { class: 'small muted', text: 'Set 0 to turn this off.' }),
      status], [cancel, save]);
    cancel.addEventListener('click', () => dlg.close());
    save.addEventListener('click', async () => {
      save.disabled = true;
      const res = await api('/api/projects/' + project.id, { method: 'PATCH', body: { name: name.value, sender_name: sender.value, sender_title: title.value, sender_company: company.value, cooldown_days: Number(days.value) } });
      save.disabled = false;
      if (res.ok) { dlg.close(); toast('Saved.'); if (onSaved) onSaved(res.project); } else setStatus(status, 'bad', errorText(res));
    });
  }

  function openDeleteDialog(project, onDeleted) {
    const typed = h('input', { type: 'text', id: 'dp-name', autocomplete: 'off', spellcheck: 'false' });
    const status = h('div', { role: 'status' });
    const del = h('button', { type: 'button', class: 'danger solid', text: 'Delete project', disabled: true });
    const keep = h('button', { type: 'button', text: 'Keep it' });
    const dlg = dialog('Delete this project?', [
      h('p', { text: 'This permanently removes "' + project.name + '", its searches, the people found, the messages written and its memory of who was contacted. Files you exported for it are deleted too. This cannot be undone.' }),
      field('dp-name', 'Type the project name to confirm', typed), status], [keep, del]);
    typed.addEventListener('input', () => { del.disabled = typed.value.trim().toLowerCase() !== project.name.trim().toLowerCase(); });
    keep.addEventListener('click', () => dlg.close());
    del.addEventListener('click', async () => {
      del.disabled = true;
      const res = await api('/api/projects/' + project.id, { method: 'DELETE', body: { confirm_name: typed.value } });
      if (res.ok) { dlg.close(); toast('The project was deleted.'); onDeleted(); } else { del.disabled = false; setStatus(status, 'bad', errorText(res)); }
    });
  }

  function projectCard(p, reload) {
    const id = 'pc-' + p.id;
    const edit = h('button', { type: 'button', text: 'Edit' });
    const del = h('button', { type: 'button', class: 'danger', text: 'Delete' });
    edit.addEventListener('click', () => openEditDialog(p, reload));
    del.addEventListener('click', () => openDeleteDialog(p, reload));
    return h('article', { class: 'card project-card', 'aria-labelledby': id },
      h('h3', null, h('a', { id, href: '#/p/' + p.id, text: p.name })),
      h('p', { class: p.about ? null : 'muted', text: p.about || 'Not set up yet. Open it and tell the AI what you offer.' }),
      h('dl', { class: 'kv' },
        h('dt', { text: 'Contacts ready' }), h('dd', { text: fmtNum(p.leads) }),
        h('dt', { text: 'Verified emails' }), h('dd', { text: fmtNum(p.verified_emails) }),
        h('dt', { text: 'Last activity' }), h('dd', { text: p.last_activity ? fmtDay(p.last_activity) : 'None yet' })),
      h('div', { class: 'row card-actions' },
        h('a', { class: 'button primary', href: '#/p/' + p.id, text: 'Open' }), edit, del));
  }

  async function pageHome(ctx) {
    const [list, ai] = await Promise.all([api('/api/projects'), api('/api/ai')]);
    if (ctx.token !== routeToken) return;
    if (ai.ok && !ai.mode && !skipped()) { go('/connect'); return; }
    if (!list.ok) return setMain(notice('bad', errorText(list)));
    const newButton = h('button', { type: 'button', class: 'primary big', text: 'New project' });
    newButton.addEventListener('click', openNewDialog);
    const kids = [h('div', { class: 'row' },
      h('div', { class: 'grow' }, h('h1', { text: 'Your projects' }),
        h('p', { class: 'lead', text: 'Each project is one product or service you want to find customers for.' })),
      list.projects.length ? newButton : null)];
    if (ai.ok && !ai.ready) {
      kids.push(h('div', { class: 'notice warn' }, 'Your AI is not connected yet. ', h('a', { href: '#/connect', text: 'Connect your AI' })));
    }
    if (!list.projects.length) {
      kids.push(h('div', { class: 'empty card' },
        h('h2', { text: 'No projects yet' }),
        h('p', { class: 'muted', text: 'Create your first project to get started.' }),
        newButton));
    } else {
      kids.push(h('div', { class: 'cards' }, ...list.projects.map((p) => projectCard(p, () => route()))));
    }
    kids.push(h('p', { class: 'small muted' }, 'Want to send emails from your own mailbox? ', h('a', { href: '#/mailbox', text: 'Connect your mailbox' }), '.'));
    setMain(...kids);
  }

  // ---- Advanced ----------------------------------------------------------------------------------

  async function pageAdvanced(ctx) {
    const list = await api('/api/projects');
    if (ctx.token !== routeToken) return;
    const links = [['#/campaigns', 'Campaigns'], ['#/analytics', 'Results'], ['#/mailbox', 'Connect your mailbox'], ['#/classic', 'Projects, the earlier view']];
    const kids = [h('h1', { text: 'Advanced' }),
      h('p', { class: 'lead', text: 'Everything from the earlier version of Lead Generator is still here.' }),
      h('section', { class: 'card stack' }, ...links.map(([href, text]) => h('p', null, h('a', { href, text }))))];
    if (list.ok && list.projects.length) {
      kids.push(h('section', { class: 'card stack' }, h('h3', { text: 'Earlier project pages' }),
        ...list.projects.map((p) => h('p', null, h('a', { href: '#/p/' + p.id + '/details', text: p.name })))));
    }
    setMain(...kids);
  }

  window.Studio = Object.assign(window.Studio || {}, {
    pill, dialog, confirmDialog, openEditDialog, openDeleteDialog, pageConnect, pageHome, pageAdvanced,
  });
})();
