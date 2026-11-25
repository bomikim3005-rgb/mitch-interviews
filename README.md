# Mitch Johnson Press Conferences

A Streamlit app that lists San Antonio Spurs head coach Mitch Johnson's pre- and post-game press conferences from the [Spurs Press Conference YouTube channel](https://www.youtube.com/@SanAntonioSpursPressConference). It has one row per game: date, tip-off time (Central), opponent, location, and links to both videos.

## How it works

`update.py` builds `interviews.csv`:

1. **Schedule.** It reads the Spurs schedule from [Basketball-Reference](https://www.basketball-reference.com/teams/SAS/2027_games.html): date, tip-off time, home/away, opponent and NBA Cup games. The location is San Antonio for home games and the opponent's city for away games. Neutral-site games go in `location_overrides.csv`, e.g. the Oct 23 game in Austin.
2. **Videos.** It lists the channel's videos with [yt-dlp](https://github.com/yt-dlp/yt-dlp) (metadata only, nothing is downloaded) and keeps the ones whose title starts with "Mitch Johnson". Upload times are cached in `videos.csv`, so later runs only look up new videos.
3. **Matching.** Titles don't say "pregame" or "postgame", so videos are matched by upload time:
   - **Pre-game:** uploaded from 3 hours before to 1 hour after tip-off (in practice within about an hour of tip-off).
   - **Post-game:** uploaded from 1 to 16 hours after tip-off (usually 3.5–4.5 hours, sometimes the next morning).

Videos outside those windows, such as preseason, practice and community events, aren't listed.

## Run it

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -r requirements.txt -r requirements-pipeline.txt
python update.py          # refresh interviews.csv
streamlit run app.py
```

Run `python update.py` after each game, or daily, to pick up new videos.

## Limitations

- **Upload-time matching relies on the channel's routine.** A video uploaded unusually late, more than 16 hours after tip-off, won't be matched.
- **Schedule changes need a rerun.** The schedule comes from Basketball-Reference's page, so postponed games update when you run `update.py` again.
- **Some neutral-site games need a manual entry.** Basketball-Reference flags NBA Cup games, but not other neutral-site games, so add those to `location_overrides.csv`.
# mitch-interviews
