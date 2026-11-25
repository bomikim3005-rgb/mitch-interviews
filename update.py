"""Build interviews.csv: Mitch Johnson's pre- and post-game press conferences per Spurs game.

Sources:
- Schedule: Basketball-Reference's Spurs schedule page (date, tip-off time in ET,
  home/away, opponent, NBA Cup remarks). One page per run.
- Videos: the @SanAntonioSpursPressConference YouTube channel, read with yt-dlp
  (metadata only, nothing is downloaded). Upload times are cached in videos.csv, so
  only new videos are looked up.

The channel's titles don't say "pregame" or "postgame", so videos are paired with
games by upload time. Pre-game pressers go up around tip-off; post-game ones a few
hours later (sometimes the next morning):
    pre-game:  uploaded from 3 hours before to 1 hour after tip-off
    post-game: uploaded from 1 to 16 hours after tip-off

Run:
    python update.py              # current season
    python update.py --season 2027  # season ending in 2027 (2026-27)
"""
import argparse
import csv
import html
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yt_dlp

BASE = Path(__file__).parent
VIDEOS_CSV = BASE / "videos.csv"
INTERVIEWS_CSV = BASE / "interviews.csv"
OVERRIDES_CSV = BASE / "location_overrides.csv"

CHANNEL = "https://www.youtube.com/@SanAntonioSpursPressConference/videos"
SCHEDULE_URL = "https://www.basketball-reference.com/teams/SAS/{season}_games.html"
SPEAKER = "Mitch Johnson"
ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")   # San Antonio
PRE = (timedelta(hours=-3), timedelta(hours=1))
POST = (timedelta(hours=1), timedelta(hours=16))
HOME_CITY = "San Antonio"

# Opponent -> the city it plays home games in (where the Spurs play when away).
TEAM_CITY = {
    "Atlanta Hawks": "Atlanta", "Boston Celtics": "Boston", "Brooklyn Nets": "Brooklyn",
    "Charlotte Hornets": "Charlotte", "Chicago Bulls": "Chicago",
    "Cleveland Cavaliers": "Cleveland", "Dallas Mavericks": "Dallas",
    "Denver Nuggets": "Denver", "Detroit Pistons": "Detroit",
    "Golden State Warriors": "San Francisco", "Houston Rockets": "Houston",
    "Indiana Pacers": "Indianapolis", "Los Angeles Clippers": "Inglewood",
    "LA Clippers": "Inglewood", "Los Angeles Lakers": "Los Angeles",
    "Memphis Grizzlies": "Memphis", "Miami Heat": "Miami", "Milwaukee Bucks": "Milwaukee",
    "Minnesota Timberwolves": "Minneapolis", "New Orleans Pelicans": "New Orleans",
    "New York Knicks": "New York", "Oklahoma City Thunder": "Oklahoma City",
    "Orlando Magic": "Orlando", "Philadelphia 76ers": "Philadelphia",
    "Phoenix Suns": "Phoenix", "Portland Trail Blazers": "Portland",
    "Sacramento Kings": "Sacramento", "Toronto Raptors": "Toronto",
    "Utah Jazz": "Salt Lake City", "Washington Wizards": "Washington",
}

FIELDS = ["date", "tipoff_ct", "game", "location", "nba_cup",
          "pregame_url", "pregame_title", "postgame_url", "postgame_title", "tipoff_utc"]


def current_season(today: date) -> int:
    """NBA seasons are named by the year they end in: 2026-27 -> 2027."""
    return today.year + 1 if today.month >= 8 else today.year


def fetch_schedule(season: int) -> list:
    req = urllib.request.Request(SCHEDULE_URL.format(season=season),
                                 headers={"User-Agent": "Mozilla/5.0 (mitch-interviews)"})
    page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    overrides = {}
    if OVERRIDES_CSV.exists():
        overrides = {r["date"]: r["location"] for r in csv.DictReader(open(OVERRIDES_CSV))}
    games = []
    for row in re.findall(r'<tr ><th scope="row" class="right " data-stat="g" >\d+</th>(.*?)</tr>',
                          page, re.S):
        c = {k: html.unescape(re.sub(r"<[^>]+>", "", v)).strip()
             for k, v in re.findall(r'data-stat="([a-z_]+)"[^>]*>(.*?)</t[dh]>', row, re.S)}
        if not c.get("date_game") or not c.get("game_start_time"):
            continue
        start = c["game_start_time"].upper().replace("P", "PM").replace("A", "AM")
        tip = datetime.strptime(f'{c["date_game"]} {start}', "%a, %b %d, %Y %I:%M%p").replace(tzinfo=ET)
        away = c.get("game_location") == "@"
        opp = c["opp_name"]
        day = tip.astimezone(CT).date().isoformat()
        location = overrides.get(day) or (
            f"{TEAM_CITY.get(opp, opp)} (away)" if away else f"{HOME_CITY} (home)")
        games.append({"tip": tip, "date": day, "away": away, "opp": opp, "location": location,
                      "nba_cup": "NBA Cup" in c.get("game_remarks", "")})
    return games


def list_videos() -> list:
    """(id, title) of every video on the channel, newest first. No downloads."""
    opts = {"extract_flat": True, "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(CHANNEL, download=False)
    return [(e["id"], e.get("title") or "") for e in info.get("entries", []) if e.get("id")]


def upload_times(ids: list) -> dict:
    """{id: upload time (UTC)} for these videos; ones that fail are skipped."""
    out = {}
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "ignoreerrors": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        for vid in ids:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
            ts = (info or {}).get("timestamp") or (info or {}).get("release_timestamp")
            if ts:
                out[vid] = datetime.fromtimestamp(ts, timezone.utc)
            else:
                print(f"  no upload time for {vid}; will retry next run", file=sys.stderr)
    return out


def load_cache() -> dict:
    if not VIDEOS_CSV.exists():
        return {}
    return {r["id"]: r for r in csv.DictReader(open(VIDEOS_CSV, newline=""))}


def save_cache(cache: dict) -> None:
    rows = sorted(cache.values(), key=lambda r: r["uploaded_utc"], reverse=True)
    with open(VIDEOS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, ["id", "title", "uploaded_utc"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def pick(videos: list, tip: datetime, window: tuple, prefer: str):
    """The video uploaded in this window around tip-off: closest to tip-off for
    pre-game, earliest for post-game."""
    lo, hi = tip + window[0], tip + window[1]
    found = [v for v in videos if lo <= v["uploaded"] <= hi]
    if not found:
        return None
    if prefer == "closest":
        return min(found, key=lambda v: abs(v["uploaded"] - tip))
    return min(found, key=lambda v: v["uploaded"])


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, default=current_season(date.today()))
    args = parser.parse_args()

    games = fetch_schedule(args.season)
    print(f"Schedule: {len(games)} games in the {args.season - 1}-{str(args.season)[2:]} season.")

    cache = load_cache()
    mitch = [(vid, title) for vid, title in list_videos()
             if title.lower().startswith(SPEAKER.lower())]
    new = [vid for vid, _ in mitch if vid not in cache]
    titles = dict(mitch)
    print(f"Channel: {len(mitch)} {SPEAKER} videos, {len(new)} new.")
    for vid, when in upload_times(new).items():
        cache[vid] = {"id": vid, "title": titles[vid], "uploaded_utc": when.isoformat()}
    save_cache(cache)

    videos = [{"id": r["id"], "title": r["title"],
               "uploaded": datetime.fromisoformat(r["uploaded_utc"])} for r in cache.values()]
    rows, used = [], set()
    for g in sorted(games, key=lambda g: g["tip"]):
        pre = pick([v for v in videos if v["id"] not in used], g["tip"], PRE, "closest")
        if pre:
            used.add(pre["id"])
        post = pick([v for v in videos if v["id"] not in used], g["tip"], POST, "earliest")
        if post:
            used.add(post["id"])
        rows.append({
            "date": g["date"],
            "tipoff_ct": g["tip"].astimezone(CT).strftime("%H:%M"),
            "game": f"{'@' if g['away'] else 'vs'} {g['opp']}",
            "location": g["location"],
            "nba_cup": "yes" if g["nba_cup"] else "",
            "pregame_url": f"https://www.youtube.com/watch?v={pre['id']}" if pre else "",
            "pregame_title": pre["title"] if pre else "",
            "postgame_url": f"https://www.youtube.com/watch?v={post['id']}" if post else "",
            "postgame_title": post["title"] if post else "",
            "tipoff_utc": g["tip"].astimezone(timezone.utc).isoformat(),
        })
    with open(INTERVIEWS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    played = [r for r in rows if r["tipoff_utc"] < datetime.now(timezone.utc).isoformat()]
    print(f"Wrote {INTERVIEWS_CSV.name}: {len(played)} games played, "
          f"{sum(1 for r in played if r['pregame_url'])} with a pre-game video, "
          f"{sum(1 for r in played if r['postgame_url'])} with a post-game video.")


if __name__ == "__main__":
    main()
