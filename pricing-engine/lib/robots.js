'use strict';
// ============================================================
// Minimal robots.txt fetcher + evaluator (no deps).
//
// Implements the parts that matter for polite scraping:
//   - User-agent group selection (specific match beats '*')
//   - Allow / Disallow with longest-match precedence (Allow wins ties)
//   - '*' wildcards and '$' end-anchors in paths
//
// Fetch policy (conservative, honours "skip any site that disallows"):
//   - robots.txt 2xx        -> parse and obey
//   - robots.txt 4xx/404    -> treat as ALLOW ALL (standard convention)
//   - 5xx / network failure -> treat as DISALLOW ALL (fail safe -> skip)
// ============================================================

const { getText } = require('./http');

function parseRobots(text) {
  const groups = [];
  let current = null;
  let lastWasAgent = false;

  for (const rawLine of String(text || '').split('\n')) {
    const line = rawLine.replace(/#.*$/, '').trim();
    if (!line) continue;
    const idx = line.indexOf(':');
    if (idx === -1) continue;
    const field = line.slice(0, idx).trim().toLowerCase();
    const value = line.slice(idx + 1).trim();

    if (field === 'user-agent') {
      if (!current || !lastWasAgent) {
        current = { agents: [], rules: [] };
        groups.push(current);
      }
      current.agents.push(value.toLowerCase());
      lastWasAgent = true;
    } else if (field === 'allow' || field === 'disallow') {
      if (!current) {
        current = { agents: ['*'], rules: [] };
        groups.push(current);
      }
      current.rules.push({ type: field, path: value });
      lastWasAgent = false;
    } else {
      lastWasAgent = false; // sitemap, crawl-delay, etc. ignored
    }
  }
  return groups;
}

/** Select the rules for the best-matching user-agent (specific > '*'). */
function selectGroup(groups, userAgent) {
  const ua = String(userAgent || '*').toLowerCase();
  let best = null;
  let bestLen = -1;
  for (const g of groups) {
    for (const agent of g.agents) {
      const isStar = agent === '*';
      const matches = isStar || ua.includes(agent);
      if (!matches) continue;
      const len = isStar ? 0 : agent.length;
      if (len > bestLen) {
        bestLen = len;
        best = g;
      }
    }
  }
  return best ? best.rules : [];
}

/** Turn a robots path pattern into a RegExp (handles '*' and '$'). */
function patternToRegex(pattern) {
  // Escape regex specials except * and $, then map.
  let re = '';
  for (let i = 0; i < pattern.length; i++) {
    const c = pattern[i];
    if (c === '*') re += '.*';
    else if (c === '$' && i === pattern.length - 1) re += '$';
    else re += c.replace(/[.+?^${}()|[\]\\]/g, '\\$&');
  }
  return new RegExp('^' + re);
}

/** Length used for longest-match (ignore wildcards/anchors). */
function matchLength(pattern) {
  return pattern.replace(/[*$]/g, '').length;
}

function isAllowedByRules(rules, pathname) {
  let decision = true; // default allow
  let decidedLen = -1;
  for (const r of rules) {
    if (r.path === '') {
      // "Disallow:" (empty) = allow everything for this group; lowest priority.
      if (r.type === 'disallow' && decidedLen < 0) decision = true;
      continue;
    }
    let matched = false;
    try {
      matched = patternToRegex(r.path).test(pathname);
    } catch (_) {
      matched = pathname.startsWith(r.path);
    }
    if (!matched) continue;
    const len = matchLength(r.path);
    if (len > decidedLen || (len === decidedLen && r.type === 'allow')) {
      decidedLen = len;
      decision = r.type === 'allow';
    }
  }
  return decision;
}

/**
 * Fetch and evaluate robots.txt for a site. Returns a checker:
 *   { allowAll, denyAll, isAllowed(urlOrPath) }
 */
async function fetchRobots(baseUrl, userAgent, opts = {}) {
  let robotsUrl;
  try {
    robotsUrl = new URL('/robots.txt', baseUrl).href;
  } catch (_) {
    return makeChecker({ mode: 'denyAll', userAgent, baseUrl });
  }

  try {
    const res = await getText(robotsUrl, { userAgent, retries: 1, timeoutMs: 10000 });
    if (res.status >= 200 && res.status < 300) {
      const groups = parseRobots(res.body);
      const rules = selectGroup(groups, userAgent);
      return makeChecker({ mode: 'rules', rules, userAgent, baseUrl });
    }
    if (res.status >= 400 && res.status < 500) {
      return makeChecker({ mode: 'allowAll', userAgent, baseUrl });
    }
    return makeChecker({ mode: 'denyAll', userAgent, baseUrl }); // 5xx -> fail safe
  } catch (_) {
    return makeChecker({ mode: 'denyAll', userAgent, baseUrl }); // unreachable -> fail safe
  }
}

function makeChecker({ mode, rules = [], userAgent, baseUrl }) {
  const toPath = (urlOrPath) => {
    try {
      return new URL(urlOrPath, baseUrl).pathname || '/';
    } catch (_) {
      return String(urlOrPath || '/');
    }
  };
  return {
    mode,
    userAgent,
    allowAll: mode === 'allowAll',
    denyAll: mode === 'denyAll',
    isAllowed(urlOrPath) {
      if (mode === 'allowAll') return true;
      if (mode === 'denyAll') return false;
      return isAllowedByRules(rules, toPath(urlOrPath));
    },
  };
}

module.exports = { parseRobots, selectGroup, isAllowedByRules, patternToRegex, fetchRobots, makeChecker };
