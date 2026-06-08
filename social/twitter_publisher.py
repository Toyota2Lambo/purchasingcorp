#!/usr/bin/env python3
# ============================================================
# PURCHASINGCORP — Twitter / X publisher
# ============================================================
# Reads a day's manifest.json (written by renderer.js) and publishes the
# rendered PNGs to Twitter / X. It reuses the SAME content/manifest and the
# SAME PNGs as the Instagram and Threads publishers — nothing extra has to be
# generated or rendered.
#
# KEY DIFFERENCE from the Meta publishers: Instagram and Threads FETCH a public
# image URL (we hand them the Vercel-served PNG link). Twitter does NOT — you
# must UPLOAD the image bytes first to get a numeric media_id, then attach that
# id to the tweet. So this publisher reads the PNGs LOCALLY from the manifest's
# folder (they are committed to the repo, so the Actions checkout already has
# them). No Vercel deploy / URL polling is needed.
#
# Publishing flow:
#   upload   : POST upload.twitter.com/1.1/media/upload.json  (multipart, raw
#              bytes, field "media") -> media_id_string
#              [+ optional POST media/metadata/create.json for alt_text]
#   tweet    : POST api.twitter.com/2/tweets  {text, media:{media_ids:[...]}}
#   carousel : Twitter allows max 4 images per tweet. A post with >4 slides is
#              posted as a THREAD — first tweet carries the caption + first 4
#              images, each following reply carries the next 4 (no caption),
#              chained via reply.in_reply_to_tweet_id. <=4 slides = one tweet.
#
# Auth is OAuth 1.0a user context (HMAC-SHA1), signed with stdlib only — the
# v2 tweet endpoint and the v1.1 media endpoint both accept it. There is no
# "long-lived bearer token" path for posting like Threads/IG have; you need
# the app's consumer key/secret AND a Read+Write access token/secret.
#
# Twitter-native text: like Threads (and unlike Instagram) a stack of hashtags
# reads as spam and the hard limit is 280 chars, so we keep at most
# TWITTER_MAX_TAGS (default 2) most-relevant tags and cap the text at 280.
# Every image also gets alt_text for screen readers.
#
# Required env:
#   TWITTER_API_KEY          app consumer key      (aka TWITTER_CONSUMER_KEY)
#   TWITTER_API_SECRET       app consumer secret   (aka TWITTER_CONSUMER_SECRET)
#   TWITTER_ACCESS_TOKEN     user access token  (the app must be Read+Write
#                            BEFORE this token is generated, or posting 403s)
#   TWITTER_ACCESS_SECRET    user access token secret
#                            (aka TWITTER_ACCESS_TOKEN_SECRET)
# Optional env:
#   TWITTER_DRY_RUN=1         plan only, no API calls
#   TWITTER_INCLUDE_STORIES=1 also post story images (default: posts only)
#   TWITTER_TEXT_LIMIT        max tweet length, default 280
#   TWITTER_MAX_TAGS          hashtags to append, default 2
#   TWITTER_STATE_MENTION=1   append a state-naming line (default OFF — 280
#                             chars is tight; on by request for reach parity
#                             with Threads). TWITTER_STATES to override the pool.
#   TWITTER_ALT_TEXT=0        skip image alt text (accessibility; default on)
#   DISCORD_WEBHOOK_URL       optional run summary
#
# Usage:
#   python social/twitter_publisher.py                       # today
#   python social/twitter_publisher.py --date 2026-06-01
#   python social/twitter_publisher.py --only post:1
#   TWITTER_DRY_RUN=1 python social/twitter_publisher.py --sample
# ============================================================

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# --- endpoints ---
TWEETS_URL = "https://api.twitter.com/2/tweets"
UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
METADATA_URL = "https://upload.twitter.com/1.1/media/metadata/create.json"

# --- credentials (OAuth 1.0a user context) ---
API_KEY = os.environ.get("TWITTER_API_KEY") or os.environ.get("TWITTER_CONSUMER_KEY", "")
API_SECRET = os.environ.get("TWITTER_API_SECRET") or os.environ.get("TWITTER_CONSUMER_SECRET", "")
ACCESS_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN", "")
ACCESS_SECRET = (
    os.environ.get("TWITTER_ACCESS_SECRET")
    or os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "")
)

# --- tuning ---
TEXT_LIMIT = int(os.environ.get("TWITTER_TEXT_LIMIT", "280"))
# Twitter, like Threads, reads a stack of hashtags as spam. Keep few.
MAX_TAGS = int(os.environ.get("TWITTER_MAX_TAGS", "2"))
MAX_IMAGES_PER_TWEET = 4  # Twitter hard limit; >4 slides become a reply thread
ALT_TEXT_ENABLED = os.environ.get("TWITTER_ALT_TEXT", "1").lower() not in ("0", "false", "no")
ALT_TEXT_LIMIT = int(os.environ.get("TWITTER_ALT_TEXT_LIMIT", "1000"))  # Twitter allows 1000
DELAY_BETWEEN_POSTS_S = int(os.environ.get("DELAY_BETWEEN_POSTS_S", "6"))
DELAY_BETWEEN_THREAD_TWEETS_S = int(os.environ.get("DELAY_BETWEEN_THREAD_TWEETS_S", "3"))

DRY_RUN = os.environ.get("TWITTER_DRY_RUN", "").lower() in ("1", "true", "yes")
INCLUDE_STORIES = os.environ.get("TWITTER_INCLUDE_STORIES", "").lower() in ("1", "true", "yes")
# Off by default: 280 chars rarely has room for a state line on top of the
# caption. Set TWITTER_STATE_MENTION=1 to match the Threads reach tactic.
STATE_MENTION_ENABLED = os.environ.get("TWITTER_STATE_MENTION", "").lower() in ("1", "true", "yes")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

UA = "PurchasingCorp-Social/1.0"


# ------------------------------------------------------------
# OAuth 1.0a signing (stdlib only)
# ------------------------------------------------------------
def _pct(s) -> str:
    """RFC 3986 percent-encoding. Python never quotes A-Za-z0-9-._~, so
    quote(safe="") yields exactly OAuth's required encoding."""
    return urllib.parse.quote(str(s), safe="")


def oauth_header(method: str, url: str) -> str:
    """Build an OAuth 1.0a Authorization header for a request whose body is
    multipart or JSON (i.e. NOT form-encoded), so only the oauth_* params are
    part of the signature base string."""
    oauth = {
        "oauth_consumer_key": API_KEY,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": ACCESS_TOKEN,
        "oauth_version": "1.0",
    }
    base_params = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(oauth.items()))
    base_string = "&".join([method.upper(), _pct(url), _pct(base_params)])
    signing_key = f"{_pct(API_SECRET)}&{_pct(ACCESS_SECRET)}"
    digest = hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    oauth["oauth_signature"] = base64.b64encode(digest).decode()
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))


# ------------------------------------------------------------
# HTTP plumbing (stdlib only)
# ------------------------------------------------------------
def _http(method: str, url: str, headers: dict, data: bytes = None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {"raw": raw}
        return e.code, parsed
    except Exception as e:
        return 0, {"error": str(e)}


# ------------------------------------------------------------
# Twitter API operations
# ------------------------------------------------------------
def upload_media(path: Path, alt_text: str = "") -> str:
    """Upload one image's bytes; return its media_id_string."""
    raw = path.read_bytes()
    ctype = mimetypes.guess_type(str(path))[0] or "image/png"
    boundary = "----PCBoundary" + secrets.token_hex(16)
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="media"; filename="{path.name}"\r\n'.encode(),
        f"Content-Type: {ctype}\r\n\r\n".encode(),
        raw,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    headers = {
        "Authorization": oauth_header("POST", UPLOAD_URL),
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": UA,
    }
    status, data = _http("POST", UPLOAD_URL, headers, body)
    media_id = data.get("media_id_string") or (str(data["media_id"]) if "media_id" in data else "")
    if status >= 300 or not media_id:
        raise RuntimeError(f"media upload failed ({status}) for {path.name}: {data}")
    if alt_text:
        set_alt_text(media_id, alt_text)
    return media_id


def set_alt_text(media_id: str, text: str) -> None:
    """Attach a screen-reader description to an uploaded image (best-effort)."""
    payload = json.dumps({"media_id": media_id, "alt_text": {"text": text[:ALT_TEXT_LIMIT]}}).encode()
    headers = {
        "Authorization": oauth_header("POST", METADATA_URL),
        "Content-Type": "application/json",
        "User-Agent": UA,
    }
    status, data = _http("POST", METADATA_URL, headers, payload)
    if status >= 300:
        print(f"[twitter] WARN alt_text failed ({status}) for media {media_id}: {data}",
              file=sys.stderr)


def create_tweet(text: str, media_ids: list = None, reply_to: str = None) -> str:
    """Create one tweet; return its id."""
    payload = {}
    if text:
        payload["text"] = text
    if media_ids:
        payload["media"] = {"media_ids": media_ids}
    if reply_to:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to}
    headers = {
        "Authorization": oauth_header("POST", TWEETS_URL),
        "Content-Type": "application/json",
        "User-Agent": UA,
    }
    status, data = _http("POST", TWEETS_URL, headers, json.dumps(payload).encode())
    tweet_id = (data.get("data") or {}).get("id")
    if status >= 300 or not tweet_id:
        raise RuntimeError(f"tweet create failed ({status}): {data}")
    return tweet_id


def publish_post(paths: list, text: str, alts: list = None) -> str:
    """Publish a feed post. <=4 images = single tweet; >4 = a reply thread.
    Returns the first (root) tweet id."""
    alts = alts or []
    groups = [paths[i:i + MAX_IMAGES_PER_TWEET] for i in range(0, len(paths), MAX_IMAGES_PER_TWEET)]
    root_id = None
    prev_id = None
    for gi, group in enumerate(groups):
        media_ids = []
        for pi, p in enumerate(group):
            flat = gi * MAX_IMAGES_PER_TWEET + pi
            alt = alts[flat] if flat < len(alts) else ""
            media_ids.append(upload_media(p, alt))
        tweet_text = text if gi == 0 else ""
        tid = create_tweet(tweet_text, media_ids, reply_to=prev_id)
        if root_id is None:
            root_id = tid
        prev_id = tid
        if gi < len(groups) - 1:
            time.sleep(DELAY_BETWEEN_THREAD_TWEETS_S)
    return root_id


# ------------------------------------------------------------
# Text / alt helpers
# ------------------------------------------------------------
def build_text(caption: str, hashtags) -> str:
    """caption + at most MAX_TAGS topic tags, capped at TEXT_LIMIT (280).
    Drops tags before truncating the caption, and a URL in the caption is left
    intact (Twitter makes it tappable and a t.co link counts as 23 chars)."""
    caption = (caption or "").strip()
    tags = []
    for h in (hashtags or []):
        h = (h or "").strip()
        if not h:
            continue
        tags.append(h if h.startswith("#") else "#" + h)
        if len(tags) >= MAX_TAGS:
            break
    tagline = " ".join(tags)
    text = f"{caption}\n\n{tagline}".strip() if tagline else caption
    if len(text) > TEXT_LIMIT:
        if caption and len(caption) <= TEXT_LIMIT:
            text = caption
        if len(text) > TEXT_LIMIT:
            text = text[:TEXT_LIMIT].rstrip()
    return text


# All 50 states, population-ordered. Same deterministic rotation as the Threads
# publisher: a different state per post, advancing day to day, never claiming
# state-only service. Only used when TWITTER_STATE_MENTION=1.
_DEFAULT_STATES = [
    "California", "Texas", "Florida", "New York", "Pennsylvania", "Illinois",
    "Ohio", "Georgia", "North Carolina", "Michigan", "New Jersey", "Virginia",
    "Washington", "Arizona", "Tennessee", "Massachusetts", "Indiana", "Missouri",
    "Maryland", "Wisconsin", "Colorado", "Minnesota", "South Carolina", "Alabama",
    "Louisiana", "Kentucky", "Oregon", "Oklahoma", "Connecticut", "Utah",
    "Iowa", "Nevada", "Arkansas", "Mississippi", "Kansas", "New Mexico",
    "Nebraska", "Idaho", "West Virginia", "Hawaii", "New Hampshire", "Maine",
    "Montana", "Rhode Island", "Delaware", "South Dakota", "North Dakota",
    "Alaska", "Vermont", "Wyoming",
]

# Shorter than the Threads lines — 280 chars is tight.
_STATE_LINES = [
    "{s} & all 50 states — same-day cash, free shipping.",
    "In {s}? We buy nationwide — real cash today.",
    "Cash for tech in {s} & nationwide, same day.",
    "{s} to coast to coast — free shipping, cash today.",
]


def _states_pool():
    raw = os.environ.get("TWITTER_STATES", "")
    pool = [s.strip() for s in raw.split(",") if s.strip()] if raw else list(_DEFAULT_STATES)
    return pool or list(_DEFAULT_STATES)


def state_line_for(date_str: str, idx: int) -> str:
    pool = _states_pool()
    try:
        seed = dt.date.fromisoformat(date_str).toordinal()
    except Exception:
        seed = 0
    idx = idx if isinstance(idx, int) and idx > 0 else 1
    state = pool[(seed * 4 + (idx - 1)) % len(pool)]
    line = _STATE_LINES[(seed + idx - 1) % len(_STATE_LINES)]
    return line.format(s=state)


def with_state_mention(caption: str, date_str: str, idx: int) -> str:
    caption = (caption or "").strip()
    if not STATE_MENTION_ENABLED:
        return caption
    line = state_line_for(date_str, idx)
    return f"{caption}\n\n{line}" if caption else line


def alt_text_for(post: dict, idx: int = 1, total: int = 1) -> str:
    if not ALT_TEXT_ENABLED:
        return ""
    role = (post.get("role") or "post").strip().rstrip(".")
    label = f"PurchasingCorp {role.lower()}"
    if total > 1:
        alt = f"{label}, slide {idx} of {total}"
    else:
        caption = (post.get("caption") or "").strip()
        lead = caption.split(". ")[0].strip() if caption else ""
        alt = f"{label}: {lead}" if lead else label
    if len(alt) > ALT_TEXT_LIMIT:
        alt = alt[:ALT_TEXT_LIMIT].rstrip(" .,;:—-")
    return alt


# ------------------------------------------------------------
# Manifest / selection helpers
# ------------------------------------------------------------
def parse_only(spec: str):
    posts, stories = set(), set()
    all_posts = all_stories = False
    for tok in (spec or "").split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        if tok in ("post", "posts"):
            all_posts = True
        elif tok in ("story", "stories"):
            all_stories = True
        elif tok.startswith("post:"):
            posts.add(int(tok.split(":", 1)[1]))
        elif tok.startswith("story:"):
            stories.add(int(tok.split(":", 1)[1]))
    return posts, stories, all_posts, all_stories


def notify_discord(summary: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        _http("POST", DISCORD_WEBHOOK_URL,
              {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA},
              urllib.parse.urlencode({"content": summary}).encode())
    except Exception as e:
        print(f"[twitter] discord notify failed: {e}", file=sys.stderr)


def load_manifest(date_str: str, sample: bool, manifest_arg: str):
    """Return (manifest_dict, media_dir). PNGs are read locally from media_dir."""
    if manifest_arg:
        p = Path(manifest_arg)
    elif sample:
        p = HERE / "_sample" / "manifest.json"
    else:
        p = HERE / date_str / "manifest.json"
    if not p.exists():
        print(f"[twitter] manifest not found: {p}\n"
              f"          run the renderer first.", file=sys.stderr)
        sys.exit(1)
    return json.loads(p.read_text()), p.resolve().parent


# ------------------------------------------------------------
# Driver
# ------------------------------------------------------------
def run() -> int:
    ap = argparse.ArgumentParser(description="Publish a day's rendered PNGs to Twitter / X.")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today, UTC).")
    ap.add_argument("--manifest", help="Explicit manifest.json path.")
    ap.add_argument("--sample", action="store_true", help="Use social/_sample/manifest.json.")
    ap.add_argument("--only", default="", help="Filter, e.g. post:1,story:2 or 'posts'.")
    args = ap.parse_args()

    if not DRY_RUN and not all([API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_SECRET]):
        print("[twitter] TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN and "
              "TWITTER_ACCESS_SECRET are required (or set TWITTER_DRY_RUN=1 to plan only).",
              file=sys.stderr)
        return 1

    date_str = args.date or dt.datetime.now(dt.timezone.utc).date().isoformat()
    manifest, media_dir = load_manifest(date_str, args.sample, args.manifest)

    only_posts, only_stories, all_posts, all_stories = parse_only(args.only)
    select_all = not (only_posts or only_stories or all_posts or all_stories)

    posts = manifest.get("posts", [])
    stories = manifest.get("stories", [])

    print(f"[twitter] {'DRY RUN — ' if DRY_RUN else ''}media_dir={media_dir}")
    print(f"[twitter] {len(posts)} posts, {len(stories)} stories in manifest "
          f"(include_stories={INCLUDE_STORIES})")

    attempted = 0
    succeeded = 0
    failures = []

    # ---- posts ----
    for post in posts:
        idx = post.get("index")
        if not (select_all or all_posts or idx in only_posts):
            continue
        files = post.get("files", [])
        paths = [media_dir / f for f in files]
        caption = with_state_mention(post.get("caption", ""), date_str, idx)
        text = build_text(caption, post.get("hashtags"))
        alts = [alt_text_for(post, i + 1, len(paths)) for i in range(len(paths))]
        n_tweets = max(1, (len(paths) + MAX_IMAGES_PER_TWEET - 1) // MAX_IMAGES_PER_TWEET)
        shape = f"thread x{n_tweets}" if n_tweets > 1 else (
            f"{len(paths)} imgs" if len(paths) > 1 else "single")
        label = f"post {idx} ({post.get('role')}, {shape})"
        attempted += 1

        if DRY_RUN:
            print(f"[twitter] PLAN {label}")
            for p in paths:
                exists = p.exists()
                size = f"{p.stat().st_size // 1024}KB" if exists else "MISSING"
                print(f"            {'ok ' if exists else 'ERR'} {p.name} ({size})")
            print(f"            text({len(text)}): {text[:90].replace(chr(10), ' ')}")
            print(f"            alt:  {(alts[0] or '—')[:90]}")
            succeeded += 1
            continue

        try:
            missing = [p.name for p in paths if not p.exists()]
            if missing:
                raise RuntimeError(f"image file(s) not found: {', '.join(missing)}")
            tid = publish_post(paths, text, alts)
            print(f"[twitter] OK {label} -> tweet {tid}")
            succeeded += 1
        except Exception as e:
            print(f"[twitter] FAIL {label}: {e}", file=sys.stderr)
            failures.append(f"{label}: {e}")
        time.sleep(DELAY_BETWEEN_POSTS_S)

    # ---- stories (opt-in; each posted as a plain single-image tweet) ----
    if INCLUDE_STORIES:
        for story in stories:
            idx = story.get("index")
            if not (select_all or all_stories or idx in only_stories):
                continue
            path = media_dir / story.get("file")
            story_alt = (f"PurchasingCorp {story.get('template')} story"
                         if ALT_TEXT_ENABLED else "")
            label = f"story {idx} ({story.get('template')})"
            attempted += 1

            if DRY_RUN:
                exists = path.exists()
                size = f"{path.stat().st_size // 1024}KB" if exists else "MISSING"
                print(f"[twitter] PLAN {label}\n            {'ok ' if exists else 'ERR'} "
                      f"{path.name} ({size})")
                succeeded += 1
                continue

            try:
                if not path.exists():
                    raise RuntimeError(f"image file not found: {path.name}")
                media_id = upload_media(path, story_alt)
                tid = create_tweet("", [media_id])
                print(f"[twitter] OK {label} -> tweet {tid}")
                succeeded += 1
            except Exception as e:
                print(f"[twitter] FAIL {label}: {e}", file=sys.stderr)
                failures.append(f"{label}: {e}")
            time.sleep(DELAY_BETWEEN_POSTS_S)

    # ---- summary ----
    summary = (f"PurchasingCorp Twitter {date_str}: {succeeded}/{attempted} published"
               + (f", {len(failures)} failed" if failures else ""))
    print(f"[twitter] {summary}")
    if failures:
        notify_discord(summary + "\n" + "\n".join(failures[:10]))
    elif attempted and not DRY_RUN:
        notify_discord(summary)

    if attempted and succeeded == 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(run())
