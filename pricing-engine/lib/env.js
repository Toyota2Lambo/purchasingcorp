'use strict';
// ============================================================
// Tiny .env loader, zero dependencies (repo has no build step).
// Loads pricing-engine/.env into process.env if present. Existing
// process.env values win (so CI secrets are never overwritten).
// In CI you don't need a .env at all, the workflow injects secrets.
// ============================================================

const fs = require('fs');
const path = require('path');

function loadEnv(file) {
  const envPath = file || path.join(__dirname, '..', '.env');
  if (!fs.existsSync(envPath)) return false;

  // Prefer Node's native loader when available (Node 20.6+/21.7+).
  if (typeof process.loadEnvFile === 'function') {
    try {
      // Native loader does NOT override already-set vars.
      process.loadEnvFile(envPath);
      return true;
    } catch (_) {
      /* fall through to manual parse */
    }
  }

  const text = fs.readFileSync(envPath, 'utf8');
  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let val = line.slice(eq + 1).trim();
    // strip matching surrounding quotes
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    if (!(key in process.env)) process.env[key] = val;
  }
  return true;
}

module.exports = { loadEnv };
