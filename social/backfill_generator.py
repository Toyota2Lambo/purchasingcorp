#!/usr/bin/env python3
# ============================================================
# PURCHASINGCORP — backfill_generator
# ============================================================
# Generate content packages for a RANGE of days in one run — handy for
# seeding the queue ahead of time, re-generating after a template change,
# or filling a gap if the daily cron missed a day.
#
# It reuses social_generator.generate_package() so a backfilled day is
# byte-for-byte the same pipeline as a live daily run (same theme
# rotation, same honesty rules, same photo resolution).
#
# Each day is written to:  social/<YYYY-MM-DD>/content.json
# Existing days are skipped unless --force is given.
#
# Usage:
#   # explicit inclusive range
#   python social/backfill_generator.py --start 2026-06-01 --end 2026-06-07
#
#   # N days ending today (or ending --until)
#   python social/backfill_generator.py --days 7
#   python social/backfill_generator.py --days 7 --until 2026-06-30
#
#   # no API key handy? prove the loop with the fixture:
#   python social/backfill_generator.py --days 3 --self-test
# ============================================================

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from social_generator import HERE, generate_package, write_package


def daterange(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def resolve_range(args) -> "tuple[dt.date, dt.date]":
    if args.start or args.end:
        if not (args.start and args.end):
            print("[backfill] --start and --end must be given together.", file=sys.stderr)
            sys.exit(2)
        start = dt.date.fromisoformat(args.start)
        end = dt.date.fromisoformat(args.end)
    elif args.days:
        end = dt.date.fromisoformat(args.until) if args.until else dt.datetime.now(dt.timezone.utc).date()
        start = end - dt.timedelta(days=args.days - 1)
    else:
        print("[backfill] specify either --start/--end or --days N.", file=sys.stderr)
        sys.exit(2)
    if start > end:
        start, end = end, start
    return start, end


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate PurchasingCorp IG content for a range of days.")
    ap.add_argument("--start", help="First day, YYYY-MM-DD (use with --end).")
    ap.add_argument("--end", help="Last day, YYYY-MM-DD inclusive (use with --start).")
    ap.add_argument("--days", type=int, help="Number of days ending at --until (default: today).")
    ap.add_argument("--until", help="Last day for --days mode, YYYY-MM-DD (default: today, UTC).")
    ap.add_argument("--posts", type=int, default=4)
    ap.add_argument("--stories", type=int, default=2)
    ap.add_argument("--out-root", help="Root dir for <date>/content.json (default: social/).")
    ap.add_argument("--force", action="store_true", help="Overwrite days that already exist.")
    ap.add_argument("--self-test", action="store_true", help="Use the fixture; no API calls.")
    ap.add_argument("--skip-photos", action="store_true", help="Don't resolve PHOTO: markers.")
    args = ap.parse_args()

    start, end = resolve_range(args)
    out_root = Path(args.out_root) if args.out_root else HERE
    days = list(daterange(start, end))
    print(f"[backfill] {start} -> {end}  ({len(days)} day(s))")

    written, skipped, failed = [], [], []
    for date in days:
        out_path = out_root / date.isoformat() / "content.json"
        if out_path.exists() and not args.force:
            print(f"[backfill] skip {date} (exists; --force to overwrite)")
            skipped.append(date)
            continue
        try:
            pkg = generate_package(
                date,
                posts=args.posts,
                stories=args.stories,
                skip_photos=args.skip_photos,
                self_test=args.self_test,
            )
            write_package(pkg, out_path)
            written.append(date)
        except Exception as e:  # keep going so one bad day doesn't sink the batch
            print(f"[backfill] FAILED {date}: {e}", file=sys.stderr)
            failed.append(date)

    print(f"[backfill] done — {len(written)} written, {len(skipped)} skipped, {len(failed)} failed")
    if failed:
        print("[backfill] failed days: " + ", ".join(d.isoformat() for d in failed), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
