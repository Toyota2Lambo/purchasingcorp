#!/usr/bin/env python3
# ============================================================
# PURCHASINGCORP — social content generator
# ============================================================
# Produces ONE day's Instagram content package as JSON:
#
#   social/<YYYY-MM-DD>/content.json
#
# Shape (the "unified content model"):
#   {
#     generated_at, model, theme, source,
#     content: {
#       posts:   [ {role, caption, hashtags, size, slides:[{template, fields}]} ],
#       stories: [ {template, fields} ]
#     }
#   }
#
# A post with one slide is a single image; 2+ slides is a carousel.
# Stories are always rendered at 1080x1920.
#
# How it stays honest: it reads social/pricing.json (produced by
# dump_pricing.js from the live site data) and hands Claude only real
# numbers. Claude is told, hard, never to invent a price.
#
# To dodge the "compiled grammar too large" failure that hits deeply
# typed tool schemas, each slide's fields arrive as a JSON *string*
# (fields_json) that we parse after the call — the tool schema itself
# stays tiny.
#
# Usage:
#   python social/social_generator.py                 # generate today (needs ANTHROPIC_API_KEY)
#   python social/social_generator.py --date 2026-06-01
#   python social/social_generator.py --self-test      # no API: copy the sample fixture
# ============================================================

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PRICING_JSON = HERE / "pricing.json"
SAMPLE_PAYLOADS = HERE / "sample-payloads.json"

# `or` (not get's default) so an empty env var — e.g. an unset CI
# variable that still renders as "" — falls back instead of blanking out.
MODEL = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-5"
# A full day is 10 posts (each with a caption + 20-28 hashtags + slide
# fields) plus 2 stories. That JSON blows well past 4096 output tokens, and
# a forced tool call that hits the cap comes back TRUNCATED — the posts[]/
# stories[] arrays arrive partial or missing and validation hard-fails,
# killing the whole day. 16000 gives comfortable headroom; still overridable.
MAX_TOKENS = int(os.environ.get("SOCIAL_MAX_TOKENS") or "16000")
TEMPERATURE = float(os.environ.get("SOCIAL_TEMPERATURE") or "0.7")
# How many times to re-ask the model if a response comes back truncated or
# otherwise unparseable. Generation is non-deterministic, so a transient bad
# roll should never sink the day's run.
GEN_ATTEMPTS = int(os.environ.get("SOCIAL_GEN_ATTEMPTS") or "4")

# Must match the keys in templates-registry.js exactly.
TEMPLATE_NAMES = [
    "offer", "board", "payout", "compare", "stat", "quote",
    "index", "carousel", "cover", "photo-cover", "lifestyle",
]

# Templates whose photo_url field should be resolved to a real image URL.
PHOTO_FIELDS = ("photo_url",)

# Photo-FIRST templates: the image IS the card, so we ALWAYS bake a real
# device photo in (an empty photo_url renders as a black/empty frame).
PHOTO_TEMPLATES_FORCED = ("photo-cover", "lifestyle")
# Templates with a subtle, OPT-IN blurred photo backdrop: only fill when the
# model asked for one via a "PHOTO:" marker (empty hides gracefully).
PHOTO_TEMPLATES_OPTIN = ("cover", "quote")

# ------------------------------------------------------------
# Device photo pool — popular CONSUMER ELECTRONICS scenes.
#
# The fetcher resolves each query to ONE stable Unsplash photo (deterministic
# by query hash), so a LARGE pool of distinct queries == a large pool of
# distinct photos. We rotate through it by date + slot (see device_query_for)
# so the same image never shows up two days running and the whole catalog
# cycles over weeks — no "same photo posted over and over".
#
# Real, searchable terms only — NEVER a fictional model number (Unsplash has
# no "iPhone 17"); a miss there would fall back to a non-device placeholder.
# ------------------------------------------------------------
DEVICE_PHOTO_QUERIES = [
    "iPhone on a wooden desk",
    "smartphone in hand close up",
    "modern smartphone on a marble table",
    "smartphone flat lay on white background",
    "smartphone on a dark surface",
    "stack of smartphones on a table",
    "MacBook laptop on a desk",
    "MacBook Air on a white table",
    "open laptop on a wooden desk",
    "laptop and coffee on a desk",
    "person typing on a laptop",
    "silver laptop close up",
    "sleek laptop in a dark room",
    "iPad tablet on a table",
    "tablet with stylus on a desk",
    "person holding a tablet",
    "tablet flat lay with accessories",
    "Apple Watch on a wrist",
    "smartwatch close up",
    "smartwatch on a table",
    "wireless earbuds on a desk",
    "earbuds charging case in hand",
    "wireless headphones on a desk",
    "over-ear headphones close up",
    "desktop computer on a clean desk",
    "minimalist desk setup with monitor",
    "home office desk with computer",
    "computer monitor on a desk",
    "PlayStation 5 console",
    "game controller on a desk",
    "DualSense controller in hand",
    "gaming console close up",
    "Xbox game console",
    "Xbox controller on a table",
    "Nintendo Switch handheld console",
    "handheld game console in hand",
    "handheld gaming PC",
    "portable game console close up",
    "VR headset on a table",
    "person wearing a VR headset",
    "gaming setup with RGB lights",
    "mechanical keyboard and mouse",
    "modern gaming desk setup",
    "tech gadgets flat lay",
    "electronics on a desk",
    "smartphone and laptop on a desk",
    "apple devices on a table",
    "new gadgets flat lay",
    "game controllers collection",
    "smartphone with cash on a table",
]
# Stride is prime and > the most photos any single day can use, and is
# coprime with the pool length (50) — so the per-day starting offset advances
# cleanly and every query is reached as the date marches on, with no within-day
# or day-boundary collisions.
_PHOTO_STRIDE = 7


def device_query_for(date_str: str, idx: int) -> str:
    """Pick the idx-th device-photo query for a given day, rotating through the
    whole pool so photos don't repeat day to day. Deterministic, so re-running a
    day renders the identical image (stable PNGs / idempotent backfills)."""
    pool = DEVICE_PHOTO_QUERIES
    try:
        seed = dt.date.fromisoformat(date_str).toordinal()
    except Exception:
        seed = 0
    return pool[(seed * _PHOTO_STRIDE + idx) % len(pool)]

# ------------------------------------------------------------
# Daily theme rotation (deterministic by day-of-month so the grid
# moves through categories/topics without repeating back to back).
# Only categories that carry live dollar prices are eligible to be
# "featured" — quote-only ones (iPad, accessories) are never headlined
# with a fabricated number.
# ------------------------------------------------------------
FEATURE_CATEGORIES = ["iphone", "macbook-air", "mac-mini", "apple-watch", "consoles"]

EDU_TOPICS = [
    "how selling to us works, end to end",
    "how to wipe and prep your iPhone before selling",
    "what actually affects your payout (condition, storage, lock status)",
    "unlocked vs. carrier-locked: why it changes your offer",
    "why cash today beats a carrier trade-in credit",
    "how we keep your sale safe (data wipe, insured label, same-day pay)",
]

COMPARE_ANGLES = [
    "our cash offer vs. a typical carrier trade-in credit",
    "our cash offer vs. a big-box store gift card",
    "our cash offer vs. waiting weeks on a marketplace listing",
    "same-day cash vs. mail-in services that pay later",
]


def pick_theme(date: dt.date) -> dict:
    d = date.day - 1
    cat = FEATURE_CATEGORIES[d % len(FEATURE_CATEGORIES)]
    return {
        "featured_category": cat,
        "educational_topic": EDU_TOPICS[d % len(EDU_TOPICS)],
        "comparison_angle": COMPARE_ANGLES[d % len(COMPARE_ANGLES)],
    }


# ------------------------------------------------------------
# Pricing context
# ------------------------------------------------------------
def load_pricing() -> dict:
    if not PRICING_JSON.exists():
        print(f"[generator] WARNING: {PRICING_JSON.name} not found — "
              f"run `node social/dump_pricing.js` first. Proceeding with no live prices.",
              file=sys.stderr)
        return {"categories": {}, "category_labels": {}}
    with PRICING_JSON.open() as f:
        return json.load(f)


def build_pricing_context(pricing: dict, featured: str) -> str:
    """A compact, human-readable block of REAL numbers for the prompt."""
    cats = pricing.get("categories", {})
    if not cats:
        return "(No pricing data available. Do not state any dollar figures; " \
               "tell people to get a quote at purchasingcorp.com instead.)"

    lines = []
    # Feature the day's category first, with a fuller row sample.
    order = [featured] + [c for c in cats if c != featured]
    for slug in order:
        c = cats.get(slug)
        if not c:
            continue
        label = c.get("label", slug)
        if c.get("quote_only"):
            lines.append(f"- {label} [{slug}]: quote-only (prices show \"Contact\"). "
                         f"Do NOT state a dollar figure for this category.")
            continue
        top = c.get("top") or {}
        top_str = f"top {top.get('price')} ({top.get('model')})" if top else "n/a"
        # A few representative real rows (model + first price column).
        sample_rows = []
        for row in (c.get("rows") or [])[:8]:
            if len(row) >= 2 and isinstance(row[1], str) and row[1].strip().startswith("$"):
                sample_rows.append(f"{row[0]} = {row[1]}")
            if len(sample_rows) >= 6:
                break
        rows_str = "; ".join(sample_rows) if sample_rows else "see site"
        n = " (FEATURED TODAY)" if slug == featured else ""
        lines.append(f"- {label} [{slug}]{n}: {top_str}. Real rows: {rows_str}")

    return "\n".join(lines)


# ------------------------------------------------------------
# Per-template field contract (mirrors templates-registry.js).
# Claude must emit fields_json containing exactly these keys.
# ------------------------------------------------------------
FIELD_CONTRACT = """\
TEMPLATE FIELD CONTRACTS. fields_json for each template must contain EXACTLY these keys:

offer (single feed post; the workhorse "we buy X")
  tag, eyebrow, headline_html, sub_html, c1_label, c1_value, c2_label, c2_value, c3_label, c3_value
  headline_html states the cash; wrap the dollar figure in <em>...</em>. c1_value = top payout (emerald),
  c2_value = turnaround (e.g. "Same day"), c3_value = condition range.

board (single feed post; a price list for one category)
  tag, eyebrow, title_html, rows, note_html
  rows = ARRAY of {"model","price","note"(optional),"soft"(optional true/false)}. 4-6 REAL model+price pairs.
  Use "soft": true to mute a non-dollar price like "Contact".

payout (single feed post; a payout receipt, PROOF)
  tag, slip_title, slip_ref, device, condition, method, turnaround, amount, status
  amount = a REAL dollar figure from the data. status usually "PAID". Anonymized: never a person's name.

compare (single feed post; "we pay more")
  tag, eyebrow, title_html, bars, note_html
  bars = ARRAY of {"label","value","pct"(0-100),"kind":"us" or "alt"}. EXACTLY one kind:"us" (our real offer, pct 100),
  then 1-2 kind:"alt" rows (competitor ESTIMATES with "~", lower pct). note_html MUST say rival figures are typical estimates.

stat (story or feed; one big number)
  eyebrow, stat_value, stat_unit, caption_html, source
  stat_value must be SHORT (renders ~440px): e.g. "50", "$675", "8". stat_unit can be "%", "categories", "" etc.

quote (story or feed; editorial pull-quote)
  quote_text_html, quote_attrib, photo_url
  Big serif line in the brand voice. photo_url = "" (no backdrop) or "PHOTO: device" for a subtle photo backdrop.

index (single feed post; a 3x2 reference grid)
  tag, eyebrow, title_html, cells, note_html
  cells = ARRAY of EXACTLY 6 {"label","num","foot","tone":"accent"|"neg"|""}. Numbers must be REAL (use category tops).

carousel (use as the SLIDES of ONE feed carousel post; 3-4 slides)
  step_num, step_label, eyebrow, headline_html, body_html
  step_num like "01". body_html may use <strong>...</strong> for one bold phrase. Tells the educational topic step by step.

cover (story; a magazine-cover announcement)
  issue, date_label, section, headline_html, deck_html, photo_url
  Short, page-dominating headline. photo_url = "" or "PHOTO: device" for a subtle photo backdrop.

photo-cover (story or feed; atmospheric photo headline)
  tag, eyebrow, headline_html, deck_html, photo_url, photo_credit
  photo_url = "PHOTO: device" (REQUIRED: this template is a full-bleed device photo). A real device image is
  auto-selected from our library and baked in, so keep the headline about the OUTCOME (cash today), not a model.
  photo_credit like "PHOTO · UNSPLASH".

lifestyle (feed or story; aspirational, outcome-forward)
  tag, eyebrow, headline_html, sub_html, photo_url, photo_credit
  photo_url = "PHOTO: device" (REQUIRED: a framed device photo is auto-selected and baked in). Sells the
  feeling (that drawer phone is cash), not a spec. Keep the copy device-agnostic so any device photo fits.
"""


def build_system_prompt() -> str:
    return f"""\
You write the daily Instagram content for PurchasingCorp, an electronics buyback business.

WHAT THE BUSINESS DOES
PurchasingCorp pays CASH for used and new Apple and gaming gear: iPhones, MacBooks (Air and Pro),
iPads, Apple Watch, Mac mini, game consoles (PlayStation, Xbox, Nintendo, Steam Deck, ROG Ally,
Meta Quest), AirPods and accessories, and bulk lots. People get a real offer, ship with a prepaid
insured label or hand off locally, and get paid the SAME DAY. The business does NOT buy gold or
jewelry anymore, so never mention gold. Site: purchasingcorp.com (quote form at purchasingcorp.com/form).

BRAND VOICE
Direct, confident, benefit-led. A sharp human who runs a buyback shop, not a corporate account.
House lines you can lean on: "cash today", "no games", "same-day payout", "a real number, not a
vague 'up to'", "more than Apple, more than Best Buy". Be specific and a little swaggering, never
fluffy.

OUTPUT
Call the emit_content tool exactly once with posts[] and stories[]. For every slide and story you
provide template + fields_json, where fields_json is a JSON OBJECT ENCODED AS A STRING.

{FIELD_CONTRACT}

COMPOSITION RULES
- Exactly ONE post must be a carousel (size "feed") whose slides are 3-4 "carousel" cards walking
  through the day's educational topic. Every other post has exactly one slide.
- Exactly ONE single feed post MUST be a PHOTO post: template "photo-cover" or "lifestyle", showing a
  real photo of a popular consumer-electronics device (iPhone, MacBook, iPad, Apple Watch, AirPods,
  PlayStation, Xbox, Nintendo Switch, and the like). Set its photo_url to "PHOTO: device". The actual
  image is auto-selected from our photo library and baked in. Keep this post's copy about the OUTCOME
  (cash today), not a model number.
- Feature the day's category in at least one offer or board post.
- The remaining single feed posts: pick varied templates from offer, board, payout, compare, index.
  If you include compare, use the day's comparison angle.
- Stories: pick varied templates from cover, stat, quote, photo-cover.
- size is "feed" for all posts (1:1) and is not set on stories (always 9:16).

HONESTY (hard rules)
- Use ONLY dollar figures present in the PRICING DATA in the user message. Never invent, inflate, or
  round to a nicer number. Quote a price that exists.
- For quote-only categories (iPad, accessories, which show "Contact"): do NOT state a dollar figure.
  Either feature a category that has prices, or say "Contact for your number".
- Competitor numbers are ALWAYS estimates: prefix with "~", call them "typical" trade-in or store-credit
  values, and never present a precise fabricated rival quote.

FORMATTING
- In *_html fields, wrap ONE key phrase in <em>...</em> (it renders as a bold emerald highlight in the
  brand accent color, upright). Use <strong>...</strong> for a single bold phrase inside body_html. No other HTML, ever.
- Captions: 1-3 short sentences plus a CTA to purchasingcorp.com or the form. PLAIN TEXT, no markdown,
  no HTML, no emoji.
- hashtags: 20-28 entries for maximum Instagram reach. Mix THREE kinds: (a) high-volume broad tags
  (#applebuyback, #sellmyiphone, #cashforphones), (b) specific device/buyer-intent tags tied to today's
  theme (#sellmacbookpro, #cashformacmini, #sellmyipad), (c) a few local/intent tags (#sellmytech,
  #electronicsbuyback). Lowercase, no spaces, each starting with '#'. Instagram rejects a caption with
  more than 30 tags, so NEVER exceed 28. No duplicates.
- Never write AI-tell filler: avoid "in today's fast-paced world", "look no further", "unlock"/"unleash"/
  "elevate", "game-changer", "dive in", "we've got you covered", "rest assured", "the world of".
- Never use em dashes (the long "—") or en dashes ("–"); they read as AI-written. Use periods, commas,
  colons, or parentheses instead. Ordinary hyphens in compound words (same-day, carrier-locked, trade-in) are fine.
"""


def build_user_prompt(theme: dict, pricing_ctx: str, date: dt.date,
                      n_posts: int, n_stories: int) -> str:
    label = theme.get("featured_label", theme["featured_category"])
    return f"""\
Create PurchasingCorp's Instagram package for {date.isoformat()}.

TODAY'S THEME
- Featured category: {label} [{theme['featured_category']}]
- Educational topic (use for the carousel): {theme['educational_topic']}
- Comparison angle (use if you make a compare post): {theme['comparison_angle']}

TARGET COUNTS
- {n_posts} posts total, INCLUDING exactly one carousel post (3-4 carousel slides).
- {n_stories} stories.

PRICING DATA (the ONLY dollar figures you may use; "~" competitor numbers must be framed as typical estimates)
{pricing_ctx}

Make the featured category the hero of at least one post. Keep every number real. Call emit_content now.
"""


# ------------------------------------------------------------
# Tool schema — deliberately small (fields_json is a plain string).
# ------------------------------------------------------------
def build_tool_schema() -> dict:
    slide = {
        "type": "object",
        "properties": {
            "template": {"type": "string", "enum": TEMPLATE_NAMES},
            "fields_json": {
                "type": "string",
                "description": "A JSON object (encoded as a string) of this template's fields.",
            },
        },
        "required": ["template", "fields_json"],
    }
    return {
        "type": "object",
        "properties": {
            "posts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "caption": {"type": "string"},
                        "hashtags": {"type": "array", "items": {"type": "string"}},
                        "size": {"type": "string", "enum": ["feed", "story"]},
                        "slides": {"type": "array", "items": slide},
                    },
                    "required": ["role", "caption", "hashtags", "size", "slides"],
                },
            },
            "stories": {"type": "array", "items": slide},
        },
        "required": ["posts", "stories"],
    }


# ------------------------------------------------------------
# Cleaning + validation
# ------------------------------------------------------------
_AI_TELLS = [
    "in today's fast-paced world", "look no further", "game-changer", "game changer",
    "dive in", "we've got you covered", "rest assured", "the world of",
    "unleash", "unlock the", "elevate your",
]


_EMDASH_RE = re.compile(r"\s*[—―]\s*")        # em dash / horizontal bar
_ENDASH_SPACED_RE = re.compile(r"\s+–\s+")    # en dash used as a clause break


def _strip_dashes(s: str) -> str:
    """Em/en dashes read as 'AI wrote this'. The prompt forbids them, but models
    slip, so strip them from every caption and rendered field as a hard
    guarantee. Ordinary hyphens (same-day, trade-in) are left untouched."""
    if not isinstance(s, str) or (
        "—" not in s and "―" not in s and "–" not in s
    ):
        return s
    s = _EMDASH_RE.sub(", ", s)
    s = _ENDASH_SPACED_RE.sub(", ", s)
    s = s.replace("–", "-")                    # tight en dash (ranges) -> hyphen
    s = re.sub(r",\s*,", ", ", s)              # collapse doubled commas
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)     # no space before punctuation
    s = re.sub(r"[ \t]{2,}", " ", s).strip()
    return s


def _sanitize_dashes(v):
    """Recursively apply _strip_dashes to every string in a fields object."""
    if isinstance(v, str):
        return _strip_dashes(v)
    if isinstance(v, list):
        return [_sanitize_dashes(x) for x in v]
    if isinstance(v, dict):
        return {k: _sanitize_dashes(x) for k, x in v.items()}
    return v


def clean_caption(s: str) -> str:
    if not s:
        return ""
    s = (s.replace("’", "'").replace("‘", "'")
           .replace("“", '"').replace("”", '"'))
    s = _strip_dashes(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)   # strip stray markdown bold
    s = re.sub(r"[ \t]+", " ", s).strip()
    low = s.lower()
    for tell in _AI_TELLS:
        if tell in low:
            print(f"[generator] WARNING: caption contains AI-tell '{tell}': {s[:80]}",
                  file=sys.stderr)
    return s


def clean_hashtags(tags) -> list:
    out = []
    for t in (tags or []):
        t = str(t).strip().lower().replace(" ", "")
        if not t:
            continue
        if not t.startswith("#"):
            t = "#" + t
        t = re.sub(r"[^#a-z0-9_]", "", t)
        if len(t) > 1 and t not in out:
            out.append(t)
    # Instagram allows at most 30 hashtags in a caption; cap there. (Threads
    # applies its own much smaller MAX_TAGS at publish time, so this generous
    # cap only ever fills out the IG caption.)
    return out[:30]


def parse_fields(fields_json: str, template: str) -> dict:
    try:
        obj = json.loads(fields_json)
    except (json.JSONDecodeError, TypeError) as e:
        raise ValueError(f"fields_json for '{template}' is not valid JSON: {e}")
    if not isinstance(obj, dict):
        raise ValueError(f"fields_json for '{template}' did not decode to an object")
    return _sanitize_dashes(obj)


def validate_and_normalize(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("tool output is not an object")
    posts = data.get("posts")
    stories = data.get("stories")
    if not isinstance(posts, list) or not isinstance(stories, list):
        raise ValueError("tool output missing posts[]/stories[] arrays")

    norm_posts = []
    for i, p in enumerate(posts):
        slides = p.get("slides") or []
        if not slides:
            raise ValueError(f"post {i} has no slides")
        norm_slides = []
        for s in slides:
            tpl = s.get("template")
            if tpl not in TEMPLATE_NAMES:
                raise ValueError(f"post {i} uses unknown template '{tpl}'")
            norm_slides.append({"template": tpl, "fields": parse_fields(s.get("fields_json", ""), tpl)})
        norm_posts.append({
            "role": str(p.get("role", norm_slides[0]["template"])),
            "caption": clean_caption(p.get("caption", "")),
            "hashtags": clean_hashtags(p.get("hashtags")),
            "size": "feed" if p.get("size") != "story" else "story",
            "slides": norm_slides,
        })

    norm_stories = []
    for s in stories:
        tpl = s.get("template")
        if tpl not in TEMPLATE_NAMES:
            raise ValueError(f"story uses unknown template '{tpl}'")
        norm_stories.append({"template": tpl, "fields": parse_fields(s.get("fields_json", ""), tpl)})

    return {"posts": norm_posts, "stories": norm_stories}


# ------------------------------------------------------------
# Photo guarantee — make sure a real DEVICE photo lands in the grid.
# ------------------------------------------------------------
# Single-slide feed posts we may safely repurpose into a photo post if the
# model forgot to include one at all (last-resort guarantee). The carousel
# (multi-slide) and the price "board" are never touched.
_CONVERTIBLE_TO_PHOTO = ("compare", "index", "payout", "quote", "stat", "offer")


def ensure_feed_photo(content: dict, theme: dict) -> None:
    """Guarantee at least one FEED post is a photo post (photo-cover/lifestyle).
    If the model emitted none, convert one eligible single-slide feed post into
    a lifestyle card so a real device photo always shows up in the grid. The
    photo URL itself is filled later by resolve_photos()."""
    posts = content.get("posts", [])
    for p in posts:
        if p.get("size") == "feed":
            for s in p.get("slides", []):
                if s.get("template") in PHOTO_TEMPLATES_FORCED:
                    return  # already have a feed photo post — nothing to do

    # Pick a conversion target: the LAST single-slide feed post on a
    # convertible template (keeps the carousel + price board + first hero).
    target = None
    for p in posts:
        if p.get("size") != "feed":
            continue
        slides = p.get("slides", [])
        if len(slides) == 1 and slides[0].get("template") in _CONVERTIBLE_TO_PHOTO:
            target = p
    if target is None:
        return  # nothing safe to convert; leave the day as-is

    target["slides"] = [{
        "template": "lifestyle",
        "fields": {
            "tag": "BUYBACK",
            "eyebrow": "CASH FOR YOUR TECH",
            "headline_html": "That drawer device is <em>cash</em>",
            "sub_html": "Phones, laptops, tablets, consoles, a real number, paid the same day.",
            "photo_url": "",            # filled by resolve_photos()
            "photo_credit": "PHOTO · UNSPLASH",
        },
    }]
    target["role"] = "Photo post: device buyback"
    target["caption"] = ("That old phone, laptop, or console in your drawer is cash. "
                         "Get a real offer in 60 seconds and get paid the same day at "
                         "purchasingcorp.com/form.")
    if not target.get("hashtags"):
        target["hashtags"] = [
            "#sellyourtech", "#cashforelectronics", "#applebuyback", "#sellmyiphone",
            "#sellmymacbook", "#sellmymac", "#cashforphones", "#techbuyback",
            "#sellmyipad", "#cashformacbook", "#sellmyapplewatch", "#tradein",
            "#sellmyphone", "#getpaidfast", "#sellelectronics", "#cashfortech",
            "#applebuyer", "#samedaypayout",
        ]


# ------------------------------------------------------------
# Photo resolution — bake real DEVICE photos into the slides.
#
# photo-cover / lifestyle (PHOTO_TEMPLATES_FORCED): always get a device photo.
# cover / quote (PHOTO_TEMPLATES_OPTIN): only when the model set a "PHOTO:"
#   marker (their backdrop is subtle and optional).
# Either way the SCENE is chosen from our rotating device pool — never the
# model's free text — so every photo is a real device and no image repeats.
# A running counter hands each photo a distinct query within the day.
# ------------------------------------------------------------
def resolve_photos(content: dict, date_str: str = None, skip: bool = False) -> None:
    if not date_str:
        date_str = dt.datetime.now(dt.timezone.utc).date().isoformat()
    try:
        from photo_fetcher import fetch_photo
    except Exception as e:  # pragma: no cover
        if not skip:
            print(f"[generator] photo_fetcher unavailable ({e}); leaving photos empty.",
                  file=sys.stderr)
        fetch_photo = None

    counter = {"n": 0}

    def assign(fields: dict, size: str):
        query = device_query_for(date_str, counter["n"])
        counter["n"] += 1
        if skip or fetch_photo is None:
            fields["photo_url"] = ""
            return
        orientation = "portrait" if size == "story" else "landscape"
        try:
            fields["photo_url"] = fetch_photo(query, orientation=orientation) or ""
        except Exception as e:
            print(f"[generator] photo fetch failed for '{query}': {e}", file=sys.stderr)
            fields["photo_url"] = ""

    def handle(template: str, fields: dict, size: str):
        if template in PHOTO_TEMPLATES_FORCED:
            assign(fields, size)                       # photo IS the card
        elif template in PHOTO_TEMPLATES_OPTIN:
            val = fields.get("photo_url")
            if isinstance(val, str) and re.match(r"\s*PHOTO\s*:\s*\S", val, re.IGNORECASE):
                assign(fields, size)                   # model opted into a backdrop

    for post in content.get("posts", []):
        for slide in post.get("slides", []):
            handle(slide.get("template", ""), slide.get("fields", {}), post.get("size", "feed"))
    for story in content.get("stories", []):
        handle(story.get("template", ""), story.get("fields", {}), "story")


# ------------------------------------------------------------
# API call
# ------------------------------------------------------------
def call_anthropic(system_prompt: str, user_prompt: str) -> dict:
    try:
        import anthropic
    except ImportError:
        print("[generator] the 'anthropic' package is required. pip install anthropic", file=sys.stderr)
        sys.exit(1)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[generator] ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=system_prompt,
        tools=[{
            "name": "emit_content",
            "description": "Emit the day's PurchasingCorp Instagram posts and stories.",
            "input_schema": build_tool_schema(),
        }],
        tool_choice={"type": "tool", "name": "emit_content"},
        messages=[{"role": "user", "content": user_prompt}],
    )
    # A response that stopped on `max_tokens` has a truncated tool_use input:
    # the JSON is cut off, so posts[]/stories[] come back partial or missing.
    # Treat it as a retryable failure rather than letting validation blow up
    # on garbage. (With MAX_TOKENS=16000 this should essentially never fire.)
    if getattr(resp, "stop_reason", None) == "max_tokens":
        raise RuntimeError(
            f"response truncated at max_tokens={MAX_TOKENS}; "
            "raise SOCIAL_MAX_TOKENS or lower --posts"
        )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_content":
            return block.input
    raise RuntimeError("model did not return an emit_content tool call")


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def write_package(package: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(package, f, indent=2, ensure_ascii=False)
    n_posts = len(package["content"]["posts"])
    n_stories = len(package["content"]["stories"])
    print(f"[generator] wrote {out_path}  ({n_posts} posts, {n_stories} stories)")


def _self_test_package() -> dict:
    if not SAMPLE_PAYLOADS.exists():
        print(f"[generator] {SAMPLE_PAYLOADS.name} missing; cannot self-test.", file=sys.stderr)
        sys.exit(1)
    with SAMPLE_PAYLOADS.open() as f:
        pkg = json.load(f)
    pkg["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    pkg["model"] = "self-test"
    return pkg


def generate_package(date: dt.date, posts: int = 4, stories: int = 2,
                     skip_photos: bool = False, self_test: bool = False) -> dict:
    """Build one day's content package. Shared by the CLI and the backfill
    tool so they can never drift apart."""
    if self_test:
        return _self_test_package()

    pricing = load_pricing()
    theme = pick_theme(date)
    theme["featured_label"] = (pricing.get("category_labels", {})
                               .get(theme["featured_category"], theme["featured_category"]))
    pricing_ctx = build_pricing_context(pricing, theme["featured_category"])

    system_prompt = build_system_prompt()
    user_prompt = build_user_prompt(theme, pricing_ctx, date, posts, stories)

    # Generation is non-deterministic: an occasional response is truncated or
    # malformed. Re-ask a few times before giving up so one bad roll doesn't
    # kill the whole day (this was the every-other-day CI failure). Hard config
    # errors inside call_anthropic (missing key/package) raise SystemExit, which
    # is NOT an Exception subclass, so they still abort immediately.
    content = None
    last_err: Exception | None = None
    for attempt in range(1, GEN_ATTEMPTS + 1):
        try:
            raw = call_anthropic(system_prompt, user_prompt)
            content = validate_and_normalize(raw)
            break
        except Exception as e:
            last_err = e
            print(f"[generator] attempt {attempt}/{GEN_ATTEMPTS} failed: {e}",
                  file=sys.stderr)
            if attempt < GEN_ATTEMPTS:
                time.sleep(min(2 ** attempt, 20))
    if content is None:
        raise RuntimeError(
            f"generation failed after {GEN_ATTEMPTS} attempts: {last_err}"
        )

    if not skip_photos:
        ensure_feed_photo(content, theme)
    resolve_photos(content, date.isoformat(), skip=skip_photos)

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "model": MODEL,
        "theme": theme,
        "source": {
            "pricing_snapshot": pricing.get("generated_at"),
            "requested": {"posts": posts, "stories": stories},
        },
        "content": content,
    }


def run_self_test(out_path: Path) -> None:
    pkg = _self_test_package()
    write_package(pkg, out_path)
    print("[generator] self-test package written (no API call, photos left empty).")


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate PurchasingCorp's daily IG content package.")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today, UTC).")
    ap.add_argument("--out", help="Output path (default: social/<date>/content.json).")
    ap.add_argument("--posts", type=int, default=int(os.environ.get("SOCIAL_POSTS", "4")))
    ap.add_argument("--stories", type=int, default=int(os.environ.get("SOCIAL_STORIES", "2")))
    ap.add_argument("--self-test", action="store_true",
                    help="Copy the sample fixture instead of calling the API.")
    ap.add_argument("--skip-photos", action="store_true", help="Don't resolve PHOTO: markers.")
    args = ap.parse_args()

    if args.date:
        date = dt.date.fromisoformat(args.date)
    else:
        date = dt.datetime.now(dt.timezone.utc).date()

    out_path = Path(args.out) if args.out else (HERE / date.isoformat() / "content.json")

    if args.self_test:
        run_self_test(out_path)
        return

    package = generate_package(date, posts=args.posts, stories=args.stories,
                               skip_photos=args.skip_photos)
    write_package(package, out_path)


if __name__ == "__main__":
    main()
