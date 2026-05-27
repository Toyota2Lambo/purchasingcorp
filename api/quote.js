export const config = { runtime: 'edge' };

const FIELD_LABELS = {
  type: 'Type',
  model: 'Make & Model',
  condition: 'Condition',
  details: 'Details',
  handoff: 'Handoff',
  contact: 'Contact',
};

const MAX_FILES = 4;
const MAX_FILE_BYTES = 8 * 1024 * 1024;
const MAX_TOTAL_BYTES = 24 * 1024 * 1024;

export default async function handler(req) {
  if (req.method !== 'POST') return json({ error: 'Method not allowed' }, 405);

  let form;
  try {
    form = await req.formData();
  } catch {
    return json({ error: 'Invalid form data' }, 400);
  }

  // Honeypot
  if (form.get('website')) return json({ ok: true });

  const type = str(form.get('type'));
  const model = str(form.get('model'));
  const contact = str(form.get('contact'));
  if (!type || !model || !contact) return json({ error: 'Missing required fields' }, 400);

  const values = {};
  for (const k of Object.keys(FIELD_LABELS)) values[k] = str(form.get(k));

  const photos = form.getAll('photos').filter((f) => f && typeof f === 'object' && f.size > 0);
  const accepted = [];
  let totalBytes = 0;
  for (const f of photos.slice(0, MAX_FILES)) {
    if (f.size > MAX_FILE_BYTES) continue;
    if (totalBytes + f.size > MAX_TOTAL_BYTES) break;
    totalBytes += f.size;
    accepted.push(f);
  }

  const tasks = [];
  if (process.env.DISCORD_WEBHOOK_URL) tasks.push(sendDiscord(values, accepted));
  if (process.env.TELEGRAM_BOT_TOKEN && process.env.TELEGRAM_CHAT_ID) tasks.push(sendTelegram(values, accepted));

  if (!tasks.length) return json({ error: 'No delivery channels configured' }, 500);

  const results = await Promise.allSettled(tasks);
  const ok = results.some((r) => r.status === 'fulfilled' && r.value === true);
  if (!ok) {
    const errors = results
      .filter((r) => r.status === 'rejected' || r.value !== true)
      .map((r) => (r.status === 'rejected' ? r.reason?.message : r.value))
      .filter(Boolean);
    return json({ error: 'Delivery failed', detail: errors.join('; ').slice(0, 300) }, 502);
  }
  return json({ ok: true });
}

async function sendDiscord(v, files) {
  const fields = Object.entries(FIELD_LABELS)
    .filter(([k]) => v[k])
    .map(([k, label]) => ({
      name: label,
      value: v[k].slice(0, 1024),
      inline: k !== 'details' && k !== 'model',
    }));
  if (files.length) fields.push({ name: 'Photos', value: `${files.length} attached below`, inline: false });

  const embed = {
    title: 'New Quote Request',
    color: 0xffffff,
    fields,
    footer: { text: 'purchasingcorp.com' },
    timestamp: new Date().toISOString(),
  };

  const out = new FormData();
  out.append(
    'payload_json',
    JSON.stringify({
      content: `📥 **New quote** — ${v.type}: ${v.model.slice(0, 80)}`,
      embeds: [embed],
      allowed_mentions: { parse: [] },
    })
  );
  files.forEach((file, i) => out.append(`files[${i}]`, file, sanitizeFilename(file.name, i)));

  const r = await fetch(process.env.DISCORD_WEBHOOK_URL, { method: 'POST', body: out });
  if (!r.ok) throw new Error(`discord ${r.status}: ${(await r.text()).slice(0, 120)}`);
  return true;
}

async function sendTelegram(v, files) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_CHAT_ID;

  const lines = [`📥 *New quote request*`, ''];
  for (const [k, label] of Object.entries(FIELD_LABELS)) {
    if (v[k]) lines.push(`*${label}:* ${escapeMd(v[k])}`);
  }
  const caption = lines.join('\n').slice(0, 1024);

  // No photos — just a text message
  if (!files.length) {
    const r = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text: caption, parse_mode: 'Markdown' }),
    });
    if (!r.ok) throw new Error(`telegram ${r.status}: ${(await r.text()).slice(0, 120)}`);
    return true;
  }

  // With photos — sendMediaGroup
  const fd = new FormData();
  fd.append('chat_id', chatId);
  const media = files.map((file, i) => {
    const ref = `photo${i}`;
    fd.append(ref, file, sanitizeFilename(file.name, i));
    return {
      type: 'photo',
      media: `attach://${ref}`,
      ...(i === 0 ? { caption, parse_mode: 'Markdown' } : {}),
    };
  });
  fd.append('media', JSON.stringify(media));

  const r = await fetch(`https://api.telegram.org/bot${token}/sendMediaGroup`, {
    method: 'POST',
    body: fd,
  });
  if (!r.ok) throw new Error(`telegram ${r.status}: ${(await r.text()).slice(0, 120)}`);
  return true;
}

function str(v) {
  return (v || '').toString().trim();
}
function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}
function sanitizeFilename(name, i) {
  return (name || `photo-${i}.jpg`).replace(/[^a-zA-Z0-9._-]/g, '_').slice(0, 80);
}
function escapeMd(s) {
  return s.replace(/([_*`\[\]])/g, '\\$1');
}
