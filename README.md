# Mitch Johnson Press Conferences

A Streamlit app that lists San Antonio Spurs head coach Mitch Johnson's pre- and post-game press conferences from the [Spurs Press Conference YouTube channel](https://www.youtube.com/@SanAntonioSpursPressConference). It has one row per game: date, tip-off time (Pacific), opponent, location, result, and links to both videos. A chart up top counts games won, lost and not yet played, and the West and East standings sit at the bottom.

## How it works

**Results and standings are read live.** The app reads the Spurs schedule and results from Basketball-Reference when it loads, refreshing every 10 minutes. Basketball-Reference can take hours to post a result, so for a game that has tipped off without one, the app takes the final score from ESPN's public scoreboard instead. It shows up within about 10 minutes of the final buzzer. The conference standings come from ESPN for the same reason, with Basketball-Reference as a fallback. If the site can't be reached, the app falls back to `interviews.csv` from the last `update.py` run.

**Videos come from `update.py`,** which refreshes `videos.csv` and writes `interviews.csv`. The shared logic is in `spurs.py`:

1. **Schedule.** It reads the Spurs schedule from [Basketball-Reference](https://www.basketball-reference.com/teams/SAS/2027_games.html): date, tip-off time, home/away, opponent and NBA Cup games. **Location** names the arena. For a played game it comes from the game's box score, which records where it was actually held, so neutral sites like the Oct 23 game at Moody Center in Austin are handled. For an upcoming game it's the host team's home arena, from its team page. Both are cached, in `venues.csv` and `arenas.csv`, so each is fetched only once.
2. **Videos.** It lists the channel's videos with [yt-dlp](https://github.com/yt-dlp/yt-dlp) (metadata only, nothing is downloaded) and keeps the ones whose title starts with "Mitch Johnson". Upload times are cached in `videos.csv`, so later runs only look up new videos.
3. **Matching.** Titles don't say "pregame" or "postgame", so videos are matched by upload time:
   - **Pre-game:** uploaded from 3 hours before to 1 hour after tip-off (in practice within about an hour of tip-off).
   - **Post-game:** uploaded from 1 to 16 hours after tip-off (usually 3.5–4.5 hours, sometimes the next morning).

**Preseason.** Basketball-Reference doesn't cover preseason, so `update.py` saves the preseason game log (date, opponent, result, arena) from the season's Wikipedia page to `preseason.csv`. Neither source publishes preseason tip-off times, so preseason videos are matched by game day: the first upload that day is pre-game, and the next one at least two hours later is post-game. You can type a `tipoff_pt` (HH:MM) into `preseason.csv` to show the time and switch that game to tip-off matching.

**82 games.** Basketball-Reference lists 80 games until the NBA schedules each team's last two after NBA Cup group play, so the table adds "date TBD" rows to reach 82.

**Interviews not tied to a game.** Any Mitch Johnson video that isn't matched to a game, including joint ones like "Dylan Harper and Mitch Johnson …", shows as a "No game" row with its posting time. The channel's videos start on Oct 6, 2026, so there's nothing from September.

## Word cloud

Next to the record chart, a word cloud shows what Mitch Johnson says in his **post-game** press conferences:

- **Size** is how often he says a word.
- **Color** shows whether he says it more after **wins (blue)** or **losses (red)**. It compares per-word rates, so the larger number of wins doesn't drown out the losses, and words used about equally either way are grey.
- **Below the cloud,** the words that lean most each way are listed with their counts.

Text comes from YouTube's automatic English captions. `update.py` saves them to `transcripts/` once per video. YouTube rejects caption requests that come too fast, so it fetches one about every 20 seconds and stops for that run at the first refusal. Run it again later and it picks up where it left off. `python update.py --skip-transcripts` skips this step. The cloud is drawn with the [`wordcloud`](https://github.com/amueller/word_cloud) library, after removing common words and spoken filler ("um", "you know", …).

## All interviews

The "All interviews" table at the bottom lists every YouTube video of Mitch Johnson interviewed on his own, from any account: the Spurs' channels, local TV (KSAT 12, WOAI, KABB), national outlets and re-upload channels. [`all_interviews.py`](all_interviews.py) finds them with YouTube searches plus the press-conference channel's videos, keeps those whose title reads as him speaking alone ("Mitch Johnson on …", "Coach Mitch Johnson speaks after …"), and drops group pressers ("Wembanyama & Mitch Johnson …") and commentary about him. It reads each new video's page once for its upload time, length and channel, and saves everything to `all_interviews.csv`. Titles are the only signal, so a few videos may be missed or misjudged.

## Run it

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -r requirements.txt -r requirements-pipeline.txt
python update.py          # refresh interviews.csv
streamlit run app.py
```

Run `python update.py` after each game, or daily, to pick up new videos.

## Publish and automate

- **App:** deploy on Streamlit Community Cloud from this repository, with main file `app.py` and Python 3.12. It needs no secrets.
- **Data:** [`.github/workflows/update.yml`](.github/workflows/update.yml) runs `update.py` twice a day (11:17 pm and 6:17 am Pacific) and commits any changed data files. Streamlit Cloud then redeploys with them. Results and standings don't depend on it, because the app reads them live. On game days it also runs every 30 minutes from 1 hour before tip-off to 5 hours after, so the pre- and post-game videos appear within about half an hour of being posted. Outside that window those extra runs stop after a few seconds.
- **New videos:** the scheduled run finds them through the channel's RSS feed (the latest 15 videos), which works from GitHub's servers. `python update.py --full-scan` lists the whole channel with yt-dlp instead.
- **Transcripts on a Mac:** [`scripts/fetch_transcripts.sh`](scripts/fetch_transcripts.sh) runs four times a day through launchd (`~/Library/LaunchAgents/com.mitch-interviews.transcripts.plist`: 2:30, 8:30, 14:30 and 20:30, or on wake if the Mac was asleep). It pulls, refreshes `all_interviews.csv` with `all_interviews.py`, fetches missing transcripts with `update.py --transcripts-only`, and commits and pushes only `transcripts/` and `all_interviews.csv`. If an earlier push failed (for example overnight, while the Mac is locked), the next run pushes it. Its log is at `~/Library/Logs/mitch-interviews-transcripts.log`.
- **Transcripts aren't fetched by the scheduled run.** YouTube's bot check blocks caption downloads from GitHub's servers. Run `python update.py` on your Mac from time to time, then commit and push `transcripts/`. It stops at the first refusal and picks up where it left off next time.

## Limitations

- **Upload-time matching relies on the channel's routine.** A video uploaded unusually late, more than 16 hours after tip-off, won't be matched.
- **Schedule changes need a rerun.** The schedule comes from Basketball-Reference's page, so postponed games update when you run `update.py` again.
- **Upcoming neutral-site games show the home team's arena** until they're played. After that, the box score corrects them.
# mitch-interviews
