export const config = { runtime: 'edge' };

const SHEET_ID = '1sXN7aWSZpFU3rxQopXIYJGhRR-I99PR2BhZJBLaG7Ew';
const APPLE_GID = 0;
const CONSOLES_GID = 1876227864;

const CATEGORY_HEADERS = {
  iphone: ['Model', 'Unlocked / Apple', 'Carrier / Locked'],
  'macbook-pro': ['Model', 'Discount on MSRP', 'Activation bonus'],
  'macbook-air': ['Model', 'Price', 'Activation bonus'],
  'mac-mini': ['Model', 'Price', 'Note'],
  'apple-watch': ['Model', 'Price', 'Note'],
  ipad: ['Model', 'Price', 'Note'],
  consoles: ['Model', 'Price', 'Note'],
  accessories: ['Item', 'Price', 'Note'],
};

export default async function handler() {
  try {
    const [appleCSV, consolesCSV] = await Promise.all([
      fetchCSV(APPLE_GID),
      fetchCSV(CONSOLES_GID),
    ]);

    const apple = parseAppleSheet(appleCSV);
    const consoles = parseConsolesSheet(consolesCSV);

    const result = {
      iphone: { headers: CATEGORY_HEADERS.iphone, rows: apple.iphone },
      'macbook-pro': { headers: CATEGORY_HEADERS['macbook-pro'], rows: apple['macbook-pro'] },
      'macbook-air': { headers: CATEGORY_HEADERS['macbook-air'], rows: apple['macbook-air'] },
      'mac-mini': { headers: CATEGORY_HEADERS['mac-mini'], rows: apple['mac-mini'] },
      'apple-watch': { headers: CATEGORY_HEADERS['apple-watch'], rows: apple['apple-watch'] },
      ipad: { headers: CATEGORY_HEADERS.ipad, rows: apple.ipad },
      consoles: { headers: CATEGORY_HEADERS.consoles, rows: consoles },
      accessories: { headers: CATEGORY_HEADERS.accessories, rows: apple.accessories },
    };

    // Sanity check: must have at least some iPhones and consoles, otherwise something broke
    if (result.iphone.rows.length < 10 || result.consoles.rows.length < 5) {
      throw new Error('Parsed data looks incomplete');
    }

    return new Response(JSON.stringify({ ok: true, data: result, updated: new Date().toISOString() }), {
      headers: {
        'content-type': 'application/json',
        'cache-control': 's-maxage=300, stale-while-revalidate=3600',
      },
    });
  } catch (e) {
    return new Response(JSON.stringify({ ok: false, error: e.message || 'fetch failed' }), {
      status: 502,
      headers: {
        'content-type': 'application/json',
        'cache-control': 'no-store',
      },
    });
  }
}

async function fetchCSV(gid) {
  const url = `https://docs.google.com/spreadsheets/d/${SHEET_ID}/export?format=csv&gid=${gid}`;
  const r = await fetch(url, { redirect: 'follow' });
  if (!r.ok) throw new Error(`gid ${gid} returned ${r.status}`);
  return await r.text();
}

function parseCSV(text) {
  const rows = [];
  let row = [];
  let cell = '';
  let inQuote = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuote) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') { inQuote = false; }
      else { cell += c; }
    } else {
      if (c === '"') { inQuote = true; }
      else if (c === ',') { row.push(cell); cell = ''; }
      else if (c === '\n') { row.push(cell); rows.push(row); row = []; cell = ''; }
      else if (c === '\r') { /* skip */ }
      else { cell += c; }
    }
  }
  if (cell.length || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

function parseAppleSheet(csv) {
  const rows = parseCSV(csv);
  const out = {
    iphone: [], 'macbook-pro': [], 'macbook-air': [], 'mac-mini': [],
    'apple-watch': [], ipad: [], accessories: [],
  };

  let category = null;
  let ipadSubsection = null;

  for (const r of rows) {
    const a = (r[0] || '').trim();
    const b = (r[1] || '').trim();
    const c = (r[2] || '').trim();

    if (!a && !b && !c) continue;

    // iPhone is the one section whose header row also contains column titles
    // in col B ("UNLOCKED FROM APPLE..."), so it's matched first regardless of b.
    if (/^iPHONE/i.test(a)) { category = 'iphone'; continue; }

    // For every other section, col B must be empty. This stops real products
    // like "Apple Watch Ultra 2,$337.50" or "AIRPOD PRO 3,CONTACT US" from
    // being mis-matched as headers.
    if (!b) {
      if (/^Macbook\s*Pro/i.test(a)) { category = 'macbook-pro'; continue; }
      if (/^Macbook\s*Air/i.test(a)) { category = 'macbook-air'; continue; }
      if (/^MAC\s*MINI/i.test(a)) { category = 'mac-mini'; continue; }
      if (/^Apple\s*Watch/i.test(a)) { category = 'apple-watch'; continue; }
      if (/^(IPAD|iPad)/i.test(a)) {
        category = 'ipad';
        ipadSubsection = humanize(a);
        continue;
      }
      if (/^(Apple\s+)?(Acc?essories|AirPods?)/i.test(a)) { category = 'accessories'; continue; }
    }

    // Skip known meta rows
    if (/^PART NUMBER/i.test(a)) continue;
    if (/^CONTACT FOR/i.test(a)) continue;
    if (/^APPLE PRODUCTS/i.test(a)) continue;
    if (/^PURCHA/i.test(a)) continue;

    // iPad sub-subsection (e.g., 'IPAD AIR 13" M3' in col B)
    if (!a && b && !/^\$/.test(b)) {
      ipadSubsection = humanize(b);
      continue;
    }

    if (!category || !a) continue;
    if (!b) continue; // need a price

    // Extract clean name (drop part numbers on later lines)
    const rawName = a.split('\n')[0].trim();
    const name = humanize(rawName);

    let displayName = name;
    if (category === 'iphone' && !/^iPhone/i.test(name)) displayName = `iPhone ${name}`;
    // Only prefix iPad subsection when the row is just a storage size (the section header has the model)
    if (category === 'ipad' && ipadSubsection && /^\d+\s*(GB|TB)$/i.test(name)) {
      displayName = `${ipadSubsection} ${name}`.replace(/\s+/g, ' ').trim();
    }

    const price = formatPrice(b);
    const note = formatNote(c);
    out[category].push([displayName, price, note]);
  }

  return out;
}

function parseConsolesSheet(csv) {
  const rows = parseCSV(csv);
  const out = [];

  for (const r of rows) {
    const a = (r[0] || '').trim();
    const b = (r[1] || '').trim();
    const c = (r[2] || '').trim();

    if (!a && !b && !c) continue;
    if (/^PURCHASING/i.test(a)) continue;
    if (/CONSOLES \(ALL/i.test(b)) continue;
    if (/^https?:\/\//i.test(b)) continue;

    // Manufacturer header row: empty A, name in B
    if (!a && b && !/^\$/.test(b) && !/CONTACT/i.test(b)) continue;

    if (!a || !b) continue;

    const name = humanize(a);
    const price = formatPrice(b);
    const note = formatNote(c);
    out.push([name, price, note]);
  }
  return out;
}

function humanize(s) {
  return s.replace(/\s+/g, ' ').trim();
}

function formatPrice(s) {
  if (!s) return '-';
  if (/CONTACT/i.test(s)) return 'Contact';
  if (s.includes('%')) return `${s.replace(/[^\d.%]/g, '')} off MSRP`;
  // Drop cents if .00, keep otherwise
  const m = s.match(/\$?([\d,]+(?:\.\d+)?)/);
  if (!m) return s;
  const num = parseFloat(m[1].replace(/,/g, ''));
  if (isNaN(num)) return s;
  if (num === Math.floor(num)) return `$${num.toLocaleString('en-US')}`;
  return `$${num.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

function formatNote(s) {
  if (!s) return '-';
  if (/first offer/i.test(s)) return 'Negotiable';
  return s.replace(/\*/g, '').trim() || '-';
}
