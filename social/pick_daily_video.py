#!/usr/bin/env python3
"""
Pick the day's video(s) from the vault pool, rotating so none repeats until the
whole pool has cycled. Writes videos/selections.json (the list the publisher
loops over) plus videos/selection.json (the first pick, kept for back-compat),
and advances videos/rotation_state.json (commit it back so tomorrow continues).

How many per day: POSTS_PER_DAY (3 by default). Override with --count N or env
PICK_COUNT=N.

Selection rule: among the pool entries posted the FEWEST times so far, pick one
at RANDOM. This still cycles the whole set evenly (nothing repeats until every
clip has been posted the same number of times) but the order within each cycle is
shuffled, so the feed no longer marches through the pool in a fixed sequence.

Within that least-posted tier the random pick is WEIGHTED by past performance:
w = 0.25 + score from videos/video_scores.json (written by social/score.py).
Unscored files default to 0.5, and with no scores file every weight is equal —
identical behavior to the unweighted picker. The 0.25 floor keeps low scorers
in rotation so the loop can't collapse onto early winners.

Usage:
  python social/pick_daily_video.py                 # advance + write selection(s)
  python social/pick_daily_video.py --count 2       # force two
  python social/pick_daily_video.py --peek          # show pick(s), don't change state
"""
import json
import os
import random
import sys
from datetime import datetime, timezone

# Videos posted per day (a re-run on the same date repicks; --count / PICK_COUNT override).
POSTS_PER_DAY = 3

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
VIDEOS = os.path.join(REPO, "videos")
CAPTIONS = os.path.join(VIDEOS, "captions.json")
STATE = os.path.join(VIDEOS, "rotation_state.json")
SELECTION = os.path.join(VIDEOS, "selection.json")
SELECTIONS = os.path.join(VIDEOS, "selections.json")
SCORES = os.environ.get("VIDEO_SCORES_PATH") or os.path.join(VIDEOS, "video_scores.json")


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    return default


def arg_value(flag):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def load_scores():
    """file → score from score.py's output; missing file/entries → 0.5, which
    makes every weight equal (identical to the old unweighted picker)."""
    data = load_json(SCORES, {})
    return {f: v.get("score", 0.5) for f, v in (data.get("videos") or {}).items()}


def daily_count(today):
    """How many to post today (constant; --count / PICK_COUNT can override)."""
    return POSTS_PER_DAY


def build_selection(chosen, counts, base_url, default_tags, today, pool_size):
    tags, seen = [], set()
    for t in list(chosen.get("hashtags", [])) + list(default_tags):
        if t not in seen:
            tags.append(t)
            seen.add(t)
    return {
        "date": today,
        "file": chosen["file"],
        "video_url": f"{base_url}/{chosen['file']}",
        "local_path": os.path.join("videos", chosen["file"]),
        "caption": chosen.get("caption", ""),
        "hashtags": tags,
        "cycle_position": f"{counts[chosen['file']] + 1} of pool size {pool_size}",
    }


def main():
    peek = "--peek" in sys.argv
    cfg = load_json(CAPTIONS, None)
    if not cfg or not cfg.get("pool"):
        print("[pick] videos/captions.json missing or empty pool", file=sys.stderr)
        return 1

    pool = cfg["pool"]
    base_url = cfg.get("base_url", "https://purchasingcorp.com/videos").rstrip("/")
    default_tags = cfg.get("default_hashtags", [])

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = arg_value("--count") or os.environ.get("PICK_COUNT")
    count = int(count) if count else daily_count(today)
    count = max(1, min(count, len(pool)))

    state = load_json(STATE, {"history": []})
    history = state.get("history", [])
    counts = {e["file"]: 0 for e in pool}
    for f in history:
        if f in counts:
            counts[f] += 1

    # Among the least-posted entries, pick at RANDOM (so the cycle order is
    # shuffled instead of fixed). Pick `count` distinct entries, bumping the
    # working count as we go so each further pick comes from the next-fewest tier.
    # Within the tier the pick is performance-weighted (w = 0.25 + score);
    # the tier gate itself is untouched, so rotation fairness is preserved.
    scores = load_scores()
    work = dict(counts)
    selections = []
    chosen_files = set()
    for _ in range(count):
        avail = [e["file"] for e in pool if e["file"] not in chosen_files]
        floor = min(work[f] for f in avail)
        tier = [f for f in avail if work[f] == floor]
        pick = random.choices(tier, weights=[0.25 + scores.get(f, 0.5) for f in tier])[0]
        chosen = next(e for e in pool if e["file"] == pick)
        selections.append(build_selection(chosen, counts, base_url, default_tags, today, len(pool)))
        chosen_files.add(pick)
        work[pick] += 1

    print(f"[pick] {today} → {count} video(s):")
    for s in selections:
        print(f"       {s['file']}  ({s['video_url']})")
    if peek:
        print("[pick] --peek: state unchanged")
        return 0

    with open(SELECTIONS, "w") as fh:
        json.dump(selections, fh, indent=2)
    with open(SELECTION, "w") as fh:           # back-compat: first pick
        json.dump(selections[0], fh, indent=2)
    history.extend(s["file"] for s in selections)
    state["history"] = history
    state["last_date"] = today
    with open(STATE, "w") as fh:
        json.dump(state, fh, indent=2)
    print(f"[pick] wrote {SELECTIONS}; history now {len(history)} posts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
