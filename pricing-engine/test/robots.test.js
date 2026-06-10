'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { parseRobots, selectGroup, isAllowedByRules } = require('../lib/robots');

const decide = (txt, ua, path) => isAllowedByRules(selectGroup(parseRobots(txt), ua), path);

test('Disallow: / blocks everything for *', () => {
  const txt = 'User-agent: *\nDisallow: /';
  assert.equal(decide(txt, 'AnyBot', '/sell/iphone'), false);
  assert.equal(decide(txt, 'AnyBot', '/'), false);
});

test('empty Disallow allows everything', () => {
  const txt = 'User-agent: *\nDisallow:';
  assert.equal(decide(txt, 'AnyBot', '/anything'), true);
});

test('Allow overrides a broader Disallow (longest match, allow wins ties)', () => {
  const txt = 'User-agent: *\nDisallow: /sell\nAllow: /sell/public';
  assert.equal(decide(txt, 'Bot', '/sell/private'), false);
  assert.equal(decide(txt, 'Bot', '/sell/public/iphone'), true);
});

test('a specific user-agent group beats the * group', () => {
  const txt = [
    'User-agent: *',
    'Disallow:',
    '',
    'User-agent: PurchasingCorpPriceBot',
    'Disallow: /sell',
  ].join('\n');
  // our bot matches the specific (more restrictive) group
  assert.equal(decide(txt, 'PurchasingCorpPriceBot/1.0', '/sell/x'), false);
  // an unrelated bot uses '*' (allowed)
  assert.equal(decide(txt, 'GoogleBot', '/sell/x'), true);
});

test('wildcard * in a path pattern', () => {
  const txt = 'User-agent: *\nDisallow: /*.json$';
  assert.equal(decide(txt, 'Bot', '/api/data.json'), false);
  assert.equal(decide(txt, 'Bot', '/api/data.html'), true);
});

test('$ anchors the end of the path', () => {
  const txt = 'User-agent: *\nDisallow: /sell$';
  assert.equal(decide(txt, 'Bot', '/sell'), false);
  assert.equal(decide(txt, 'Bot', '/sell/iphone'), true); // not an exact end match
});
