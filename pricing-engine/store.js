'use strict';
// ============================================================
// Raw result storage. Every run is written with a timestamp so you
// keep an auditable history of what the market said and what we
// offered. JSON files for now (no DB dependency); the migration
// plan (Supabase price_points / price_runs) can replace this later
// without touching the rest of the engine.
// ============================================================

const fs = require('fs');
const path = require('path');

const DATA_DIR = path.join(__dirname, 'data');
const RAW_DIR = path.join(DATA_DIR, 'raw');

function ensureDirs() {
  fs.mkdirSync(RAW_DIR, { recursive: true });
}

/** Filesystem-safe ISO timestamp, e.g. 2026-06-09T14-03-22-101Z */
function stamp(d = new Date()) {
  return d.toISOString().replace(/:/g, '-').replace(/\./g, '-');
}

/**
 * Persist a full run. `run` should include { startedAt, finishedAt,
 * config snapshot, variants:[{variantKey, summary, sources, offers}] }.
 * Writes data/raw/<stamp>.json AND overwrites data/latest.json.
 */
function writeRun(run) {
  ensureDirs();
  const ts = stamp(new Date(run.finishedAt || Date.now()));
  const rawPath = path.join(RAW_DIR, `${ts}.json`);
  const latestPath = path.join(DATA_DIR, 'latest.json');
  const payload = JSON.stringify(run, null, 2);
  fs.writeFileSync(rawPath, payload);
  fs.writeFileSync(latestPath, payload);
  return { rawPath, latestPath };
}

function readLatest() {
  const latestPath = path.join(DATA_DIR, 'latest.json');
  if (!fs.existsSync(latestPath)) return null;
  return JSON.parse(fs.readFileSync(latestPath, 'utf8'));
}

module.exports = { writeRun, readLatest, DATA_DIR, RAW_DIR, stamp };
