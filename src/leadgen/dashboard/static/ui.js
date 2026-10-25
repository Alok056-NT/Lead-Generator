/* Lead Generator UI kit: collapsible cards, the layout switch, the agent strip and the live timeline.
   Plain JavaScript, no libraries. Loaded before studio.js; uses app.js's h, clear and fmtNum at run time.
   Card and layout choices are remembered per browser (localStorage keys start with "lg."). */
(function () {
  'use strict';

  function load(key, fallback) {
    try {
      const value = window.localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch (err) { return fallback; }
  }
  function save(key, value) {
    try { window.localStorage.setItem(key, value); } catch (err) { /* a private window: fine */ }
  }

  // A card that opens and closes. id: stable text such as 'p.progress'. opts: { open, action, count }.
  // Returns the <section>; put content in node.inner, open or close it with node.setOpen(bool).
  function card(id, title, opts, ...kids) {
    const o = opts || {};
    const key = 'lg.card.' + id;
    const open = load(key, o.open ? '1' : '0') === '1';
    const bodyId = 'cb-' + id.replace(/[^a-z0-9]/gi, '-');
    const count = h('span', { class: 'card-count' });
    if (o.count !== undefined && o.count !== null) count.textContent = String(o.count);
    const toggle = h('button', { type: 'button', class: 'card-toggle', 'aria-expanded': String(open), 'aria-controls': bodyId },
      h('span', { class: 'chev', 'aria-hidden': 'true' }), h('span', { class: 'card-title', text: title }), count);
    const inner = h('div', { class: 'card-inner' }, ...kids);
    const body = h('div', { class: 'card-body', id: bodyId }, inner);
    const node = h('section', { class: 'card ui-card', 'data-card': id, 'data-open': String(open) },
      h('header', { class: 'card-head' }, toggle, o.action || null), body);
    function setOpen(next) {
      node.dataset.open = String(next);
      toggle.setAttribute('aria-expanded', String(next));
      body.inert = !next;
      save(key, next ? '1' : '0');
    }
    toggle.addEventListener('click', () => setOpen(node.dataset.open !== 'true'));
    body.inert = !open;
    node.setOpen = setOpen;
    node.inner = inner;
    node.setCount = (text) => { count.textContent = text === null || text === undefined ? '' : String(text); };
    return node;
  }

  // Open or close every card inside root.
  function setAll(root, open) {
    for (const node of root.querySelectorAll('.ui-card')) node.setOpen(open);
  }

  // One button that closes every card in root, then opens them all again.
  function collapseAllButton(root) {
    const button = h('button', { type: 'button', class: 'ghost', text: 'Collapse all' });
    button.addEventListener('click', () => {
      const closing = button.textContent === 'Collapse all';
      setAll(root, !closing);
      button.textContent = closing ? 'Expand all' : 'Collapse all';
    });
    return button;
  }

  // Segmented control that sets grid.dataset.layout. choices: [[value, label], ...].
  function layoutSwitch(grid, name, choices, fallback) {
    const key = 'lg.layout.' + name;
    let current = load(key, fallback);
    if (!choices.some(([value]) => value === current)) current = fallback;
    grid.dataset.layout = current;
    const group = h('div', { class: 'segmented', role: 'radiogroup', 'aria-label': 'Layout' });
    for (const [value, labelText] of choices) {
      const b = h('button', { type: 'button', role: 'radio', 'aria-checked': String(value === current), text: labelText });
      b.addEventListener('click', () => {
        grid.dataset.layout = value;
        save(key, value);
        for (const other of group.children) other.setAttribute('aria-checked', String(other === b));
      });
      group.appendChild(b);
    }
    return group;
  }

  // "12.3k" style token counts.
  function tokens(n) {
    const v = Number(n) || 0;
    if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (v >= 1e4) return (v / 1e3).toFixed(1) + 'k';
    return fmtNum(v);
  }
  function money(usd) {
    if (usd === null || usd === undefined) return '';
    return 'about $' + (usd < 0.01 && usd > 0 ? '0.01' : Number(usd).toFixed(2));
  }
  function usageLine(d) {
    const total = (d.input_tokens || 0) + (d.output_tokens || 0) + (d.cache_read_tokens || 0) + (d.cache_write_tokens || 0);
    const parts = ['This reply: ' + tokens(total) + ' tokens'];
    if (d.cost_usd !== null && d.cost_usd !== undefined) parts.push(money(d.cost_usd) + ' (estimate)');
    return parts.join(' · ');
  }

  // The agent strip and live timeline. Returns { node, start, thinking, text, tool, refreshed, usage, error, done, idle }.
  function timeline() {
    const orb = h('div', { class: 'orb', 'data-state': 'idle', 'aria-hidden': 'true' });
    const bars = h('div', { class: 'bars', 'data-state': 'idle', 'aria-hidden': 'true' });
    for (let i = 0; i < 15; i += 1) {
      const bar = h('i');
      bar.style.setProperty('--i', String(i));
      bars.appendChild(bar);
    }
    const status = h('p', { class: 'tl-status', role: 'status', text: 'Waiting for your message' });
    const list = h('ol', { class: 'tl', 'aria-label': 'What the AI is doing' });
    const foot = h('p', { class: 'tl-foot muted small' });
    const node = h('div', { class: 'agent' },
      h('div', { class: 'agent-head' }, orb, h('div', { class: 'grow' }, status, bars)), list, foot);
    const steps = {};
    let active = null; // the thinking or writing step now running (tools are tracked by id)
    let started = 0;
    let n = 0;

    function mode(state, text) {
      orb.dataset.state = state;
      bars.dataset.state = state;
      status.textContent = text;
      status.classList.toggle('shimmer', state !== 'idle');
    }
    function finish(step, outcome) {
      if (!step || step.li.dataset.state !== 'active') return;
      step.li.dataset.state = outcome;
      const secs = Math.round((Date.now() - step.at) / 1000);
      step.time.textContent = secs ? secs + ' s' : '';
    }
    function add(id, labelText, kind, state) {
      const time = h('span', { class: 'tl-time' });
      const li = h('li', { class: 'tl-step', 'data-state': state || 'active', 'data-kind': kind },
        h('span', { class: 'tl-dot', 'aria-hidden': 'true' }), h('span', { class: 'tl-label', text: labelText }), time);
      list.appendChild(li);
      list.scrollTop = list.scrollHeight;
      const step = { li, time, kind, at: Date.now() };
      steps[id] = step;
      return step;
    }
    function phase(kind, labelText) {
      if (active && active.kind === kind) return;
      finish(active, 'ok');
      n += 1;
      active = add(kind + n, labelText, kind);
    }

    return {
      node,
      start() {
        clear(list);
        clear(foot);
        started = Date.now();
        active = null;
        add('read', 'Read your message', 'note', 'ok');
        phase('think', 'Thinking');
        mode('thinking', 'Thinking...');
      },
      thinking() { phase('think', 'Thinking'); mode('thinking', 'Thinking...'); },
      text() { phase('write', 'Writing a reply'); mode('talking', 'Writing a reply...'); },
      tool(d) {
        if (d.state === 'started') {
          finish(active, 'ok');
          active = null;
          add(d.id, d.label || 'Working...', 'tool');
          mode('working', d.label || 'Working...');
        } else if (steps[d.id]) {
          finish(steps[d.id], d.ok ? 'ok' : 'bad');
          mode('thinking', 'Thinking...');
        }
      },
      refreshed() {
        node.classList.remove('flash');
        void node.offsetWidth; // restart the animation
        node.classList.add('flash');
      },
      usage(d) { foot.textContent = usageLine(d); },
      error(d) {
        finish(active, 'bad');
        active = null;
        add('error' + Date.now(), d.message || 'Stopped with a problem', 'note', 'bad');
      },
      done(d) {
        finish(active, d.ok ? 'ok' : 'bad');
        active = null;
        for (const step of Object.values(steps)) finish(step, d.ok ? 'ok' : 'bad');
        const secs = Math.max(1, Math.round((Date.now() - started) / 1000));
        const text = d.cancelled ? 'Stopped' : d.ok ? 'Done in ' + secs + ' s' : 'Stopped';
        add('done' + Date.now(), text, 'note', d.ok ? 'ok' : 'bad');
        mode('idle', d.ok ? 'Ready for your next message' : 'Stopped');
      },
      idle() { mode('idle', 'Waiting for your message'); },
    };
  }

  window.UI = { card, setAll, collapseAllButton, layoutSwitch, timeline, tokens, money, usageLine, load, save };
})();
