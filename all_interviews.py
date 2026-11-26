"""Refresh all_interviews.csv: every YouTube video of Mitch Johnson interviewed on his
own, from any channel (the Spurs' channels, local TV, national outlets, re-uploads).

- Found with YouTube searches (yt-dlp, metadata only) plus the press-conference
  channel's videos in videos.csv.
- Kept only if the title says it's Mitch Johnson speaking on his own (see is_solo):
  "Mitch Johnson on ...", "Coach Mitch Johnson speaks after ...", "FULL PRESS
  CONFERENCE: Spurs Coach Mitch Johnson ...". Group pressers ("Wembanyama & Mitch
  Johnson ...") and commentary about him ("Colin Cowherd BLAMES Mitch Johnson ...")
  are left out. Titles are the only signal, so a few will be misjudged.
- Each new video's page is read once for its upload time, length and channel, a
  couple of seconds apart; if YouTube refuses, the rest wait for the next run.

Runs from the Mac job (scripts/fetch_transcripts.sh): YouTube blocks searches from
GitHub's servers.

Run:
    python all_interviews.py
"""
import csv
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yt_dlp

os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")

import spurs as S

CSV = S.BASE / "all_interviews.csv"
FIELDS = ["id", "title", "channel", "uploaded", "seconds"]
QUERIES = [
    "Mitch Johnson Spurs", "Mitch Johnson interview", "Mitch Johnson postgame",
    "Mitch Johnson pregame", "Mitch Johnson press conference", "Coach Mitch Johnson",
    "Spurs coach Mitch Johnson",
]
RESULTS_PER_QUERY = 200
PAGE_PAUSE = 2  # seconds between video pages

# Before the name: an optional "Spurs head coach"-style label.
SOLO_RE = re.compile(
    r"^(?:(?:san antonio )?spurs(?:'s?)? )?(?:(?:head|assistant) )?(?:coach |hc )?"
    r"mitch johnson\b(?!\s*(?:,|&|\+|/|and\b))")
LEADING_DATE_RE = re.compile(r"^\d{1,2}[-./]\d{1,2}[-./]\d{2,4}\s+")
LEFT_OUT_RE = re.compile(r"introduc|march madness")   # intro presser with others; his playing days
SPURS_RE = re.compile(r"spurs|san antonio|wemb")


def is_solo(title: str) -> bool:
    """True if the title reads as Mitch Johnson speaking on his own."""
    t = title.strip().lower().replace("’", "'")
    if LEFT_OUT_RE.search(t):
        return False
    t = LEADING_DATE_RE.sub("", t)
    t = re.sub(r"^1-on-1 with ", "", t)
    head, colon, rest = t.partition(":")
    if colon and "mitch johnson" not in head:   # "FULL PRESS CONFERENCE: Coach Mitch ..."
        t = rest.strip()
    return bool(SOLO_RE.match(t))


def about_spurs(entry: dict) -> bool:
    text = " ".join(str(entry.get(k) or "") for k in ("title", "channel", "description"))
    return bool(SPURS_RE.search(text.lower()))


def search() -> dict:
    """{id: title} of solo-interview videos found by the searches."""
    found = {}
    with yt_dlp.YoutubeDL({"extract_flat": True, "quiet": True, "no_warnings": True}) as ydl:
        for q in QUERIES:
            info = ydl.extract_info(f"ytsearch{RESULTS_PER_QUERY}:{q}", download=False)
            for e in info.get("entries") or []:
                if e and e.get("id") and is_solo(e.get("title", "")) and about_spurs(e):
                    found[e["id"]] = e["title"]
    return found


def video_details(video_id: str) -> dict:
    """Upload time, length and channel from the video's page."""
    req = urllib.request.Request(f"https://www.youtube.com/watch?v={video_id}",
                                 headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US"})
    page = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")

    def first(pattern):
        m = re.search(pattern, page)
        return m.group(1) if m else ""
    uploaded = first(r'"uploadDate":"([^"]+)"') or first(r'itemprop="datePublished" content="([^"]+)"')
    if not uploaded:
        raise RuntimeError("no upload date on the page (YouTube may be refusing requests)")
    channel = first(r'"ownerChannelName":"((?:[^"\\]|\\.)*)"')
    return {"uploaded": uploaded, "seconds": first(r'"lengthSeconds":"(\d+)"'),
            "channel": channel.encode().decode("unicode_escape", "ignore") if "\\" in channel else channel}


def load() -> dict:
    if not CSV.exists():
        return {}
    with CSV.open(newline="") as f:
        return {r["id"]: r for r in csv.DictReader(f)}


def save(rows: dict) -> None:
    with CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(sorted(rows.values(), key=lambda r: r["uploaded"], reverse=True))


def main() -> None:
    rows = load()
    candidates = search()
    for v in S.load_videos(S.VIDEOS_CSV):   # the press-conference channel: all solo
        if is_solo(v["title"]):
            candidates.setdefault(v["id"], v["title"])
    new = [vid for vid in candidates if vid not in rows]
    print(f"All interviews: {len(candidates)} found, {len(new)} new.")
    added = 0
    for vid in new:
        try:
            details = video_details(vid)
        except (urllib.error.URLError, RuntimeError) as e:
            print(f"  Stopped at {vid} ({e}); the rest will be read next run.")
            break
        rows[vid] = {"id": vid, "title": candidates[vid], **details}
        added += 1
        if added % 25 == 0:
            save(rows)  # keep progress if the run is interrupted
        time.sleep(PAGE_PAUSE)
    save(rows)
    print(f"All interviews: {added} added, {len(rows)} in total.")


if __name__ == "__main__":
    main()
