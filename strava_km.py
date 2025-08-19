import os
import base64
import math
import requests
from datetime import datetime, timezone
from dateutil import parser as dtparser
from dateutil.relativedelta import relativedelta
from PIL import Image, ImageDraw, ImageFont

YELLOW = (254, 221, 0)   # #FEDD00
BLACK  = (0, 0, 0)
WHITE  = (255, 255, 255)
GREY   = (230, 230, 230)

def env(key, default=None, required=False):
    v = os.getenv(key, default)
    if required and (v is None or str(v).strip() == ""):
        raise RuntimeError(f"Missing required env var: {key}")
    return v

def refresh_access_token():
    client_id = env("STRAVA_CLIENT_ID", required=True)
    client_secret = env("STRAVA_CLIENT_SECRET", required=True)
    refresh_token = env("STRAVA_REFRESH_TOKEN", required=True)
    r = requests.post(
        "https://www.strava.com/oauth/token",
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

def iso_to_unix(dt):
    # Accepts 'YYYY-MM-DD' or ISO string; returns unix seconds (UTC)
    if isinstance(dt, datetime):
        d = dt
    else:
        d = dtparser.parse(str(dt))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp())

def fetch_km(access_token, start_iso, end_iso):
    after = iso_to_unix(start_iso)
    before = iso_to_unix(end_iso)
    km_total = 0.0
    page = 1
    per_page = 200
    while True:
        resp = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"after": after, "before": before, "page": page, "per_page": per_page},
            timeout=30,
        )
        resp.raise_for_status()
        acts = resp.json()
        if not acts:
            break
        for a in acts:
            t = a.get("type", "")
            # Räkna primärt landsväg. Lägg gärna till "VirtualRide" om du vill.
            if t in ("Ride", "GravelRide", "VirtualRide"):
                meters = a.get("distance", 0) or 0
                km_total += meters / 1000.0
        page += 1
    return km_total

def draw_style_3(km, goal_km, period_label, out_png, out_svg):
    # Canvas
    W, H = 1200, 700
    PAD = 60
    img = Image.new("RGB", (W, H), WHITE)
    d = ImageDraw.Draw(img)

    # Fonts (fallback till PIL default om DejaVu inte finns i runnern)
    def load_font(sz):
        for name in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]:
            try:
                return ImageFont.truetype(name, sz)
            except:
                continue
        return ImageFont.load_default()

    f_big = load_font(140)      # “376 km”
    f_med = load_font(48)       # “376 / 5000 km cyklade”
    f_small = load_font(36)     # period text

    # Kort bakgrund
    card_r = 28
    d.rounded_rectangle([PAD, PAD, W-PAD, H-PAD], radius=card_r, fill=YELLOW, outline=None)

    # Text: stor siffra
    km_txt = f"{int(round(km))} km"
    tw, th = d.textsize(km_txt, font=f_big)
    d.text(((W-tw)/2, PAD+70), km_txt, font=f_big, fill=BLACK)

    # Progressbar
    bar_w = W - 2*PAD - 160
    bar_h = 50
    bar_x = (W - bar_w) // 2
    bar_y = PAD + 70 + th + 60

    # back
    d.rounded_rectangle([bar_x, bar_y, bar_x+bar_w, bar_y+bar_h], radius=bar_h//2, fill=WHITE)
    # fill
    pct = 0.0 if goal_km <= 0 else max(0.0, min(1.0, km/goal_km))
    fill_w = int(bar_w * pct)
    if fill_w > 0:
        d.rounded_rectangle([bar_x, bar_y, bar_x+fill_w, bar_y+bar_h], radius=bar_h//2, fill=BLACK)

    # Undertext
    sub = f"{int(round(km))} / {int(goal_km)} km cyklade"
    tw2, th2 = d.textsize(sub, font=f_med)
    d.text(((W-tw2)/2, bar_y + bar_h + 30), sub, font=f_med, fill=BLACK)

    # Period text
    tw3, th3 = d.textsize(period_label, font=f_small)
    d.text(((W-tw3)/2, bar_y + bar_h + 30 + th2 + 18), period_label, font=f_small, fill=BLACK)

    img.save(out_png, format="PNG", optimize=True)

    # Enkel SVG-version (för den som vill bädda SVG)
    pct_px = fill_w
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
  <rect x="0" y="0" width="{W}" height="{H}" fill="white"/>
  <rect x="{PAD}" y="{PAD}" rx="{card_r}" ry="{card_r}" width="{W-2*PAD}" height="{H-2*PAD}" fill="#FEDD00"/>
  <text x="{W/2}" y="{PAD+70+110}" font-size="140" font-family="DejaVu Sans, Arial, sans-serif" font-weight="700" fill="#000" text-anchor="middle">{km_txt}</text>
  <rect x="{bar_x}" y="{bar_y}" rx="{bar_h/2}" ry="{bar_h/2}" width="{bar_w}" height="{bar_h}" fill="#FFFFFF"/>
  <rect x="{bar_x}" y="{bar_y}" rx="{bar_h/2}" ry="{bar_h/2}" width="{pct_px}" height="{bar_h}" fill="#000000"/>
  <text x="{W/2}" y="{bar_y + bar_h + 30 + 40}" font-size="48" font-family="DejaVu Sans, Arial, sans-serif" fill="#000" text-anchor="middle">{sub}</text>
  <text x="{W/2}" y="{bar_y + bar_h + 30 + 40 + 18 + 36}" font-size="36" font-family="DejaVu Sans, Arial, sans-serif" fill="#000" text-anchor="middle">{period_label}</text>
</svg>"""
    with open(out_svg, "w", encoding="utf-8") as f:
        f.write(svg)

def main():
    access = refresh_access_token()
    # Period
    period_start = env("PERIOD_START", "2025-10-01")  # skarp default
    period_end = env("PERIOD_END", None)              # None => now (UTC)
    if period_end is None or period_end.strip() == "":
        period_end = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    goal_km = float(env("GOAL_KM", "5000"))
    label = env("PERIOD_LABEL", f"Sedan {period_start}")

    km = fetch_km(access, period_start, period_end)
    os.makedirs("docs", exist_ok=True)
    out_png = os.path.join("docs", "strava_km_style3.png")
    out_svg = os.path.join("docs", "strava_km_style3.svg")
    draw_style_3(km, goal_km, label, out_png, out_svg)

    # Liten txt för debug/enkelt embed
    with open(os.path.join("docs", "latest.txt"), "w", encoding="utf-8") as f:
        f.write(f"{int(round(km))} / {int(goal_km)} km")

if __name__ == "__main__":
    main()
