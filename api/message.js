export const config = { runtime: 'edge' };

// POST /api/message  (JSON)
// Posts a chat message on an inquiry thread, then emails the other party.
//
// Why a server endpoint instead of a direct Supabase insert from the browser:
// we need a trusted place to (a) confirm the sender's role and ownership and
// (b) send the notification email. The browser talks to Supabase with the anon
// key under RLS for READS; writes now go through here with the service_role key.
//
// Auth: `Authorization: Bearer <jwt>` — the caller's Supabase access token.
// Body (JSON): { inquiry_id, body }
//
// Role is derived server-side, never trusted from the client:
//   • caller in public.admins           → sender_role = 'admin'   → emails the customer
//   • caller owns the inquiry (user_id)  → sender_role = 'customer'→ emails OWNER_EMAIL
//   • otherwise                          → 403
//
// Required env: SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY.
// Email is best-effort (see api/_email.js) and never blocks saving the message.

import {
  sendEmail,
  ownerEmail,
  adminReplyEmail,
  customerReplyEmail,
} from './_email.js';

const MAX_BODY = 4000;

export default async function handler(req) {
  if (req.method !== 'POST') return json({ error: 'Method not allowed' }, 405);

  const url = process.env.SUPABASE_URL;
  const anonKey = process.env.SUPABASE_ANON_KEY;
  const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !anonKey || !serviceKey) {
    return json({ error: 'Messaging is not configured' }, 501);
  }

  const authz = req.headers.get('authorization') || '';
  const jwt = authz.toLowerCase().startsWith('bearer ') ? authz.slice(7).trim() : '';
  if (!jwt) return json({ error: 'Sign in to send a message' }, 401);

  let payload = {};
  try { payload = await req.json(); } catch {}
  const inquiryId = str(payload.inquiry_id);
  const body = str(payload.body).slice(0, MAX_BODY);
  if (!inquiryId || !body) return json({ error: 'Missing inquiry or message' }, 400);

  // 1. Verify the caller's session and identity.
  let user;
  try {
    const r = await fetch(`${url}/auth/v1/user`, {
      headers: { apikey: anonKey, Authorization: `Bearer ${jwt}` },
    });
    if (!r.ok) return json({ error: 'Your session expired, sign in again' }, 401);
    user = await r.json();
  } catch {
    return json({ error: 'Could not verify your session' }, 502);
  }
  if (!user || !user.id) return json({ error: 'Your session expired, sign in again' }, 401);

  // 2. Load the inquiry (service role) and figure out the caller's role.
  const inquiry = await getInquiry(url, serviceKey, inquiryId);
  if (!inquiry) return json({ error: 'Quote not found' }, 404);

  const isAdmin = await callerIsAdmin(url, serviceKey, user.id);
  const ownsInquiry = inquiry.user_id && inquiry.user_id === user.id;
  if (!isAdmin && !ownsInquiry) return json({ error: 'Not allowed' }, 403);

  const senderRole = isAdmin ? 'admin' : 'customer';

  // 3. Insert the message (service role bypasses RLS; role is server-decided).
  const inserted = await insertMessage(url, serviceKey, {
    inquiry_id: inquiryId,
    sender_role: senderRole,
    sender_id: user.id,
    body,
  });
  if (!inserted) return json({ error: 'Could not send your message' }, 502);

  // 4. Notify the other party by email (best-effort).
  const device = inquiry.model || inquiry.type || 'your device';
  let emailed = false;
  if (senderRole === 'admin') {
    if (inquiry.email) {
      const tmpl = adminReplyEmail({ device, snippet: body });
      emailed = await sendEmail({ to: inquiry.email, subject: tmpl.subject, html: tmpl.html, text: tmpl.text });
    }
  } else {
    const owner = ownerEmail();
    if (owner) {
      const tmpl = customerReplyEmail({ device, contact: inquiry.contact || inquiry.email, snippet: body });
      emailed = await sendEmail({ to: owner, subject: tmpl.subject, html: tmpl.html, text: tmpl.text });
    }
  }

  return json({ ok: true, id: inserted.id, created_at: inserted.created_at, sender_role: senderRole, emailed });
}

// --- Supabase helpers -----------------------------------------------------

async function getInquiry(url, key, id) {
  try {
    const q =
      `${url}/rest/v1/inquiries` +
      `?id=eq.${encodeURIComponent(id)}` +
      `&select=id,user_id,email,contact,type,model&limit=1`;
    const r = await fetch(q, { headers: svcHeaders(key) });
    if (!r.ok) return null;
    const rows = await r.json().catch(() => []);
    return Array.isArray(rows) && rows[0] ? rows[0] : null;
  } catch {
    return null;
  }
}

async function callerIsAdmin(url, key, userId) {
  try {
    const q = `${url}/rest/v1/admins?user_id=eq.${encodeURIComponent(userId)}&select=user_id&limit=1`;
    const r = await fetch(q, { headers: svcHeaders(key) });
    if (!r.ok) return false;
    const rows = await r.json().catch(() => []);
    return Array.isArray(rows) && rows.length > 0;
  } catch {
    return false;
  }
}

async function insertMessage(url, key, row) {
  try {
    const r = await fetch(`${url}/rest/v1/messages`, {
      method: 'POST',
      headers: { ...svcHeaders(key), 'content-type': 'application/json', Prefer: 'return=representation' },
      body: JSON.stringify(row),
    });
    if (!r.ok) return null;
    const rows = await r.json().catch(() => []);
    return Array.isArray(rows) && rows[0] ? rows[0] : { id: null, created_at: null };
  } catch {
    return null;
  }
}

function svcHeaders(key) {
  return { apikey: key, Authorization: `Bearer ${key}` };
}

// --- misc -----------------------------------------------------------------

function str(v) {
  return (v == null ? '' : v).toString().trim();
}
function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}
