#!/usr/bin/env python3
# ============================================================
# PURCHASINGCORP — Instagram commenter
# ============================================================
# Reads target_posts.json, generates a contextual comment for
# each post via Claude, and posts it via the Instagram Graph API.
#
# To add posts to comment on:
#   1. Open social/target_posts.json
#   2. Add the Instagram post URL (copy from any public post)
#   3. Optionally add a "context" note to help Claude write better
#
# Flow:
#   1. Load comment_log.json — skip already-commented posts.
#   2. Read target_posts.json for the list of posts to comment on.
#   3. Resolve each URL to an Instagram media ID via the oEmbed API.
#   4. Ask Claude to write a short, genuine comment.
#   5. POST /{media-id}/comments with the generated text.
#   6. Save updated comment_log.json.
#
# Required env:
#   IG_ACCESS_TOKEN              long-lived Instagram Graph API token
#   IG_BUSINESS_ACCOUNT_ID       your IG business account id
#   ANTHROPIC_API_KEY            for comment generation
# Optional env:
#   IG_COMMENTS_PER_RUN          max comments to post per run (default 5)
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
# ============================================================

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

API_VERSION = os.environ.get("IG_API_VERSION", "v21.0")
GRAPH_BASE  = f"https://graph.instagram.com/{API_VERSION}"

TOKEN      = os.environ.get("IG_ACCESS_TOKEN", "")
ACCOUNT_ID = os.environ.get("IG_BUSINESS_ACCOUNT_ID", "")
ANTH_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL      = os.environ.get("ANTHROPIC_MODEL") or "claude-haiku-4-5-20251001"

MAX_COMMENTS  = int(os.environ.get("IG_COMMENTS_PER_RUN", "5"))
COMMENT_DELAY = int(os.environ.get("IG_COMMENT_DELAY_S",  "4"))
LOG_PATH      = Path(os.environ.get("IG_COMMENT_LOG") or HERE / "comment_log.json")
POSTS_PATH    = HERE / "target_posts.json"
LOG_CAP       = 2000

DRY_RUN             = os.environ.get("IG_DRY_RUN", "").lower() in ("1", "true", "yes")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

UA = "PurchasingCorp-Social/1.0"

COMMENT_SYSTEM = """You are a social media manager for PurchasingCorp, an electronics buyback company. We pay top dollar for used iPhones, Samsung phones, MacBooks, iPads — free shipping, fast payment, all 50 states.

Given context about an Instagram post, write ONE short, genuine comment (under 150 characters).

Rules:
- Sound like a real person, not a brand
- Be relevant to the post content or context provided
- Mix it up: 60% pure engagement, 30% soft brand mention, 10% friendly CTA
- Soft mention examples: "we pay top dollar for those 👀" / "love seeing this — btw we buy these 🙌"
- CTA examples: "We pay market rate if you ever want to sell!" / "DM us — we buy those fast 🔥"
- No hashtags; 1–2 emojis max; casual friendly tone
- Never be pushy or spammy

Reply with ONLY the comment text, nothing else."""


# ------------------------------------------------------------
# HTTP plumbing
# ------------------------------------------------------------

def _request(method: str, url: str, data: dict = None, headers: dict = None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    h    = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:    parsed = json.loads(raw)
        except: parsed = {"raw": raw}
        return e.code, parsed
    except Exception as e:
        return 0, {"error": str(e)}


def graph_post(path: str, params: dict):
    return _request("POST", f"{GRAPH_BASE}/{path}", params)


# ------------------------------------------------------------
# Resolve Instagram URL → media ID
# ------------------------------------------------------------

def extract_shortcode(url: str) -> str | None:
    m = re.search(r'/(?:p|reel|tv)/([A-Za-z0-9_-]+)', url)
    return m.group(1) if m else None


def resolve_media_id(url: str) -> str | None:
    """Use the Instagram oEmbed API to get the media ID from a post URL."""
    shortcode = extract_shortcode(url)
    if not shortcode:
        print(f"[comment] could not parse shortcode from URL: {url}", file=sys.stderr)
        return None

    # oEmbed endpoint — no auth needed for public posts
    oembed_url = (
        f"https://graph.facebook.com/v{API_VERSION.lstrip('v')}/instagram_oembed"
        f"?url={urllib.parse.quote(url, safe='')}"
        f"&access_token={TOKEN}"
    )
    status, data = _request("GET", oembed_url)
    if status >= 300:
        # Fall back: shortcode can sometimes be used directly as media ID
        print(f"[comment] oEmbed lookup failed ({status}) for {url} — "
              f"trying shortcode as media id", file=sys.stderr)
        return shortcode

    # oEmbed gives us the media ID in some API versions
    media_id = data.get("media_id") or data.get("id")
    if not media_id:
        # Last resort: return the shortcode and let the Graph API reject if wrong
        return shortcode
    return str(media_id)


# ------------------------------------------------------------
# Anthropic — comment generation
# ------------------------------------------------------------

def _anthropic_post(payload: dict) -> dict:
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body, method="POST",
        headers={
            "x-api-key":         ANTH_KEY,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
            "User-Agent":        UA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Anthropic API error {e.code}: {e.read().decode()}")


def generate_comment(post_url: str, context: str) -> str:
    user_msg = f"Post URL: {post_url}"
    if context:
        user_msg += f"\nContext about this post: {context}"
    data = _anthropic_post({
        "model":      MODEL,
        "max_tokens": 80,
        "system":     COMMENT_SYSTEM,
        "messages":   [{"role": "user", "content": user_msg}],
    })
    try:
        return data["content"][0]["text"].strip().strip('"')
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Anthropic response: {data}") from e


# ------------------------------------------------------------
# Comment log
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
        "commented":    entries,
        "last_updated": dt.datetime.now(dt.timezone.utc).isoformat(),
    }, indent=2))


# ------------------------------------------------------------
# Post a comment
# ------------------------------------------------------------

def post_comment(media_id: str, text: str) -> str:
    status, data = graph_post(f"{media_id}/comments", {
        "message":      text,
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
    argparse.ArgumentParser(description="Comment on specific IG posts from target_posts.json.").parse_args()

    if not DRY_RUN and (not TOKEN or not ACCOUNT_ID):
        print("[comment] IG_ACCESS_TOKEN and IG_BUSINESS_ACCOUNT_ID are required "
              "(or set IG_DRY_RUN=1).", file=sys.stderr)
        return 1
    if not DRY_RUN and not ANTH_KEY:
        print("[comment] ANTHROPIC_API_KEY is required (or set IG_DRY_RUN=1).",
              file=sys.stderr)
        return 1

    if not POSTS_PATH.exists():
        print(f"[comment] {POSTS_PATH} not found. Create it with your target post URLs.",
              file=sys.stderr)
        return 1

    raw        = json.loads(POSTS_PATH.read_text())
    all_posts  = [p for p in raw.get("posts", []) if "example" not in p.get("url", "")]

    if not all_posts:
        print("[comment] No posts in target_posts.json (remove the example entry and add real URLs).",
              file=sys.stderr)
        return 1

    already_commented = load_log()

    # Filter already-commented by URL (use URL as the dedup key since we may not have media ID yet)
    pending = [p for p in all_posts if p["url"] not in already_commented][:MAX_COMMENTS]

    print(f"[comment] {'DRY RUN — ' if DRY_RUN else ''}"
          f"{len(all_posts)} posts in config, {len(pending)} pending, "
          f"max {MAX_COMMENTS} per run")

    succeeded = 0
    failures: list[str] = []

    for entry in pending:
        url     = entry["url"]
        context = entry.get("context") or entry.get("note") or ""
        label   = f"post {url}"

        # Skip placeholder notes
        if "replace" in context.lower():
            context = ""

        # Generate comment first
        try:
            comment_text = generate_comment(url, context)
        except Exception as e:
            print(f"[comment] SKIP {label}: generate failed: {e}", file=sys.stderr)
            failures.append(f"generate {label}: {e}")
            continue

        if DRY_RUN:
            print(f"[comment] PLAN {label}")
            print(f"            context : {context or '(none)'}")
            print(f"            comment : {comment_text}")
            already_commented.add(url)
            succeeded += 1
            continue

        # Resolve URL → media ID
        media_id = resolve_media_id(url)
        if not media_id:
            print(f"[comment] SKIP {label}: could not resolve media ID", file=sys.stderr)
            failures.append(f"resolve {label}")
            continue

        try:
            comment_id = post_comment(media_id, comment_text)
            print(f"[comment] OK {label} -> comment {comment_id}: {comment_text}")
            already_commented.add(url)
            succeeded += 1
        except Exception as e:
            print(f"[comment] FAIL {label}: {e}", file=sys.stderr)
            failures.append(f"{label}: {e}")

        time.sleep(COMMENT_DELAY)

    save_log(already_commented)

    summary = (f"PurchasingCorp IG comments: {succeeded}/{len(pending)} posted"
               + (f", {len(failures)} failed" if failures else ""))
    print(f"[comment] {summary}")
    if failures:
        notify_discord(summary + "\n" + "\n".join(failures[:10]))
    elif succeeded and not DRY_RUN:
        notify_discord(summary)

    if pending and succeeded == 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(run())
