"""Spurs schedule, results, standings, and matching press-conference videos to games.

Schedule, results and standings come from Basketball-Reference (one page each). Results
Basketball-Reference hasn't posted yet come from ESPN's scoreboard.
Used by update.py (daily video refresh) and app.py (live results and standings).
"""
import csv
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).parent
VIDEOS_CSV = BASE / "videos.csv"
INTERVIEWS_CSV = BASE / "interviews.csv"
VENUES_CSV = BASE / "venues.csv"   # where each played game was held (from box scores)
ARENAS_CSV = BASE / "arenas.csv"   # each team's home arena this season
PRESEASON_CSV = BASE / "preseason.csv"  # preseason games (from Wikipedia)
STANDINGS_CSV = BASE / "standings.csv"   # last saved standings (fallback for the app)
TRANSCRIPTS_DIR = BASE / "transcripts"    # <video id>.txt: YouTube's automatic captions

CHANNEL = "https://www.youtube.com/@SanAntonioSpursPressConference/videos"
SCHEDULE_URL = "https://www.basketball-reference.com/teams/SAS/{season}_games.html"
STANDINGS_URL = "https://www.basketball-reference.com/leagues/NBA_{season}_standings.html"
TEAM_URL = "https://www.basketball-reference.com/teams/{code}/{season}.html"
BOX_SCORE_URL = "https://www.basketball-reference.com{path}"
# ESPN's public scoreboard has final scores minutes after a game ends; Basketball-Reference
# can take hours. Used only for games Basketball-Reference has no result for yet.
ESPN_SCOREBOARD_URL = ("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/"
                       "scoreboard?dates={day}")
ESPN_STANDINGS_URL = ("https://site.api.espn.com/apis/v2/sports/basketball/nba/"
                      "standings?season={season}")
WIKI_URL = "https://en.wikipedia.org/api/rest_v1/page/html/{title}"
REGULAR_SEASON_GAMES = 82
USER_AGENT = "Mozilla/5.0 (mitch-interviews)"
SPEAKER = "Mitch Johnson"
ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")   # San Antonio
PT = ZoneInfo("America/Los_Angeles")
PRE = (timedelta(hours=-3), timedelta(hours=1))
POST = (timedelta(hours=1), timedelta(hours=16))
HOME_CITY = "San Antonio"

# Basketball-Reference team codes (for each team's page, which names its arena).
TEAM_CODES = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BRK",
    "Charlotte Hornets": "CHO", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "LA Clippers": "LAC", "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP", "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHO", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR", "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}
SPURS = "San Antonio Spurs"

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


def current_season(today: date) -> int:
    """NBA seasons are named by the year they end in: 2026-27 -> 2027."""
    return today.year + 1 if today.month >= 8 else today.year


def get_page(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8")


def cells(row_html: str) -> dict:
    """{data-stat: text} for one Basketball-Reference table row."""
    return {k: html.unescape(re.sub(r"<[^>]+>", "", v)).replace("\xa0", " ").strip()
            for k, v in re.findall(r'data-stat="([a-z_]+)"[^>]*>(.*?)</t[dh]>', row_html, re.S)}


def fetch_schedule(season: int) -> list:
    """Every Spurs game this season, with the result once it's been played."""
    page = get_page(SCHEDULE_URL.format(season=season))
    games = []
    for row in re.findall(r'<tr ><th scope="row" class="right " data-stat="g" >\d+</th>(.*?)</tr>',
                          page, re.S):
        c = cells(row)
        if not c.get("date_game") or not c.get("game_start_time"):
            continue
        start = c["game_start_time"].upper().replace("P", "PM").replace("A", "AM")
        tip = datetime.strptime(f'{c["date_game"]} {start}', "%a, %b %d, %Y %I:%M%p").replace(tzinfo=ET)
        away = c.get("game_location") == "@"
        opp = c["opp_name"]
        day = tip.astimezone(CT).date().isoformat()
        # A first guess; describe_location() swaps in the arena from venues/arenas.
        location = f"{TEAM_CITY.get(opp, opp)} (away)" if away else f"{HOME_CITY} (home)"
        box = re.search(r'data-stat="box_score_text"[^>]*><a href="(/boxscores/[^"]+)"', row)
        result = ""
        if c.get("game_result") in ("W", "L") and c.get("pts") and c.get("opp_pts"):
            ot = f" ({c['overtimes']})" if c.get("overtimes") else ""
            result = f"{c['game_result']} {c['pts']}–{c['opp_pts']}{ot}"
        games.append({"kind": "Regular season", "tip": tip, "date": day, "away": away,
                      "opp": opp, "location": location,
                      "nba_cup": "NBA Cup" in c.get("game_remarks", ""),
                      "outcome": c.get("game_result", ""), "result": result,
                      "box_score": box.group(1) if box else ""})
    fill_recent_results(games)
    return games


def espn_result(tip: datetime) -> tuple:
    """(outcome, result) of the Spurs game tipping off at `tip`, from ESPN's scoreboard,
    or ("", "") if it isn't final there. Same format as Basketball-Reference's results."""
    day = tip.astimezone(ET).strftime("%Y%m%d")
    data = json.loads(get_page(ESPN_SCOREBOARD_URL.format(day=day)))
    for event in data.get("events", []):
        comp = event["competitions"][0]
        teams = {t["team"].get("displayName"): t for t in comp["competitors"]}
        if SPURS not in teams or len(teams) != 2:
            continue
        status = event.get("status", {})
        if not status.get("type", {}).get("completed"):
            return "", ""
        us = int(teams.pop(SPURS)["score"])
        them = int(next(iter(teams.values()))["score"])
        extra = int(status.get("period") or 4) - 4
        ot = "" if extra <= 0 else f" ({'' if extra == 1 else extra}OT)"
        outcome = "W" if us > them else "L"
        return outcome, f"{outcome} {us}–{them}{ot}"
    return "", ""


def fill_recent_results(games: list) -> None:
    """Fill in results from ESPN for games from the last 3 days that have tipped off but
    have no result on Basketball-Reference yet. Never fails: a game it can't fill keeps
    waiting for Basketball-Reference."""
    now = datetime.now(timezone.utc)
    for g in games:
        if g["result"] or not (now - timedelta(days=3) < g["tip"] < now):
            continue
        try:
            g["outcome"], g["result"] = espn_result(g["tip"])
        except Exception:
            pass


def fetch_team_arena(team: str, season: int) -> str:
    """The team's home arena this season, from its Basketball-Reference page."""
    page = get_page(TEAM_URL.format(code=TEAM_CODES[team], season=season))
    m = re.search(r"<strong>Arena:</strong>\s*([^<]+?)\s*<", page)
    return html.unescape(m.group(1)).strip() if m else ""


def fetch_venue(box_score_path: str) -> str:
    """Where a played game was held, e.g. "Moody Center, Austin" (from its box score)."""
    page = get_page(BOX_SCORE_URL.format(path=box_score_path))
    meta = re.search(r'<div class="scorebox_meta">(.*?)</div>\s*</div>', page, re.S)
    divs = [html.unescape(re.sub(r"<[^>]+>", "", d)).strip()
            for d in re.findall(r"<div>(.*?)</div>", meta.group(1) if meta else "", re.S)]
    # The venue follows the tip-off time ("10:00 PM, November 25, 2026"). NBA Cup games
    # have an extra "NBA Cup" line first, so it isn't always the second line.
    when = next((i for i, d in enumerate(divs) if re.match(r"\d{1,2}:\d{2} [AP]M, ", d)), None)
    if when is None or when + 1 >= len(divs):
        return ""
    venue = divs[when + 1]
    return re.sub(r",\s*[A-Za-z .]+$", "", venue) if venue.count(",") >= 2 else venue  # drop state


def load_lookup(path: Path, key: str, value: str) -> dict:
    if not path.exists():
        return {}
    return {r[key]: r[value] for r in csv.DictReader(open(path, newline=""))}


def describe_location(game: dict, venues: dict, arenas: dict) -> str:
    """"Frost Bank Center, San Antonio (home)": the box score's venue once a game is
    played, otherwise the home team's arena and city."""
    side = "away" if game["away"] else "home"
    venue = venues.get(game["box_score"], "")
    if not venue:
        host = game["opp"] if game["away"] else SPURS
        city = TEAM_CITY.get(game["opp"], game["opp"]) if game["away"] else HOME_CITY
        arena = arenas.get(host, "")
        venue = f"{arena}, {city}" if arena else city
    return f"{venue} ({side})"


def fetch_standings(season: int) -> dict:
    """{"West": [...], "East": [...]}: each team's seed, W, L, W/L%, GB, PS/G, PA/G.
    From ESPN, which updates minutes after each game; Basketball-Reference if that fails."""
    try:
        return fetch_espn_standings(season)
    except Exception:
        return fetch_bref_standings(season)


def fetch_espn_standings(season: int) -> dict:
    """Standings from ESPN, in the same shape and number formats as Basketball-Reference's."""
    data = json.loads(get_page(ESPN_STANDINGS_URL.format(season=season)))
    out = {}
    for child in data["children"]:
        conf = "West" if "West" in child["name"] else "East"
        teams = []
        for e in child["standings"]["entries"]:
            st = {x["name"]: x for x in e["stats"]}
            num = lambda k: float(st[k]["value"])
            gb = num("gamesBehind")
            name = e["team"]["displayName"]
            teams.append({"seed": int(num("playoffSeed")),
                          "team": "Los Angeles Clippers" if name == "LA Clippers" else name,
                          "wins": int(num("wins")), "losses": int(num("losses")),
                          "pct": st["winPercent"]["displayValue"],
                          "gb": "—" if gb == 0 else f"{gb:.1f}",
                          "ps_g": f"{num('avgPointsFor'):.1f}",
                          "pa_g": f"{num('avgPointsAgainst'):.1f}"})
        out[conf] = sorted(teams, key=lambda t: t["seed"])
    if sorted(out) != ["East", "West"] or any(len(t) != 15 for t in out.values()):
        raise ValueError("unexpected ESPN standings")
    return out


def fetch_bref_standings(season: int) -> dict:
    """Standings from Basketball-Reference."""
    page = get_page(STANDINGS_URL.format(season=season))
    out = {}
    for conf, table_id in (("West", "confs_standings_W"), ("East", "confs_standings_E")):
        table = re.search(rf'<table[^>]*id="{table_id}".*?</table>', page, re.S)
        teams = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(0) if table else "", re.S):
            c = cells(row)
            m = re.match(r"^(.*?)\s*\((\d+)\)\*?$", c.get("team_name", "").replace("*", "").strip())
            if not m or not c.get("wins", "").isdigit():
                continue  # header rows
            teams.append({"seed": int(m[2]), "team": m[1], "wins": int(c["wins"]),
                          "losses": int(c["losses"]), "pct": c.get("win_loss_pct", ""),
                          "gb": c.get("gb", ""), "ps_g": c.get("pts_per_g", ""),
                          "pa_g": c.get("opp_pts_per_g", "")})
        out[conf] = sorted(teams, key=lambda t: t["seed"])
    return out


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




def match_videos(games: list, videos: list) -> list:
    """One row per game: the game plus its pre- and post-game videos (or None).
    videos: [{"id", "title", "uploaded" (aware datetime)}]."""
    rows, used = [], set()
    for g in sorted(games, key=lambda g: g["tip"]):
        pre = pick([v for v in videos if v["id"] not in used], g["tip"], PRE, "closest")
        if pre:
            used.add(pre["id"])
        post = pick([v for v in videos if v["id"] not in used], g["tip"], POST, "earliest")
        if post:
            used.add(post["id"])
        rows.append({**g, "pre": pre, "post": post})
    return rows


def full_team_name(short: str) -> str:
    """'Atlanta' / 'L.A. Lakers' / 'Phoenix' -> the full NBA team name (else unchanged)."""
    short = short.replace("L.A.", "Los Angeles").replace("LA ", "Los Angeles ").strip()
    for team in TEAM_CODES:
        if team == short or team.startswith(short + " ") or team.endswith(" " + short):
            return team
    return short


def fetch_preseason(season: int) -> list:
    """The preseason game log from the season's Wikipedia page:
    [{"date", "opp", "away", "result", "arena"}]."""
    title = f"{season - 1}\u2013{str(season)[2:]}_San_Antonio_Spurs_season"
    req = urllib.request.Request(WIKI_URL.format(title=urllib.parse.quote(title)),
                                 headers={"User-Agent": "mitch-interviews/1.0 (personal project)"})
    page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    m = re.search(r"(?is)preseason game log.*?(<table.*?</table>)", page)
    games = []
    for row in re.findall(r"(?is)<tr.*?</tr>", m.group(1) if m else ""):
        raw = re.findall(r"(?is)<td[^>]*>(.*?)</td>", row)
        if len(raw) < 8:
            continue
        text = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip() for c in raw]
        if not text[0].isdigit():
            continue
        month_day = datetime.strptime(text[1], "%B %d")
        year = season - 1 if month_day.month >= 8 else season
        score = re.search(r"\b([WL]) (\d+)\s*[–-]\s*(\d+)", text[3])
        team = text[2]
        arena = html.unescape(re.sub(r"<[^>]+>", "", re.split(r"(?i)<br\s*/?>", raw[7])[0])).strip()
        games.append({
            "date": month_day.replace(year=year).date().isoformat(),
            "opp": full_team_name(team.lstrip("@ ").strip()),
            "away": "yes" if team.startswith("@") else "",
            "result": f"{score[1]} {score[2]}–{score[3]}" if score else "",
            "arena": re.sub(r"[\d,]+$", "", arena).strip(),
        })
    return games


def load_preseason() -> list:
    """Preseason games as game dicts. preseason.csv may have a tipoff_pt (HH:MM) column
    filled in by hand; without it the tip-off time is unknown."""
    if not PRESEASON_CSV.exists():
        return []
    games = []
    for r in csv.DictReader(open(PRESEASON_CSV, newline="")):
        away = bool(r["away"])
        city = TEAM_CITY.get(r["opp"], r["opp"]) if away else HOME_CITY
        tip = None
        if r.get("tipoff_pt"):
            tip = datetime.fromisoformat(f'{r["date"]}T{r["tipoff_pt"]}').replace(tzinfo=PT)
        games.append({
            "kind": "Preseason", "tip": tip, "date": r["date"], "away": away, "opp": r["opp"],
            "location": f'{r["arena"] + ", " if r["arena"] else ""}{city} ({"away" if away else "home"})',
            "nba_cup": False, "result": r["result"], "outcome": r["result"][:1],
            "box_score": "",
        })
    return games


def match_by_day(games: list, videos: list, used: set) -> list:
    """For games without a tip-off time: the Mitch videos uploaded on game day (from
    10:00 Central to noon the next day). The first is pre-game; the first one at least
    two hours later is post-game. A lone video counts as pre-game if it went up
    before 9 pm Central."""
    rows = []
    for g in games:
        if g["tip"]:
            rows.extend(match_videos([g], [v for v in videos if v["id"] not in used]))
            used.update(v["id"] for v in (rows[-1]["pre"], rows[-1]["post"]) if v)
            continue
        day = datetime.fromisoformat(g["date"]).replace(tzinfo=CT)
        found = sorted((v for v in videos if v["id"] not in used
                        and day + timedelta(hours=10) <= v["uploaded"] <= day + timedelta(hours=36)),
                       key=lambda v: v["uploaded"])
        pre = post = None
        if len(found) == 1:
            local = found[0]["uploaded"].astimezone(CT)
            if local.date().isoformat() == g["date"] and local.hour < 21:
                pre = found[0]
            else:
                post = found[0]
        elif found:
            pre = found[0]
            post = next((v for v in found[1:] if v["uploaded"] - pre["uploaded"] >= timedelta(hours=2)), None)
        used.update(v["id"] for v in (pre, post) if v)
        rows.append({**g, "pre": pre, "post": post})
    return rows


def build_rows(regular: list, preseason: list, videos: list, season: int) -> list:
    """Every row for the table:
    - regular-season games (matched by tip-off time), plus "date TBD" rows up to 82
      (the NBA schedules each team's last two games after NBA Cup group play);
    - preseason games (matched by game day);
    - Mitch Johnson interviews that aren't tied to a game ("No game").
    Each row has a "when" for sorting: tip-off, game day, upload time, or season end."""
    rows = match_videos(regular, videos)
    used = {v["id"] for r in rows for v in (r["pre"], r["post"]) if v}
    rows += match_by_day(sorted(preseason, key=lambda g: g["date"]), videos, used)
    season_end = datetime(season, 4, 30, tzinfo=CT)
    for n in range(len(regular) + 1, REGULAR_SEASON_GAMES + 1):
        rows.append({"kind": "Regular season", "tip": None, "date": "", "away": False,
                     "opp": f"Game {n}: date TBD (set after NBA Cup group play)",
                     "location": "", "nba_cup": True, "result": "", "outcome": "",
                     "box_score": "", "pre": None, "post": None})
    first = min([r["tip"] for r in regular if r["tip"]]
                + [datetime.fromisoformat(g["date"]).replace(tzinfo=CT) for g in preseason],
                default=None)
    for v in videos:
        if v["id"] not in used and first and v["uploaded"] >= first - timedelta(days=5):
            rows.append({"kind": "No game", "tip": v["uploaded"],
                         "date": v["uploaded"].astimezone(CT).date().isoformat(), "away": False,
                         "opp": "", "location": "", "nba_cup": False, "result": "", "outcome": "",
                         "box_score": "", "pre": None, "post": None, "other": v})
    for r in rows:
        r.setdefault("other", None)
        r["when"] = (r["tip"] or (datetime.fromisoformat(r["date"]).replace(tzinfo=CT, hour=19)
                                  if r["date"] else season_end))
    return rows


def load_saved_standings() -> dict:
    """The standings as saved by update.py, in fetch_standings()'s shape."""
    out = {"West": [], "East": []}
    if STANDINGS_CSV.exists():
        for r in csv.DictReader(open(STANDINGS_CSV, newline="")):
            conf = r.pop("conference")
            r["seed"], r["wins"], r["losses"] = int(r["seed"]), int(r["wins"]), int(r["losses"])
            out.setdefault(conf, []).append(r)
    return out


def load_videos(path: Path) -> list:
    if not path.exists():
        return []
    return [{"id": r["id"], "title": r["title"], "uploaded": datetime.fromisoformat(r["uploaded_utc"])}
            for r in csv.DictReader(open(path, newline=""))]
