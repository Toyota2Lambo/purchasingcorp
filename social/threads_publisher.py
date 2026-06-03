#!/usr/bin/env python3
# ============================================================
# PURCHASINGCORP — Threads publisher
# ============================================================
# Reads a day's manifest.json (written by renderer.js) and publishes the
# rendered PNGs to Meta Threads via the Threads API. It reuses the SAME
# content/manifest and the SAME public Vercel-served PNGs as the Instagram
# publisher — nothing extra has to be generated or rendered.
#
# Threads is a text/image feed (there is no "stories" format), so by
# default this publishes the FEED POSTS only — single image + carousel —
# and skips the IG-only stories. Set THREADS_INCLUDE_STORIES=1 to also
# post each story image as a plain (text-less) image post.
#
# Publishing flow (per the Threads Content Publishing API):
#   single image : create container (media_type=IMAGE, image_url[, text, alt_text])
#                  -> poll status == FINISHED -> threads_publish
#   carousel     : create N item containers (media_type=IMAGE,
#                     is_carousel_item=true[, alt_text])
#                  -> create parent (media_type=CAROUSEL, children=csv,
#                     text) -> publish parent
#
# Threads-native text: unlike Instagram, Threads surfaces ONE topic tag per
# post and treats a stack of hashtags as spam (which can suppress reach), so
# we append at most THREADS_MAX_TAGS (default 1). Every image also gets
# alt_text for screen readers. URLs in the caption stay put — Threads makes
# them tappable, so the CTA link doubles as the post's link.
#
# State mention (Threads only): posts that NAME a specific US state tend to
# get more reach with that state's audience, so each feed post appends one
# short location line naming a state. The state + phrasing rotate
# DETERMINISTICALLY by date + post index — same date/post always yields the
# same text (so a re-run republishes identically; this publisher has no
# dedupe) — while different posts in a day hit DIFFERENT states to widen the
# net. Every line keeps the nationwide truth ("all 50 states" / "free
# shipping"), so naming a state never implies we only serve it. The Instagram
# publisher is intentionally left untouched. Toggle with THREADS_STATE_MENTION.
#
# The images must be reachable at PUBLIC URLs. The renderer's PNGs are
# committed and served by Vercel, so we first poll each image URL until it
# returns 200 (the deploy has landed) before handing it to Threads.
#
# Required env:
#   THREADS_ACCESS_TOKEN     long-lived Threads API token
#                            (scopes: threads_basic, threads_content_publish)
#   THREADS_USER_ID          numeric Threads user id to publish to (or "me")
# Optional env:
#   THREADS_PUBLIC_BASE_URL  default: IG_PUBLIC_BASE_URL or
#                            https://purchasingcorp.com
#   THREADS_API_VERSION      default v1.0
#   THREADS_DRY_RUN=1        plan only, no API calls
#   THREADS_INCLUDE_STORIES=1  also post story images (default: posts only)
#   THREADS_TEXT_LIMIT       max post length, default 500
#   THREADS_MAX_TAGS         hashtags to append, default 1 (Threads-native)
#   THREADS_STATE_MENTION=0  don't append a state-naming location line
#   THREADS_STATES           comma-sep pool to rotate (default: all 50 states)
#   THREADS_ALT_TEXT=0       skip image alt text (accessibility; default on)
#   DISCORD_WEBHOOK_URL      optional run summary
#
# Usage:
#   python social/threads_publisher.py                       # today
#   python social/threads_publisher.py --date 2026-06-01
#   python social/threads_publisher.py --only post:1
#   THREADS_DRY_RUN=1 python social/threads_publisher.py --sample
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
ROOT = HERE.parent

API_VERSION = os.environ.get("THREADS_API_VERSION", "v1.0")
GRAPH_BASE = f"https://graph.threads.net/{API_VERSION}"
BASE_URL = (
    os.environ.get("THREADS_PUBLIC_BASE_URL")
    or os.environ.get("IG_PUBLIC_BASE_URL")
    or "https://purchasingcorp.com"
).rstrip("/")

TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
USER_ID = os.environ.get("THREADS_USER_ID") or os.environ.get("THREADS_ACCOUNT_ID") or "me"

TEXT_LIMIT = int(os.environ.get("THREADS_TEXT_LIMIT", "500"))
# Threads surfaces ONE topic tag per post and reads a stack of hashtags as
# spam (an Instagram habit), which can suppress reach. Keep at most this many,
# most-relevant-first. Default 1; raise to 2-3 only with a deliberate reason.
MAX_TAGS = int(os.environ.get("THREADS_MAX_TAGS", "1"))
# Accessibility: alt text label per image. Threads documents no hard limit;
# keep labels concise. Set THREADS_ALT_TEXT=0 to skip them.
ALT_TEXT_ENABLED = os.environ.get("THREADS_ALT_TEXT", "1").lower() not in ("0", "false", "no")
ALT_TEXT_LIMIT = int(os.environ.get("THREADS_ALT_TEXT_LIMIT", "420"))
DELAY_BETWEEN_POSTS_S = int(os.environ.get("DELAY_BETWEEN_POSTS_S", "6"))
IMAGE_DEPLOY_TIMEOUT_S = int(os.environ.get("IMAGE_DEPLOY_TIMEOUT_S", "180"))
CONTAINER_POLL_TRIES = int(os.environ.get("THREADS_CONTAINER_POLL_TRIES", "30"))
CONTAINER_POLL_DELAY_S = int(os.environ.get("THREADS_CONTAINER_POLL_DELAY_S", "3"))

DRY_RUN = os.environ.get("THREADS_DRY_RUN", "").lower() in ("1", "true", "yes")
INCLUDE_STORIES = os.environ.get("THREADS_INCLUDE_STORIES", "").lower() in ("1", "true", "yes")
# Append a state-naming location line to each post (Threads reach booster).
# On by default; set THREADS_STATE_MENTION=0 to disable.
STATE_MENTION_ENABLED = os.environ.get("THREADS_STATE_MENTION", "1").lower() not in ("0", "false", "no")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

UA = "PurchasingCorp-Social/1.0"


# ------------------------------------------------------------
# HTTP plumbing (stdlib only)
# ------------------------------------------------------------
def _request(method: str, url: str, data: dict = None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": UA})
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
    return _request("GET", f"{GRAPH_BASE}/{path}?{urllib.parse.urlencode(params)}")


def graph_post(path: str, params: dict):
    return _request("POST", f"{GRAPH_BASE}/{path}", params)


def wait_for_url(url: str, timeout: int) -> bool:
    """Poll a public URL until it serves 200 (Vercel deploy has landed)."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, method="GET", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                if 200 <= r.status < 300:
                    return True
                last = r.status
        except urllib.error.HTTPError as e:
            last = e.code
        except Exception as e:
            last = str(e)
        time.sleep(5)
    print(f"[threads] timed out waiting for {url} (last={last})", file=sys.stderr)
    return False


# ------------------------------------------------------------
# Threads API operations
# ------------------------------------------------------------
def create_container(params: dict) -> str:
    params = dict(params, access_token=TOKEN)
    status, data = graph_post(f"{USER_ID}/threads", params)
    if status >= 300 or "id" not in data:
        raise RuntimeError(f"container create failed ({status}): {data}")
    return data["id"]


def wait_container(container_id: str) -> None:
    """Poll the container until Threads reports it FINISHED (media processed)."""
    last = None
    for _ in range(CONTAINER_POLL_TRIES):
        status, data = graph_get(container_id, {"fields": "status,error_message",
                                                "access_token": TOKEN})
        code = data.get("status")
        last = code or data
        if code in ("FINISHED", "PUBLISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {container_id} returned {code}: {data}")
        time.sleep(CONTAINER_POLL_DELAY_S)
    raise RuntimeError(f"container {container_id} not FINISHED after "
                       f"{CONTAINER_POLL_TRIES * CONTAINER_POLL_DELAY_S}s (last={last})")


def publish_container(creation_id: str) -> str:
    status, data = graph_post(f"{USER_ID}/threads_publish",
                              {"creation_id": creation_id, "access_token": TOKEN})
    if status >= 300 or "id" not in data:
        raise RuntimeError(f"threads_publish failed ({status}): {data}")
    return data["id"]


def publish_single_image(image_url: str, text: str, alt_text: str = "") -> str:
    params = {"media_type": "IMAGE", "image_url": image_url}
    if text:
        params["text"] = text
    if alt_text:
        params["alt_text"] = alt_text
    cid = create_container(params)
    wait_container(cid)
    return publish_container(cid)


def publish_carousel(image_urls: list, text: str, alts: list = None) -> str:
    if not (2 <= len(image_urls) <= 20):
        raise ValueError(f"carousel needs 2-20 images, got {len(image_urls)}")
    alts = alts or []
    children = []
    for i, url in enumerate(image_urls):
        item = {"media_type": "IMAGE", "image_url": url, "is_carousel_item": "true"}
        if i < len(alts) and alts[i]:
            item["alt_text"] = alts[i]  # alt_text lives on the item, not the parent
        cid = create_container(item)
        wait_container(cid)
        children.append(cid)
    parent_params = {"media_type": "CAROUSEL", "children": ",".join(children)}
    if text:
        parent_params["text"] = text
    parent = create_container(parent_params)
    wait_container(parent)
    return publish_container(parent)


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def build_text(caption: str, hashtags) -> str:
    """Compose a Threads post's text: caption + at most MAX_TAGS topic tags.

    Threads is not Instagram. It surfaces a single topic tag per post and
    treats a stack of hashtags as spam, which can suppress reach — so we keep
    only the most-relevant tag(s) (MAX_TAGS, default 1) on their own line at
    the end. Any URL in the caption is left intact: Threads makes it tappable.
    """
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
        # Prefer to keep the caption whole; drop tags before truncating it.
        if caption and len(caption) <= TEXT_LIMIT:
            text = caption
        if len(text) > TEXT_LIMIT:
            text = text[:TEXT_LIMIT].rstrip()
    return text


# All 50 states, population-ordered (bigger markets surface first each cycle).
# The rotation below uses a different one for every post and won't repeat a
# state for ~13 days, so naming "a different state every time" holds in
# practice. Every phrasing keeps the nationwide truth, so this never claims
# state-only service.
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

# Each phrasing NAMES a state ({s}) and keeps the "all states / free shipping"
# truth. Wording rotates too, so a day's posts don't read as copy-paste.
_STATE_LINES = [
    "Serving {s} and all 50 states — same-day cash, free shipping.",
    "In {s}? We buy from every state — get your real cash offer today.",
    "Cash for your tech in {s} and nationwide, paid the same day.",
    "From {s} to coast to coast — free shipping, cash today.",
]


def _states_pool():
    raw = os.environ.get("THREADS_STATES", "")
    pool = [s.strip() for s in raw.split(",") if s.strip()] if raw else list(_DEFAULT_STATES)
    return pool or list(_DEFAULT_STATES)


def state_line_for(date_str: str, idx: int) -> str:
    """Pick the state-naming location line for one post.

    Deterministic by date + 1-based post index: the same date/post always
    yields the same state and phrasing (so a re-run republishes identical
    text), while different posts in a day land on DIFFERENT states and the
    window advances day to day.
    """
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
    """Append the state-naming location line to a caption (no-op if disabled)."""
    caption = (caption or "").strip()
    if not STATE_MENTION_ENABLED:
        return caption
    line = state_line_for(date_str, idx)
    return f"{caption}\n\n{line}" if caption else line


def alt_text_for(post: dict, idx: int = 1, total: int = 1) -> str:
    """Build a concise screen-reader label for a post's image.

    The cards are mostly text rendered as an image, so we name the brand and
    card type. Single images add the caption's opening line; carousel items
    get a position ("slide 2 of 4") so the sequence stays navigable.
    """
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


def image_url_for(base_path: str, file: str) -> str:
    return f"{BASE_URL}/{base_path}/{file}"


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
        _request("POST", DISCORD_WEBHOOK_URL, {"content": summary})
    except Exception as e:
        print(f"[threads] discord notify failed: {e}", file=sys.stderr)


def load_manifest(date_str: str, sample: bool, manifest_arg: str):
    if manifest_arg:
        p = Path(manifest_arg)
    elif sample:
        p = HERE / "_sample" / "manifest.json"
    else:
        p = HERE / date_str / "manifest.json"
    if not p.exists():
        print(f"[threads] manifest not found: {p}\n"
              f"          run the renderer first.", file=sys.stderr)
        sys.exit(1)
    return json.loads(p.read_text())


# ------------------------------------------------------------
# Driver
# ------------------------------------------------------------
def run() -> int:
    ap = argparse.ArgumentParser(description="Publish a day's rendered PNGs to Threads.")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today, UTC).")
    ap.add_argument("--manifest", help="Explicit manifest.json path.")
    ap.add_argument("--sample", action="store_true", help="Use social/_sample/manifest.json.")
    ap.add_argument("--only", default="", help="Filter, e.g. post:1,story:2 or 'posts'.")
    args = ap.parse_args()

    if not DRY_RUN and (not TOKEN or not USER_ID):
        print("[threads] THREADS_ACCESS_TOKEN and THREADS_USER_ID are required "
              "(or set THREADS_DRY_RUN=1 to plan only).", file=sys.stderr)
        return 1

    date_str = args.date or dt.datetime.now(dt.timezone.utc).date().isoformat()
    manifest = load_manifest(date_str, args.sample, args.manifest)
    base_path = manifest.get("base_path") or f"social/{date_str}"

    only_posts, only_stories, all_posts, all_stories = parse_only(args.only)
    select_all = not (only_posts or only_stories or all_posts or all_stories)

    posts = manifest.get("posts", [])
    stories = manifest.get("stories", [])

    print(f"[threads] {'DRY RUN — ' if DRY_RUN else ''}base={BASE_URL}/{base_path}")
    print(f"[threads] {len(posts)} posts, {len(stories)} stories in manifest "
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
        urls = [image_url_for(base_path, f) for f in files]
        caption = with_state_mention(post.get("caption", ""), date_str, idx)
        text = build_text(caption, post.get("hashtags"))
        is_carousel = len(urls) > 1
        alts = [alt_text_for(post, i + 1, len(urls)) for i in range(len(urls))]
        label = f"post {idx} ({post.get('role')}, {'carousel x' + str(len(urls)) if is_carousel else 'single'})"
        attempted += 1

        if DRY_RUN:
            print(f"[threads] PLAN {label}")
            for u in urls:
                print(f"            {u}")
            print(f"            text: {text[:90].replace(chr(10), ' ')}")
            if STATE_MENTION_ENABLED:
                print(f"            state: {state_line_for(date_str, idx)}")
            print(f"            alt:  {(alts[0] or '—')[:90]}")
            succeeded += 1
            continue

        try:
            for u in urls:
                if not wait_for_url(u, IMAGE_DEPLOY_TIMEOUT_S):
                    raise RuntimeError(f"image not reachable: {u}")
            media_id = (publish_carousel(urls, text, alts) if is_carousel
                        else publish_single_image(urls[0], text, alts[0]))
            print(f"[threads] OK {label} -> media {media_id}")
            succeeded += 1
        except Exception as e:
            print(f"[threads] FAIL {label}: {e}", file=sys.stderr)
            failures.append(f"{label}: {e}")
        time.sleep(DELAY_BETWEEN_POSTS_S)

    # ---- stories (opt-in; posted as plain image posts) ----
    if INCLUDE_STORIES:
        for story in stories:
            idx = story.get("index")
            if not (select_all or all_stories or idx in only_stories):
                continue
            url = image_url_for(base_path, story.get("file"))
            story_alt = (f"PurchasingCorp {story.get('template')} story"
                         if ALT_TEXT_ENABLED else "")
            label = f"story {idx} ({story.get('template')})"
            attempted += 1

            if DRY_RUN:
                print(f"[threads] PLAN {label}\n            {url}")
                succeeded += 1
                continue

            try:
                if not wait_for_url(url, IMAGE_DEPLOY_TIMEOUT_S):
                    raise RuntimeError(f"image not reachable: {url}")
                media_id = publish_single_image(url, "", story_alt)
                print(f"[threads] OK {label} -> media {media_id}")
                succeeded += 1
            except Exception as e:
                print(f"[threads] FAIL {label}: {e}", file=sys.stderr)
                failures.append(f"{label}: {e}")
            time.sleep(DELAY_BETWEEN_POSTS_S)

    # ---- summary ----
    summary = (f"PurchasingCorp Threads {date_str}: {succeeded}/{attempted} published"
               + (f", {len(failures)} failed" if failures else ""))
    print(f"[threads] {summary}")
    if failures:
        notify_discord(summary + "\n" + "\n".join(failures[:10]))
    elif attempted and not DRY_RUN:
        notify_discord(summary)

    # Exit non-zero only if everything we tried failed.
    if attempted and succeeded == 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(run())
