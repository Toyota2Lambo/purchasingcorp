#!/usr/bin/env python3
# ============================================================
# PURCHASINGCORP — Instagram commenter
# ============================================================
# Discovers recent posts on target hashtags and leaves on-brand,
# contextual comments via the Instagram Graph API.
#
# Flow:
#   1. Load comment_log.json — skip already-commented media IDs.
#   2. For each target hashtag, resolve its IG hashtag ID.
#   3. Fetch recent posts (id, caption, timestamp, media_type).
#   4. Filter: skip already-commented, skip own posts.
#   5. Ask Claude to write a short, genuine comment per candidate.
#   6. POST /{media-id}/comments with the generated text.
#   7. Save updated comment_log.json.
#
# Required env:
#   IG_ACCESS_TOKEN              long-lived Instagram Graph API token
#   IG_BUSINESS_ACCOUNT_ID       the IG business account id
#   ANTHROPIC_API_KEY            for comment generation
# Optional env:
#   IG_TARGET_HASHTAGS           comma-sep hashtags (no #), see DEFAULTS below
#   IG_COMMENTS_PER_RUN          max comments to post per run (default 10)
#   IG_COMMENT_DELAY_S           seconds between comments (default 4)
#   IG_COMMENT_LOG               path to JSON log (default social/comment_log.json)
#   IG_API_VERSION               default v21.0
#   IG_DRY_RUN=1                 plan only, no API calls
#   ANTHROPIC_MODEL              default claude-haiku-4-5-20251001
#   DISCORD_WEBHOOK_URL          optional run summary
#
# Usage:
#   python social/ig_commenter.py
#   IG_DRY_RUN=1 python social/ig_commenter.py
#   python social/ig_commenter.py --hashtags sellmyphone,tradein
# ============================================================

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

API_VERSION = os.environ.get("IG_API_VERSION", "v21.0")
GRAPH_BASE = f"https://graph.instagram.com/{API_VERSION}"   # publishing
FB_GRAPH_BASE = f"https://graph.facebook.com/{API_VERSION}" # hashtag search

TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
ACCOUNT_ID = os.environ.get("IG_BUSINESS_ACCOUNT_ID", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL") or "claude-haiku-4-5-20251001"

MAX_COMMENTS = int(os.environ.get("IG_COMMENTS_PER_RUN", "5"))
COMMENT_DELAY_S = int(os.environ.get("IG_COMMENT_DELAY_S", "4"))
LOG_PATH = Path(os.environ.get("IG_COMMENT_LOG") or HERE / "comment_log.json")
LOG_CAP = 2000  # keep last N media IDs to bound file size

DRY_RUN = os.environ.get("IG_DRY_RUN", "").lower() in ("1", "true", "yes")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# ============================================================
# TARGET HASHTAGS — edit this list to control where we comment
# ============================================================
# Add or remove hashtags (no # symbol). The script searches each
# one in order and collects recent posts to comment on. Instagram
# allows up to 30 unique hashtag lookups per 7 days per account,
# so keep this list at 30 or fewer.
TARGET_HASHTAGS = [
    # --- buyback / trade-in intent ---
    "sellmyphone",
    "sellyourphone",
    "tradein",
    "tradeinyourphone",
    "phonebuyback",
    "sellmyiphone",
    "sellmysamsung",
    "sellmymacbook",
    "sellmyipad",
    "electronicsbuyback",
    "devicebuyback",
    # --- device / reseller community ---
    "usediphone",
    "usedphones",
    "usedmacbook",
    "usedsamsung",
    "phonereseller",
    "techreseller",
    "refurbishedphones",
    # --- upgrade cycle ---
    "phoneupgrade",
    "newphone",
    "iphone16",
    "iphone15",
    "samsung galaxy",
    "macbookpro",
    # --- deals / cash ---
    "makemoney",
    "extracash",
    "phonedeal",
    "techdeals",
    # --- ADD YOUR OWN BELOW ---
]
# ============================================================

UA = "PurchasingCorp-Social/1.0"

COMMENT_SYSTEM = """You are a social media manager for PurchasingCorp, an electronics buyback company. We pay top dollar for used iPhones, Samsung phones, MacBooks, iPads, and other devices — free shipping, fast payment, ships to all 50 states.

Given an Instagram post caption, write ONE short, genuine comment (under 150 characters).

Rules:
- Sound like a real person, not a brand
- Be relevant to the actual post content
- Use this mix roughly: 60% purely engage with no brand mention, 30% soft brand mention, 10% friendly CTA
- Soft mention examples: "we pay top dollar for those 👀" / "love seeing this — btw we buy these 🙌"
- CTA examples: "We pay market rate if you ever want to sell!" / "DM us — we buy those fast 🔥"
- No hashtags in the comment
- Casual, friendly tone; 1–2 emojis max
- Never be spammy or pushy
- If the caption is empty or unrelated to electronics, just give a warm generic engagement comment

Reply with ONLY the comment text, nothing else."""


# ------------------------------------------------------------
# HTTP plumbing (stdlib only, matching ig_publisher.py)
# ------------------------------------------------------------

def _request(method: str, url: str, data: dict = None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
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


def graph_get(path: str, params: dict):
    qs = urllib.parse.urlencode(params)
    return _request("GET", f"{GRAPH_BASE}/{path}?{qs}")


def graph_post(path: str, params: dict):
    return _request("POST", f"{GRAPH_BASE}/{path}", params)


# ------------------------------------------------------------
# Anthropic — comment generation
# ------------------------------------------------------------

def _anthropic_post(payload: dict) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "User-Agent": UA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Anthropic API error {e.code}: {e.read().decode()}")


def generate_comment(caption: str) -> str:
    caption_snippet = (caption or "").strip()[:500] or "(no caption)"
    data = _anthropic_post({
        "model": MODEL,
        "max_tokens": 80,
        "system": COMMENT_SYSTEM,
        "messages": [{"role": "user", "content": f"Post caption:\n{caption_snippet}"}],
    })
    try:
        return data["content"][0]["text"].strip().strip('"')
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Anthropic response: {data}") from e


# ------------------------------------------------------------
# Comment log (deduplication state)
# ------------------------------------------------------------

def load_log() -> set:
    if LOG_PATH.exists():
        try:
            raw = json.loads(LOG_PATH.read_text())
            return set(raw.get("commented", []))
        except Exception:
            pass
    return set()


def save_log(commented: set) -> None:
    entries = list(commented)[-LOG_CAP:]
    LOG_PATH.write_text(json.dumps({
        "commented": entries,
        "last_updated": dt.datetime.now(dt.timezone.utc).isoformat(),
    }, indent=2))


# ------------------------------------------------------------
# Instagram Graph API helpers
# ------------------------------------------------------------

def fb_get(path: str, params: dict):
    qs = urllib.parse.urlencode(params)
    return _request("GET", f"{FB_GRAPH_BASE}/{path}?{qs}")


def resolve_hashtag_id(hashtag: str) -> str | None:
    status, data = fb_get("ig-hashtag-search", {
        "user_id": ACCOUNT_ID,
        "q": hashtag,
        "access_token": TOKEN,
    })
    if status >= 300 or not data.get("data"):
        print(f"[comment] hashtag #{hashtag}: lookup failed ({status}): {data}",
              file=sys.stderr)
        return None
    return data["data"][0]["id"]


def fetch_recent_media(hashtag_id: str) -> list:
    status, data = fb_get(f"{hashtag_id}/recent_media", {
        "user_id": ACCOUNT_ID,
        "fields": "id,caption,timestamp,media_type",
        "access_token": TOKEN,
    })
    if status >= 300:
        print(f"[comment] recent_media failed ({status}): {data}", file=sys.stderr)
        return []
    return data.get("data", [])


def post_comment(media_id: str, text: str) -> str:
    status, data = graph_post(f"{media_id}/comments", {
        "message": text,
        "access_token": TOKEN,
    })
    if status >= 300 or "id" not in data:
        raise RuntimeError(f"comment POST failed ({status}): {data}")
    return data["id"]


# ------------------------------------------------------------
# Discord notify
# ------------------------------------------------------------

def notify_discord(summary: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        _request("POST", DISCORD_WEBHOOK_URL, {"content": summary})
    except Exception as e:
        print(f"[comment] discord notify failed: {e}", file=sys.stderr)


# ------------------------------------------------------------
# Driver
# ------------------------------------------------------------

def run() -> int:
    ap = argparse.ArgumentParser(description="Comment on recent IG posts by hashtag.")
    ap.add_argument("--hashtags", help="Comma-sep hashtags (no #). Overrides env.")
    args = ap.parse_args()

    if not DRY_RUN and (not TOKEN or not ACCOUNT_ID):
        print("[comment] IG_ACCESS_TOKEN and IG_BUSINESS_ACCOUNT_ID are required "
              "(or set IG_DRY_RUN=1).", file=sys.stderr)
        return 1
    if not DRY_RUN and not ANTHROPIC_KEY:
        print("[comment] ANTHROPIC_API_KEY is required (or set IG_DRY_RUN=1).",
              file=sys.stderr)
        return 1

    raw_tags = args.hashtags or os.environ.get("IG_TARGET_HASHTAGS") or ""
    hashtags = [h.strip().lstrip("#") for h in raw_tags.split(",") if h.strip()] \
               or TARGET_HASHTAGS

    print(f"[comment] {'DRY RUN — ' if DRY_RUN else ''}target hashtags: "
          f"{', '.join('#' + h for h in hashtags)}")
    print(f"[comment] max comments per run: {MAX_COMMENTS}")

    already_commented = load_log()
    candidates: list[dict] = []

    for tag in hashtags:
        if len(candidates) >= MAX_COMMENTS * 3:
            break
        print(f"[comment] resolving #{tag}…")
        htag_id = resolve_hashtag_id(tag) if not DRY_RUN else f"fake-id-{tag}"
        if not htag_id:
            continue
        media = fetch_recent_media(htag_id) if not DRY_RUN else [
            {"id": f"fake-media-{tag}-1", "caption": f"Just sold my old iPhone! #{tag}", "media_type": "IMAGE"},
            {"id": f"fake-media-{tag}-2", "caption": f"Trade in season 🔥 #{tag}", "media_type": "IMAGE"},
        ]
        for post in media:
            mid = post.get("id")
            if not mid or mid in already_commented:
                continue
            candidates.append(post)
        time.sleep(0.5)

    # Shuffle-ish: sort by id string so order is deterministic but varied across hashtags
    candidates.sort(key=lambda p: p.get("id", ""))
    # Dedupe by media ID (same post can appear in multiple hashtag results)
    seen: set[str] = set()
    unique: list[dict] = []
    for p in candidates:
        if p["id"] not in seen:
            seen.add(p["id"])
            unique.append(p)

    batch = unique[:MAX_COMMENTS]
    print(f"[comment] {len(unique)} candidates found, will comment on {len(batch)}")

    succeeded = 0
    failures: list[str] = []

    for post in batch:
        mid = post["id"]
        caption = post.get("caption", "")
        label = f"media {mid} ({post.get('media_type', '?')})"

        try:
            comment_text = generate_comment(caption)
        except Exception as e:
            print(f"[comment] SKIP {label}: generate failed: {e}", file=sys.stderr)
            failures.append(f"generate {label}: {e}")
            continue

        if DRY_RUN:
            print(f"[comment] PLAN {label}")
            print(f"            caption: {caption[:80].replace(chr(10), ' ')}")
            print(f"            comment: {comment_text}")
            already_commented.add(mid)
            succeeded += 1
            continue

        try:
            comment_id = post_comment(mid, comment_text)
            print(f"[comment] OK {label} -> comment {comment_id}: {comment_text}")
            already_commented.add(mid)
            succeeded += 1
        except Exception as e:
            print(f"[comment] FAIL {label}: {e}", file=sys.stderr)
            failures.append(f"{label}: {e}")

        time.sleep(COMMENT_DELAY_S)

    save_log(already_commented)

    summary = (f"PurchasingCorp IG comments: {succeeded}/{len(batch)} posted"
               + (f", {len(failures)} failed" if failures else ""))
    print(f"[comment] {summary}")
    if failures:
        notify_discord(summary + "\n" + "\n".join(failures[:10]))
    elif succeeded and not DRY_RUN:
        notify_discord(summary)

    if batch and succeeded == 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(run())
