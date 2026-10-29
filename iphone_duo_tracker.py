#!/usr/bin/env python3
"""iPhone Duo Apple Store pickup tracker.

Checks Apple's pickup availability for your ZIP and alerts you the moment a
nearby store flips to "available". Standard library only.

Config (environment variables):
  ZIP            Your ZIP code (required)
  PARTS          Optional. Comma-separated Apple part numbers. Leave it unset and the
                 script finds every iPhone Duo in 256GB, 512GB and 1TB (both colors)
                 on its own from Apple's product page.
  MAX_STORES     How many of the nearest stores to watch (default 5)

  Alerts - set at least one:
  NTFY_TOPIC     Free push notifications via the ntfy app (instant setup)
  TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, TWILIO_TO   Real SMS via Twilio

Usage:
  python iphone_duo_tracker.py          # one check (what the scheduler runs)
  python iphone_duo_tracker.py --test   # print what Apple returns + send a test alert
  python iphone_duo_tracker.py --list-parts   # show which models it will watch
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

STATE_FILE = os.environ.get("STATE_FILE", "state.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")


def fetch_availability(zip_code, parts):
    params = [("pl", "true"), ("mts.0", "regular"), ("mts.1", "compact"),
              ("location", zip_code)]
    params += [(f"parts.{i}", p) for i, p in enumerate(parts)]
    url = "https://www.apple.com/shop/fulfillment-messages?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    stores = data["body"]["content"]["pickupMessage"].get("stores", [])
    return stores


WANTED = re.compile(r"iPhone Duo.*\b(256GB|512GB|1TB)\b", re.I)
PART_RE = re.compile(r"\bM[A-Z0-9]{4}LL/A\b")


def discover_parts(zip_code):
    """Find iPhone Duo 256GB/512GB/1TB part numbers (any color) automatically."""
    req = urllib.request.Request("https://www.apple.com/shop/buy-iphone/iphone-duo",
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        html = r.read().decode("utf-8", "ignore")
    candidates = sorted(set(PART_RE.findall(html)))
    if not candidates:
        sys.exit("Couldn't find part numbers on Apple's page. Set PARTS manually (see README).")
    found = {}
    rejected = 0
    for part in candidates:
        try:
            stores = fetch_availability(zip_code, [part])
        except urllib.error.HTTPError:
            rejected += 1          # Apple rejects codes that aren't real products
            continue
        finally:
            time.sleep(0.3)
        for store in stores[:1]:
            info = store.get("partsAvailability", {}).get(part, {})
            title = info.get("messageTypes", {}).get("regular", {}).get("storePickupProductTitle", "")
            if WANTED.search(title):
                found[part] = title
    print(f"Tried {len(candidates)} codes from Apple's page; {rejected} rejected; {len(found)} matched.")
    if not found:
        sys.exit("Found part numbers but none matched iPhone Duo 256GB/512GB/1TB. Set PARTS manually.")
    return found


def find_available(stores, parts, max_stores):
    hits = {}
    for store in stores[:max_stores]:
        for part in parts:
            info = store.get("partsAvailability", {}).get(part, {})
            if info.get("pickupDisplay") == "available":
                key = f"{store['storeName']}|{part}"
                label = info.get("messageTypes", {}).get("regular", {}).get("storePickupProductTitle", part)
                hits[key] = f"{label} at {store['storeName']} ({store.get('city', '')}, {store.get('state', '')})"
    return hits


def send_ntfy(topic, message):
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}", data=message.encode(),
        headers={"Title": "iPhone Duo in stock!", "Priority": "urgent", "Tags": "iphone"})
    urllib.request.urlopen(req, timeout=15).read()


def send_sms(message):
    sid, token = os.environ["TWILIO_SID"], os.environ["TWILIO_TOKEN"]
    body = urllib.parse.urlencode({"From": os.environ["TWILIO_FROM"],
                                   "To": os.environ["TWILIO_TO"],
                                   "Body": message}).encode()
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=body)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode())
    urllib.request.urlopen(req, timeout=15).read()


def notify(message):
    sent = False
    if os.environ.get("NTFY_TOPIC"):
        send_ntfy(os.environ["NTFY_TOPIC"], message)
        sent = True
    if os.environ.get("TWILIO_SID"):
        send_sms(message)
        sent = True
    if not sent:
        print("No alert channel configured (set NTFY_TOPIC and/or TWILIO_*).")
    print("ALERT:", message)


def load_state():
    """State = discovered part numbers + store/model pairs already alerted on."""
    try:
        data = json.load(open(STATE_FILE))
    except (FileNotFoundError, ValueError):
        data = {}
    if not isinstance(data, dict):      # older format was a bare list
        data = {"seen": data}
    return {"parts": data.get("parts", {}), "seen": data.get("seen", [])}


def main():
    zip_code = os.environ.get("ZIP")
    parts = [p.strip() for p in os.environ.get("PARTS", "").split(",") if p.strip()]
    if not zip_code:
        sys.exit("Set the ZIP environment variable. See README.md.")
    max_stores = int(os.environ.get("MAX_STORES", "5"))

    state = load_state()
    if not parts:
        found = state["parts"] if state["parts"] and "--list-parts" not in sys.argv else None
        if not found:
            found = discover_parts(zip_code)
            state["parts"] = found
        parts = sorted(found)
        if "--list-parts" in sys.argv or "--test" in sys.argv:
            for p in parts:
                print(p, "-", found[p])
        if "--list-parts" in sys.argv:
            return

    stores = fetch_availability(zip_code, parts)
    hits = find_available(stores, parts, max_stores)

    if "--test" in sys.argv:
        for s in stores[:max_stores]:
            print(s["storeName"], {p: s.get("partsAvailability", {}).get(p, {}).get("pickupDisplay")
                                   for p in parts})
        notify("Test alert from your iPhone Duo tracker.")
        return

    previous = set(state["seen"])

    new = {k: v for k, v in hits.items() if k not in previous}
    if new:
        notify("iPhone Duo available for pickup: " + "; ".join(new.values())
               + " - https://www.apple.com/shop/buy-iphone")
    else:
        print(f"Checked {len(stores[:max_stores])} stores; nothing new.")

    state["seen"] = sorted(hits)
    json.dump(state, open(STATE_FILE, "w"))


if __name__ == "__main__":
    main()
