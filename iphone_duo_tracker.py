#!/usr/bin/env python3
"""iPhone Duo Apple Store pickup tracker.

Checks Apple's pickup availability for your ZIP and alerts you the moment a
nearby store shows an iPhone Duo as available. Standard library only.

Watches the six US models: 256GB, 512GB and 1TB in Night Sky and Star White.

Config (environment variables):
  ZIP            Your ZIP code (required)
  MAX_STORES     How many of the nearest stores to watch (default 5)
  PARTS          Optional. Comma-separated part numbers to watch instead of the
                 six built-in ones.

  Alerts - set at least one:
  NTFY_TOPIC     Free push notifications via the ntfy app (instant setup)
  TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, TWILIO_TO   Real SMS via Twilio

Usage:
  python iphone_duo_tracker.py          # one check (what the scheduler runs)
  python iphone_duo_tracker.py --test   # show each store's status + send a test alert
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

STATE_FILE = os.environ.get("STATE_FILE", "state.json")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

# US part numbers, read from apple.com/shop/buy-iphone/iphone-duo (2TB left out on purpose).
DEFAULT_PARTS = {
    "MK1F4LL/A": "iPhone Duo 256GB Night Sky",
    "MK1E4LL/A": "iPhone Duo 256GB Star White",
    "MK1H4LL/A": "iPhone Duo 512GB Night Sky",
    "MK1G4LL/A": "iPhone Duo 512GB Star White",
    "MK1L4LL/A": "iPhone Duo 1TB Night Sky",
    "MK1J4LL/A": "iPhone Duo 1TB Star White",
}


def fetch_stores(zip_code, part):
    """Nearest stores (closest first) with their availability for one part number."""
    params = [("mts.0", "regular"), ("mts.1", "compact"), ("cppart", "UNLOCKED/US"),
              ("product", part), ("little", "false"), ("location", zip_code)]
    url = ("https://www.apple.com/shop/pickup-message-recommendations?"
           + urllib.parse.urlencode(params, safe="/"))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    return data["body"]["PickupMessage"].get("stores", [])


def check_part(zip_code, part, max_stores):
    """Return {store name: description} for stores where this part is available."""
    hits = {}
    for store in fetch_stores(zip_code, part)[:max_stores]:
        info = store.get("partsAvailability", {}).get(part, {})
        if info.get("pickupDisplay") == "available":
            hits[f"{store['storeName']}|{part}"] = (store["storeName"], store.get("city", ""),
                                                    store.get("state", ""))
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


def load_seen():
    try:
        data = json.load(open(STATE_FILE))
    except (FileNotFoundError, ValueError):
        return set()
    if isinstance(data, dict):
        data = data.get("seen", [])
    return set(data)


def main():
    zip_code = os.environ.get("ZIP")
    if not zip_code:
        sys.exit("Set the ZIP environment variable. See README.md.")
    max_stores = int(os.environ.get("MAX_STORES", "5"))
    custom = [p.strip() for p in os.environ.get("PARTS", "").split(",") if p.strip()]
    parts = {p: DEFAULT_PARTS.get(p, p) for p in custom} if custom else dict(DEFAULT_PARTS)

    hits, failures = {}, 0
    for part, title in parts.items():
        try:
            found = check_part(zip_code, part, max_stores)
        except (urllib.error.URLError, ValueError, KeyError) as e:
            failures += 1
            print(f"Check failed for {title} ({part}): {e}")
            continue
        finally:
            time.sleep(0.5)
        print(f"{title}: " + (", ".join(v[0] for v in found.values()) if found else "not available"))
        for key, (store, city, st) in found.items():
            hits[key] = f"{title} at {store} ({city}, {st})"

    if failures == len(parts):
        sys.exit("Every check failed - Apple may be blocking this server or changed its site.")

    if "--test" in sys.argv:
        notify("Test alert from your iPhone Duo tracker.")
        return

    previous = load_seen()
    new = {k: v for k, v in hits.items() if k not in previous}
    if new:
        notify("iPhone Duo available for pickup: " + "; ".join(new.values())
               + " - https://www.apple.com/shop/buy-iphone/iphone-duo")
    else:
        print(f"Checked {len(parts)} models near {zip_code}; nothing new.")

    # Only forget a store/model once a check ran cleanly, so a failed check never re-alerts.
    keep = set(hits) | (previous if failures else set())
    json.dump({"seen": sorted(keep)}, open(STATE_FILE, "w"))


if __name__ == "__main__":
    main()
