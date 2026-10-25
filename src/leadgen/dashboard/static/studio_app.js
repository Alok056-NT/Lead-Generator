/* Lead Generator Studio, part 3: the app-wide pages. Connectors: the keys, grouped by what they do.
   Plain JavaScript, loaded before app.js; uses app.js's helpers and window.UI. Everything from the
   server is put on the page as text. */
(function () {
  'use strict';

  const SEGMENT_HELP = {
    search: 'Used to find companies and people. Serper and Tavily have free allowances without a card.',
    finder: "Given a person's LinkedIn profile address, these look up a work email (and a phone number only if you ask). Every email found is still checked before it is used. Phone numbers are shown as found, not checked.",
    verify: 'Checks that an email address really exists. Only checked addresses are ever exported or sent.',
    sending: 'Optional. Send from your own mailbox, or hand the contacts to lemlist.',
  };

  async function pageConnectors(ctx) {
    const reply = await api('/api/providers');
    if (ctx.token !== routeToken) return;
    if (!reply.ok) return setMain(notice('bad', errorText(reply)));
    const byKey = {};
    for (const p of reply.providers) byKey[p.key] = p;
    const list = h('div', { class: 'stack' });
    for (const seg of reply.segments) {
      const items = seg.providers.map((key) => byKey[key]).filter(Boolean);
      const on = items.filter((p) => p.configured).length;
      list.appendChild(UI.card('c.' + seg.key, seg.label,
        { open: seg.key === 'search', count: on ? on + ' connected' : 'none connected' },
        h('p', { class: 'muted small', text: SEGMENT_HELP[seg.key] || '' }), ...items.map(providerCard)));
    }
    setMain(
      h('div', { class: 'page-head' }, h('h1', { text: 'Connectors' }), UI.collapseAllButton(list)),
      h('p', { class: 'lead', text: 'Services Lead Generator can use with your own keys. Every key stays on this computer and is never shown again (only its last four characters). All of them are optional: the AI tells you when one would help.' }),
      list);
  }

  window.Studio = Object.assign(window.Studio || {}, { pageConnectors });
})();
