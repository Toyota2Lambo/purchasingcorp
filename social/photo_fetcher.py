#!/usr/bin/env python3
# ============================================================
# PURCHASINGCORP — photo_fetcher
# ============================================================
# Turns a short scene description into a real, PUBLIC image URL that
# the renderer can load (Puppeteer fetches it at render time and bakes
# the pixels into the PNG, so the URL only has to resolve during the
# render step — not at publish time).
#
# Strategy:
#   1. Unsplash Search API  (if UNSPLASH_ACCESS_KEY is set) — real,
#      tasteful stock photography, sized + cropped to the slot via the
#      built-in imgix params on urls.raw.
#   2. Picsum fallback       (always available, no key) — a deterministic
#      placeholder seeded by the query so a given scene is stable across
#      runs and across the carousel.
#
# The generator calls:  fetch_photo(query, orientation="portrait"|"landscape")
# and treats "" / None as "no photo" (the template then renders its
# graphical fallback).
#
# Results are cached to a temp JSON file keyed by orientation+query so a
# re-run (or the same scene used twice in a day) doesn't re-hit the API.
# The cache lives in the system temp dir by default so it never lands in
# the repo; override with PHOTO_CACHE_PATH if you want it elsewhere.
#
# Stdlib only.  Quick check:
#   python social/photo_fetcher.py "iphone on a wooden desk" portrait
# ============================================================

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import urllib.parse
import urllib.request

USER_AGENT = "PurchasingCorp-Social/1.0"
UNSPLASH_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "").strip()
UNSPLASH_SEARCH = "https://api.unsplash.com/search/photos"
TIMEOUT_S = float(os.environ.get("PHOTO_TIMEOUT_S", "12"))
CACHE_PATH = (os.environ.get("PHOTO_CACHE_PATH")
              or os.path.join(tempfile.gettempdir(), "purchasingcorp-photo-cache.json"))

# Pixel slots per orientation. The renderer shoots at 2x, but these are
# generous enough to stay crisp either way and keep request payload sane.
DIMS = {
    "portrait": (1080, 1920),
    "landscape": (1440, 1080),
    "squarish": (1080, 1080),
}


def _norm_orientation(o: str) -> str:
    o = (o or "").lower().strip()
    if o in ("portrait", "vertical", "story", "tall"):
        return "portrait"
    if o in ("squarish", "square", "feed-square"):
        return "squarish"
    return "landscape"


def _dims(orientation: str):
    return DIMS.get(orientation, DIMS["landscape"])


# ---- cache -------------------------------------------------
def _load_cache() -> dict:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        pass  # caching is a nicety, never fatal


# ---- http --------------------------------------------------
def _get_json(url: str, headers: dict = None) -> dict:
    base = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        base.update(headers)
    req = urllib.request.Request(url, headers=base)
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read().decode("utf-8"))


# ---- providers ---------------------------------------------
def _picsum(query: str, orientation: str) -> str:
    """Deterministic placeholder. Always works, no key required."""
    w, h = _dims(orientation)
    seed = hashlib.sha1(f"{orientation}:{query}".encode("utf-8")).hexdigest()[:16]
    return f"https://picsum.photos/seed/{seed}/{w}/{h}"


def _trigger_download(download_location: str) -> None:
    """Unsplash API guideline: ping the download endpoint when a photo is
    used. Best-effort; failure here never matters to us."""
    if not download_location:
        return
    try:
        _get_json(download_location, headers={"Authorization": f"Client-ID {UNSPLASH_KEY}"})
    except Exception:
        pass


def _unsplash(query: str, orientation: str):
    if not UNSPLASH_KEY:
        return None
    w, h = _dims(orientation)
    params = urllib.parse.urlencode({
        "query": query,
        "orientation": orientation,
        "per_page": 12,
        "content_filter": "high",
    })
    data = _get_json(f"{UNSPLASH_SEARCH}?{params}",
                     headers={"Authorization": f"Client-ID {UNSPLASH_KEY}"})
    results = data.get("results") or []
    if not results:
        return None

    # Deterministic pick by query hash so the same scene resolves to the
    # same photo on every run (stable carousels, reproducible grids).
    idx = int(hashlib.sha1(query.encode("utf-8")).hexdigest(), 16) % len(results)
    photo = results[idx]
    urls = photo.get("urls") or {}

    raw = urls.get("raw")
    if raw:
        sep = "&" if "?" in raw else "?"
        sized = raw + sep + urllib.parse.urlencode({
            "w": w, "h": h, "fit": "crop", "crop": "entropy",
            "q": 80, "fm": "jpg", "auto": "format",
        })
    else:
        sized = urls.get("regular") or urls.get("full")
    if not sized:
        return None

    _trigger_download((photo.get("links") or {}).get("download_location"))
    return sized


# ---- entry point -------------------------------------------
def fetch_photo(query: str, orientation: str = "landscape") -> str:
    """Return a public image URL for `query`, or "" if nothing resolves."""
    query = (query or "").strip()
    if not query:
        return ""
    orientation = _norm_orientation(orientation)
    key = f"{orientation}:{query.lower()}"

    cache = _load_cache()
    cached = cache.get(key)
    if cached:
        return cached

    url = None
    try:
        url = _unsplash(query, orientation)
    except Exception as e:  # network / quota / parse — fall through to picsum
        print(f"[photo_fetcher] unsplash failed for '{query}': {e}", file=sys.stderr)
    if not url:
        url = _picsum(query, orientation)

    cache[key] = url
    _save_cache(cache)
    return url


def _main(argv) -> int:
    if len(argv) < 2:
        print("usage: photo_fetcher.py \"<query>\" [portrait|landscape|squarish]",
              file=sys.stderr)
        return 2
    query = argv[1]
    orientation = argv[2] if len(argv) > 2 else "landscape"
    url = fetch_photo(query, orientation=orientation)
    source = "unsplash" if (UNSPLASH_KEY and "unsplash" in url) else "picsum"
    print(f"[{source}] {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
