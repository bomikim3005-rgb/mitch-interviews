"""Mitch Johnson press conferences - Streamlit app.

One row per Spurs game with links to head coach Mitch Johnson's pre- and post-game
press conferences on the @SanAntonioSpursPressConference YouTube channel.
interviews.csv is built by update.py (see README.md).

Run:
    pip install -r requirements.txt
    streamlit run app.py
"""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

DATA = Path(__file__).parent / "interviews.csv"

st.set_page_config(page_title="Mitch Johnson Press Conferences", layout="wide")
st.title("Mitch Johnson Press Conferences")
st.caption("San Antonio Spurs head coach Mitch Johnson's pre- and post-game press "
           "conferences, from the Spurs Press Conference YouTube channel. Times are "
           "Central (San Antonio).")


@st.cache_data
def load(mtime):
    # mtime only makes the cache reload when interviews.csv changes.
    df = pd.read_csv(DATA, dtype=str).fillna("")
    df["tipoff"] = pd.to_datetime(df["tipoff_utc"], utc=True)
    return df


if not DATA.exists():
    st.info("No data yet. Run `python update.py` to build interviews.csv.")
    st.stop()

games = load(DATA.stat().st_mtime)
now = datetime.now(timezone.utc)
played = games[games["tipoff"] <= now]

with st.sidebar:
    show = st.radio("Games", ["Played", "Upcoming", "All"])
    where = st.radio("Location", ["All", "Home", "Away"])
    opponent = st.text_input("Opponent contains", placeholder="e.g. Lakers")

view = {"Played": played, "Upcoming": games[games["tipoff"] > now], "All": games}[show]
if where != "All":
    view = view[view["location"].str.contains(f"({where.lower()})", regex=False)]
if opponent.strip():
    view = view[view["game"].str.contains(opponent.strip(), case=False, regex=False)]
view = view.sort_values("tipoff", ascending=show == "Upcoming")

c1, c2, c3 = st.columns(3)
c1.metric("Games played", len(played))
c2.metric("Pre-game videos", int((played["pregame_url"] != "").sum()))
c3.metric("Post-game videos", int((played["postgame_url"] != "").sum()))

table = pd.DataFrame({
    "Date": pd.to_datetime(view["date"]).dt.strftime("%a, %b %-d, %Y"),
    "Tip-off (CT)": pd.to_datetime(view["tipoff_ct"], format="%H:%M").dt.strftime("%-I:%M %p"),
    "Game": view["game"] + view["nba_cup"].map(lambda c: "  (NBA Cup)" if c else ""),
    "Location": view["location"],
    "Pre-game": view["pregame_url"].replace("", None),
    "Pre-game topic": view["pregame_title"].str.replace(r"^Mitch Johnson [Oo]n ", "", regex=True),
    "Post-game": view["postgame_url"].replace("", None),
    "Post-game topic": view["postgame_title"].str.replace(r"^Mitch Johnson [Oo]n ", "", regex=True),
})
st.dataframe(
    table,
    column_config={
        "Pre-game": st.column_config.LinkColumn("Pre-game", display_text="▶ Watch"),
        "Post-game": st.column_config.LinkColumn("Post-game", display_text="▶ Watch"),
        "Pre-game topic": st.column_config.TextColumn(width="medium"),
        "Post-game topic": st.column_config.TextColumn(width="medium"),
    },
    hide_index=True, width="stretch", height=min(800, 38 + 35 * max(len(table), 1)),
)
st.caption("Videos are matched to games by upload time: pre-game from 3 hours before "
           "to 1 hour after tip-off, post-game from 1 to 16 hours after. Schedule from "
           "Basketball-Reference.")
