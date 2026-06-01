#!/usr/bin/env node
// ============================================================
// PURCHASINGCORP — renderer
// ============================================================
// Turns a content.json (the unified model) into PNGs with Puppeteer,
// then writes a manifest.json the publisher reads.
//
//   posts[]   -> one PNG per slide  (1 slide = single image, 2+ = carousel)
//   stories[] -> one PNG each (1080x1920)
//
// Why we render from a temp file instead of page.setContent: each
// template links _shared.css relatively (<link href="_shared.css">).
// That only resolves if the HTML is loaded from a file that sits next
// to a real _shared.css. So we write each filled template into a temp
// dir that contains a copy of _shared.css and load it over file://.
//
// Output naming (also recorded in the manifest, so the publisher never
// has to guess):
//   post-<NN>-slide-<MM>.png
//   story-<NN>.png
//
// Usage:
//   node social/renderer.js                         # today's content.json
//   node social/renderer.js --content social/2026-06-01/content.json
//   node social/renderer.js --sample                # render the fixture to social/_sample/
// ============================================================

'use strict';

const fs = require('fs');
const path = require('path');
const registry = require('./templates-registry');

const HERE = __dirname;
const ROOT = path.join(HERE, '..');
const TEMPLATES_DIR = path.join(HERE, 'templates');
const SHARED_CSS = path.join(TEMPLATES_DIR, '_shared.css');

const SIZES = {
  feed: { width: 1080, height: 1080 },
  story: { width: 1080, height: 1920 },
};
const SCALE = Number(process.env.RENDER_SCALE || 2); // device pixel ratio; 1 => exactly 1080px PNGs

function sanitizeSize(s) {
  return s === 'story' ? 'story' : 'feed';
}

function pad2(n) {
  return String(n).padStart(2, '0');
}

// Substitute {{key}}. Keys ending in _html are inserted raw (already
// made safe by registry.expandFields); everything else is escaped.
function fillTemplate(html, fields, size) {
  const merged = Object.assign({}, fields, { size: sanitizeSize(size) });
  return html.replace(/\{\{\s*([a-zA-Z0-9_]+)\s*\}\}/g, (_m, key) => {
    if (key === 'size') return sanitizeSize(merged.size);
    const v = merged[key];
    if (v == null) return '';
    return key.endsWith('_html') ? String(v) : registry.esc(String(v));
  });
}

function templateHtml(name) {
  const spec = registry.specFor(name);
  return fs.readFileSync(path.join(TEMPLATES_DIR, spec.file), 'utf8');
}

function parseArgs(argv) {
  const args = { content: null, sample: false, outDir: null };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--sample') args.sample = true;
    else if (a === '--content') args.content = argv[++i];
    else if (a === '--out-dir') args.outDir = argv[++i];
    else if (!a.startsWith('--') && !args.content) args.content = a;
  }
  return args;
}

function resolveContentPath(args) {
  if (args.sample) return path.join(HERE, 'sample-payloads.json');
  if (args.content) return path.resolve(args.content);
  const today = new Date().toISOString().slice(0, 10);
  return path.join(HERE, today, 'content.json');
}

async function main() {
  const args = parseArgs(process.argv);
  const contentPath = resolveContentPath(args);
  if (!fs.existsSync(contentPath)) {
    console.error(`[renderer] content file not found: ${contentPath}`);
    process.exit(1);
  }

  const pkg = JSON.parse(fs.readFileSync(contentPath, 'utf8'));
  const content = pkg.content || pkg; // tolerate a bare {posts,stories}
  const posts = content.posts || [];
  const stories = content.stories || [];

  const outDir = args.outDir
    ? path.resolve(args.outDir)
    : (args.sample ? path.join(HERE, '_sample') : path.dirname(contentPath));
  fs.mkdirSync(outDir, { recursive: true });

  // temp render dir holding a copy of _shared.css so relative <link> resolves
  const tmpDir = path.join(outDir, '.render-tmp');
  fs.mkdirSync(tmpDir, { recursive: true });
  fs.copyFileSync(SHARED_CSS, path.join(tmpDir, '_shared.css'));

  let puppeteer;
  try {
    puppeteer = require('puppeteer');
  } catch (e) {
    console.error('[renderer] puppeteer is required. Run `npm install` in social/.');
    process.exit(1);
  }

  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--font-render-hinting=none'],
  });
  const page = await browser.newPage();
  page.on('pageerror', (err) => console.warn('[renderer] page error:', err.message));

  let counter = 0;
  async function renderOne(template, fields, size, outFile) {
    counter += 1;
    const placeholders = registry.expandFields(template, fields || {});
    const html = fillTemplate(templateHtml(template), placeholders, size);
    const tmpHtml = path.join(tmpDir, `render-${counter}.html`);
    fs.writeFileSync(tmpHtml, html);

    const dims = SIZES[sanitizeSize(size)];
    await page.setViewport({ width: dims.width, height: dims.height, deviceScaleFactor: SCALE });
    await page.goto('file://' + tmpHtml, { waitUntil: 'networkidle0', timeout: 60000 });
    try { await page.evaluate(() => (document.fonts ? document.fonts.ready : null)); } catch (e) { /* noop */ }
    await new Promise((r) => setTimeout(r, 180)); // let the final paint settle
    await page.screenshot({ path: outFile, type: 'png' });
    console.log(`[renderer] ${path.basename(outFile)}  <-  ${template} (${sanitizeSize(size)})`);
  }

  const manifest = {
    rendered_at: new Date().toISOString(),
    content_file: path.relative(ROOT, contentPath),
    base_path: path.relative(ROOT, outDir),
    scale: SCALE,
    posts: [],
    stories: [],
  };

  // ---- posts ----
  for (let pi = 0; pi < posts.length; pi++) {
    const post = posts[pi];
    const size = sanitizeSize(post.size);
    const slides = post.slides || [];
    const files = [];
    for (let si = 0; si < slides.length; si++) {
      const file = `post-${pad2(pi + 1)}-slide-${pad2(si + 1)}.png`;
      await renderOne(slides[si].template, slides[si].fields, size, path.join(outDir, file));
      files.push(file);
    }
    manifest.posts.push({
      index: pi + 1,
      role: post.role || (slides[0] && slides[0].template) || 'post',
      caption: post.caption || '',
      hashtags: post.hashtags || [],
      size,
      is_carousel: files.length > 1,
      files,
    });
  }

  // ---- stories ----
  for (let i = 0; i < stories.length; i++) {
    const story = stories[i];
    const file = `story-${pad2(i + 1)}.png`;
    await renderOne(story.template, story.fields, 'story', path.join(outDir, file));
    manifest.stories.push({ index: i + 1, template: story.template, file });
  }

  await browser.close();

  // cleanup temp render dir
  try {
    for (const f of fs.readdirSync(tmpDir)) fs.unlinkSync(path.join(tmpDir, f));
    fs.rmdirSync(tmpDir);
  } catch (e) { /* noop */ }

  const manifestPath = path.join(outDir, 'manifest.json');
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
  console.log(`[renderer] wrote ${path.relative(ROOT, manifestPath)} — `
    + `${manifest.posts.length} posts, ${manifest.stories.length} stories`);
}

main().catch((err) => {
  console.error('[renderer] fatal:', err);
  process.exit(1);
});
