#!/usr/bin/env python3
"""
Pick the day's video from the vault pool, rotating so none repeats until the
whole pool has cycled. Writes videos/selection.json for the publishers and
advances videos/rotation_state.json (commit it back so tomorrow continues).

Selection rule: the pool entry posted the FEWEST times so far wins; ties break
by pool order. This cycles the whole set evenly and is fully deterministic.

Usage:
  python social/pick_daily_video.py                 # advance + write selection
  python social/pick_daily_video.py --peek          # show pick, don't change state
"""
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


def load_json(path, default):
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    return default


def main():
    peek = "--peek" in sys.argv
    cfg = load_json(CAPTIONS, None)
    if not cfg or not cfg.get("pool"):
        print("[pick] videos/captions.json missing or empty pool", file=sys.stderr)
        return 1

    pool = cfg["pool"]
    base_url = cfg.get("base_url", "https://purchasingcorp.com/videos").rstrip("/")
    default_tags = cfg.get("default_hashtags", [])

    state = load_json(STATE, {"history": []})
    history = state.get("history", [])
    counts = {e["file"]: 0 for e in pool}
    for f in history:
        if f in counts:
            counts[f] += 1

    # fewest posts wins; ties broken by pool order (stable sort over index)
    order = sorted(range(len(pool)), key=lambda i: (counts[pool[i]["file"]], i))
    chosen = pool[order[0]]

    # merge default hashtags after the per-video ones, de-duped, order-preserving
    tags, seen = [], set()
    for t in list(chosen.get("hashtags", [])) + list(default_tags):
        if t not in seen:
            tags.append(t)
            seen.add(t)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    selection = {
        "date": today,
        "file": chosen["file"],
        "video_url": f"{base_url}/{chosen['file']}",
        "local_path": os.path.join("videos", chosen["file"]),
        "caption": chosen.get("caption", ""),
        "hashtags": tags,
        "cycle_position": f"{counts[chosen['file']] + 1} of pool size {len(pool)}",
    }

    print(f"[pick] {today} → {chosen['file']}")
    print(f"       {selection['video_url']}")
    if peek:
        print("[pick] --peek: state unchanged")
        return 0

    with open(SELECTION, "w") as fh:
        json.dump(selection, fh, indent=2)
    history.append(chosen["file"])
    state["history"] = history
    state["last_date"] = today
    with open(STATE, "w") as fh:
        json.dump(state, fh, indent=2)
    print(f"[pick] wrote {SELECTION}; history now {len(history)} posts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
