#!/usr/bin/env python3
"""
Health-check the Meta tokens the posters depend on — before they lapse.

Both IG_ACCESS_TOKEN and THREADS_ACCESS_TOKEN are long-lived (~60 day) tokens.
When one expires, every publish that day dies with OAuthException code 190 and
the run is simply lost — there is no retry and no warning. Worse, an expired
token can NOT be renewed: `refresh_access_token` only works while the token is
still valid, so lapsing means a full re-mint through Meta's OAuth flow.

Each platform gets three checks, because the weak ones pass too easily:
  1. /me            — the token authenticates at all
  2. id cross-check — the token's account matches the *_ACCOUNT_ID/USER_ID
                      secret. These are separate secrets: a token minted
                      against the wrong app or account sails through /me and
                      then 400s on every single publish.
  3. publishing_limit — requires the content-publish permission, so a 200 here
                      proves the token can actually POST rather than merely
                      read. /me alone does not tell you this.

It also warns on AGE, which is the only check that buys lead time. Rotation
dates live in social/token_rotation.json (update it when you rotate); at
WARN_DAYS the run is annotated and Discord is pinged, so you get a week of
notice instead of discovering it from a failed post.

Env:
  IG_ACCESS_TOKEN, IG_BUSINESS_ACCOUNT_ID
  THREADS_ACCESS_TOKEN, THREADS_USER_ID
  DISCORD_WEBHOOK_URL          (optional alert on warn/fail)
  TOKEN_WARN_DAYS=50           age at which to start warning

Usage:
  python social/token_check.py
  python social/token_check.py --only ig
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ROTATION = os.path.join(HERE, "token_rotation.json")

UA = "purchasingcorp-token-check/1.0"
LIFETIME_DAYS = 60
WARN_DAYS = int(os.environ.get("TOKEN_WARN_DAYS", "50"))
DISCORD = os.environ.get("DISCORD_WEBHOOK_URL", "")

IG_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
IG_ACCOUNT = os.environ.get("IG_BUSINESS_ACCOUNT_ID") or os.environ.get("IG_ACCOUNT_ID", "")
IG_GRAPH = f"https://graph.instagram.com/{os.environ.get('IG_API_VERSION', 'v21.0')}"

TH_TOKEN = os.environ.get("THREADS_ACCESS_TOKEN", "")
TH_USER = os.environ.get("THREADS_USER_ID") or os.environ.get("THREADS_ACCOUNT_ID", "")
TH_GRAPH = f"https://graph.threads.net/{os.environ.get('THREADS_API_VERSION', 'v1.0')}"


def http(url, token, timeout=30):
    """GET with the token as a Bearer header — never in the query string, so it
    cannot leak into a redirect, a proxy log, or an error message."""
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}
    except Exception as e:
        return 0, {"error": {"message": str(e)}}


def api_error(body):
    err = body.get("error") or {}
    msg = err.get("message") or body.get("raw") or "unknown error"
    code = err.get("code")
    return f"{msg}" + (f" (code {code})" if code else "")


def token_age(platform):
    """Days since this token was last rotated, or None if unrecorded."""
    try:
        with open(ROTATION) as fh:
            data = json.load(fh)
    except Exception:
        return None
    stamp = (data.get(platform) or {}).get("rotated")
    if not stamp:
        return None
    try:
        when = datetime.strptime(stamp, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - when).days


def check(platform, label, token, graph, account_secret, id_field, limit_edge, scope_hint):
    """Returns (ok, warnings) and prints a per-platform report."""
    print(f"\n── {label} ──")
    if not token:
        print(f"  FAIL  {platform.upper()}_ACCESS_TOKEN is not set")
        return False, []

    status, body = http(f"{graph}/me?fields=id,username", token)
    if status != 200:
        print(f"  FAIL  /me → HTTP {status}: {api_error(body)}")
        print(f"        Re-mint the token and update the secret ({scope_hint}).")
        return False, []
    me_id, handle = body.get("id", ""), body.get("username", "?")
    print(f"  ok    /me → {handle} ({me_id})")

    # A token for the wrong account authenticates fine but cannot publish.
    if account_secret and account_secret != "me" and account_secret != me_id:
        print(f"  FAIL  token account {me_id} != {id_field} secret {account_secret}")
        print("        Every publish will 400 until these agree.")
        return False, []
    if not account_secret:
        print(f"  warn  {id_field} is not set — cannot cross-check the account")

    status, body = http(f"{graph}/{me_id}/{limit_edge}?fields=quota_usage,config", token)
    if status != 200:
        print(f"  FAIL  {limit_edge} → HTTP {status}: {api_error(body)}")
        print(f"        Token lacks the publish permission ({scope_hint}).")
        return False, []
    row = (body.get("data") or [{}])[0]
    used = row.get("quota_usage", "?")
    total = (row.get("config") or {}).get("quota_total", "?")
    print(f"  ok    publish scope confirmed — quota {used}/{total} used today")

    warnings = []
    age = token_age(platform)
    if age is None:
        warnings.append(f"{label}: rotation date unrecorded in social/token_rotation.json")
        print("  warn  no rotation date recorded — age unknown")
    else:
        left = LIFETIME_DAYS - age
        if age >= WARN_DAYS:
            warnings.append(f"{label}: token is {age}d old, ~{left}d left — rotate now")
            print(f"  warn  {age}d old, ~{left}d before expiry — rotate now")
        else:
            print(f"  ok    {age}d old, ~{left}d before expiry")
    return True, warnings


def notify_discord(summary):
    if not DISCORD:
        return
    try:
        req = urllib.request.Request(
            DISCORD, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": UA},
            data=json.dumps({"content": summary}).encode())
        urllib.request.urlopen(req, timeout=30).read()
    except Exception:
        pass


def main():
    only = ""
    for i, a in enumerate(sys.argv):
        if a == "--only" and i + 1 < len(sys.argv):
            only = sys.argv[i + 1]
    wanted = [p.strip() for p in only.split(",") if p.strip()] if only else ["ig", "threads"]

    failures, warnings = [], []

    if "ig" in wanted:
        ok, warns = check("ig", "Instagram", IG_TOKEN, IG_GRAPH, IG_ACCOUNT,
                          "IG_BUSINESS_ACCOUNT_ID", "content_publishing_limit",
                          "needs instagram_business_content_publish")
        warnings += warns
        if not ok:
            failures.append("Instagram")

    if "threads" in wanted:
        ok, warns = check("threads", "Threads", TH_TOKEN, TH_GRAPH, TH_USER,
                          "THREADS_USER_ID", "threads_publishing_limit",
                          "needs threads_basic + threads_content_publish")
        warnings += warns
        if not ok:
            failures.append("Threads")

    print()
    if failures:
        summary = ("**PurchasingCorp token check FAILED** — "
                   + ", ".join(failures)
                   + " cannot publish. Re-mint via Meta and `gh secret set`; "
                     "expired tokens cannot be refreshed.")
        print(summary)
        notify_discord(summary)
        return 1
    if warnings:
        summary = "**PurchasingCorp token check — action needed soon**\n" + "\n".join(
            f"• {w}" for w in warnings)
        print(summary)
        notify_discord(summary)
        return 0
    print("All tokens healthy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
