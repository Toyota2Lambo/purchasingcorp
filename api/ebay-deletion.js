export const config = { runtime: 'edge' };

// ============================================================
// eBay Marketplace Account Deletion / Closure Notification endpoint.
//
// eBay REQUIRES every Production keyset to register an HTTPS endpoint
// here. This one does the minimum the policy demands:
//
//   1) GET  ?challenge_code=...  -> ownership verification.
//      eBay hits this when you click "Save/Verify" in the developer
//      portal. We must return HTTP 200 + JSON:
//        { "challengeResponse": sha256(challengeCode + token + endpoint) }
//      (the three values concatenated IN THAT ORDER, hex digest).
//
//   2) POST <deletion notification>  -> runtime notices.
//      When an eBay user closes their account, eBay POSTs here so apps
//      can purge that user's data. WE STORE NO eBay USER DATA (the
//      pricing engine only reads public market comps), so there is
//      nothing to delete, we just acknowledge with 200.
//
// Required env (Vercel -> Project -> Settings -> Environment Variables):
//   EBAY_VERIFICATION_TOKEN   the 32-80 char token you also paste into
//                             the eBay form (alphanumeric, _ and - only)
//   EBAY_DELETION_ENDPOINT    the EXACT URL you register with eBay, e.g.
//                             https://www.purchasingcorp.com/api/ebay-deletion
//                             (must match byte-for-byte or the hash fails)
// ============================================================

async function sha256Hex(str) {
  const data = new TextEncoder().encode(str);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

export default async function handler(req) {
  const token = process.env.EBAY_VERIFICATION_TOKEN || '';
  const url = new URL(req.url);

  // The endpoint string hashed must be EXACTLY what's registered with eBay.
  // Prefer the explicit env value; fall back to this request's own origin+path.
  const endpoint = process.env.EBAY_DELETION_ENDPOINT || `${url.origin}${url.pathname}`;

  // ---- 1) Ownership challenge -------------------------------------------
  if (req.method === 'GET') {
    const challengeCode = url.searchParams.get('challenge_code');
    if (!challengeCode) {
      return json({ ok: false, error: 'missing challenge_code' }, 400);
    }
    if (!token) {
      // Misconfiguration: fail loudly so you notice before pasting into eBay.
      return json({ ok: false, error: 'EBAY_VERIFICATION_TOKEN not set' }, 500);
    }
    const challengeResponse = await sha256Hex(challengeCode + token + endpoint);
    return json({ challengeResponse });
  }

  // ---- 2) Deletion notification (acknowledge; nothing stored) ------------
  if (req.method === 'POST') {
    // We intentionally do NOT persist or log the payload, it contains an
    // eBay user's username/userId, and we keep no eBay user data to purge.
    // eBay only needs a prompt 2xx to consider the notice delivered.
    return new Response(null, { status: 200 });
  }

  return json({ ok: false, error: 'method not allowed' }, 405);
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  });
}
