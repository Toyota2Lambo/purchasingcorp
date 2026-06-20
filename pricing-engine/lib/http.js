'use strict';
// ============================================================
// Fetch helpers, built on Node 18+ global fetch (no deps).
// Adds timeout, a default User-Agent, JSON parsing, and a couple
// of polite retries on 429/5xx with backoff.
// ============================================================

const DEFAULT_UA = 'PurchasingCorpPriceBot/1.0 (+https://purchasingcorp.com/bot)';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function fetchWithTimeout(url, opts = {}) {
  const { timeoutMs = 15000, ...rest } = opts;
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...rest, signal: ctrl.signal });
  } finally {
    clearTimeout(t);
  }
}

/** GET text with retries on transient failures. */
async function getText(url, opts = {}) {
  const { retries = 2, userAgent = DEFAULT_UA, headers = {}, ...rest } = opts;
  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const res = await fetchWithTimeout(url, {
        ...rest,
        headers: { 'user-agent': userAgent, accept: '*/*', ...headers },
      });
      if (res.status === 429 || res.status >= 500) {
        lastErr = new Error(`HTTP ${res.status} for ${url}`);
        await sleep(500 * (attempt + 1));
        continue;
      }
      const body = await res.text();
      return { ok: res.ok, status: res.status, body, headers: res.headers };
    } catch (e) {
      lastErr = e;
      await sleep(500 * (attempt + 1));
    }
  }
  throw lastErr || new Error(`failed to GET ${url}`);
}

/** GET + JSON.parse. Throws on non-2xx or invalid JSON. */
async function getJson(url, opts = {}) {
  const r = await getText(url, { ...opts, headers: { accept: 'application/json', ...(opts.headers || {}) } });
  if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}: ${r.body.slice(0, 200)}`);
  return JSON.parse(r.body);
}

module.exports = { fetchWithTimeout, getText, getJson, DEFAULT_UA, sleep };
