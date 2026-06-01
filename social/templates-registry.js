// ============================================================
// PURCHASINGCORP — Template registry
// ============================================================
// The single source of truth that ties the Python generator, the
// Node renderer, and the HTML templates together.
//
// For every template it declares:
//   - file    : the .html file in templates/
//   - role    : a one-line description the generator reads to decide
//               when to reach for this card
//   - fields  : the LOGICAL field names Claude fills (arrays allowed)
//   - expand  : turns logical fields into the flat {{placeholder}}
//               values the template expects — escaping prose, allowing
//               a small inline-format set (<em>/<strong>/<br>), and
//               building the repeated-row chunks (board rows, compare
//               bars, index cells) from arrays
//   - sample  : a brand-accurate logical payload (used by
//               sample-payloads.json and as a render smoke test)
//
// Data-flow contract with renderer.js:
//   expandFields(name, logical)  ->  { key: value, ... }
//   The renderer substitutes {{key}}; any key ending in "_html" is
//   inserted raw (it has already been made safe here), every other key
//   is HTML-escaped by the renderer. So prose is escaped exactly once
//   and our generated markup is never double-escaped.
//
// Honesty rule baked into the samples: every dollar figure below is a
// real number from pricing-data.js. Competitor figures are framed as
// "typical" estimates, never invented precise quotes.
// ============================================================

'use strict';

// ---------- escaping helpers (shared with renderer) ----------

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Escape everything, then re-allow a tiny, attribute-free formatting set.
// <em>      -> Instrument Serif italic emerald accent (the signature move)
// <strong>  -> bold white emphasis inside body copy
// <br>      -> a forced line break in a headline
function escAllowEm(s) {
  return esc(s)
    .replace(/&lt;(\/?)(em|strong)&gt;/g, '<$1$2>')
    .replace(/&lt;br\s*\/?&gt;/g, '<br>');
}

// Map over a logical-field object, making any *_html prose field safe.
// Non-html fields are passed through untouched (the renderer escapes them).
function htmlPass(fields) {
  const out = {};
  for (const [k, v] of Object.entries(fields)) {
    out[k] = k.endsWith('_html') ? escAllowEm(v) : v;
  }
  return out;
}

// ---------- repeated-row chunk builders ----------

// board-card: model on the left (+ optional mono note under it), cash
// offer on the right in emerald. `soft: true` mutes a price (e.g. "Contact").
function boardRows(rows) {
  return (rows || []).map((r) => {
    const note = r.note ? `<span class="row-note">${esc(r.note)}</span>` : '';
    const soft = r.soft ? ' soft' : '';
    return (
      '<div class="board-row">' +
        `<div><div class="row-model">${esc(r.model)}${note}</div></div>` +
        `<div class="row-price${soft}">${esc(r.price)}</div>` +
      '</div>'
    );
  }).join('\n');
}

// compare-card: a horizontal bar. kind "us" = emerald (our offer),
// "alt" = muted red (a typical competitor estimate). pct drives width.
function compareBars(bars) {
  return (bars || []).map((b) => {
    const kind = b.kind === 'us' ? 'us' : 'alt';
    const pct = Math.max(0, Math.min(100, Number(b.pct) || 0));
    return (
      `<div class="bar-row ${kind}">` +
        '<div class="bar-meta">' +
          `<span>${esc(b.label)}</span>` +
          `<span class="bar-value">${esc(b.value)}</span>` +
        '</div>' +
        '<div class="bar-track">' +
          `<div class="bar-fill" style="--w:${pct}%"></div>` +
        '</div>' +
      '</div>'
    );
  }).join('\n');
}

// index-card: an atomic cell — mono label, big number, mono footnote.
// tone "accent" = emerald number, "neg" = red, "" = default white.
function indexCells(cells) {
  return (cells || []).map((c) => {
    const tone = c.tone === 'accent' ? ' accent' : c.tone === 'neg' ? ' neg' : '';
    return (
      '<div class="idx-cell">' +
        `<div class="cell-label">${esc(c.label)}</div>` +
        `<div class="cell-num${tone}">${esc(c.num)}</div>` +
        `<div class="cell-foot">${esc(c.foot)}</div>` +
      '</div>'
    );
  }).join('\n');
}

// ============================================================
// TEMPLATES
// ============================================================

const TEMPLATES = {
  // -------- offer-card : the workhorse "we buy X" post --------
  offer: {
    file: 'offer-card.html',
    role: 'The workhorse "we buy X" post: a giant cash headline for one ' +
      'model/category plus a 3-cell spec row (top payout / turnaround / ' +
      'condition). Best as a feed square. The dollar figure is the thumbstop.',
    fields: ['tag', 'eyebrow', 'headline_html', 'sub_html',
      'c1_label', 'c1_value', 'c2_label', 'c2_value', 'c3_label', 'c3_value'],
    expand: (f) => htmlPass(f),
    sample: {
      tag: 'WE BUY · IPHONE',
      eyebrow: 'IPHONE 17 PRO MAX · UNLOCKED',
      headline_html: 'Up to <em>$1,241</em> for your iPhone 17 Pro Max',
      sub_html: 'Top tier, unlocked, 2TB. Every storage size has its own number — ask for yours.',
      c1_label: 'TOP PAYOUT', c1_value: '$1,241',
      c2_label: 'TURNAROUND', c2_value: 'Same day',
      c3_label: 'CONDITION', c3_value: 'New–Good',
    },
  },

  // -------- board-card : today's numbers for one category --------
  board: {
    file: 'board-card.html',
    role: 'A price list for one category — model on the left, our cash ' +
      'offer on the right. Reads like a quote board. Pull real model+price ' +
      'pairs from pricing only; never invent a row. Feed square.',
    fields: ['tag', 'eyebrow', 'title_html', 'rows', 'note_html'],
    expand: (f) => {
      const { rows, ...rest } = f;
      return Object.assign(htmlPass(rest), { rows_html: boardRows(rows) });
    },
    sample: {
      tag: 'PRICE BOARD · IPHONE',
      eyebrow: 'IPHONE 17 PRO MAX · UNLOCKED / APPLE',
      title_html: "iPhone 17 Pro Max, <em>today's</em> cash",
      rows: [
        { model: '2TB', price: '$1,241' },
        { model: '1TB', price: '$1,050' },
        { model: '512GB', price: '$975' },
        { model: '256GB', price: '$855' },
      ],
      note_html: 'Unlocked / Apple-financed. Carrier-locked pays a little less — ask for your exact tier.',
    },
  },

  // -------- payout-card : the proof receipt --------
  payout: {
    file: 'payout-card.html',
    role: 'A proof post styled as a payout receipt: device, condition, ' +
      'method, turnaround, and a big emerald amount stamped PAID. ' +
      'Anonymized — never a name. Use a real payout figure from pricing. ' +
      'Feed square.',
    fields: ['tag', 'slip_title', 'slip_ref', 'device', 'condition',
      'method', 'turnaround', 'amount', 'status'],
    expand: (f) => htmlPass(f),
    sample: {
      tag: 'PAID OUT',
      slip_title: 'PAYOUT SLIP',
      slip_ref: 'NO. 4471',
      device: 'MacBook Air 15" M4',
      condition: 'Excellent · 16GB / 512GB',
      method: 'Zelle',
      turnaround: 'Same day',
      amount: '$816',
      status: 'PAID',
    },
  },

  // -------- compare-card : "we pay more" --------
  compare: {
    file: 'compare-card.html',
    role: 'A horizontal bar chart making "we pay more" obvious: our offer ' +
      'is the long emerald bar, typical trade-in alternatives sit below in ' +
      'muted red. Our number is real; rival figures are framed as typical ' +
      'estimates in the note. Feed square.',
    fields: ['tag', 'eyebrow', 'title_html', 'bars', 'note_html'],
    expand: (f) => {
      const { bars, ...rest } = f;
      return Object.assign(htmlPass(rest), { bars_html: compareBars(bars) });
    },
    sample: {
      tag: 'WE PAY MORE',
      eyebrow: 'IPHONE 15 PRO · 256GB',
      title_html: 'Same phone. <em>Bigger</em> check.',
      bars: [
        { label: 'PurchasingCorp', value: '$450', pct: 100, kind: 'us' },
        { label: 'Carrier trade-in credit', value: '~$320', pct: 71, kind: 'alt' },
        { label: 'Big-box gift card', value: '~$300', pct: 67, kind: 'alt' },
      ],
      note_html: 'Our number is cash, today. Competitor figures are typical trade-in estimates — usually store credit, not cash.',
    },
  },

  // -------- stat-card : one big number --------
  stat: {
    file: 'stat-card.html',
    role: 'One enormous number weaponized: a payout, a turnaround, a count. ' +
      'Keep the value short (it renders at ~440px). Caption explains why it ' +
      'matters; source line receipts it. Great as a story or feed.',
    fields: ['eyebrow', 'stat_value', 'stat_unit', 'caption_html', 'source'],
    expand: (f) => htmlPass(f),
    sample: {
      eyebrow: 'MACBOOK PRO PAYOUT',
      stat_value: '50',
      stat_unit: '%',
      caption_html: 'A current-gen MacBook Pro pays out around <em>half its MSRP</em> — in cash, same day.',
      source: 'purchasingcorp.com/pricing',
    },
  },

  // -------- quote-card : editorial pull-quote --------
  quote: {
    file: 'quote-card.html',
    role: 'A literary pull-quote in big Instrument Serif italic — the brand ' +
      'voice as a line ("More than Apple. More than Best Buy."). Pure ' +
      'typography, optional photo backdrop. Story or feed.',
    fields: ['quote_text_html', 'quote_attrib', 'photo_url'],
    expand: (f) => htmlPass(f),
    sample: {
      quote_text_html: 'More than Apple. <em>More than Best Buy.</em>',
      quote_attrib: 'The PurchasingCorp promise',
      photo_url: '',
    },
  },

  // -------- index-card : data-dense reference grid --------
  index: {
    file: 'index-card.html',
    role: 'A 3x2 (feed) / 2x3 (story) reference grid — each cell is a ' +
      'label, a big number, a footnote. Ideal for "what we buy" and ' +
      'category top-payout ranges. Numbers must be real.',
    fields: ['tag', 'eyebrow', 'title_html', 'cells', 'note_html'],
    expand: (f) => {
      const { cells, ...rest } = f;
      return Object.assign(htmlPass(rest), { index_cells_html: indexCells(cells) });
    },
    sample: {
      tag: 'WHAT WE BUY',
      eyebrow: 'EIGHT CATEGORIES · CASH FOR ALL',
      title_html: "We don't just buy <em>phones</em>",
      cells: [
        { label: 'IPHONE', num: '$1,241', foot: 'UP TO · 17 PRO MAX', tone: 'accent' },
        { label: 'MACBOOK AIR', num: '$976', foot: 'UP TO · 15" M4', tone: 'accent' },
        { label: 'MAC MINI', num: '$675', foot: 'UP TO · M4 PRO', tone: 'accent' },
        { label: 'APPLE WATCH', num: '$473', foot: 'UP TO · ULTRA 3', tone: 'accent' },
        { label: 'CONSOLES', num: '$425', foot: 'UP TO · PS5 PRO', tone: 'accent' },
        { label: 'IPAD · MORE', num: 'Cash', foot: 'AIRPODS · BULK LOTS', tone: '' },
      ],
      note_html: 'Phones, laptops, tablets, watches, consoles, AirPods, and bulk lots — one offer, one payout.',
    },
  },

  // -------- carousel-card : numbered explainer slide --------
  carousel: {
    file: 'carousel-card.html',
    role: 'A numbered slide for multi-card threads ("how it works", "how to ' +
      'wipe your iPhone", "what affects your payout"). Ghosted serif numeral. ' +
      'Use 3-5 of these as the slides of one carousel post.',
    fields: ['step_num', 'step_label', 'eyebrow', 'headline_html', 'body_html'],
    expand: (f) => htmlPass(f),
    sample: {
      step_num: '01',
      step_label: 'STEP 01',
      eyebrow: 'HOW IT WORKS',
      headline_html: 'Tell us <em>what you have</em>',
      body_html: 'Pick your device and condition on the form. Takes a minute, and you get a <strong>real number</strong> back — not a vague "up to".',
    },
  },

  // -------- cover-card : magazine-cover announcement --------
  cover: {
    file: 'cover-card.html',
    role: 'A cinematic magazine-cover slide for announcing something (price ' +
      'bumps, a new category, a seasonal push). Masthead, meta line, one ' +
      'page-dominating headline. Low information, high impact. Optional photo.',
    fields: ['issue', 'date_label', 'section', 'headline_html', 'deck_html', 'photo_url'],
    expand: (f) => htmlPass(f),
    sample: {
      issue: 'NO. 07',
      date_label: 'MAY 2026',
      section: 'PRICING',
      headline_html: 'Cash <em>today</em>',
      deck_html: 'Fresh numbers across iPhone, MacBook, iPad, and consoles — and the same same-day payout.',
      photo_url: '',
    },
  },

  // -------- photo-cover-card : atmospheric photo headline --------
  'photo-cover': {
    file: 'photo-cover-card.html',
    role: 'A full-bleed atmospheric photo (desaturated, dimmed) with a ' +
      'headline reading over a dark gradient. Reserved for moments that ' +
      'benefit from atmosphere. Needs a photo query. Story or feed.',
    fields: ['tag', 'eyebrow', 'headline_html', 'deck_html', 'photo_url', 'photo_credit'],
    expand: (f) => htmlPass(f),
    sample: {
      tag: 'SAME-DAY PAYOUT',
      eyebrow: 'CASH FOR YOUR DEVICES',
      headline_html: 'Your old tech is <em>money</em>',
      deck_html: 'Phones, laptops, consoles — turned into cash the same day you send them in.',
      photo_url: '',
      photo_credit: 'PHOTO · UNSPLASH',
    },
  },

  // -------- lifestyle-card : aspirational outcome --------
  lifestyle: {
    file: 'lifestyle-card.html',
    role: 'The aspirational, outcome-forward post: a framed editorial photo ' +
      'up top (a drawer of old phones, a hand of cash) with the message ' +
      'below. Sells the feeling — that dead drawer device is cash — not a ' +
      'spec. Needs a photo query. Feed or story.',
    fields: ['tag', 'eyebrow', 'headline_html', 'sub_html', 'photo_url', 'photo_credit'],
    expand: (f) => htmlPass(f),
    sample: {
      tag: 'CASH TODAY',
      eyebrow: 'THE DRAWER FULL OF OLD PHONES',
      headline_html: 'That old phone is <em>real cash</em>',
      sub_html: 'The one you stopped using two upgrades ago still has value. A few minutes, a real number, same-day payout.',
      photo_url: '',
      photo_credit: 'PHOTO · UNSPLASH',
    },
  },

  // -------- meme-post : the off-duty shareable --------
  meme: {
    file: 'meme-post.html',
    role: 'The shareable, off-duty post. Instrument Serif italic setup up ' +
      'top, a clean CSS scene in the middle (drawer device turns into ' +
      'emerald cash via the brand arrow), punchline below. Relatable, never ' +
      'mean. Keep both lines short. Feed square.',
    fields: ['top_text', 'bottom_text', 'image_concept'],
    expand: (f) => htmlPass(f),
    sample: {
      top_text: 'Letting a $400 phone die in a drawer',
      bottom_text: 'because the trade-in felt like a whole project',
      image_concept: 'drawer phone -> same-day cash',
    },
  },
};

// ============================================================
// PUBLIC API
// ============================================================

function listTemplates() {
  return Object.keys(TEMPLATES);
}

function specFor(name) {
  const spec = TEMPLATES[name];
  if (!spec) {
    throw new Error(`Unknown template "${name}". Known: ${listTemplates().join(', ')}`);
  }
  return spec;
}

// Turn a slide's logical fields into the flat {{placeholder}} map the
// template expects. `size` is intentionally NOT handled here — the
// renderer injects and sanitizes it.
function expandFields(name, logicalFields) {
  const spec = specFor(name);
  return spec.expand(logicalFields || {});
}

// The brand-accurate sample slide for a template: { template, fields }.
function sampleFor(name) {
  const spec = specFor(name);
  return { template: name, fields: JSON.parse(JSON.stringify(spec.sample)) };
}

module.exports = {
  TEMPLATES,
  listTemplates,
  specFor,
  expandFields,
  sampleFor,
  // helpers shared with the renderer
  esc,
  escAllowEm,
  htmlPass,
  boardRows,
  compareBars,
  indexCells,
};
