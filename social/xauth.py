#!/usr/bin/env python3
"""
OAuth 1.0a signing for the X / Twitter API — factored out of
video_publisher.py so insights.py can sign its public_metrics reads with the
same helper. stdlib only.

Reads the same env the publishers already use:
  TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET
"""
import base64
import hashlib
import hmac
import os
import secrets
import time
import urllib.parse

TW_KEY = os.environ.get("TWITTER_API_KEY", "")
TW_SECRET = os.environ.get("TWITTER_API_SECRET", "")
TW_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN", "")
TW_TSECRET = os.environ.get("TWITTER_ACCESS_SECRET", "")


def have_creds():
    return all([TW_KEY, TW_SECRET, TW_TOKEN, TW_TSECRET])


def _pct(s):
    return urllib.parse.quote(str(s), safe="")


def oauth_header(method, url, params=None):
    """OAuth 1.0a header; `params` (query/form fields) are folded into the
    signature base string. Pass None for multipart bodies (binary not signed)."""
    oauth = {
        "oauth_consumer_key": TW_KEY,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": TW_TOKEN,
        "oauth_version": "1.0",
    }
    allp = dict(params or {}, **oauth)
    base = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(allp.items()))
    base_string = "&".join([method.upper(), _pct(url), _pct(base)])
    key = f"{_pct(TW_SECRET)}&{_pct(TW_TSECRET)}"
    sig = base64.b64encode(hmac.new(key.encode(), base_string.encode(), hashlib.sha1).digest()).decode()
    oauth["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))
