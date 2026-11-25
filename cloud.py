"""Word cloud of what Mitch Johnson says in post-game press conferences.

Size: how often he says the word after games. Color: whether he says it more after
wins (blue) or after losses (red), comparing rates so the larger number of wins
doesn't drown out the losses. Words he uses about equally either way are grey.

Built with the `wordcloud` library from the captions update.py saves in transcripts/.
"""
import io
import re
from collections import Counter

from wordcloud import STOPWORDS, WordCloud

import spurs as S

# Diverging pair from the reference palette (dark-mode steps): blue = wins, red = losses.
# The neutral is a light grey so it stays readable as text on the dark background.
WIN_RGB = (57, 135, 229)     # #3987e5
LOSS_RGB = (230, 103, 103)   # #e66767
NEUTRAL_RGB = (165, 164, 158)

# Words that say little on their own in spoken answers, on top of the usual stopwords.
SPOKEN_FILLER = {
    "um", "uh", "yeah", "like", "know", "think", "really", "just", "going", "go", "got",
    "get", "gotta", "thing", "things", "kind", "lot", "little", "obviously", "mean", "want",
    "say", "said", "way", "well", "something", "right", "able", "much", "even", "still",
    "um", "okay", "sure", "us", "guys", "guy", "one", "two", "come", "came", "make", "made",
    "look", "see", "try", "trying", "take", "put", "keep", "can", "will", "also", "every",
    "thats", "dont", "theyre", "weve", "youre", "theres", "lets", "didnt", "doesnt", "isnt",
    "wasnt", "cant", "ive", "im", "hes", "were", "its", "uh", "gonna", "wanna", "kinda",
    "maybe", "probably", "definitely", "certainly", "actually", "pretty", "whatever",
    "tonight", "game", "games", "music", "applause", "question", "coach", "mitch",
}
STOP = {w.replace("'", "") for w in STOPWORDS} | SPOKEN_FILLER
MAX_WORDS = 70
MIN_COUNT = 3


def words(text: str) -> list:
    tokens = re.findall(r"[a-z][a-z']+", text.lower())
    return [t.replace("'", "") for t in tokens
            if len(t) > 2 and t.replace("'", "") not in STOP]


def post_game_counts(rows: list) -> tuple:
    """(Counter after wins, Counter after losses, games won, games lost) from the
    post-game transcripts of every played game, preseason included."""
    win, loss = Counter(), Counter()
    n_win = n_loss = 0
    for r in rows:
        video, outcome = r.get("post"), r.get("outcome")
        if not video or outcome not in ("W", "L"):
            continue
        path = S.TRANSCRIPTS_DIR / f"{video['id']}.txt"
        if not path.exists():
            continue
        counts = Counter(words(path.read_text()))
        if outcome == "W":
            win.update(counts)
            n_win += 1
        else:
            loss.update(counts)
            n_loss += 1
    return win, loss, n_win, n_loss


def leans(win: Counter, loss: Counter) -> dict:
    """{word: lean}: -1 = only after wins, +1 = only after losses, 0 = even.

    Compares how often per word he says it after wins vs after losses, with add-one
    smoothing over the whole vocabulary so rarely used words stay near neutral. If
    there's nothing on one side (no win or no loss transcripts yet), there's nothing
    to compare, so every word is neutral."""
    vocab = set(win) | set(loss)
    total_w, total_l = sum(win.values()), sum(loss.values())
    if not total_w or not total_l:
        return {w: 0.0 for w in vocab}
    out = {}
    for w in vocab:
        rate_w = (win[w] + 1) / (total_w + len(vocab))
        rate_l = (loss[w] + 1) / (total_l + len(vocab))
        out[w] = (rate_l - rate_w) / (rate_l + rate_w)
    return out


def mix(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def lean_color(lean: float) -> str:
    rgb = mix(NEUTRAL_RGB, LOSS_RGB, lean) if lean >= 0 else mix(NEUTRAL_RGB, WIN_RGB, -lean)
    return "rgb({}, {}, {})".format(*rgb)


def build(rows: list):
    """Returns (PNG bytes, details) or (None, details) if there's too little text.
    details: games counted and the words that lean most toward wins and losses."""
    win, loss, n_win, n_loss = post_game_counts(rows)
    total = win + loss
    freqs = {w: c for w, c in total.most_common(MAX_WORDS) if c >= MIN_COUNT}
    lean = leans(win, loss)
    details = {
        "wins": n_win, "losses": n_loss,
        "loss_words": sorted((w for w in freqs if lean[w] > 0.25),
                             key=lambda w: (-lean[w] * freqs[w]))[:6],
        "win_words": sorted((w for w in freqs if lean[w] < -0.25),
                            key=lambda w: (lean[w] * freqs[w]))[:6],
        "counts": {w: (win[w], loss[w]) for w in freqs},
    }
    if len(freqs) < 5 or not n_win or not n_loss:  # needs both a win and a loss to compare
        return None, details
    cloud = WordCloud(
        width=900, height=520, mode="RGBA", background_color=None, prefer_horizontal=0.85,
        max_words=MAX_WORDS, relative_scaling=0.55, min_font_size=11, margin=3, random_state=7,
        color_func=lambda word, **_: lean_color(lean.get(word, 0.0)),
    ).generate_from_frequencies(freqs)
    buf = io.BytesIO()
    cloud.to_image().save(buf, format="PNG")
    return buf.getvalue(), details
