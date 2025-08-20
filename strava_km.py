import os
import requests
import time
from datetime import datetime, timezone
from dateutil import parser as dtparser
from PIL import Image, ImageDraw, ImageFont

# Färger (Team Rynkeby)
YELLOW = (254, 221, 0)   # #FEDD00
BLACK  = (0, 0, 0)
WHITE  = (255, 255, 255)

# ---------------------------
# Hjälpfunktioner & util
# ---------------------------
def env(key, default=None, required=False):
    v = os.getenv(key, default)
    if required and (v is None or str(v).strip() == ""):
        raise RuntimeError(f"Missing required env var: {key}")
    return v

def iso_to_unix(dt):
    """Tar 'YYYY-MM-DD' eller ISO-sträng -> unix-epoch (sekunder, UTC)."""
    if isinstance(dt, datetime):
        d = dt
    else:
        d = dtparser.parse(str(dt))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp())

def text_wh(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont):
    """
    Returnerar (bredd, höjd) för text i Pillow 10+ (textbbox) med fallback för äldre.
    """
    try:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        return right - left, bottom - top
    except AttributeError:
        return draw.textsize(text, font=font)

def load_font(size, bold=True):
    candidates = []
    if bold:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    candidates += [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()

# ---------------------------
# Strava API
# ---------------------------

def should_count(activity: dict) -> bool:
    """
    Returnerar True om aktiviteten ska räknas som cykelpass.
    - Räknar: Ride, GravelRide, VirtualRide
    - Räknar inte: EBikeRide (m.fl.)
    Stödjer både sport_type (nyare fält) och type (äldre fält).
    """
    # Nyare Strava: sport_type
    st = (activity.get("sport_type") or "").strip()
    if st:
        return st in ("Ride", "GravelRide", "VirtualRide")

    # Äldre fält: type
    t = (activity.get("type") or "").strip()
    return t in ("Ride", "GravelRide", "VirtualRide")


def refresh_access_token():
    client_id = env("STRAVA_CLIENT_ID", required=True)
    client_secret = env("STRAVA_CLIENT_SECRET", required=True)
    refresh_token = env("STRAVA_REFRESH_TOKEN", required=True)

    r = requests.post(
        "https://www.strava.com/api/v3/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]

def fetch_km(access_token, start_iso, end_iso):
    after = iso_to_unix(start_iso)
    before = iso_to_unix(end_iso)

    if after >= before:
        raise RuntimeError(
            f"Ogiltig period: PERIOD_START={start_iso} måste vara före PERIOD_END={end_iso}."
        )

    url = "https://www.strava.com/api/v3/athlete/activities"
    headers = {"Authorization": f"Bearer {access_token}"}
    per_page = 200
    page = 1
    total_km = 0.0

    # Minimal retry/backoff
    def _get(params, tries=5, base_sleep=1.5):
        for i in range(tries):
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            # Särskilda fel först
            if resp.status_code == 401:
                raise RuntimeError(
                    "401 från Strava: access_token saknar troligen 'activity:read'/'activity:read_all' "
                    "eller är ogiltig. Gör om OAuth och uppdatera STRAVA_REFRESH_TOKEN i Secrets."
                )
            if resp.status_code == 400:
                raise RuntimeError("400 från Strava: kontrollera att 'after' < 'before' (startdatum före slutdatum).")
            if resp.status_code in (429, 500, 502, 503, 504):
                # backoff och försök igen
                sleep_s = base_sleep * (2 ** i)
                time.sleep(sleep_s)
                continue
            resp.raise_for_status()
            return resp
        # Om vi hamnar här: för många misslyckade försök
        resp.raise_for_status()

    while True:
        params = {"after": after, "before": before, "page": page, "per_page": per_page}
        resp = _get(params)
        activities = resp.json()
        if not activities:
            break

        for a in activities:
            if should_count(a):
                meters = a.get("distance") or 0
                total_km += float(meters) / 1000.0

        page += 1

    return total_km

# ---------------------------
# Rendering (Stil 3)
# ---------------------------
def draw_style_3(km, goal_km, period_label, out_png, out_svg):
    # Canvas
    W, H = 1200, 700
    PAD = 60
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    f_big = load_font(140, bold=True)    # stor siffra
    f_med = load_font(48, bold=False)    # undertext
    f_small = load_font(36, bold=False)  # period

    # Kort bakgrund
    card_r = 28
    d.rounded_rectangle([PAD, PAD, W-PAD, H-PAD], radius=card_r, fill=YELLOW, outline=None)

    # Stor siffra, ex "376 km"
    km_int = int(round(km))
    goal_int = int(round(goal_km))
    km_txt = f"{km_int} km"
    tw, th = text_wh(d, km_txt, font=f_big)
    d.text(((W - tw) / 2, PAD + 70), km_txt, font=f_big, fill=BLACK)

    # Progressbar
    bar_w = W - 2 * PAD - 160
    bar_h = 50
    bar_x = (W - bar_w) // 2
    bar_y = PAD + 70 + th + 60

    # back
    d.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=bar_h // 2, fill=WHITE)
    # fill
    pct = 0.0 if goal_km <= 0 else max(0.0, min(1.0, km / goal_km))
    fill_w = int(bar_w * pct)
    if fill_w > 0:
        d.rounded_rectangle([bar_x, bar_y, bar_x + fill_w, bar_y + bar_h], radius=bar_h // 2, fill=BLACK)

    # Undertext
    sub = f"{km_int} / {goal_int} km cyklade"
    tw2, th2 = text_wh(d, sub, font=f_med)
    d.text(((W - tw2) / 2, bar_y + bar_h + 30), sub, font=f_med, fill=BLACK)

    # Period
    tw3, th3 = text_wh(d, period_label, font=f_small)
    d.text(((W - tw3) / 2, bar_y + bar_h + 30 + th2 + 18), period_label, font=f_small, fill=BLACK)

    # Spara PNG
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    img.save(out_png, format="PNG", optimize=True)

    # Enkel SVG (om du vill bädda SVG istället)
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
  <rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>
  <rect x="{PAD}" y="{PAD}" rx="{card_r}" ry="{card_r}" width="{W-2*PAD}" height="{H-2*PAD}" fill="#FEDD00"/>
  <text x="{W/2}" y="{PAD+70+110}" font-size="140" font-family="DejaVu Sans, Arial, sans-serif" font-weight="700" fill="#000000" text-anchor="middle">{km_txt}</text>
  <rect x="{bar_x}" y="{bar_y}" rx="{bar_h/2}" ry="{bar_h/2}" width="{bar_w}" height="{bar_h}" fill="#FFFFFF"/>
  <rect x="{bar_x}" y="{bar_y}" rx="{bar_h/2}" ry="{bar_h/2}" width="{fill_w}" height="{bar_h}" fill="#000000"/>
  <text x="{W/2}" y="{bar_y + bar_h + 30 + 40}" font-size="48" font-family="DejaVu Sans, Arial, sans-serif" fill="#000000" text-anchor="middle">{sub}</text>
  <text x="{W/2}" y="{bar_y + bar_h + 30 + 40 + 18 + 36}" font-size="36" font-family="DejaVu Sans, Arial, sans-serif" fill="#000000" text-anchor="middle">{period_label}</text>
</svg>"""
    with open(out_svg, "w", encoding="utf-8") as f:
        f.write(svg)

# ---------------------------
# Main
# ---------------------------
def main():
    access = refresh_access_token()

    period_start = env("PERIOD_START", "2025-10-01")  # skarp default
    period_end = env("PERIOD_END", None)              # None => idag (UTC)
    if not period_end or str(period_end).strip() == "":
        period_end = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    goal_km = float(env("GOAL_KM", "5000"))
    label = env("PERIOD_LABEL", f"Sedan {period_start}")

    # Sanity-check innan API-anrop
    start_ts = iso_to_unix(period_start)
    end_ts = iso_to_unix(period_end)
    if start_ts >= end_ts:
        raise RuntimeError(
            f"Ogiltig period: PERIOD_START={period_start} måste vara före PERIOD_END={period_end}. "
            "Kör workflow i test_mode=true eller justera update.yml."
        )

    km = fetch_km(access, period_start, period_end)

    os.makedirs("docs", exist_ok=True)
    out_png = os.path.join("docs", "strava_km.png")
    out_svg = os.path.join("docs", "strava_km.svg")
    draw_style_3(km, goal_km, label, out_png, out_svg)

    # Liten txt för enkelhet/debug
    with open(os.path.join("docs", "latest.txt"), "w", encoding="utf-8") as f:
        f.write(f"{int(round(km))} / {int(round(goal_km))} km")

if __name__ == "__main__":
    main()
