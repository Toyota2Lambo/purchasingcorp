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
#   single image : create container (media_type=IMAGE, image_url[, text])
#                  -> poll status == FINISHED -> threads_publish
#   carousel     : create N item containers (media_type=IMAGE,
#                     is_carousel_item=true)
#                  -> create parent (media_type=CAROUSEL, children=csv,
#                     text) -> publish parent
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
#                            https://purchasingcorp.vercel.app
#   THREADS_API_VERSION      default v1.0
#   THREADS_DRY_RUN=1        plan only, no API calls
#   THREADS_INCLUDE_STORIES=1  also post story images (default: posts only)
#   THREADS_TEXT_LIMIT       max post length, default 500
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
    or "https://purchasingcorp.vercel.app"
).rstrip("/")

TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
USER_ID = os.environ.get("THREADS_USER_ID") or os.environ.get("THREADS_ACCOUNT_ID") or "me"

TEXT_LIMIT = int(os.environ.get("THREADS_TEXT_LIMIT", "500"))
DELAY_BETWEEN_POSTS_S = int(os.environ.get("DELAY_BETWEEN_POSTS_S", "6"))
IMAGE_DEPLOY_TIMEOUT_S = int(os.environ.get("IMAGE_DEPLOY_TIMEOUT_S", "180"))
CONTAINER_POLL_TRIES = int(os.environ.get("THREADS_CONTAINER_POLL_TRIES", "30"))
CONTAINER_POLL_DELAY_S = int(os.environ.get("THREADS_CONTAINER_POLL_DELAY_S", "3"))

DRY_RUN = os.environ.get("THREADS_DRY_RUN", "").lower() in ("1", "true", "yes")
INCLUDE_STORIES = os.environ.get("THREADS_INCLUDE_STORIES", "").lower() in ("1", "true", "yes")
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


def publish_single_image(image_url: str, text: str) -> str:
    params = {"media_type": "IMAGE", "image_url": image_url}
    if text:
        params["text"] = text
    cid = create_container(params)
    wait_container(cid)
    return publish_container(cid)


def publish_carousel(image_urls: list, text: str) -> str:
    if not (2 <= len(image_urls) <= 20):
        raise ValueError(f"carousel needs 2-20 images, got {len(image_urls)}")
    children = []
    for url in image_urls:
        cid = create_container({"media_type": "IMAGE", "image_url": url,
                                "is_carousel_item": "true"})
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
    """Compose post text and cap it at the Threads length limit."""
    caption = (caption or "").strip()
    tags = " ".join(hashtags or [])
    text = f"{caption}\n\n{tags}".strip() if tags else caption
    if len(text) > TEXT_LIMIT:
        # Prefer to keep the caption whole; drop hashtags before truncating it.
        if caption and len(caption) <= TEXT_LIMIT:
            text = caption
        if len(text) > TEXT_LIMIT:
            text = text[:TEXT_LIMIT].rstrip()
    return text


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
        text = build_text(post.get("caption", ""), post.get("hashtags"))
        is_carousel = len(urls) > 1
        label = f"post {idx} ({post.get('role')}, {'carousel x' + str(len(urls)) if is_carousel else 'single'})"
        attempted += 1

        if DRY_RUN:
            print(f"[threads] PLAN {label}")
            for u in urls:
                print(f"            {u}")
            print(f"            text: {text[:90].replace(chr(10), ' ')}")
            succeeded += 1
            continue

        try:
            for u in urls:
                if not wait_for_url(u, IMAGE_DEPLOY_TIMEOUT_S):
                    raise RuntimeError(f"image not reachable: {u}")
            media_id = (publish_carousel(urls, text) if is_carousel
                        else publish_single_image(urls[0], text))
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
            label = f"story {idx} ({story.get('template')})"
            attempted += 1

            if DRY_RUN:
                print(f"[threads] PLAN {label}\n            {url}")
                succeeded += 1
                continue

            try:
                if not wait_for_url(url, IMAGE_DEPLOY_TIMEOUT_S):
                    raise RuntimeError(f"image not reachable: {url}")
                media_id = publish_single_image(url, "")
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
