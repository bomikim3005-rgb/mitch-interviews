"""Refresh videos.csv (Mitch Johnson's press-conference videos) and interviews.csv.

- Videos: the @SanAntonioSpursPressConference YouTube channel, read with yt-dlp
  (metadata only, nothing is downloaded). Upload times are cached in videos.csv,
  so only new videos are looked up.
- transcripts/<id>.txt: each video's English captions (YouTube's automatic captions),
  for the word cloud. Fetched once per video, slowly: YouTube refuses caption requests
  that come too fast (HTTP 429), so this pauses between videos and stops for the run
  if it keeps being refused; the rest are fetched next run.
- venues.csv / arenas.csv: where each played game was held (from its box score) and
  each team's home arena (for upcoming games), from Basketball-Reference. Only missing
  ones are fetched, a few seconds apart to stay within the site's rate limit.
- interviews.csv: one row per game with its result and pre/post-game videos (see
  spurs.match_videos). The app builds the same table live; it uses this file only
  if Basketball-Reference can't be reached.

Run:
    python update.py                # current season
    python update.py --season 2027  # season ending in 2027 (2026-27)
"""
import argparse
import csv
import json
import os
import re
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import date, datetime, timezone

import yt_dlp

# yt-dlp looks for Deno (its recommended JavaScript runtime for YouTube) on PATH; the
# `deno` package installs it next to this Python, so add that folder.
os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")

import spurs as S

CHANNEL = "https://www.youtube.com/@SanAntonioSpursPressConference/videos"
SPEAKER = "Mitch Johnson"
FIELDS = ["kind", "date", "tipoff_utc", "game", "location", "nba_cup", "result", "outcome",
          "pregame_url", "pregame_title", "postgame_url", "postgame_title",
          "other_url", "other_title"]


def list_videos() -> list:
    """(id, title) of every video on the channel, newest first. No downloads."""
    opts = {"extract_flat": True, "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(CHANNEL, download=False)
    return [(e["id"], e.get("title") or "") for e in info.get("entries", []) if e.get("id")]


def upload_times(ids: list) -> dict:
    """{id: upload time (UTC)} for these videos; ones that fail are retried next run."""
    out = {}
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "ignoreerrors": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        for vid in ids:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False) or {}
            ts = info.get("timestamp") or info.get("release_timestamp")
            if ts:
                out[vid] = datetime.fromtimestamp(ts, timezone.utc)
            else:
                print(f"  no upload time for {vid}; will retry next run", file=sys.stderr)
    return out


CHANNEL_ID = "UCwM35PC2YVTBAERgtfIuVhA"
FEED_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"


def feed_videos() -> list:
    """(id, title, upload time) of the channel's latest 15 videos, from YouTube's public
    RSS feed. Its times match the videos' upload times exactly, and it works from cloud
    servers where YouTube may challenge yt-dlp."""
    atom = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    root = ET.fromstring(urllib.request.urlopen(FEED_URL, timeout=30).read())
    return [(e.find("yt:videoId", atom).text, e.find("a:title", atom).text,
             datetime.fromisoformat(e.find("a:published", atom).text))
            for e in root.findall("a:entry", atom)]


def refresh_videos(full_scan: bool) -> list:
    """Add new Mitch Johnson videos to videos.csv. Normally from the RSS feed (latest 15
    videos, so run at least every few days); with full_scan, from the whole channel."""
    cache = {}
    if S.VIDEOS_CSV.exists():
        cache = {r["id"]: r for r in csv.DictReader(open(S.VIDEOS_CSV, newline=""))}
    # Any title with his name, including joint interviews ("Dylan Harper and Mitch Johnson ...").
    is_mitch = lambda title: SPEAKER.lower() in (title or "").lower()
    if full_scan or not cache:
        mitch = [(vid, title) for vid, title in list_videos() if is_mitch(title)]
        new = [vid for vid, _ in mitch if vid not in cache]
        titles = dict(mitch)
        for vid, when in upload_times(new).items():
            cache[vid] = {"id": vid, "title": titles[vid], "uploaded_utc": when.isoformat()}
        print(f"Channel (full scan): {len(mitch)} {SPEAKER} videos, {len(new)} new.")
    else:
        latest = [(vid, title, when) for vid, title, when in feed_videos() if is_mitch(title)]
        new = [(vid, title, when) for vid, title, when in latest if vid not in cache]
        for vid, title, when in new:
            cache[vid] = {"id": vid, "title": title,
                          "uploaded_utc": when.astimezone(timezone.utc).isoformat()}
        print(f"Channel (RSS feed): {len(cache)} {SPEAKER} videos saved, {len(new)} new.")
    with open(S.VIDEOS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, ["id", "title", "uploaded_utc"], lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(cache.values(), key=lambda r: r["uploaded_utc"], reverse=True))
    return S.load_videos(S.VIDEOS_CSV)


CAPTION_PAUSE = 20       # seconds between caption requests
CAPTION_BACKOFF = 0      # no retrying: hammering a refusal only prolongs the block
CAPTION_MAX_REFUSALS = 1 # stop for this run at the first "too many requests"


def caption_text(json3_path: Path) -> str:
    """Plain text from a YouTube json3 caption file."""
    data = json.loads(json3_path.read_text())
    words = [seg.get("utf8", "") for event in data.get("events", []) for seg in event.get("segs") or []]
    return re.sub(r"\s+", " ", " ".join(words)).replace("[Music]", "").strip()


def refresh_transcripts(video_ids: list) -> None:
    S.TRANSCRIPTS_DIR.mkdir(exist_ok=True)
    missing = [v for v in video_ids if not (S.TRANSCRIPTS_DIR / f"{v}.txt").exists()]
    if not missing:
        return
    print(f"Transcripts: fetching {len(missing)} (about {CAPTION_PAUSE}s each)...")
    refusals = got = 0
    with tempfile.TemporaryDirectory() as tmp:
        opts = {"skip_download": True, "writeautomaticsub": True, "writesubtitles": True,
                "subtitleslangs": ["en"], "subtitlesformat": "json3",
                "outtmpl": f"{tmp}/%(id)s", "quiet": True, "no_warnings": True}
        for vid in missing:
            time.sleep(CAPTION_PAUSE)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([f"https://www.youtube.com/watch?v={vid}"])
            except yt_dlp.utils.DownloadError as e:
                # "Too Many Requests" or YouTube's bot check: it's refusing this network
                # for now, so stop rather than keep asking.
                if "429" not in str(e) and "not a bot" not in str(e):
                    print(f"  no captions for {vid}: {str(e).splitlines()[0][:100]}")
                    continue
                refusals += 1
                if refusals >= CAPTION_MAX_REFUSALS:
                    print("  YouTube is limiting caption downloads right now; the rest will be "
                          "fetched next run (try again in several hours).")
                    break
                print(f"  too many requests; waiting {CAPTION_BACKOFF}s")
                time.sleep(CAPTION_BACKOFF)
                continue  # (only reached if CAPTION_MAX_REFUSALS > 1)
            found = list(Path(tmp).glob(f"{vid}*.json3"))
            if found:
                (S.TRANSCRIPTS_DIR / f"{vid}.txt").write_text(caption_text(found[0]))
                got += 1
    print(f"Transcripts: {got} new, {len(list(S.TRANSCRIPTS_DIR.glob('*.txt')))} in total.")


PAUSE = 3.5  # seconds between Basketball-Reference requests (limit: 20 a minute)


def write_lookup(path, fields, rows) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def refresh_places(games: list, season: int) -> tuple:
    """Fill in venues for newly played games and arenas for this season's teams."""
    venues = S.load_lookup(S.VENUES_CSV, "box_score", "venue")
    for g in games:
        # Only played games have a box score with the venue.
        if g["result"] and g["box_score"] and g["box_score"] not in venues:
            time.sleep(PAUSE)
            try:
                venues[g["box_score"]] = S.fetch_venue(g["box_score"])
            except Exception as e:
                print(f"  venue for {g['date']} not found ({type(e).__name__}); will retry")
    write_lookup(S.VENUES_CSV, ["box_score", "venue"],
                 [{"box_score": k, "venue": v} for k, v in sorted(venues.items()) if v])

    rows = list(csv.DictReader(open(S.ARENAS_CSV, newline=""))) if S.ARENAS_CSV.exists() else []
    arenas = {r["team"]: r["arena"] for r in rows if r["season"] == str(season)}
    for team in sorted({S.SPURS, *(g["opp"] for g in games)}):
        if team not in arenas and team in S.TEAM_CODES:
            time.sleep(PAUSE)
            try:
                arenas[team] = S.fetch_team_arena(team, season)
            except Exception as e:
                print(f"  arena for {team} not found ({type(e).__name__}); will retry")
    write_lookup(S.ARENAS_CSV, ["season", "team", "arena"],
                 [{"season": season, "team": t, "arena": a} for t, a in sorted(arenas.items()) if a])
    print(f"Places: {len(venues)} game venues, {len(arenas)} team arenas.")
    return venues, arenas


def refresh_preseason(season: int) -> None:
    """Save the preseason game log to preseason.csv, keeping any tipoff_pt typed in."""
    try:
        games = S.fetch_preseason(season)
    except Exception as e:
        print(f"  preseason not updated ({type(e).__name__}); keeping preseason.csv")
        return
    if not games:
        return
    times = {}
    if S.PRESEASON_CSV.exists():
        times = {r["date"]: r.get("tipoff_pt", "") for r in csv.DictReader(open(S.PRESEASON_CSV))}
    for g in games:
        g["tipoff_pt"] = times.get(g["date"], "")
    write_lookup(S.PRESEASON_CSV, ["date", "opp", "away", "result", "arena", "tipoff_pt"], games)
    print(f"Preseason: {len(games)} games.")


def save_standings(season: int) -> None:
    """A copy of the standings for the app to show if Basketball-Reference can't be reached."""
    try:
        standings = S.fetch_standings(season)
    except Exception as e:
        print(f"  standings not saved ({type(e).__name__})")
        return
    rows = [{"conference": conf, **team} for conf, teams in standings.items() for team in teams]
    if rows:
        write_lookup(S.STANDINGS_CSV, list(rows[0]), rows)


def url(video) -> str:
    return f"https://www.youtube.com/watch?v={video['id']}" if video else ""


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, default=S.current_season(date.today()))
    parser.add_argument("--full-scan", action="store_true",
                        help="list the whole channel with yt-dlp instead of the RSS feed")
    parser.add_argument("--transcripts-only", action="store_true",
                        help="only fetch missing transcripts (the Mac's daily job)")
    parser.add_argument("--skip-transcripts", action="store_true",
                        help="don't fetch captions for the word cloud this run")
    args = parser.parse_args()
    if args.transcripts_only:
        refresh_transcripts([v["id"] for v in S.load_videos(S.VIDEOS_CSV)])
        return

    games = S.fetch_schedule(args.season)
    print(f"Schedule: {len(games)} games in the {args.season - 1}-{str(args.season)[2:]} season.")
    venues, arenas = refresh_places(games, args.season)
    refresh_preseason(args.season)
    for g in games:
        g["location"] = S.describe_location(g, venues, arenas)
    videos = refresh_videos(args.full_scan)
    save_standings(args.season)
    rows = S.build_rows(games, S.load_preseason(), videos, args.season)
    with open(S.INTERVIEWS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows, key=lambda r: r["when"]):
            w.writerow({
                "kind": r["kind"], "date": r["date"],
                "tipoff_utc": r["tip"].astimezone(timezone.utc).isoformat() if r["tip"] else "",
                "game": (f"{'@' if r['away'] else 'vs'} {r['opp']}" if r["opp"] and r["date"]
                         else r["opp"]),
                "location": r["location"], "nba_cup": "yes" if r["nba_cup"] else "",
                "result": r["result"], "outcome": r["outcome"],
                "pregame_url": url(r["pre"]), "pregame_title": (r["pre"] or {}).get("title", ""),
                "postgame_url": url(r["post"]), "postgame_title": (r["post"] or {}).get("title", ""),
                "other_url": url(r["other"]), "other_title": (r["other"] or {}).get("title", ""),
            })
    for kind in ("Preseason", "Regular season"):
        played = [r for r in rows if r["kind"] == kind and r["result"]]
        print(f"{kind}: {sum(1 for r in rows if r['kind'] == kind)} games, {len(played)} played, "
              f"{sum(1 for r in played if r['pre'])} with a pre-game video, "
              f"{sum(1 for r in played if r['post'])} with a post-game video.")
    print(f"Interviews not tied to a game: {sum(1 for r in rows if r['kind'] == 'No game')}.")
    if not args.skip_transcripts:
        refresh_transcripts([v["id"] for v in videos])


if __name__ == "__main__":
    main()
