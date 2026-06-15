#!/usr/bin/env python3
"""
Pick the day's video(s) from the vault pool, rotating so none repeats until the
whole pool has cycled. Writes videos/selections.json (the list the publisher
loops over) plus videos/selection.json (the first pick, kept for back-compat),
and advances videos/rotation_state.json (commit it back so tomorrow continues).

How many per day: 1 or 2, chosen deterministically from the UTC date so the
cadence varies day to day but a re-run on the same date is stable. Override with
--count N (or env PICK_COUNT=N).

Selection rule: the pool entries posted the FEWEST times so far win; ties break
by pool order. This cycles the whole set evenly and is fully deterministic.

Usage:
  python social/pick_daily_video.py                 # advance + write selection(s)
  python social/pick_daily_video.py --count 2       # force two
  python social/pick_daily_video.py --peek          # show pick(s), don't change state
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
VIDEOS = os.path.join(REPO, "videos")
CAPTIONS = os.path.join(VIDEOS, "captions.json")
STATE = os.path.join(VIDEOS, "rotation_state.json")
SELECTION = os.path.join(VIDEOS, "selection.json")
SELECTIONS = os.path.join(VIDEOS, "selections.json")


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


def daily_count(today):
    """Deterministic 1 or 2 from the date (≈ half the days get two)."""
    h = int(hashlib.sha256(today.encode()).hexdigest(), 16)
    return 2 if h % 2 == 0 else 1


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

    # fewest posts wins; ties broken by pool order. Pick `count` distinct entries,
    # decrementing the working count as we go so the second pick is the next-fewest.
    work = dict(counts)
    selections = []
    for _ in range(count):
        order = sorted(range(len(pool)), key=lambda i: (work[pool[i]["file"]], i))
        # skip ones already chosen this run
        idx = next(i for i in order if pool[i]["file"] not in {s["file"] for s in selections})
        chosen = pool[idx]
        selections.append(build_selection(chosen, counts, base_url, default_tags, today, len(pool)))
        work[chosen["file"]] += 1

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
