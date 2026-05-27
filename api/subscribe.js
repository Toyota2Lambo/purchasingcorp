export const config = { runtime: 'edge' };

// POST /api/subscribe
// body: { email?, consent: bool, consent_marketing: bool, path?, referrer? }
//
// Inserts a row into the Supabase `subscribers` table. Email is unique:
// if it already exists, we update the consent timestamps (upsert).
//
// Required env vars (set in Vercel project settings):
//   SUPABASE_URL              e.g. https://xxxxxxxx.supabase.co
//   SUPABASE_SERVICE_ROLE_KEY (Settings → API → service_role key)
//
// If env vars are missing the endpoint returns 200 with {stored:false}
// so the frontend banner still dismisses cleanly. Real failures return 5xx.

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export default async function handler(req) {
  if (req.method !== 'POST') return json({ error: 'Method not allowed' }, 405);

  let body;
  try {
    body = await req.json();
  } catch {
    return json({ error: 'Invalid JSON' }, 400);
  }

  const email = (body.email || '').toString().trim().toLowerCase() || null;
  const consent = body.consent === true;
  const consentMarketing = body.consent_marketing === true;
  const path = (body.path || '').toString().slice(0, 256) || null;
  const referrer = (body.referrer || '').toString().slice(0, 512) || null;

  if (!consent) return json({ error: 'Consent required' }, 400);
  if (email && !EMAIL_RE.test(email)) return json({ error: 'Invalid email' }, 400);

  // Crude honeypot — frontend never sends "url"
  if (body.url) return json({ ok: true });

  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) {
    // Not configured yet — accept silently so banner dismisses.
    return json({ ok: true, stored: false, reason: 'not_configured' });
  }

  const ip = await anonIp(req.headers.get('x-forwarded-for'));
  const ua = (req.headers.get('user-agent') || '').slice(0, 512);
  const country = req.headers.get('x-vercel-ip-country') || null;

  const row = {
    email,
    consent_essential: true,
    consent_marketing: consentMarketing,
    consent_at: new Date().toISOString(),
    path,
    referrer,
    country,
    ip_hash: ip,
    user_agent: ua,
  };

  // Use Supabase REST upsert — if email already exists, update consent fields.
  // For null-email rows (cookie-only consent) we just insert.
  const endpoint = `${url}/rest/v1/subscribers${email ? '?on_conflict=email' : ''}`;
  const r = await fetch(endpoint, {
    method: 'POST',
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
      'content-type': 'application/json',
      Prefer: email ? 'resolution=merge-duplicates,return=minimal' : 'return=minimal',
    },
    body: JSON.stringify(row),
  });

  if (!r.ok) {
    const detail = (await r.text()).slice(0, 200);
    return json({ ok: false, error: 'Supabase rejected the insert', detail }, 502);
  }
  return json({ ok: true, stored: true });
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

// Hash the first IP in x-forwarded-for so we record one row per visitor
// without storing the raw address. Synchronous-ish via SubtleCrypto.
async function anonIp(xff) {
  if (!xff) return null;
  const ip = xff.split(',')[0].trim();
  if (!ip) return null;
  try {
    const buf = new TextEncoder().encode(ip + '|purchasingcorp');
    const hash = await crypto.subtle.digest('SHA-256', buf);
    return Array.from(new Uint8Array(hash))
      .slice(0, 8)
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('');
  } catch {
    return null;
  }
}
