#!/usr/bin/env python3
"""Single-pass drop check for GitHub Actions. Diffs links vs state.json, alerts via ntfy."""
import json
import os
import re

import requests

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
WATCH_URLS = [u.strip() for u in (
    os.environ.get("WATCH_URLS") or "https://www.chromehearts.com/"
).split(",") if u.strip()]
STATE_FILE = "state.json"
FAIL_ALERT_AT = 6   # consecutive failed runs (~30 min) before alerting once
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.I)
HREF_RE = re.compile(r'href=["\'](.*?)["\']', re.I)


def notify(title, body):
    print(f"[notify] {title}: {body}", flush=True)
    if not NTFY_TOPIC:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers={"Title": title, "Priority": "urgent", "Tags": "rotating_light"},
            timeout=10,
        )
    except Exception as e:
        print("notify failed:", e, flush=True)


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.text


def extract_links(url):
    text = fetch(url)
    if "<loc>" in text.lower():
        locs = set(LOC_RE.findall(text))
        for child in [l for l in locs if l.endswith(".xml")]:
            try:
                locs |= set(LOC_RE.findall(fetch(child)))
            except Exception as e:
                print("child sitemap failed:", child, e, flush=True)
        return locs
    parts = url.split("/", 3)
    origin = parts[0] + "//" + parts[2]
    # Menu-style pages: keep only same-site top-level links like /baccarat or /socks.
    # HTML_MAX_DEPTH=0 disables the depth filter.
    max_depth = int(os.environ.get("HTML_MAX_DEPTH") or "1")
    links = set()
    for h in HREF_RE.findall(text):
        if h.startswith("/"):
            h = origin + h
        if not h.startswith(origin):
            continue
        h = h.split("#")[0].split("?")[0].rstrip("/")
        path = h[len(origin):].strip("/")
        depth = len(path.split("/")) if path else 0
        if depth == 0 or (max_depth and depth > max_depth):
            continue
        links.add(h)
    return links


def main():
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except Exception:
        state = {}
    links_state = {k: set(v) for k, v in state.get("links", {}).items()}
    fails = state.get("fails", {})

    for url in WATCH_URLS:
        try:
            links = extract_links(url)
        except Exception as e:
            fails[url] = fails.get(url, 0) + 1
            print(f"fetch failed ({fails[url]}): {url}: {e}", flush=True)
            if fails[url] == FAIL_ALERT_AT:
                notify("Monitor failing", f"{url}\n{e}")
            continue

        if fails.get(url, 0) >= FAIL_ALERT_AT:
            notify("Monitor recovered", url)
        fails[url] = 0

        if url not in links_state:
            links_state[url] = links
            print(f"baseline {len(links)} links for {url}", flush=True)
            continue
        new = links - links_state[url]
        if new:
            sample = "\n".join(sorted(new)[:6])
            notify("POSSIBLE DROP", f"{len(new)} new link(s) on {url}\n{sample}")
            links_state[url] |= links

    with open(STATE_FILE, "w") as f:
        json.dump({"links": {k: sorted(v) for k, v in links_state.items()},
                   "fails": fails}, f, indent=0, sort_keys=True)


if __name__ == "__main__":
    main()
