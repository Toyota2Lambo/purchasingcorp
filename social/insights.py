#!/usr/bin/env python3
"""
Collect performance snapshots for every post in videos/posts_ledger.json.

Each ledger row gets up to three checkpoints under row["insights"]:
  1d  — due once the post is ≥20h old (a daily cron slot can't miss it)
  3d  — due at ≥72h
  7d  — due at ≥168h
A snapshot is {at, views, reach, likes, comments, saved, shares}. Filled
checkpoints are never re-fetched (idempotent) unless --force.

Platforms:
  instagram : GET /{id}/insights?metric=views,reach,likes,comments,saved,
              shares,total_interactions — retries with `plays` when the API
              rejects `views` with error #100 (metric churn across versions)
  threads   : GET /{id}/insights?metric=views,likes,replies,reposts,quotes,shares
  x         : GET /2/tweets/{id}?tweet.fields=public_metrics (OAuth1 via
              xauth.py) — fetched ONLY at the 7d checkpoint: the free tier
              allows ~100 reads/month, so each post gets exactly one read
  tiktok    : skipped (no insights API wired yet)

Also backfills row["permalink"] once for IG/Threads posts that missed it.

Env: same tokens as video_publisher.py (IG_ACCESS_TOKEN, THREADS_ACCESS_TOKEN,
TWITTER_*); POSTS_LEDGER overrides the ledger path (fixtures/testing).

Usage:
  python social/insights.py               # fetch everything due
  python social/insights.py --dry-run     # print the fetch plan, no network
  python social/insights.py --force       # re-fetch even filled checkpoints
  python social/insights.py --probe <id>          # one-off fetch (tries IG, then Threads)
  python social/insights.py --probe threads:<id>  # explicit platform (ig/threads/x)

Exit codes: 0 ok (per-post fetch errors are reported, not fatal),
2 auth/permission error — fix or re-authorize the token, then re-run.
"""
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import xauth

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LEDGER = os.environ.get("POSTS_LEDGER") or os.path.join(REPO, "videos", "posts_ledger.json")
UA = "PurchasingCorp-Social/1.0"

IG_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
IG_GRAPH = f"https://graph.instagram.com/{os.environ.get('IG_API_VERSION', 'v21.0')}"
TH_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
TH_GRAPH = f"https://graph.threads.net/{os.environ.get('THREADS_API_VERSION', 'v1.0')}"
TW_TWEET = "https://api.twitter.com/2/tweets"

# checkpoint → hours after posted_at when it becomes due (1d fires early so
# the daily cron slot can't perpetually miss it by minutes)
CHECKPOINTS = (("1d", 20.0), ("3d", 72.0), ("7d", 168.0))

IG_METRICS = "views,reach,likes,comments,saved,shares,total_interactions"
TH_METRICS = "views,likes,replies,reposts,quotes,shares"


class AuthError(Exception):
    """Expired/denied token or missing insights scope — retrying is pointless."""

# ─────────────────────────── HTTP ───────────────────────────

def http(method, url, headers=None, data=None, timeout=60):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}
    except Exception as e:
        return 0, {"error": str(e)}

# ─────────────────────────── time / ledger ───────────────────────────

def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_hours(posted_at):
    try:
        dt = datetime.fromisoformat(str(posted_at).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def load_ledger():
    try:
        return json.load(open(LEDGER))
    except Exception:
        return []


def save_ledger(rows):
    d = os.path.dirname(LEDGER)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".ledger-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(rows, fh, indent=2)
    os.replace(tmp, LEDGER)

# ─────────────────────────── Meta helpers ───────────────────────────

def _meta_error(d):
    return (d or {}).get("error") or {}


def _is_meta_auth(status, d):
    """190 = bad/expired token; 102 = session; 10 & 200–299 = missing scope
    (e.g. instagram_business_manage_insights / threads_manage_insights)."""
    code = _meta_error(d).get("code")
    return status == 401 or code in (190, 102, 10) or (isinstance(code, int) and 200 <= code <= 299)


def _insights_map(d):
    """Flatten a Graph insights payload to {metric: value} — handles both the
    values[] shape and the newer total_value shape."""
    out = {}
    for item in (d.get("data") or []):
        name = item.get("name")
        tv = item.get("total_value")
        if isinstance(tv, dict) and "value" in tv:
            out[name] = tv["value"]
            continue
        vals = item.get("values") or []
        if vals and isinstance(vals[0], dict) and "value" in vals[0]:
            out[name] = vals[0]["value"]
    return out

# ─────────────────────────── per-platform fetchers ───────────────────────────
# each returns (snapshot, None) or (None, "reason"); raises AuthError

def fetch_instagram(pid):
    if not IG_TOKEN:
        return None, "IG_ACCESS_TOKEN not set"
    metrics = IG_METRICS
    for attempt in (1, 2):
        s, d = http("GET", f"{IG_GRAPH}/{pid}/insights?metric={metrics}&access_token={IG_TOKEN}",
                    {"User-Agent": UA})
        if s < 300:
            m = _insights_map(d)
            return {"at": _now_iso(),
                    "views": m.get("views", m.get("plays")),
                    "reach": m.get("reach"),
                    "likes": m.get("likes"),
                    "comments": m.get("comments"),
                    "saved": m.get("saved"),
                    "shares": m.get("shares")}, None
        if _is_meta_auth(s, d):
            raise AuthError(f"instagram ({s}): {_meta_error(d).get('message') or d}")
        # metric churn: some accounts/API versions want `plays`, not `views`
        if attempt == 1 and _meta_error(d).get("code") == 100 and "views" in metrics:
            metrics = metrics.replace("views", "plays")
            continue
        return None, f"({s}) {_meta_error(d).get('message') or d}"


def fetch_threads(pid):
    if not TH_TOKEN:
        return None, "THREADS_ACCESS_TOKEN not set"
    s, d = http("GET", f"{TH_GRAPH}/{pid}/insights?metric={TH_METRICS}&access_token={TH_TOKEN}",
                {"User-Agent": UA})
    if _is_meta_auth(s, d):
        raise AuthError(f"threads ({s}): {_meta_error(d).get('message') or d}")
    if s >= 300:
        return None, f"({s}) {_meta_error(d).get('message') or d}"
    m = _insights_map(d)
    # reposts + quotes + native shares all count as "shares" for scoring
    shares = sum(m.get(k) or 0 for k in ("reposts", "quotes", "shares"))
    return {"at": _now_iso(), "views": m.get("views"), "reach": None,
            "likes": m.get("likes"), "comments": m.get("replies"),
            "saved": None, "shares": shares}, None


def fetch_x(pid):
    if not xauth.have_creds():
        return None, "TWITTER_* credentials not set"
    params = {"tweet.fields": "public_metrics"}
    url = f"{TW_TWEET}/{pid}"
    s, d = http("GET", url + "?" + urllib.parse.urlencode(params),
                {"Authorization": xauth.oauth_header("GET", url, params), "User-Agent": UA})
    if s == 401:
        raise AuthError(f"x (401): {d}")
    if s >= 300:
        return None, f"({s}) {d}"     # 403/429 = tier limits, report not fatal
    pm = (d.get("data") or {}).get("public_metrics") or {}
    return {"at": _now_iso(), "views": pm.get("impression_count"), "reach": None,
            "likes": pm.get("like_count"), "comments": pm.get("reply_count"),
            "saved": pm.get("bookmark_count"),
            "shares": (pm.get("retweet_count") or 0) + (pm.get("quote_count") or 0)}, None


FETCHERS = {"instagram": fetch_instagram, "threads": fetch_threads, "x": fetch_x}

# ─────────────────────────── plan / backfill ───────────────────────────

def due_checkpoints(row, force=False):
    age = _age_hours(row.get("posted_at", ""))
    if age is None:
        return []
    ins = row.get("insights") or {}
    due = []
    for cp, hours in CHECKPOINTS:
        if row.get("platform") == "x" and cp != "7d":
            continue                    # X read budget: one read per post, at 7d
        if age >= hours and (force or not ins.get(cp)):
            due.append(cp)
    return due


def backfill_permalink(row):
    """One-time best-effort permalink fill for IG/Threads rows that missed it."""
    plat, pid = row.get("platform"), row.get("post_id")
    if row.get("permalink") or not pid:
        return False
    if plat == "instagram" and IG_TOKEN:
        s, d = http("GET", f"{IG_GRAPH}/{pid}?fields=permalink&access_token={IG_TOKEN}",
                    {"User-Agent": UA})
    elif plat == "threads" and TH_TOKEN:
        s, d = http("GET", f"{TH_GRAPH}/{pid}?fields=permalink&access_token={TH_TOKEN}",
                    {"User-Agent": UA})
    else:
        return False
    if s < 300 and d.get("permalink"):
        row["permalink"] = d["permalink"]
        return True
    return False

# ─────────────────────────── probe / main ───────────────────────────

def probe(arg):
    """One-off read-only insights fetch, e.g. `--probe 1790…` or
    `--probe threads:1790…` — the scope smoke test the plan calls for."""
    plat, _, pid = arg.partition(":")
    if not pid:
        plat, pid = "", plat
    alias = {"ig": "instagram", "tw": "x", "twitter": "x"}
    tries = [alias.get(plat, plat)] if plat else ["instagram", "threads"]
    for p in tries:
        fn = FETCHERS.get(p)
        if not fn:
            print(f"✗ unknown platform '{p}' (use ig/instagram, threads, x)")
            return 1
        try:
            snap, err = fn(pid)
        except AuthError as e:
            print(f"✗ AUTH — {e}")
            return 2
        if snap is not None:
            print(f"✓ {p} {pid}:")
            print(json.dumps(snap, indent=2))
            return 0
        print(f"– {p}: {err}")
    return 0


def main():
    args = sys.argv[1:]
    if "--probe" in args:
        i = args.index("--probe")
        if i + 1 >= len(args):
            print("✗ --probe needs a media id (optionally platform-prefixed, e.g. threads:123)")
            return 1
        return probe(args[i + 1])
    dry = "--dry-run" in args
    force = "--force" in args

    rows = load_ledger()
    if not rows:
        print(f"📭 no ledger at {LEDGER} — nothing to do")
        return 0

    plan, skipped = [], 0
    for row in rows:
        if row.get("platform") not in FETCHERS:
            skipped += 1                # e.g. tiktok — no insights API wired
            continue
        for cp in due_checkpoints(row, force):
            plan.append((row, cp))
    if skipped:
        print(f"– {skipped} row(s) on platforms without insights (tiktok) skipped")
    if not plan:
        print("✓ nothing due — every checkpoint is filled or still too fresh")
        return 0

    print(f"📈 {len(plan)} checkpoint(s) due across {len({id(r) for r, _ in plan})} post(s)"
          + (" — DRY RUN, no network" if dry else ""))
    changed, auth_fail, dead = False, False, set()
    for row, cp in plan:
        plat, pid = row.get("platform"), row.get("post_id")
        tag = f"{row.get('date', '?')} {row.get('file', '')[:44]} {plat}/{cp}"
        if dry:
            print(f"  • would fetch {tag}  (post {pid})")
            continue
        if plat in dead:
            continue
        try:
            snap, err = FETCHERS[plat](pid)
        except AuthError as e:
            print(f"  ✗ {tag}: AUTH — {e}")
            auth_fail = True
            dead.add(plat)              # don't hammer a dead token
            continue
        if snap is None:
            print(f"  ✗ {tag}: {err}")
            continue
        row.setdefault("insights", {})[cp] = snap
        changed = True
        print(f"  ✓ {tag}: views={snap.get('views')} likes={snap.get('likes')} "
              f"comments={snap.get('comments')}")
        if backfill_permalink(row):
            changed = True
        time.sleep(0.5)
    if changed and not dry:
        save_ledger(rows)
        print(f"💾 ledger updated → {LEDGER}")
    return 2 if auth_fail else 0


if __name__ == "__main__":
    sys.exit(main())
