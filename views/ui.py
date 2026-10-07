"""Small UI helpers shared by several pages."""
import re

import streamlit as st

from core import rag

_ANY_CITE = re.compile(r"\[[^\[\]\n]*?(?:p\.|slide |part )\d+\]")


def markdown(text: str, target=st):
    """Render Markdown; Arabic text reads right-to-left with citations kept left-to-right."""
    if rag.detect_language(text) == "ar":
        # keep "[file.pdf p.5]" as an isolated LTR run so bidi doesn't flip its brackets
        # full citations ([file.pdf p.3]) and the short ones used by study tools ([p.3], [slide 4])
        text = _ANY_CITE.sub(lambda m: f'<bdi dir="ltr">{m.group(0)}</bdi>', text)
        target.markdown(f'<div dir="rtl" style="text-align: right">\n\n{text}\n\n</div>', unsafe_allow_html=True)
    else:
        target.markdown(text)


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


def demo_cap(n: int) -> int:
    """Largest number of questions/cards allowed in one go (smaller in the demo)."""
    from core import config
    return min(n, config.DEMO_MAX_QUESTIONS) if config.DEMO_MODE else n


def progress_bar(text: str = "Starting…"):
    """A progress bar plus a callback with the (fraction, message) signature the core uses."""
    bar = st.progress(0.0, text=text)
    return bar, (lambda f, m: bar.progress(min(max(f, 0.0), 1.0), text=m))
