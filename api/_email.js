// Shared transactional-email helper (Resend).
//
// Underscore-prefixed so Vercel does NOT treat this as a routable function;
// it's imported by api/inquiry.js and api/message.js.
//
// Required env (Vercel → Project → Settings → Environment Variables):
//   RESEND_API_KEY   Resend → API Keys. Without it, sends are no-ops (the
//                    calling endpoint still succeeds — email never blocks a lead).
// Optional env:
//   EMAIL_FROM       Verified sender, e.g. 'PurchasingCorp <hello@purchasingcorp.com>'
//                    (default below). The domain must be verified in Resend.
//   EMAIL_REPLY_TO   Where customer replies land, e.g. 'hello@purchasingcorp.com'.
//   OWNER_EMAIL      Your inbox, for owner-copy notifications on customer replies.
//   SITE_URL         Public base URL for links (default https://purchasingcorp.com).

const RESEND_ENDPOINT = 'https://api.resend.com/emails';
const DEFAULT_FROM = 'PurchasingCorp <hello@purchasingcorp.com>';

export function siteUrl() {
  return (process.env.SITE_URL || 'https://purchasingcorp.com').replace(/\/+$/, '');
}

// One or more owner inboxes for notifications. OWNER_EMAIL may be a single
// address or a comma-separated list (e.g. "me@gmail.com,hello@purchasingcorp.com").
// Returns an array of addresses, or null when unset.
export function ownerEmail() {
  const list = (process.env.OWNER_EMAIL || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  return list.length ? list : null;
}

// Low-level send. Best-effort: returns true on a 2xx from Resend, false on any
// failure or when RESEND_API_KEY is unset. Never throws.
export async function sendEmail({ to, subject, html, text, replyTo }) {
  const key = process.env.RESEND_API_KEY;
  if (!key) return false;
  const recipients = (Array.isArray(to) ? to : [to])
    .map((s) => String(s || '').trim())
    .filter(Boolean);
  if (!recipients.length || !subject) return false;

  const reply = replyTo || process.env.EMAIL_REPLY_TO || null;
  const body = {
    from: process.env.EMAIL_FROM || DEFAULT_FROM,
    to: recipients,
    subject,
    ...(html ? { html } : {}),
    ...(text ? { text } : {}),
    ...(reply ? { reply_to: reply } : {}),
  };

  try {
    const r = await fetch(RESEND_ENDPOINT, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${key}`,
        'content-type': 'application/json',
      },
      body: JSON.stringify(body),
    });
    return r.ok;
  } catch {
    return false;
  }
}

// --- Templates ------------------------------------------------------------

// Shared dark shell matching the site (ink palette, Inter). Email clients are
// finicky, so this stays inline-styled and table-free-simple on purpose.
function shell(innerHtml) {
  return `<!doctype html><html><body style="margin:0;background:#050505;padding:32px 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:520px;margin:0 auto;background:#0b0b0c;border:1px solid rgba(255,255,255,0.08);border-radius:16px;overflow:hidden;">
    <div style="padding:24px 28px;border-bottom:1px solid rgba(255,255,255,0.06);">
      <span style="color:#e6e6e8;font-size:16px;font-weight:600;letter-spacing:-0.01em;">PurchasingCorp</span>
    </div>
    <div style="padding:28px;color:#c9c9ce;font-size:14px;line-height:1.65;">
      ${innerHtml}
    </div>
    <div style="padding:18px 28px;border-top:1px solid rgba(255,255,255,0.06);color:#6b6b73;font-size:11.5px;line-height:1.6;">
      PurchasingCorp · Cash for phones, laptops & consoles — same day.<br>
      This email was sent because you started a quote at
      <a href="${siteUrl()}" style="color:#9a9aa2;">purchasingcorp.com</a>.
    </div>
  </div>
</body></html>`;
}

function button(href, label) {
  return `<a href="${href}" style="display:inline-block;background:#ffffff;color:#000000;text-decoration:none;font-weight:600;font-size:13.5px;padding:10px 18px;border-radius:9999px;">${label}</a>`;
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Confirmation the customer receives right after submitting a quote.
export function inquiryConfirmationEmail({ device, condition, offer }) {
  const acct = `${siteUrl()}/account`;
  const line = offer
    ? `Our first offer for your <strong style="color:#e6e6e8;">${esc(device)}</strong> is <strong style="color:#e6e6e8;">$${esc(offer)}</strong> as described. Clean photos and accurate condition can push it higher.`
    : `We've got your <strong style="color:#e6e6e8;">${esc(device)}</strong> and we're confirming the offer now. You'll hear back fast — usually within the hour.`;
  const inner = `
    <p style="margin:0 0 14px;color:#e6e6e8;font-size:16px;font-weight:600;">We got your quote request.</p>
    <p style="margin:0 0 16px;">${line}</p>
    ${condition ? `<p style="margin:0 0 16px;color:#9a9aa2;font-size:13px;">Condition you selected: ${esc(condition)}</p>` : ''}
    <p style="margin:0 0 22px;">You can track this quote, chat with us, and accept your offer from your dashboard.</p>
    <p style="margin:0 0 6px;">${button(acct, 'View my quote')}</p>`;
  return {
    subject: `We got your quote — ${device}`,
    html: shell(inner),
    text: `We got your quote request for ${device}. ${offer ? `First offer: $${offer}.` : 'We are confirming your offer now.'} Track it and chat with us at ${acct}`,
  };
}

// Sent to the customer when an admin replies in their inquiry thread.
export function adminReplyEmail({ device, snippet }) {
  const acct = `${siteUrl()}/account`;
  const inner = `
    <p style="margin:0 0 14px;color:#e6e6e8;font-size:16px;font-weight:600;">New reply about your ${esc(device)}</p>
    <div style="margin:0 0 20px;padding:14px 16px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:12px;color:#e6e6e8;font-size:14px;white-space:pre-wrap;">${esc(snippet)}</div>
    <p style="margin:0 0 22px;">Reply back and see the full conversation on your dashboard.</p>
    <p style="margin:0 0 6px;">${button(acct, 'Open the conversation')}</p>`;
  return {
    subject: `PurchasingCorp replied about your ${device}`,
    html: shell(inner),
    text: `New reply about your ${device}:\n\n${snippet}\n\nReply from your dashboard: ${acct}`,
  };
}

// Owner-copy: sent to OWNER_EMAIL when a customer replies in a thread.
export function customerReplyEmail({ device, contact, snippet }) {
  const admin = `${siteUrl()}/admin`;
  const inner = `
    <p style="margin:0 0 14px;color:#e6e6e8;font-size:16px;font-weight:600;">Customer replied — ${esc(device)}</p>
    ${contact ? `<p style="margin:0 0 12px;color:#9a9aa2;font-size:13px;">${esc(contact)}</p>` : ''}
    <div style="margin:0 0 20px;padding:14px 16px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:12px;color:#e6e6e8;font-size:14px;white-space:pre-wrap;">${esc(snippet)}</div>
    <p style="margin:0 0 6px;">${button(admin, 'Reply in admin')}</p>`;
  return {
    subject: `Customer replied — ${device}`,
    html: shell(inner),
    text: `Customer replied about ${device}${contact ? ` (${contact})` : ''}:\n\n${snippet}\n\nReply in admin: ${admin}`,
  };
}
