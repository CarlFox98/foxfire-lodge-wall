"""Foxfire Lodge portrait wall: collects opt-ins, builds the two picture sheets and publishes them to docs/ (GitHub Pages).

Runs every 15 minutes in GitHub Actions (see .github/workflows/update-wall.yml). Opt-in only:
  * Discord: everyone who reacts with the fox emoji on the pinned sign-up message is on the wall; removing the reaction removes them.
  * Twitch:  redeeming the "Put me on the Lodge wall" channel-points reward adds you; "Take me off the Lodge wall" removes you.
Anyone in blocklist.txt (one "discord:<id>" / "twitch:<id>" / name per line) never appears.

Outputs (layouts must match M4HallwayBuilder in the Unity project):
  docs/hallway.png  2048x2048: portraits 10x6 cells of 200x240 px from the top-left (58 used); names 8x12 strips of 256x48 px from y=1440
  docs/mosaic.png   2048x2048: 16x16 tiles of 128 px (240 used, newest first)
  docs/wall.json    {"count": n, "updated": "Oct 23, 2026"}
Hallway order: anyone who joined in the last 7 days first (newest first), the rest shuffled once per hour.
"""
import datetime as dt, hashlib, io, json, os, random, sys, time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).parent
DOCS, STATE = ROOT / "docs", ROOT / "state"
ROSTER = STATE / "roster.json"
HALL_SLOTS, MOSAIC_SLOTS, NEW_DAYS = 58, 240, 7
UA = {"User-Agent": "FoxfireLodgeWall/1.0 (+https://github.com/CarlFox98/foxfire-lodge-wall)"}
FONT_DIRS = [ROOT / "fonts", Path("/usr/share/fonts")]


# ----------------------------------------------------------------------------- roster
def load_roster():
    return json.loads(ROSTER.read_text("utf-8")) if ROSTER.exists() else {"members": {}}


def save_roster(r):
    STATE.mkdir(exist_ok=True)
    ROSTER.write_text(json.dumps(r, indent=1, ensure_ascii=False, sort_keys=True), "utf-8")


def blocklist():
    p = ROOT / "blocklist.txt"
    if not p.exists():
        return set()
    return {l.strip().lower() for l in p.read_text("utf-8").splitlines() if l.strip() and not l.startswith("#")}


# ----------------------------------------------------------------------------- Discord (reactions on the pinned sign-up message)
def discord_members():
    tok, ch, msg = os.environ.get("DISCORD_BOT_TOKEN"), os.environ.get("DISCORD_CHANNEL_ID"), os.environ.get("DISCORD_MESSAGE_ID")
    emoji = os.environ.get("DISCORD_EMOJI", "\N{FOX FACE}")
    if not (tok and ch and msg):
        print("Discord: not configured, skipped"); return None
    h = {**UA, "Authorization": f"Bot {tok}"}
    users, after = [], None
    while True:
        url = f"https://discord.com/api/v10/channels/{ch}/messages/{msg}/reactions/{requests.utils.quote(emoji)}?limit=100" + (f"&after={after}" if after else "")
        r = requests.get(url, headers=h, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5); continue
        r.raise_for_status(); page = r.json(); users += page
        if len(page) < 100: break
        after = page[-1]["id"]
    out = {}
    for u in users:
        if u.get("bot"): continue
        av = f"https://cdn.discordapp.com/avatars/{u['id']}/{u['avatar']}.png?size=256" if u.get("avatar") else f"https://cdn.discordapp.com/embed/avatars/{(int(u['id']) >> 22) % 6}.png"
        out[f"discord:{u['id']}"] = {"name": u.get("global_name") or u["username"], "avatar": av}
    print(f"Discord: {len(out)} signed up"); return out


# ----------------------------------------------------------------------------- Twitch (channel-points redemptions)
def twitch_session():
    """Refreshes the broadcaster token. The refresh token may change on every refresh, so it lives encrypted in state/twitch.enc
    (key = TWITCH_STATE_KEY secret); the very first run takes it from the TWITCH_REFRESH_TOKEN secret instead."""
    cid, sec = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
    key = os.environ.get("TWITCH_STATE_KEY")
    if not (cid and sec and key):
        print("Twitch: not configured, skipped"); return None
    from cryptography.fernet import Fernet
    f, enc = Fernet(key.encode()), STATE / "twitch.enc"
    rt = f.decrypt(enc.read_bytes()).decode() if enc.exists() else os.environ.get("TWITCH_REFRESH_TOKEN")
    if not rt:
        print("Twitch: no refresh token yet, skipped"); return None
    r = requests.post("https://id.twitch.tv/oauth2/token", data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": cid, "client_secret": sec}, timeout=30)
    if not r.ok:  # Twitch's message names the problem (e.g. "Invalid refresh token", "invalid client secret") without echoing any secret
        try: why = r.json().get("message", "")
        except ValueError: why = r.text[:200]
        print(f"::warning::Twitch sign-in failed ({r.status_code}: {why}). Twitch skipped this run; check the TWITCH_* secrets.")
        return None
    t = r.json()
    if t["refresh_token"] != rt or not enc.exists():  # only rewrite when it really changed (keeps the history quiet)
        STATE.mkdir(exist_ok=True); enc.write_bytes(f.encrypt(t["refresh_token"].encode()))
    h = {**UA, "Client-Id": cid, "Authorization": f"Bearer {t['access_token']}"}
    me = requests.get("https://api.twitch.tv/helix/users", headers=h, timeout=30).json()["data"][0]
    return h, me["id"]


JOIN_TITLE, LEAVE_TITLE = "Put me on the Lodge wall", "Take me off the Lodge wall"


def twitch_rewards(h, bid):
    """Finds (or creates) the two rewards. They must be created by this app, or Twitch won't let it read/complete redemptions."""
    r = requests.get("https://api.twitch.tv/helix/channel_points/custom_rewards", headers=h, params={"broadcaster_id": bid, "only_manageable_rewards": "true"}, timeout=30)
    r.raise_for_status(); have = {x["title"]: x["id"] for x in r.json()["data"]}
    for title, cost, prompt in ((JOIN_TITLE, 1, "Your Twitch profile picture and name go on the portrait wall in Foxfire Lodge (VRChat). Public; leave any time."),
                                (LEAVE_TITLE, 1, "Removes your portrait from the Foxfire Lodge wall within 15 minutes.")):
        if title not in have:
            c = requests.post("https://api.twitch.tv/helix/channel_points/custom_rewards", headers=h, params={"broadcaster_id": bid},
                              json={"title": title, "cost": cost, "prompt": prompt, "background_color": "#C2622D"}, timeout=30)
            c.raise_for_status(); have[title] = c.json()["data"][0]["id"]; print(f"Twitch: created reward '{title}'")
    return have[JOIN_TITLE], have[LEAVE_TITLE]


def twitch_events():
    s = twitch_session()
    if not s: return None
    h, bid = s
    join_id, leave_id = twitch_rewards(h, bid)
    events = []
    for rid, kind in ((join_id, "join"), (leave_id, "leave")):
        cursor = None
        while True:
            p = {"broadcaster_id": bid, "reward_id": rid, "status": "UNFULFILLED", "first": 50, "sort": "OLDEST"}
            if cursor: p["after"] = cursor
            r = requests.get("https://api.twitch.tv/helix/channel_points/custom_rewards/redemptions", headers=h, params=p, timeout=30)
            r.raise_for_status(); j = r.json()
            for x in j["data"]: events.append((x["redeemed_at"], kind, x["user_id"], x["user_name"], rid, x["id"]))
            cursor = j.get("pagination", {}).get("cursor")
            if not cursor or not j["data"]: break
    events.sort()
    ids = sorted({e[2] for e in events})
    pics = {}
    for i in range(0, len(ids), 100):
        r = requests.get("https://api.twitch.tv/helix/users", headers=h, params=[("id", x) for x in ids[i:i + 100]], timeout=30)
        r.raise_for_status(); pics.update({u["id"]: u["profile_image_url"] for u in r.json()["data"]})
    print(f"Twitch: {len(events)} new redemptions")
    return events, pics, h, bid


def twitch_complete(h, bid, events):
    """Marks processed redemptions FULFILLED (so they aren't processed again)."""
    by_reward = {}
    for e in events: by_reward.setdefault(e[4], []).append(e[5])
    for rid, ids in by_reward.items():
        for i in range(0, len(ids), 50):
            requests.patch("https://api.twitch.tv/helix/channel_points/custom_rewards/redemptions", headers=h,
                           params=[("broadcaster_id", bid), ("reward_id", rid)] + [("id", x) for x in ids[i:i + 50]], json={"status": "FULFILLED"}, timeout=30).raise_for_status()


# ----------------------------------------------------------------------------- update the roster
def update_roster(roster, discord, twitch, now):
    m = roster["members"]
    if discord is not None:  # the reaction list is the truth: add new reactors, drop anyone who removed theirs
        for k in [k for k in m if k.startswith("discord:") and k not in discord]: del m[k]
        for k, v in discord.items():
            e = m.setdefault(k, {"joined": now}); e.update(v)
    if twitch is not None:
        events, pics = twitch[0], twitch[1]
        for _, kind, uid, name, *_ in events:
            k = f"twitch:{uid}"
            if kind == "join":
                e = m.setdefault(k, {"joined": now}); e.update({"name": name, "avatar": pics.get(uid, "")})
            else:
                m.pop(k, None)
        for k, e in m.items():  # keep Twitch pictures current
            if k.startswith("twitch:") and k[7:] in pics: e["avatar"] = pics[k[7:]]
    return roster


# ----------------------------------------------------------------------------- render
def font(names, size):
    for n in names:
        for d in FONT_DIRS:
            for p in d.rglob(n) if d.exists() else []:
                try:
                    f = ImageFont.truetype(str(p), size)
                    try: f.set_variation_by_axes([700])  # variable fonts default to their thinnest weight
                    except Exception: pass
                    return f
                except OSError: pass
    return ImageFont.load_default(size)


NAME_FONTS = ["JosefinSans*wght*.ttf", "NotoSans-SemiBold.ttf", "NotoSans-Regular.ttf", "DejaVuSans-Bold.ttf"]
CJK_FONTS = ["NotoSansCJK-Bold.ttc", "NotoSansCJK-Regular.ttc", "NotoSansCJKjp-Regular.otf"]


def fits_glyphs(f, text):
    try:
        return all(f.getmask(ch).getbbox() is not None or ch.isspace() for ch in text)
    except Exception:
        return False


def name_strip(name):
    w, h = 256, 48
    im = Image.new("RGB", (w, h)); d = ImageDraw.Draw(im)
    for r in range(h):  # brushed brass
        f = 0.82 + 0.18 * (1 - abs(r - 18) / 30); d.line([(0, r), (w, r)], fill=(int(196 * f), int(152 * f), int(82 * f)))
    text = " ".join(name.split())[:28]
    for size in range(24, 11, -1):
        f = font(NAME_FONTS, size)
        if not fits_glyphs(f, text): f = font(CJK_FONTS, size)
        if d.textlength(text, font=f) <= w - 18: break
    tw = d.textlength(text, font=f); y = (h - size) / 2 - 3
    d.text(((w - tw) / 2, y + 1), text, fill=(236, 204, 136), font=f)  # engraved: light lip under dark letters
    d.text(((w - tw) / 2, y), text, fill=(44, 26, 8), font=f)
    return im


def fetch(url, cache):
    key = hashlib.sha1(url.encode()).hexdigest()[:16]; p = cache / f"{key}.img"
    if not p.exists():
        r = requests.get(url, headers=UA, timeout=30); r.raise_for_status(); p.write_bytes(r.content)
    return Image.open(io.BytesIO(p.read_bytes())).convert("RGB")


def portrait_cell(img):
    return ImageOps.fit(img, (200, 240), Image.LANCZOS, centering=(0.5, 0.42))


def render(roster, now_dt, placeholder_fox=None):
    DOCS.mkdir(exist_ok=True); cache = ROOT / ".cache"; cache.mkdir(exist_ok=True)
    bl = blocklist()
    people = [(k, v) for k, v in roster["members"].items() if k.lower() not in bl and v.get("name", "").lower() not in bl]
    people.sort(key=lambda kv: kv[1]["joined"], reverse=True)  # newest first
    cutoff = (now_dt - dt.timedelta(days=NEW_DAYS)).isoformat()
    new = [p for p in people if p[1]["joined"] >= cutoff]
    rest = [p for p in people if p[1]["joined"] < cutoff]
    random.Random(now_dt.strftime("%Y%m%d%H")).shuffle(rest)  # same order for the whole hour
    hall = (new + rest)[:HALL_SLOTS]
    pics = {}
    for k, v in people[:max(HALL_SLOTS, MOSAIC_SLOTS)]:
        try: pics[k] = fetch(v["avatar"], cache)
        except Exception as e: print(f"  picture for {v.get('name')} failed: {e}")

    H = Image.open(ROOT / "templates" / "hallway_empty.png").convert("RGB")
    for i, (k, v) in enumerate(hall):
        if k in pics: H.paste(portrait_cell(pics[k]), ((i % 10) * 200, (i // 10) * 240))
        H.paste(name_strip(v["name"]), ((i % 8) * 256, 1440 + (i // 8) * 48))
    H.save(DOCS / "hallway.png", optimize=True)

    Mo = Image.open(ROOT / "templates" / "mosaic_empty.png").convert("RGB")
    for j, (k, v) in enumerate(people[:MOSAIC_SLOTS]):
        if k in pics: Mo.paste(ImageOps.fit(pics[k], (128, 128), Image.LANCZOS), ((j % 16) * 128, (j // 16) * 128))
    Mo.save(DOCS / "mosaic.png", optimize=True)

    (DOCS / "wall.json").write_text(json.dumps({"count": len(people), "updated": now_dt.strftime("%b %d, %Y").replace(" 0", " ")}), "utf-8")
    (DOCS / ".nojekyll").write_text("")
    print(f"Rendered: {len(people)} on the wall, {len(hall)} in the hallway")


def publish_flag(now_dt):
    """Tells the workflow whether the sheets changed (deploy only then) and keeps the schedule alive:
    GitHub pauses scheduled workflows in public repos after 60 days without commits, so a heartbeat is committed monthly."""
    h = hashlib.sha256()
    for n in ("hallway.png", "mosaic.png", "wall.json"): h.update((DOCS / n).read_bytes())
    digest, last = h.hexdigest(), STATE / "published.sha"
    changed = not last.exists() or last.read_text().strip() != digest
    if changed: last.write_text(digest)
    (ROOT / ".publish").write_text("yes" if changed else "no")
    hb = STATE / "heartbeat.txt"
    if not hb.exists() or (now_dt - dt.datetime.fromisoformat(hb.read_text().strip())).days >= 30: hb.write_text(now_dt.isoformat())


def main():
    now_dt = dt.datetime.now(dt.timezone.utc).replace(microsecond=0); now = now_dt.isoformat()
    roster = load_roster()
    discord = discord_members()
    tw = twitch_events()
    update_roster(roster, discord, tw, now)
    save_roster(roster)
    render(roster, now_dt)
    publish_flag(now_dt)
    if tw:  # only after everything is saved, so a crash can't lose a redemption
        twitch_complete(tw[2], tw[3], tw[0])


if __name__ == "__main__":
    main()
