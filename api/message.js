export const config = { runtime: 'edge' };

// POST /api/message  (multipart/form-data)
// Posts a chat message on an inquiry thread, then emails the other party.
//
// Why a server endpoint instead of a direct Supabase insert from the browser:
// we need a trusted place to (a) confirm the sender's role and ownership,
// (b) upload any attached photos with the service_role key, and (c) send the
// notification email. The browser talks to Supabase with the anon key under RLS
// for READS; writes go through here with the service_role key.
//
// Auth: `Authorization: Bearer <jwt>` — the caller's Supabase access token.
// Fields: inquiry_id, body (optional if photos present), photos[] (files).
// A message must carry text, at least one photo, or both.
//
// Role is derived server-side, never trusted from the client:
//   • caller in public.admins           → sender_role = 'admin'   → emails the customer
//   • caller owns the inquiry (user_id)  → sender_role = 'customer'→ emails OWNER_EMAIL
//   • otherwise                          → 403
//
// Required env: SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY.
// Photos reuse the PUBLIC `inquiry-photos` Storage bucket under <inquiry>/chat/.
// Email is best-effort (see api/_email.js) and never blocks saving the message.

import {
  sendEmail,
  ownerEmail,
  adminReplyEmail,
  customerReplyEmail,
} from './_email.js';

const MAX_BODY = 4000;
const PHOTO_BUCKET = 'inquiry-photos';
const MAX_FILES = 4;
const MAX_FILE_BYTES = 8 * 1024 * 1024;
const MAX_TOTAL_BYTES = 24 * 1024 * 1024;

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

  let form;
  try { form = await req.formData(); } catch { return json({ error: 'Invalid form data' }, 400); }
  const inquiryId = str(form.get('inquiry_id'));
  const body = str(form.get('body')).slice(0, MAX_BODY);

  // Collect attached photos within limits.
  const photos = form.getAll('photos').filter((f) => f && typeof f === 'object' && f.size > 0);
  const accepted = [];
  let total = 0;
  for (const f of photos.slice(0, MAX_FILES)) {
    if (f.size > MAX_FILE_BYTES) continue;
    if (total + f.size > MAX_TOTAL_BYTES) break;
    total += f.size;
    accepted.push(f);
  }

  if (!inquiryId || (!body && !accepted.length)) {
    return json({ error: 'Missing inquiry or message' }, 400);
  }

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

  // 3. Upload any photos first (best-effort; a failed upload just omits that
  // URL). They land in the public `inquiry-photos` bucket under <inquiry>/chat/.
  const attachments = accepted.length
    ? await uploadChatPhotos(url, serviceKey, inquiryId, accepted)
    : [];
  // Every photo failed to upload and there's no text → nothing worth saving.
  if (!body && !attachments.length) return json({ error: 'Could not send your message' }, 502);

  // 4. Insert the message (service role bypasses RLS; role is server-decided).
  const inserted = await insertMessage(url, serviceKey, {
    inquiry_id: inquiryId,
    sender_role: senderRole,
    sender_id: user.id,
    body: body || null,
    attachments,
  });
  if (!inserted) return json({ error: 'Could not send your message' }, 502);

  // 5. Notify the other party by email (best-effort).
  const device = inquiry.model || inquiry.type || 'your device';
  const photoCount = attachments.length;
  let emailed = false;
  if (senderRole === 'admin') {
    if (inquiry.email) {
      const tmpl = adminReplyEmail({ device, snippet: body, photos: photoCount });
      emailed = await sendEmail({ to: inquiry.email, subject: tmpl.subject, html: tmpl.html, text: tmpl.text });
    }
  } else {
    const owner = ownerEmail();
    if (owner) {
      const tmpl = customerReplyEmail({ device, contact: inquiry.contact || inquiry.email, snippet: body, photos: photoCount });
      emailed = await sendEmail({ to: owner, subject: tmpl.subject, html: tmpl.html, text: tmpl.text });
    }
  }

  return json({
    ok: true,
    id: inserted.id,
    created_at: inserted.created_at,
    sender_role: senderRole,
    attachments,
    emailed,
  });
}

// --- Supabase Storage -----------------------------------------------------

async function uploadChatPhotos(url, key, inquiryId, files) {
  const urls = [];
  const prefix = `${encodeURIComponent(inquiryId)}/chat/${uuid()}`;
  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    const objectPath = `${prefix}-${i + 1}.${extFor(f)}`;
    try {
      const r = await fetch(`${url}/storage/v1/object/${PHOTO_BUCKET}/${objectPath}`, {
        method: 'POST',
        headers: {
          apikey: key,
          Authorization: `Bearer ${key}`,
          'content-type': f.type || 'application/octet-stream',
          'cache-control': '3600',
          'x-upsert': 'true',
        },
        body: f,
      });
      if (r.ok) urls.push(`${url}/storage/v1/object/public/${PHOTO_BUCKET}/${objectPath}`);
    } catch {
      // skip this photo, keep the rest
    }
  }
  return urls;
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
function extFor(f) {
  const fromType = (f.type || '').split('/')[1];
  if (fromType) {
    const clean = fromType.replace(/[^a-z0-9]/gi, '').toLowerCase();
    if (clean) return clean === 'jpeg' ? 'jpg' : clean.slice(0, 5);
  }
  const fromName = (f.name || '').split('.').pop();
  return fromName && /^[a-z0-9]+$/i.test(fromName) ? fromName.toLowerCase() : 'bin';
}
function uuid() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}
