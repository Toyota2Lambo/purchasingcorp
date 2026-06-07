#!/usr/bin/env node
// Renders social/og-image.html -> og-image.new.png at 2x (2400x1260).
// A separate `sips` pass downscales it to the final 1200x630 og-image.png,
// which supersamples the text for crisp edges at the declared og dimensions.
'use strict';

const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer');

const HTML = 'file://' + path.join(__dirname, 'og-image.html');
const OUT = path.join(__dirname, '..', 'og-image.new.png');
const SCALE = 2;

// The cached Chrome-for-Testing binary fails to spawn on this machine, so
// prefer the system-installed Google Chrome when present.
const SYS_CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

(async () => {
  const launchOpts = {
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--font-render-hinting=none'],
  };
  if (process.env.PUPPETEER_EXECUTABLE_PATH) launchOpts.executablePath = process.env.PUPPETEER_EXECUTABLE_PATH;
  else if (fs.existsSync(SYS_CHROME)) launchOpts.executablePath = SYS_CHROME;

  const browser = await puppeteer.launch(launchOpts);
  const page = await browser.newPage();
  page.on('pageerror', (e) => console.warn('[og] page error:', e.message));
  await page.setViewport({ width: 1200, height: 630, deviceScaleFactor: SCALE });
  await page.goto(HTML, { waitUntil: 'networkidle0', timeout: 60000 });
  try { await page.evaluate(() => (document.fonts ? document.fonts.ready : null)); } catch (e) { /* noop */ }
  await new Promise((r) => setTimeout(r, 200));
  await page.screenshot({ path: OUT, type: 'png' });
  await browser.close();
  console.log('[og] wrote', OUT);
})().catch((e) => { console.error('[og] fatal:', e); process.exit(1); });
