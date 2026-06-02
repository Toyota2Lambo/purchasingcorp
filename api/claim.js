export const config = { runtime: 'edge' };

// POST /api/claim
// Links anonymous quote rows to the signed-in user's account.
//
// Auth: send the user's Supabase access token as `Authorization: Bearer <jwt>`.
// We verify it against GoTrue (`/auth/v1/user`) with the anon key, which also
// tells us whether the account's email is confirmed.
//
// Body (JSON): { tokens: [{ id, token }, ...] }
//   `tokens` are the per-quote { id, claim_token } pairs handed back by
//   /api/inquiry and stashed in the browser's localStorage. They let us claim
//   a quote the user submitted BEFORE they had an account, even if they used a
//   different contact method than their account email.
//
// Claiming runs with the service_role key (bypasses RLS), in this order:
//   1. By token: PATCH rows where id + claim_token match AND user_id is null.
//   2. By confirmed email: if the account email is confirmed, also claim any
//      still-unowned rows whose `email` equals it.
// Token-first ordering means an email-matched row already claimed in step 1
// is skipped in step 2 (its user_id is no longer null), so nothing double-counts.
//
// Returns { ok: true, claimed: <count>, email_confirmed: <bool> }.

export default async function handler(req) {
  if (req.method !== 'POST') return json({ error: 'Method not allowed' }, 405);

  const url = process.env.SUPABASE_URL;
  const anonKey = process.env.SUPABASE_ANON_KEY;
  const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !anonKey || !serviceKey) {
    return json({ error: 'Accounts are not configured' }, 501);
  }

  const authz = req.headers.get('authorization') || '';
  const jwt = authz.toLowerCase().startsWith('bearer ') ? authz.slice(7).trim() : '';
  if (!jwt) return json({ error: 'Missing bearer token' }, 401);

  // Verify the token and load the user from GoTrue.
  let user;
  try {
    const r = await fetch(`${url}/auth/v1/user`, {
      headers: { apikey: anonKey, Authorization: `Bearer ${jwt}` },
    });
    if (!r.ok) return json({ error: 'Invalid session' }, 401);
    user = await r.json();
  } catch {
    return json({ error: 'Could not verify session' }, 502);
  }
  const userId = user && user.id;
  if (!userId) return json({ error: 'Invalid session' }, 401);

  const email = (user.email || '').toLowerCase();
  const emailConfirmed = Boolean(user.email_confirmed_at || user.confirmed_at);

  let body = {};
  try { body = await req.json(); } catch {}
  const tokens = Array.isArray(body.tokens) ? body.tokens.slice(0, 25) : [];

  let claimed = 0;

  // 1. Claim by per-quote token (works regardless of email).
  for (const t of tokens) {
    const id = str(t && t.id);
    const token = str(t && t.token);
    if (!id || !token) continue;
    const q =
      `${url}/rest/v1/inquiries` +
      `?id=eq.${encodeURIComponent(id)}` +
      `&claim_token=eq.${encodeURIComponent(token)}` +
      `&user_id=is.null`;
    claimed += await patchCount(q, serviceKey, userId);
  }

  // 2. Claim by confirmed email (links quotes whose contact email matches).
  if (emailConfirmed && email) {
    const q =
      `${url}/rest/v1/inquiries` +
      `?email=eq.${encodeURIComponent(email)}` +
      `&user_id=is.null`;
    claimed += await patchCount(q, serviceKey, userId);
  }

  return json({ ok: true, claimed, email_confirmed: emailConfirmed });
}

// PATCH the matched rows to set user_id, returning how many rows changed.
async function patchCount(queryUrl, serviceKey, userId) {
  try {
    const r = await fetch(queryUrl, {
      method: 'PATCH',
      headers: {
        apikey: serviceKey,
        Authorization: `Bearer ${serviceKey}`,
        'content-type': 'application/json',
        Prefer: 'return=representation',
      },
      body: JSON.stringify({ user_id: userId }),
    });
    if (!r.ok) return 0;
    const rows = await r.json().catch(() => []);
    return Array.isArray(rows) ? rows.length : 0;
  } catch {
    return 0;
  }
}

function str(v) {
  return (v == null ? '' : v).toString().trim();
}
function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}
