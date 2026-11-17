"""Aqar watcher: Telegram alerts when a listing is added to or removed from your Aqar searches.

Runs on GitHub Actions every ~10 minutes. Remembers listings in state.json (committed back to the repo).

  python watch.py          normal check
  python watch.py --test   send a test Telegram message and exit
"""
from __future__ import annotations

import html as htmlmod
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urljoin

from curl_cffi import requests as cc

# ─────────────────────────────── SETTINGS — edit these ───────────────────────────────
CITY = "الجبيل"  # Jubail (Aqar's own spelling)

# (label, Aqar category slug). Delete any line you don't want watched.
SEARCHES = [
    ("Apartment for rent", "شقق-للإيجار"),
    ("Villa for rent",     "فلل-للإيجار"),
]

MISSES_BEFORE_CHECK = 2   # a listing must be missing from this many runs in a row before we verify it's gone
MAX_PAGES = 60            # 20 listings per page → up to 1,200 per search
MAX_DETAIL_FETCHES = 40   # per run, for new-listing details (price, size…)
# ──────────────────────────────────────────────────────────────────────────────────────

BASE = "https://sa.aqar.fm"
STATE_FILE = Path(__file__).with_name("state.json")
PAGE_SIZE = 20
LISTING_RE = re.compile(r"-(\d{6,})/?$")
ITEMS_RE = re.compile(r'"numberOfItems":(\d+)')
HREF_RE = re.compile(r'href="([^"]+)"')
CLOSED_RE = re.compile(r'\\?"closed\\?"\s*:\s*true')
DEAD_MARKERS = ("تم حذف الإعلان", "الإعلان غير متوفر", "الإعلان غير نشط", "الإعلان منتهي",
                "Ad has been removed", "Ad not available")

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")


# ─────────────────────────────── HTTP ───────────────────────────────
_session = None
_last = 0.0


def _sess():
    global _session
    if _session is None:
        _session = cc.Session(impersonate="chrome")  # real-Chrome TLS fingerprint; it sets the User-Agent
        _session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ar,en-US;q=0.7,en;q=0.6",
        })
    return _session


def fetch(url: str, tries: int = 3):
    """(status_code, text). status 0 = network failure."""
    global _last
    for attempt in range(tries):
        wait = 1.3 - (time.monotonic() - _last)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.4))
        _last = time.monotonic()
        try:
            r = _sess().get(url, timeout=30, allow_redirects=True)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {type(e).__name__} on {url}")
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(5 * (attempt + 1))
            continue
        return r.status_code, r.text
    return 0, ""


# ─────────────────────────────── parsing ───────────────────────────────
def page_listings(html: str) -> dict[str, str]:
    """{listing_id: full_url} for every listing linked on a search page."""
    out: dict[str, str] = {}
    for m in HREF_RE.finditer(html):
        href = htmlmod.unescape(m.group(1)).split("?")[0].split("#")[0]
        mm = LISTING_RE.search(href)
        if mm and mm.group(1) not in out:
            out[mm.group(1)] = urljoin(BASE, href)
    return out


def norm_ar(s: str) -> str:
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي"), ("-", ""), (" ", "")):
        s = s.replace(a, b)
    return s


def title_from_url(url: str) -> str:
    seg = unquote(url.rstrip("/").rsplit("/", 1)[-1])
    seg = LISTING_RE.sub("", seg)
    return seg.replace("-", " ").strip() or "Listing"


def flight_blob(html: str) -> str:
    """Decode the Next.js RSC payload (self.__next_f.push chunks) into plain JSON text."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)*)"\]\)', html, re.S)
    out = []
    for c in chunks:
        try:
            out.append(json.loads('"' + c + '"'))
        except ValueError:
            pass
    return "".join(out) or html


def listing_obj(html: str) -> dict | None:
    blob = flight_blob(html)
    dec = json.JSONDecoder()
    for m in re.finditer(r'"listing"\s*:\s*\{', blob):
        try:
            obj, _ = dec.raw_decode(blob, blob.index("{", m.start() + 9))
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("id") is not None:
            return obj
    return None


def ld_listing(html: str) -> dict | None:
    """The schema.org RealEstateListing block Aqar puts on every live ad page."""
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            j = json.loads(m.group(1))
        except ValueError:
            continue
        for item in (j if isinstance(j, list) else [j]):
            t = item.get("@type") if isinstance(item, dict) else None
            if t and "RealEstateListing" in (t if isinstance(t, list) else [t]):
                return item
    return None


def details(html: str) -> dict:
    o = listing_obj(html) or {}
    ld = ld_listing(html) or {}
    d = {}
    if ld.get("name"):
        d["title"] = ld["name"]
    img = ld.get("image")
    if isinstance(img, list) and img:
        d["image"] = img[0]
    elif isinstance(img, str):
        d["image"] = img
    offer = ld.get("offers") if isinstance(ld.get("offers"), dict) else {}
    if isinstance(offer.get("price"), (int, float)) and offer["price"] > 0:
        d["price"] = f"{int(offer['price']):,} SAR"
    price = (o.get("price_text") if o.get("price_text") and any(ch.isdigit() for ch in o["price_text"]) else None) or (f"{o['price']:,}" if isinstance(o.get("price"), (int, float)) and o["price"] else None)
    if price:
        period = o.get("rent_period_text") or ""
        d["price"] = f"{price} SAR {period}".strip()
    if o.get("categoryName"):
        d["type"] = o["categoryName"]
    if o.get("address_text"):
        d["address"] = str(o["address_text"]).replace(", undefined", "")
    if isinstance(o.get("area"), (int, float)) and o["area"]:
        d["area"] = f"{int(o['area'])} m²"
    for k, label in (("beds", "bedrooms"), ("livings", "living rooms"), ("wc", "bathrooms")):
        if isinstance(o.get(k), int) and o[k] > 0:
            d.setdefault("rooms", []).append(f"{o[k]} {label}")
    return d


def page_is_gone(status: int, html: str) -> bool | None:
    """True = removed/closed, False = still live, None = couldn't tell (network trouble)."""
    if status in (404, 410):
        return True
    if status != 200:
        return None
    if any(m in html for m in DEAD_MARKERS):
        return True
    o = listing_obj(html)
    if o is not None:
        if o.get("closed") is True:
            return True
        st = o.get("status")
        if isinstance(st, str) and st.lower() in ("closed", "deleted", "expired", "suspended"):
            return True
        return False
    if CLOSED_RE.search(html):
        return True
    if ld_listing(html):
        return False
    return None  # page loaded but we couldn't read it — don't guess


# ─────────────────────────────── Telegram ───────────────────────────────
def tg(text: str) -> bool:
    if not (TG_TOKEN and TG_CHAT):
        print("[telegram not configured]\n" + text)
        return True
    for _ in range(3):
        try:
            r = cc.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", timeout=20, json={
                "chat_id": TG_CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False})
            if r.status_code == 200:
                return True
            if r.status_code == 429:
                time.sleep(int(r.json().get("parameters", {}).get("retry_after", 5)) + 1)
                continue
            print("telegram error", r.status_code, r.text[:200])
            return False
        except Exception as e:  # noqa: BLE001
            print("telegram error", e)
            time.sleep(3)
    return False


def tg_photo(photo: str, caption: str) -> bool:
    if not (TG_TOKEN and TG_CHAT):
        return tg(caption)
    try:
        r = cc.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto", timeout=30, json={
            "chat_id": TG_CHAT, "photo": photo, "caption": caption[:1024], "parse_mode": "HTML"})
        if r.status_code == 200:
            return True
    except Exception:  # noqa: BLE001
        pass
    return tg(caption)  # photo failed → plain text


def esc(s) -> str:
    return htmlmod.escape(str(s), quote=False)


def msg_new(info: dict, relisted: bool) -> str:
    head = "🔁 <b>Back on Aqar</b>" if relisted else "🆕 <b>New listing</b>"
    lines = [f"{head} · {esc(info['search'])}", f"<b>{esc(info.get('title') or 'Listing')}</b>"]
    if info.get("price"):
        lines.append(f"💰 {esc(info['price'])}")
    if info.get("address"):
        lines.append(f"📍 {esc(info['address'])}")
    extra = [x for x in (info.get("area"), ", ".join(info.get("rooms") or []) or None) if x]
    if extra:
        lines.append("📐 " + esc(" · ".join(extra)))
    lines.append(info["url"])
    return "\n".join(lines)


def msg_gone(info: dict) -> str:
    lines = [f"❌ <b>Listing removed</b> · {esc(info['search'])}", f"<s>{esc(info.get('title') or 'Listing')}</s>"]
    if info.get("price"):
        lines.append(f"💰 was {esc(info['price'])}")
    days = (time.time() - info.get("first_seen", time.time())) / 86400
    if not info.get("seeded"):
        lines.append(f"⏱ was up {days:.1f} days")
    lines.append(info["url"])
    return "\n".join(lines)


# ─────────────────────────────── main logic ───────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"listings": {}, "gone": {}, "fail_streak": 0, "warned": False}


def save_state(st: dict) -> None:
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def crawl(slug: str) -> tuple[dict[str, str], bool]:
    """Walk every results page of one search. Returns ({id: url}, complete)."""
    found: dict[str, str] = {}
    last_page = MAX_PAGES
    page = 0
    city_n = norm_ar(CITY)
    while page < last_page:
        page += 1
        url = f"{BASE}/{slug}/{CITY}" + (f"/{page}" if page > 1 else "")
        status, html = fetch(url)
        if status != 200:
            print(f"  ! page {page}: HTTP {status}")
            return found, False
        m = ITEMS_RE.search(html)
        if m:
            last_page = min(MAX_PAGES, max(1, -(-int(m.group(1)) // PAGE_SIZE)))
        links = page_listings(html)
        if page == 1 and not m and not links:
            print("  ! page 1 has no results data (blocked or layout changed)")
            return found, False
        # Aqar answers an unknown city with a nationwide feed — refuse to treat that as Jubail.
        if len(links) >= 8 and not any(city_n in norm_ar(unquote(u)) for u in links.values()):
            print("  ! page isn't scoped to the city — stopping")
            return found, False
        new = {k: v for k, v in links.items() if k not in found}
        found.update(new)
        if not new:
            break
    return found, True


def run() -> int:
    st = load_state()
    first_run = not st["listings"]
    now = time.time()
    notes_new, notes_gone = [], []
    detail_budget = MAX_DETAIL_FETCHES
    all_ok = True
    seen_this_run: set[str] = set()

    for label, slug in SEARCHES:
        print(f"→ {label}")
        found, complete = crawl(slug)
        print(f"  {len(found)} listings ({'complete' if complete else 'INCOMPLETE'})")
        if not found:
            all_ok = all_ok and complete
            if not complete:
                continue
        all_ok = all_ok and complete
        seen_this_run.update(found)

        for lid, url in found.items():
            rec = st["listings"].get(lid)
            if rec:
                rec["misses"] = 0
                rec["url"] = url
                continue
            info = {"search": label, "url": url, "title": title_from_url(url), "first_seen": now, "misses": 0}
            relisted = lid in st["gone"]
            if first_run:
                info["seeded"] = True
            elif detail_budget > 0:
                detail_budget -= 1
                status, html = fetch(url)
                if status == 200:
                    info.update(details(html))
            st["listings"][lid] = info
            st["gone"].pop(lid, None)
            if not first_run:
                notes_new.append((info.get("image"), msg_new(info, relisted)))

        # Only judge removals when this search was read completely.
        if complete:
            for lid, rec in list(st["listings"].items()):
                if rec["search"] != label or lid in found:
                    continue
                rec["misses"] = rec.get("misses", 0) + 1
                if rec["misses"] < MISSES_BEFORE_CHECK:
                    continue
                status, html = fetch(rec["url"])
                verdict = page_is_gone(status, html)
                if verdict is True:
                    notes_gone.append((None, msg_gone(rec)))
                    st["gone"][lid] = {"t": now, "title": rec.get("title")}
                    del st["listings"][lid]
                elif verdict is False and rec["misses"] >= 6:
                    # Live, but no longer appears in your search (e.g. moved out of Jubail or changed type).
                    rec["misses"] = 0

    # Trim the "gone" memory to 90 days
    st["gone"] = {k: v for k, v in st["gone"].items() if now - v.get("t", now) < 90 * 86400}

    # Health: warn once if Aqar keeps blocking us
    if all_ok:
        if st.get("warned"):
            tg("✅ Aqar watcher is working again.")
        st["fail_streak"], st["warned"] = 0, False
    else:
        st["fail_streak"] = st.get("fail_streak", 0) + 1
        if st["fail_streak"] >= 6 and not st.get("warned"):
            tg("⚠️ Aqar watcher couldn't read Aqar for the last 6 checks (maybe blocked). It keeps trying.")
            st["warned"] = True

    if first_run and st["listings"]:
        counts = {}
        for r in st["listings"].values():
            counts[r["search"]] = counts.get(r["search"], 0) + 1
        tg("👀 <b>Aqar watcher is live</b> for Jubail.\nNow tracking:\n" +
           "\n".join(f"• {esc(k)}: {v}" for k, v in counts.items()) +
           "\nYou'll get a message when one is added or removed.")
    for photo, m in notes_new + notes_gone:
        tg_photo(photo, m) if photo else tg(m)
        time.sleep(1.1)

    save_state(st)
    print(f"done: {len(notes_new)} new, {len(notes_gone)} removed, tracking {len(st['listings'])}")
    return 0


if __name__ == "__main__":
    if "--test" in sys.argv:
        ok = tg("✅ Test from your Aqar watcher — Telegram is set up.")
        sys.exit(0 if ok else 1)
    sys.exit(run())
