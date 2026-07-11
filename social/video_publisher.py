#!/usr/bin/env python3
"""
Publish ONE vault video (chosen by pick_daily_video.py) to every platform.

  Instagram : Reels   — create REELS container (video_url), poll, publish
  Threads   : Video    — create VIDEO container (video_url), poll, publish
  X / Twitter: chunked v1.1 media upload (tweet_video) → v2 tweet with media_id
  TikTok    : Content Posting API, PULL_FROM_URL  (DORMANT until a token is set)

IG / Threads / TikTok FETCH the public video_url (purchasingcorp.com/videos/...),
so the day's clip must be committed + Vercel-deployed first. X uploads the bytes
from the local checkout, like the image publisher.

Reads videos/selection.json by default. stdlib only.

Every real success is appended to videos/posts_ledger.json ({date, file,
platform, post_id, permalink, posted_at}) so insights.py can collect the
post's performance later. Dry runs write nothing.

Env (reuses the existing image-poster secrets):
  IG_ACCESS_TOKEN, IG_BUSINESS_ACCOUNT_ID
  THREADS_ACCESS_TOKEN, THREADS_USER_ID
  TWITTER_API_KEY/_API_SECRET/_ACCESS_TOKEN/_ACCESS_SECRET
  TIKTOK_ACCESS_TOKEN          (optional; TikTok skipped if unset)
  DISCORD_WEBHOOK_URL          (optional summary)
  VIDEO_DRY_RUN=1              plan only, no API calls
  VIDEO_ONLY=ig,threads,x      restrict platforms (default: all available)

Usage:
  python social/video_publisher.py
  python social/video_publisher.py --dry-run
  python social/video_publisher.py --only ig,x
"""
import json
import mimetypes
import os
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from xauth import TW_KEY, TW_SECRET, TW_TOKEN, TW_TSECRET, oauth_header as _oauth

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SELECTION = os.path.join(REPO, "videos", "selection.json")
SELECTIONS = os.path.join(REPO, "videos", "selections.json")
CAPTIONS = os.path.join(REPO, "videos", "captions.json")
LEDGER = os.environ.get("POSTS_LEDGER") or os.path.join(REPO, "videos", "posts_ledger.json")
UA = "PurchasingCorp-Social/1.0"

DRY_RUN = os.environ.get("VIDEO_DRY_RUN", "").lower() in ("1", "true", "yes") or "--dry-run" in sys.argv

# ── creds ──
IG_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
IG_ACCOUNT = os.environ.get("IG_BUSINESS_ACCOUNT_ID") or os.environ.get("IG_ACCOUNT_ID", "")
IG_GRAPH = f"https://graph.instagram.com/{os.environ.get('IG_API_VERSION', 'v21.0')}"

TH_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
TH_USER = os.environ.get("THREADS_USER_ID") or os.environ.get("THREADS_ACCOUNT_ID") or "me"
TH_GRAPH = f"https://graph.threads.net/{os.environ.get('THREADS_API_VERSION', 'v1.0')}"

TW_UPLOAD = "https://upload.twitter.com/1.1/media/upload.json"
TW_TWEETS = "https://api.twitter.com/2/tweets"

TT_TOKEN = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
TT_INIT = "https://open.tiktokapis.com/v2/post/publish/video/init/"

DISCORD = os.environ.get("DISCORD_WEBHOOK_URL", "")
POLL_TRIES = int(os.environ.get("VIDEO_POLL_TRIES", "60"))
POLL_DELAY = int(os.environ.get("VIDEO_POLL_DELAY_S", "5"))

# ─────────────────────────── HTTP ───────────────────────────

def http(method, url, headers=None, data=None, timeout=120):
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


def form(method, url, params):
    body = urllib.parse.urlencode(params).encode()
    return http(method, url, {"User-Agent": UA,
                              "Content-Type": "application/x-www-form-urlencoded"}, body)

# ─────────────────────────── captions ───────────────────────────

def caption_long(sel):
    tags = " ".join("#" + t for t in sel.get("hashtags", []))
    base = sel.get("caption", "").strip()
    return (base + ("\n\n" + tags if tags else "")).strip()


def caption_x(sel, limit=280):
    base = sel.get("caption", "").strip()
    tags = ["#" + t for t in sel.get("hashtags", [])[:2]]
    suffix = (" " + " ".join(tags)) if tags else ""
    if len(base) + len(suffix) > limit:
        base = base[: max(0, limit - len(suffix) - 1)].rstrip()
    return (base + suffix).strip()

# ─────────────────────────── posts ledger ───────────────────────────

def ledger_append(row):
    """Append one posted-video row to videos/posts_ledger.json (created on
    first write). Atomic temp+rename so a crash can't truncate the history —
    insights.py fills the rows' performance checkpoints later."""
    rows = []
    if os.path.exists(LEDGER):
        try:
            rows = json.load(open(LEDGER))
        except Exception:
            rows = []
    rows.append(row)
    d = os.path.dirname(LEDGER)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".ledger-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(rows, fh, indent=2)
    os.replace(tmp, LEDGER)


def _permalink(plat, post_id):
    """Best-effort public URL for a fresh post — never raises, '' on failure."""
    try:
        if plat == "instagram":
            s, d = http("GET", f"{IG_GRAPH}/{post_id}?fields=permalink&access_token={IG_TOKEN}",
                        {"User-Agent": UA})
            return d.get("permalink", "") if s < 300 else ""
        if plat == "threads":
            s, d = http("GET", f"{TH_GRAPH}/{post_id}?fields=permalink&access_token={TH_TOKEN}",
                        {"User-Agent": UA})
            return d.get("permalink", "") if s < 300 else ""
        if plat == "x":
            return f"https://x.com/i/status/{post_id}"
    except Exception:
        pass
    return ""

# ─────────────────────────── Instagram (Reels) ───────────────────────────

def post_instagram(sel):
    if not (IG_TOKEN and IG_ACCOUNT):
        return ("instagram", "skipped", "IG_ACCESS_TOKEN / IG_BUSINESS_ACCOUNT_ID not set", None)
    cap = caption_long(sel)
    if DRY_RUN:
        return ("instagram", "dry-run", f"REELS {sel['video_url']} | {len(cap)} chars", None)
    s, d = form("POST", f"{IG_GRAPH}/{IG_ACCOUNT}/media", {
        "media_type": "REELS", "video_url": sel["video_url"],
        "caption": cap, "access_token": IG_TOKEN})
    cid = d.get("id")
    if s >= 300 or not cid:
        return ("instagram", "error", f"container failed ({s}): {d}", None)
    for _ in range(POLL_TRIES):
        sp, dp = http("GET", f"{IG_GRAPH}/{cid}?fields=status_code&access_token={IG_TOKEN}",
                      {"User-Agent": UA})
        code = dp.get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            return ("instagram", "error", f"processing ERROR: {dp}", None)
        time.sleep(POLL_DELAY)
    else:
        return ("instagram", "error", "container not FINISHED in time", None)
    sp, dp = form("POST", f"{IG_GRAPH}/{IG_ACCOUNT}/media_publish",
                  {"creation_id": cid, "access_token": IG_TOKEN})
    mid = dp.get("id")
    if sp >= 300 or not mid:
        return ("instagram", "error", f"publish failed ({sp}): {dp}", None)
    return ("instagram", "ok", f"media {mid}", mid)

# ─────────────────────────── Threads (video) ───────────────────────────

def post_threads(sel):
    if not (TH_TOKEN and TH_USER):
        return ("threads", "skipped", "THREADS_ACCESS_TOKEN / THREADS_USER_ID not set", None)
    cap = caption_long(sel)
    if DRY_RUN:
        return ("threads", "dry-run", f"VIDEO {sel['video_url']} | {len(cap)} chars", None)
    s, d = form("POST", f"{TH_GRAPH}/{TH_USER}/threads", {
        "media_type": "VIDEO", "video_url": sel["video_url"],
        "text": cap, "access_token": TH_TOKEN})
    cid = d.get("id")
    if s >= 300 or not cid:
        return ("threads", "error", f"container failed ({s}): {d}", None)
    for _ in range(POLL_TRIES):
        sp, dp = http("GET", f"{TH_GRAPH}/{cid}?fields=status,error_message&access_token={TH_TOKEN}",
                      {"User-Agent": UA})
        code = dp.get("status")
        if code in ("FINISHED", "PUBLISHED"):
            break
        if code in ("ERROR", "EXPIRED"):
            return ("threads", "error", f"processing {code}: {dp}", None)
        time.sleep(POLL_DELAY)
    else:
        return ("threads", "error", "container not FINISHED in time", None)
    sp, dp = form("POST", f"{TH_GRAPH}/{TH_USER}/threads_publish",
                  {"creation_id": cid, "access_token": TH_TOKEN})
    mid = dp.get("id")
    if sp >= 300 or not mid:
        return ("threads", "error", f"publish failed ({sp}): {dp}", None)
    return ("threads", "ok", f"post {mid}", mid)

# ─────────────────────────── X / Twitter (chunked video) ───────────────────────────

def _tw_q(params):
    return TW_UPLOAD + "?" + urllib.parse.urlencode(params)


def post_twitter(sel):
    if not all([TW_KEY, TW_SECRET, TW_TOKEN, TW_TSECRET]):
        return ("x", "skipped", "TWITTER_* credentials not set", None)
    path = os.path.join(REPO, sel["local_path"])
    if not os.path.exists(path):
        return ("x", "error", f"local file missing: {path}", None)
    size = os.path.getsize(path)
    text = caption_x(sel)
    if DRY_RUN:
        return ("x", "dry-run", f"chunked upload {sel['file']} ({size} B) + tweet | {len(text)} chars", None)

    # INIT
    p = {"command": "INIT", "total_bytes": str(size),
         "media_type": "video/mp4", "media_category": "tweet_video"}
    s, d = http("POST", _tw_q(p), {"Authorization": _oauth("POST", TW_UPLOAD, p), "User-Agent": UA})
    mid = d.get("media_id_string")
    if s >= 300 or not mid:
        return ("x", "error", f"INIT failed ({s}): {d}", None)

    # APPEND (1MB chunks; control params in query, bytes in multipart body)
    with open(path, "rb") as fh:
        seg = 0
        while True:
            chunk = fh.read(1 << 20)
            if not chunk:
                break
            ap = {"command": "APPEND", "media_id": mid, "segment_index": str(seg)}
            boundary = "----PC" + secrets.token_hex(12)
            body = b"".join([
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="media"; filename="chunk"\r\n',
                b"Content-Type: application/octet-stream\r\n\r\n",
                chunk, f"\r\n--{boundary}--\r\n".encode()])
            hdr = {"Authorization": _oauth("POST", TW_UPLOAD, ap),
                   "Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": UA}
            sa, da = http("POST", _tw_q(ap), hdr, body)
            if sa >= 300:
                return ("x", "error", f"APPEND seg {seg} failed ({sa}): {da}", None)
            seg += 1

    # FINALIZE
    fp = {"command": "FINALIZE", "media_id": mid}
    s, d = http("POST", _tw_q(fp), {"Authorization": _oauth("POST", TW_UPLOAD, fp), "User-Agent": UA})
    if s >= 300:
        return ("x", "error", f"FINALIZE failed ({s}): {d}", None)

    # poll async transcode if required
    info = d.get("processing_info")
    while info and info.get("state") in ("pending", "in_progress"):
        time.sleep(max(1, int(info.get("check_after_secs", POLL_DELAY))))
        gp = {"command": "STATUS", "media_id": mid}
        s, d = http("GET", _tw_q(gp), {"Authorization": _oauth("GET", TW_UPLOAD, gp), "User-Agent": UA})
        info = d.get("processing_info")
        if info and info.get("state") == "failed":
            return ("x", "error", f"transcode failed: {info}", None)

    # tweet
    payload = json.dumps({"text": text, "media": {"media_ids": [mid]}}).encode()
    s, d = http("POST", TW_TWEETS,
                {"Authorization": _oauth("POST", TW_TWEETS), "Content-Type": "application/json",
                 "User-Agent": UA}, payload)
    tid = (d.get("data") or {}).get("id")
    if s >= 300 or not tid:
        return ("x", "error", f"tweet failed ({s}): {d}", None)
    return ("x", "ok", f"tweet {tid}", tid)

# ─────────────────────────── TikTok (PULL_FROM_URL, dormant) ───────────────────────────

def post_tiktok(sel):
    if not TT_TOKEN:
        return ("tiktok", "skipped",
                "TIKTOK_ACCESS_TOKEN not set (Content Posting API needs app review first)", None)
    cap = caption_long(sel)[:2200]
    if DRY_RUN:
        return ("tiktok", "dry-run", f"PULL_FROM_URL {sel['video_url']} | {len(cap)} chars", None)
    payload = json.dumps({
        "post_info": {"title": cap, "privacy_level": "PUBLIC_TO_EVERYONE",
                      "disable_comment": False},
        "source_info": {"source": "PULL_FROM_URL", "video_url": sel["video_url"]},
    }).encode()
    s, d = http("POST", TT_INIT, {"Authorization": f"Bearer {TT_TOKEN}",
                                  "Content-Type": "application/json; charset=UTF-8",
                                  "User-Agent": UA}, payload)
    pub = (d.get("data") or {}).get("publish_id")
    if s >= 300 or not pub:
        return ("tiktok", "error", f"init failed ({s}): {d}", None)
    return ("tiktok", "ok", f"publish_id {pub}", pub)

# ─────────────────────────── driver ───────────────────────────

PLATFORMS = {"ig": post_instagram, "instagram": post_instagram,
             "threads": post_threads, "x": post_twitter, "twitter": post_twitter,
             "tiktok": post_tiktok}
ORDER = [("instagram", post_instagram), ("threads", post_threads),
         ("x", post_twitter), ("tiktok", post_tiktok)]


def notify_discord(summary):
    if not DISCORD:
        return
    try:
        http("POST", DISCORD, {"Content-Type": "application/json", "User-Agent": UA},
             json.dumps({"content": summary}).encode())
    except Exception:
        pass


def publish_one(sel, runners):
    """Post a single selection to every runner; returns (results, failed)."""
    print(f"[video] {sel['date']} → {sel['file']}  ({sel['video_url']})")
    print(f"[video] {'DRY RUN — ' if DRY_RUN else ''}posting to: {', '.join(n for n, _ in runners)}")
    results, failed = [], 0
    for name, fn in runners:
        try:
            plat, status, detail, post_id = fn(sel)
        except Exception as e:
            plat, status, detail, post_id = name, "error", str(e), None
        icon = {"ok": "✓", "dry-run": "•", "skipped": "–", "error": "✗"}.get(status, "?")
        print(f"  {icon} {plat:9} {status:8} {detail}")
        results.append((plat, status, detail))
        if status == "error":
            failed += 1
        if status == "ok" and post_id and not DRY_RUN:
            try:
                ledger_append({
                    "date": sel.get("date", ""),
                    "file": sel.get("file", ""),
                    "platform": plat,
                    "post_id": str(post_id),
                    "permalink": _permalink(plat, post_id),
                    "posted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
            except Exception as e:
                print(f"  ⚠️ ledger append failed ({plat}): {e}")
    summary = (f"📹 PurchasingCorp video — {sel['file']} ({sel['date']})\n" +
               "\n".join(f"{p}: {s}" for p, s, _ in results))
    notify_discord(summary)
    return results, failed


def retire_selection(sel):
    """Once a clip has really been posted somewhere, remove it from the
    autoposter: drop its captions.json pool entry and delete the MP4 so it can
    never repeat. The workflow commits both, which also takes the file off the
    live site. Atomic temp+rename on captions.json, same as the ledger."""
    fname = sel.get("file", "")
    if not fname:
        return
    try:
        cfg = json.load(open(CAPTIONS))
        pool = cfg.get("pool", [])
        kept = [e for e in pool if e.get("file") != fname]
        if len(kept) != len(pool):
            cfg["pool"] = kept
            d = os.path.dirname(CAPTIONS)
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".captions-", suffix=".json")
            with os.fdopen(fd, "w") as fh:
                json.dump(cfg, fh, indent=2)
            os.replace(tmp, CAPTIONS)
        path = os.path.join(REPO, sel.get("local_path", os.path.join("videos", fname)))
        if os.path.exists(path):
            os.remove(path)
        print(f"  🗑 retired {fname} from the pool ({len(kept)} clip(s) left)")
    except Exception as e:
        print(f"  ⚠️ retire failed for {fname}: {e}")


def load_selections(args):
    """All selections to post: --all → selections.json list; --file → that one;
    else selection.json (single). Always returns a list."""
    if "--all" in args:
        if os.path.exists(SELECTIONS):
            return json.load(open(SELECTIONS))
        if os.path.exists(SELECTION):           # fall back to the single pick
            return [json.load(open(SELECTION))]
        return []
    sel_path = args[args.index("--file") + 1] if "--file" in args else SELECTION
    return [json.load(open(sel_path))] if os.path.exists(sel_path) else []


def main():
    args = sys.argv[1:]
    only = os.environ.get("VIDEO_ONLY", "")
    if "--only" in args:
        only = args[args.index("--only") + 1]

    sels = load_selections(args)
    if not sels:
        print("[video] no selection(s); run pick_daily_video.py first", file=sys.stderr)
        return 1

    chosen = {p.strip().lower() for p in only.split(",") if p.strip()} if only else None
    runners = [(name, fn) for name, fn in ORDER
               if (not chosen or name in chosen or (name == "x" and "twitter" in chosen)
                   or (name == "instagram" and "ig" in chosen))]

    failed = 0
    for i, sel in enumerate(sels):
        if i:
            print()
        results, f = publish_one(sel, runners)
        failed += f
        # X uploads local bytes, so only retire after every runner has finished.
        if not DRY_RUN and any(status == "ok" for _, status, _ in results):
            retire_selection(sel)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
