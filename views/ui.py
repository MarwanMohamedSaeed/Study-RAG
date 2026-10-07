"""Small UI helpers shared by several pages."""
import html
import re

import streamlit as st

from core import rag

_ANY_CITE = re.compile(r"\[[^\[\]\n]*?(?:p\.|slide |part )\d+\]")

# Colours come from the theme (.streamlit/config.toml); these rules only add layout and accents, using
# translucent teal/amber so they read well on both the light and the dark palette.
_CSS = """
<style>
@import url("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&display=swap");
[dir="rtl"], [dir="rtl"] * { font-family: "IBM Plex Sans Arabic", "Inter", sans-serif; }
[data-testid="stBottomBlockContainer"] { max-width: 1100px; }
.block-container { padding-top: 4.5rem; max-width: 1100px; }
/* Inter has no Arabic letters: list the Arabic font next, so Arabic anywhere (buttons, inputs) uses it */
.stApp, .stApp button, .stApp textarea, .stApp input { font-family: "Inter", "IBM Plex Sans Arabic", "Source Sans", sans-serif; }
.srag-hero { display: flex; gap: 1rem; align-items: center; margin: 0 0 1.4rem; }
.srag-hero-icon { flex: none; width: 3.1rem; height: 3.1rem; border-radius: 0.9rem; display: grid; place-items: center;
  font-size: 1.6rem; background: linear-gradient(135deg, rgba(20,184,166,.22), rgba(245,158,11,.18));
  border: 1px solid rgba(20,184,166,.28); }
.srag-hero-title, .srag-welcome h3 { font-family: "Plus Jakarta Sans", "IBM Plex Sans Arabic", sans-serif; }
.srag-hero-title { font-size: 1.85rem; font-weight: 800;
  line-height: 1.15; letter-spacing: -0.02em; margin: 0; }
.srag-hero-sub { margin: 0.25rem 0 0; opacity: .72; font-size: .98rem; }
.srag-tag { font-size: .8rem; opacity: .65; margin: -.2rem 0 .55rem; }
.srag-pills { display: flex; flex-wrap: wrap; gap: .4rem; margin-bottom: .6rem; }
.srag-pill { font-size: .75rem; padding: .2rem .6rem; border-radius: 999px; border: 1px solid rgba(127,127,127,.25);
  background: rgba(127,127,127,.08); white-space: nowrap; }
.srag-pill .dot { display: inline-block; width: .5rem; height: .5rem; border-radius: 50%; margin-right: .35rem; }
.srag-pill .ok { background: #22C55E; box-shadow: 0 0 0 3px rgba(34,197,94,.2); }
.srag-pill .bad { background: #EF4444; box-shadow: 0 0 0 3px rgba(239,68,68,.2); }
.srag-card { border: 1px solid rgba(20,184,166,.3); border-radius: .9rem; padding: .8rem .9rem;
  background: linear-gradient(135deg, rgba(20,184,166,.10), rgba(245,158,11,.07)); font-size: .86rem; margin-bottom: .8rem; }
.srag-card b { font-weight: 700; }
.srag-card a { color: #0F766E; font-weight: 600; text-decoration: none; }
@media (prefers-color-scheme: dark) { .srag-card a { color: #2DD4BF; } }
.srag-meter { height: .4rem; border-radius: 999px; background: rgba(127,127,127,.2); overflow: hidden; margin: .55rem 0 .35rem; }
.srag-meter > div { height: 100%; border-radius: 999px; background: linear-gradient(90deg, #14B8A6, #F59E0B); }
.srag-muted { opacity: .7; font-size: .8rem; }
.srag-label { font-size: .72rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; opacity: .6;
  margin: 1rem 0 .3rem; }
.srag-welcome { text-align: center; padding: 2.2rem 1rem 1rem; }
.srag-welcome-icon { font-size: 2.6rem; }
.srag-welcome h3 { margin: .4rem 0 .2rem; font-weight: 800; }
.srag-welcome p { opacity: .7; margin: 0 auto; max-width: 34rem; }
[data-testid="stChatMessage"] { border-radius: 1rem; }
[data-testid="stMetric"] { border: 1px solid rgba(127,127,127,.2); border-radius: .9rem; padding: .7rem .9rem;
  background: rgba(127,127,127,.05); }
</style>
"""


def inject_css() -> None:
    st.html(_CSS)


def page_header(icon: str, title: str, subtitle: str = "") -> None:
    """The title block at the top of every page: an icon tile, the title and a one-line description."""
    sub = f'<p class="srag-hero-sub">{html.escape(subtitle)}</p>' if subtitle else ""
    st.html(f'<div class="srag-hero"><div class="srag-hero-icon">{icon}</div>'
            f'<div><p class="srag-hero-title">{html.escape(title)}</p>{sub}</div></div>')


def sidebar_brand(llm_label: str, llm_ok: bool, search_mode: str) -> None:
    st.html('<div class="srag-tag">Chat with your lectures · quiz yourself</div>'
            f'<div class="srag-pills"><span class="srag-pill"><span class="dot {"ok" if llm_ok else "bad"}"></span>'
            f'{html.escape(llm_label)}</span><span class="srag-pill">🔎 {html.escape(search_mode)}</span></div>')


def label(text: str, target=st) -> None:
    """A small uppercase section label (lighter than a subheader)."""
    target.html(f'<div class="srag-label">{html.escape(text)}</div>')


def markdown(text: str, target=st):
    """Render Markdown; Arabic text reads right-to-left with citations kept left-to-right."""
    if rag.detect_language(text) == "ar":
        # keep "[file.pdf p.5]" as an isolated LTR run so bidi doesn't flip its brackets
        # full citations ([file.pdf p.3]) and the short ones used by study tools ([p.3], [slide 4])
        text = _ANY_CITE.sub(lambda m: f'<bdi dir="ltr">{m.group(0)}</bdi>', text)
        target.markdown(f'<div dir="rtl" style="text-align: right">\n\n{text}\n\n</div>', unsafe_allow_html=True)
    else:
        target.markdown(text)


def demo_banner() -> None:
    """The sidebar's demo notice. It lives in a placeholder so demo_guard() can refresh the count
    in the same run (the sidebar is drawn before the page spends anything)."""
    from core import config
    ss = st.session_state
    slot = ss.get("demo_slot")
    if slot is None:
        return
    left = max(0, config.DEMO_ACTIONS - ss.get("demo_used", 0))
    pct = 100 * left / max(config.DEMO_ACTIONS, 1)
    repo = (f' · <a href="{html.escape(config.REPO_URL)}" target="_blank">run it locally</a> for unlimited use'
            if config.REPO_URL else "")
    slot.html(f'<div class="srag-card"><b>✨ Live demo</b>: try the sample lectures or upload your own.'
              f'<div class="srag-meter"><div style="width:{pct:.0f}%"></div></div>'
              f'<span class="srag-muted"><b>{left}</b> of {config.DEMO_ACTIONS} AI actions left{repo}</span></div>')


def demo_guard(cost: int) -> None:
    """Demo mode: spend `cost` AI actions from this visitor's budget, or stop with a message.
    Outside demo mode this does nothing."""
    from core import config
    if not config.DEMO_MODE:
        return
    ss = st.session_state
    if ss.get("demo_used", 0) + cost > config.DEMO_ACTIONS:
        repo = f" You can [run StudyRAG locally]({config.REPO_URL}) for free, without limits." if config.REPO_URL else ""
        st.warning(f"This demo session has used its AI budget ({config.DEMO_ACTIONS} actions) so the shared free "
                   f"quota lasts for everyone.{repo}")
        st.stop()
    ss.demo_used = ss.get("demo_used", 0) + cost
    demo_banner()


def demo_cap(n: int) -> int:
    """Largest number of questions/cards allowed in one go (smaller in the demo)."""
    from core import config
    return min(n, config.DEMO_MAX_QUESTIONS) if config.DEMO_MODE else n


def progress_bar(text: str = "Starting…"):
    """A progress bar plus a callback with the (fraction, message) signature the core uses."""
    bar = st.progress(0.0, text=text)
    return bar, (lambda f, m: bar.progress(min(max(f, 0.0), 1.0), text=m))
