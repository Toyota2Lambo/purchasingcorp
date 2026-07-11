#!/usr/bin/env python3
"""
Turn the posts ledger's insights into scores that close the loop back to the
factory and the daily picker.

  per-post   = 0.6 × view percentile + 0.4 × engagement-rate percentile,
               each within the SAME platform's history (an IG reel only
               competes with IG reels). Needs a mature snapshot: 7d
               preferred, else 3d — a 1d-only post isn't scored yet.
  per-video  = mean of its post scores across platforms.
  per-format = EMA (α=0.3) over its videos in first-posted order, recomputed
               from scratch every run so re-runs are idempotent.

file → format join: the factory writes a provenance sidecar next to every
render — $FACTORY_VAULT/<file>.meta.json (default ~/videos/_vault) with
format_id/winner_id. Files with no sidecar are attributed to "legacy".

Writes videos/video_scores.json (read by pick_daily_video.py) and mirrors the
format EMAs into the factory's formats/registry.json as
{"score": {ema, n, updated}} per entry — $FACTORY_DIR points at the adfactory
checkout (warn + skip when absent; the single cross-repo write).

Usage:
  python social/score.py             # compute + write scores + sync registry
  python social/score.py --report    # …and print the league table

Env: POSTS_LEDGER, VIDEO_SCORES_PATH, FACTORY_VAULT, FACTORY_DIR override paths.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LEDGER = os.environ.get("POSTS_LEDGER") or os.path.join(REPO, "videos", "posts_ledger.json")
SCORES = os.environ.get("VIDEO_SCORES_PATH") or os.path.join(REPO, "videos", "video_scores.json")
VAULT = os.path.expanduser(os.environ.get("FACTORY_VAULT") or "~/videos/_vault")
FACTORY_DIR = os.path.expanduser(os.environ.get("FACTORY_DIR")
                                 or "~/videos/purchasingcorp-explainer/adfactory")
REGISTRY = os.path.join(FACTORY_DIR, "formats", "registry.json")

VIEW_W, ENG_W = 0.6, 0.4
EMA_ALPHA = 0.3


def _atomic_write(path, obj):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".scores-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump(obj, fh, indent=2)
    os.replace(tmp, path)

# ─────────────────────────── per-post scores ───────────────────────────

def _snapshot(row):
    """Latest mature snapshot — 7d preferred, else 3d (1d alone is too noisy)."""
    ins = row.get("insights") or {}
    return ins.get("7d") or ins.get("3d")


def _pctile(value, values):
    """Fraction of `values` below `value`, ties counting half — always in
    [0,1], and a single-sample platform lands at a neutral 0.5."""
    if not values:
        return 0.5
    below = sum(1 for v in values if v < value)
    ties = sum(1 for v in values if v == value)
    return (below + 0.5 * ties) / len(values)


def _eng_rate(snap):
    eng = sum(snap.get(k) or 0 for k in ("likes", "comments", "saved", "shares"))
    return eng / max(1, snap.get("views") or 0)


def collect_posts(rows):
    posts = []
    for row in rows:
        snap = _snapshot(row)
        if not snap or snap.get("views") is None or not row.get("file"):
            continue
        posts.append({"file": row["file"], "platform": row.get("platform", "?"),
                      "posted_at": row.get("posted_at", ""),
                      "views": snap.get("views") or 0, "eng": _eng_rate(snap)})
    return posts


def score_posts(posts):
    by_plat = {}
    for p in posts:
        by_plat.setdefault(p["platform"], []).append(p)
    for group in by_plat.values():
        views = [g["views"] for g in group]
        engs = [g["eng"] for g in group]
        for g in group:
            g["score"] = VIEW_W * _pctile(g["views"], views) + ENG_W * _pctile(g["eng"], engs)
    return posts

# ─────────────────────────── file → format join ───────────────────────────

def format_of(file):
    """Provenance sidecar lookup: format_id, or None when the sidecar exists
    but the video came from no registry format, or "legacy" with no sidecar."""
    try:
        meta = json.load(open(os.path.join(VAULT, file + ".meta.json")))
    except Exception:
        return "legacy"
    return meta.get("format_id") or (meta.get("provenance") or {}).get("format_id")

# ─────────────────────────── aggregate ───────────────────────────

def build_scores(rows):
    posts = score_posts(collect_posts(rows))
    acc = {}
    for p in posts:
        v = acc.setdefault(p["file"], {"scores": [], "views": 0, "first": p["posted_at"]})
        v["scores"].append(p["score"])
        v["views"] += p["views"]
        v["first"] = min(v["first"], p["posted_at"])
    videos = {}
    for f, v in acc.items():
        videos[f] = {"score": round(sum(v["scores"]) / len(v["scores"]), 4),
                     "views_7d": v["views"], "n_posts": len(v["scores"]),
                     "format_id": format_of(f)}
    # per-format EMA in first-posted order, from scratch → idempotent re-runs
    formats = {}
    for f in sorted(acc, key=lambda f: acc[f]["first"]):
        fid = videos[f]["format_id"]
        if not fid:
            continue
        cur = formats.setdefault(fid, {"ema": None, "n": 0})
        s = videos[f]["score"]
        cur["ema"] = s if cur["ema"] is None else (1 - EMA_ALPHA) * cur["ema"] + EMA_ALPHA * s
        cur["n"] += 1
    for cur in formats.values():
        cur["ema"] = round(cur["ema"], 4)
    return {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "videos": videos, "formats": formats}

# ─────────────────────────── registry sync ───────────────────────────

def update_registry(formats):
    """Mirror format EMAs into the factory registry (record_score semantics:
    per-entry {"score": {ema, n, updated}}). The one cross-repo write."""
    if not os.path.exists(REGISTRY):
        print(f"⚠️  factory registry not found at {REGISTRY} — skipping format sync "
              f"(set FACTORY_DIR to the adfactory checkout)")
        return
    try:
        entries = json.load(open(REGISTRY))
    except Exception as e:
        print(f"⚠️  registry unreadable ({e}) — skipping format sync")
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hit = 0
    for e in entries:
        st = formats.get(e.get("id"))
        if st:
            e["score"] = {"ema": st["ema"], "n": st["n"], "updated": today}
            hit += 1
    if not hit:
        print("– no scored format ids matched the registry (all legacy?) — registry untouched")
        return
    _atomic_write(REGISTRY, entries)
    print(f"✓ registry: {hit} format score(s) updated → {REGISTRY}")

# ─────────────────────────── report / main ───────────────────────────

def report(data):
    videos = data.get("videos") or {}
    print(f"\n🏁 video league table  ({len(videos)} scored, updated {data.get('updated', '?')})")
    print(f"   {'score':>6}  {'views':>9}  {'posts':>5}  {'format':8}  file")
    for f, v in sorted(videos.items(), key=lambda kv: -kv[1]["score"]):
        print(f"   {v['score']:6.3f}  {v['views_7d']:>9,}  {v['n_posts']:>5}  "
              f"{str(v['format_id'] or '—'):8}  {f}")
    formats = data.get("formats") or {}
    if formats:
        print(f"\n📐 format EMAs (α={EMA_ALPHA})")
        for fid, st in sorted(formats.items(), key=lambda kv: -kv[1]["ema"]):
            print(f"   {fid:8} ema={st['ema']:.3f}  n={st['n']}")
    print()


def main():
    try:
        rows = json.load(open(LEDGER))
    except Exception:
        rows = []
    if not rows:
        print(f"📭 no ledger at {LEDGER} — nothing to score")
        return 0
    data = build_scores(rows)
    if not data["videos"]:
        print("⏳ no post has a 3d/7d snapshot yet — run insights.py and wait for maturity")
        return 0
    _atomic_write(SCORES, data)
    print(f"💾 {len(data['videos'])} video score(s), {len(data['formats'])} format(s) → {SCORES}")
    update_registry(data["formats"])
    if "--report" in sys.argv:
        report(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
