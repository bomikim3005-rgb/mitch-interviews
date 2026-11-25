"""Mitch Johnson press conferences - Streamlit app.

One row per Spurs game (preseason and regular season) with the result and links to
head coach Mitch Johnson's pre- and post-game press conferences on the
@SanAntonioSpursPressConference YouTube channel, plus his interviews that aren't tied
to a game, a won/lost/not-played chart and the conference standings.

Regular-season schedule, results and standings are read live from Basketball-Reference
(refreshed every 10 minutes). Videos (videos.csv), the preseason (preseason.csv) and
arenas (venues.csv, arenas.csv) come from update.py.

Run:
    pip install -r requirements.txt
    streamlit run app.py
"""
from datetime import date, datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st

import cloud
import spurs as S

REFRESH_SECONDS = 600
# Reference palette, dark-mode steps: its diverging pair (blue = won, red = lost; the
# same colors as the word cloud) and a neutral grey.
OUTCOME_COLORS = {"Won": "#3987e5", "Lost": "#e66767", "Not played": "#6b6a64"}
KINDS = ["Regular season", "Preseason", "No game"]

st.set_page_config(page_title="Mitch Johnson Press Conferences", layout="wide")
st.title("Mitch Johnson Press Conferences")
st.caption("San Antonio Spurs head coach Mitch Johnson's pre- and post-game press "
           "conferences, from the Spurs Press Conference YouTube channel. Times are Pacific.")

season = S.current_season(date.today())


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner="Loading the schedule and results...")
def live_schedule(season: int):
    return S.fetch_schedule(season)


@st.cache_data(ttl=REFRESH_SECONDS, show_spinner="Loading the standings...")
def live_standings(season: int):
    return S.fetch_standings(season)


def mtime(path) -> float:
    return path.stat().st_mtime if path.exists() else 0


@st.cache_data
def local_data(mtimes):
    # mtimes only make the cache reload when update.py rewrites these files.
    arenas = {}
    if S.ARENAS_CSV.exists():
        arenas = {r.team: r.arena for r in pd.read_csv(S.ARENAS_CSV, dtype=str).itertuples()
                  if r.season == str(season)}
    return (S.load_videos(S.VIDEOS_CSV), S.load_preseason(),
            S.load_lookup(S.VENUES_CSV, "box_score", "venue"), arenas)


def saved_regular_season() -> list:
    """Regular-season games as of the last update.py run (if Basketball-Reference is down)."""
    df = pd.read_csv(S.INTERVIEWS_CSV, dtype=str).fillna("")
    df = df[(df["kind"] == "Regular season") & (df["tipoff_utc"] != "")]
    return [{"kind": r.kind, "tip": datetime.fromisoformat(r.tipoff_utc), "date": r.date,
             "away": r.game.startswith("@"), "opp": r.game.split(" ", 1)[1],
             "location": r.location, "nba_cup": bool(r.nba_cup), "outcome": r.outcome,
             "result": r.result, "box_score": ""} for r in df.itertuples()]


videos, preseason, venues, arenas = local_data(tuple(mtime(p) for p in (
    S.VIDEOS_CSV, S.PRESEASON_CSV, S.VENUES_CSV, S.ARENAS_CSV)))
try:
    regular = live_schedule(season)
    for g in regular:
        g["location"] = S.describe_location(g, venues, arenas)
except Exception as e:  # network or page change: fall back to the last saved copy
    if not S.INTERVIEWS_CSV.exists():
        st.error(f"Couldn't load the schedule ({type(e).__name__}). Run `python update.py`.")
        st.stop()
    st.warning(f"Couldn't reach Basketball-Reference ({type(e).__name__}); showing "
               "results as of the last update.")
    regular = saved_regular_season()

df = pd.DataFrame(S.build_rows(regular, preseason, videos, season))
season_games = df[df["kind"] == "Regular season"]

# ---- won / lost / not played (regular season)
outcome = season_games["outcome"].map({"W": "Won", "L": "Lost"}).fillna("Not played")
counts = (outcome.value_counts().reindex(list(OUTCOME_COLORS), fill_value=0)
          .rename_axis("outcome").reset_index(name="games"))
won, lost = int(counts.loc[0, "games"]), int(counts.loc[1, "games"])
@st.cache_data
def word_cloud(key):
    # key: the post-game videos and outcomes, plus how many transcripts exist, so the
    # cloud is rebuilt only when one of those changes.
    return cloud.build(df.to_dict("records"))


left, right = st.columns([2, 3])
with left:
    st.subheader(f"Record: {won}–{lost}")
    base = alt.Chart(counts).encode(
        theta=alt.Theta("games:Q", stack=True),
        order=alt.Order("order:Q"),
        color=alt.Color("outcome:N", scale=alt.Scale(domain=list(OUTCOME_COLORS),
                                                     range=list(OUTCOME_COLORS.values())),
                        legend=alt.Legend(title=None, orient="bottom")),
        tooltip=[alt.Tooltip("outcome:N", title="Games"), alt.Tooltip("games:Q", title="Count")],
    ).transform_calculate(order=f"indexof({list(OUTCOME_COLORS)}, datum.outcome)")
    donut = base.mark_arc(innerRadius=60, outerRadius=110, stroke="#0e1117", strokeWidth=2)
    labels = base.mark_text(radius=135, fontSize=13).encode(
        text=alt.Text("label:N"), color=alt.value("#c3c2b7"),
    ).transform_calculate(label="datum.games > 0 ? datum.outcome + ' ' + datum.games : ''")
    st.altair_chart((donut + labels).properties(height=300), width="stretch")
    tbd = int((season_games["date"] == "").sum())
    pre = df[df["kind"] == "Preseason"]
    st.caption(
        f"{len(season_games)} regular-season games: {won + lost} played, "
        f"{len(season_games) - won - lost} to go"
        + (f" ({tbd} still to be scheduled after NBA Cup group play)" if tbd else "") + ". "
        f"Preseason: {(pre['outcome'] == 'W').sum()}–{(pre['outcome'] == 'L').sum()}. "
        f"Results update within {REFRESH_SECONDS // 60} minutes of Basketball-Reference "
        "posting them; videos update when update.py runs.")
with right:
    st.subheader("What he says after wins and losses")
    key = (tuple((r["post"]["id"], r["outcome"]) for r in df.to_dict("records")
                 if isinstance(r["post"], dict) and r["outcome"] in ("W", "L")),
           len(list(S.TRANSCRIPTS_DIR.glob("*.txt"))) if S.TRANSCRIPTS_DIR.exists() else 0)
    png, info = word_cloud(key)
    if png is None:
        st.info("The word cloud appears once update.py has fetched the post-game transcripts.")
    else:
        st.image(png, width="stretch")
        st.markdown(
            '<div style="display:flex;align-items:center;gap:0.5rem;font-size:0.8rem;opacity:0.85">'
            '<span>said more after wins</span>'
            '<span style="flex:1;height:8px;border-radius:4px;background:linear-gradient(90deg,'
            'rgb(57,135,229),rgb(165,164,158),rgb(230,103,103))"></span>'
            '<span>said more after losses</span></div>', unsafe_allow_html=True)

        def listing(ws):
            return ", ".join(f"**{w}** ({info['counts'][w][0]} W · {info['counts'][w][1]} L)" for w in ws)

        if info["loss_words"]:
            st.markdown("After losses: " + listing(info["loss_words"]))
        if info["win_words"]:
            st.markdown("After wins: " + listing(info["win_words"]))
        st.caption(f"From his post-game press conferences: {info['wins']} wins, {info['losses']} "
                   "losses (preseason included). Size is how often he said the word; color "
                   "compares how often per word he says it after wins vs losses. Transcripts "
                   "are YouTube's automatic captions, so a few words may be misheard.")

with st.sidebar:
    kinds = st.multiselect("Type", KINDS, default=KINDS)
    show = st.radio("Games", ["Played", "Upcoming", "All"])
    where = st.radio("Location", ["All", "Home", "Away"])
    opponent = st.text_input("Opponent contains", placeholder="e.g. Lakers")

# ---- games table
now = datetime.now(timezone.utc)
view = df[df["kind"].isin(kinds)]
played = view["result"].ne("") | ((view["kind"] == "No game") & (view["when"] <= now))
view = view[{"Played": played, "Upcoming": ~played, "All": played | ~played}[show]]
if where != "All":
    view = view[view["location"].str.endswith(f"({where.lower()})")]
if opponent.strip():
    view = view[view["opp"].str.contains(opponent.strip(), case=False, regex=False)]
view = view.sort_values("when", ascending=show == "Upcoming")

RESULT_RE = r"^([WL]) (\d+)–(\d+)(?: \((\d*OT)\))?$"
BLOWOUT = 20  # margin (points) at which the tint is at full strength


def parse_result(result: str):
    """'W 117–111 (OT)' -> ('W', 117, 111, 'OT'), or None if not played."""
    m = pd.Series([result]).str.extract(RESULT_RE).iloc[0]
    return None if pd.isna(m[0]) else (m[0], int(m[1]), int(m[2]), m[3] if isinstance(m[3], str) else "")


def result_label(result: str) -> str:
    """'W 123–107 · +16', with OT when it went to overtime."""
    p = parse_result(result)
    if not p:
        return ""
    outcome, us, them, ot = p
    margin = f"{us - them:+d}".replace("-", "−")  # a true minus sign for losses
    return f"{outcome} {us}–{them}{' ' + ot if ot else ''} · {margin}"


def result_style(result: str) -> str:
    """Blue tint for a win, red for a loss (the donut's colors); the bigger the
    margin, the stronger the tint. The text stays in the normal text color."""
    p = parse_result(result)
    if not p:
        return ""
    outcome, us, them, _ = p
    strength = min(abs(us - them), BLOWOUT) / BLOWOUT
    rgb = "57, 135, 229" if outcome == "W" else "230, 103, 103"
    return (f"background-color: rgba({rgb}, {0.12 + 0.48 * strength:.2f}); "
            f"font-weight: {600 if strength >= 0.5 else 400}")


def link(video):
    return f"https://www.youtube.com/watch?v={video['id']}" if isinstance(video, dict) else None


def topic(video):
    if not isinstance(video, dict):
        return ""
    return (video["title"].removeprefix("Mitch Johnson on ").removeprefix("Mitch Johnson On "))


def game_label(r) -> str:
    if r["kind"] == "No game":
        return "No game"
    if not r["date"]:
        return r["opp"]  # "Game 81: date TBD ..."
    return (f"{'@' if r['away'] else 'vs'} {r['opp']}" + ("  (NBA Cup)" if r["nba_cup"] else ""))


def date_label(r) -> str:
    return datetime.fromisoformat(r["date"]).strftime("%a, %b %-d, %Y") if r["date"] else "TBD"


def time_label(r) -> str:
    if r["kind"] == "No game":
        return r["when"].astimezone(S.PT).strftime("%-I:%M %p") + " (posted)"
    return r["tip"].astimezone(S.PT).strftime("%-I:%M %p") if r["tip"] is not None and not pd.isna(r["tip"]) else "—"


table = pd.DataFrame({
    "Date": [date_label(r) for _, r in view.iterrows()],
    "Tip-off (PT)": [time_label(r) for _, r in view.iterrows()],
    "Type": view["kind"],
    "Game": [game_label(r) for _, r in view.iterrows()],
    "Result": view["result"].map(result_label),
    "Pre-game": view["pre"].map(link),
    "Pre-game topic": view["pre"].map(topic),
    "Post-game": view["post"].map(link),
    "Post-game topic": view["post"].map(topic),
    "Interview (no game)": view["other"].map(link),
    "Interview topic": view["other"].map(topic),
    "Location": view["location"],
})
if not (view["kind"] == "No game").any():  # keep the table narrow when there are none
    table = table.drop(columns=["Interview (no game)", "Interview topic"])
raw_results = view["result"].tolist()
if view.empty:
    st.info("No games or interviews match these filters.")
st.dataframe(
    table.style.apply(lambda col: [result_style(r) for r in raw_results], subset=["Result"]),
    column_config={
        "Result": st.column_config.TextColumn(width=150),  # fits "W 117–111 OT · +6"
        "Pre-game": st.column_config.LinkColumn("Pre-game", display_text="▶ Watch"),
        "Post-game": st.column_config.LinkColumn("Post-game", display_text="▶ Watch"),
        "Interview (no game)": st.column_config.LinkColumn(display_text="▶ Watch"),
        "Pre-game topic": st.column_config.TextColumn(width="medium"),
        "Post-game topic": st.column_config.TextColumn(width="medium"),
        "Interview topic": st.column_config.TextColumn(width="medium"),
    },
    hide_index=True, width="stretch", height=min(600, 38 + 35 * max(len(table), 1)),
)
st.caption(f"Result: blue for a win, red for a loss. The deeper the shade, the bigger "
           f"the margin (full strength at {BLOWOUT}+ points). Videos are matched to games by "
           "upload time: pre-game from 3 hours before to 1 hour after tip-off, post-game "
           "from 1 to 16 hours after. Preseason tip-off times aren't published in the sources "
           "used, so preseason videos are matched by game day.")

# ---- standings
st.subheader("Standings")
try:
    standings = live_standings(season)
except Exception as e:  # fall back to the copy update.py saved
    standings = S.load_saved_standings()
    st.caption(f"Couldn't reach Basketball-Reference ({type(e).__name__}); showing the "
               "standings as of the last update." if any(standings.values())
               else f"Couldn't load the standings ({type(e).__name__}).")
if standings:
    cols = st.columns(2)
    for col, conf in zip(cols, ("West", "East")):
        with col:
            st.markdown(f"**{conf}ern Conference**")
            t = pd.DataFrame(standings[conf]).rename(columns={
                "seed": "#", "team": "Team", "wins": "W", "losses": "L", "pct": "W/L%",
                "gb": "GB", "ps_g": "PS/G", "pa_g": "PA/G"})
            st.dataframe(
                t.style.apply(lambda r: ["font-weight: bold; background-color: rgba(57, 135, 229, 0.18)"
                                         if r["Team"] == "San Antonio Spurs" else ""] * len(r),
                              axis=1),
                hide_index=True, width="stretch", height=38 + 35 * len(t),
            )
st.caption("Regular-season schedule, results and standings from Basketball-Reference; "
           "preseason from Wikipedia.")
