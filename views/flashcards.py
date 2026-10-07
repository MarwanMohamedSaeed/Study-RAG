"""Flashcards with spaced repetition (SM-2): review due cards, add cards, browse and export to Anki."""
from datetime import date

import pandas as pd
import streamlit as st

from core import cards, srs, store
from core.export import anki_package, cards_csv
from core.ingest import fmt_ref
from core.llm import LLMError
from views.ui import demo_cap, demo_guard, markdown, progress_bar

ss = st.session_state
ss.setdefault("fc_revealed", False)
st.header("🃏 Flashcards")

scope = ss.selected_docs or None
due = store.list_cards(scope, due_only=True)
all_cards = store.list_cards(scope)
m1, m2, m3 = st.columns(3)
m1.metric("Due now", len(due))
m2.metric("Cards", len(all_cards))
m3.metric("Reviewed today", store.reviewed_today())

t_review, t_add, t_browse = st.tabs(["🔁 Review", "➕ Add cards", "📚 Browse & export"])

# ---------------------------------------------------------------- review
with t_review:
    if not all_cards:
        st.info("No cards yet. Add some in the **➕ Add cards** tab: from your glossary, from a lecture, "
                "or from the questions you got wrong.")
    elif not due:
        nxt = min(c["due"] for c in all_cards)
        st.success(f"🎉 All caught up! Next card is due on **{nxt}**.")
    else:
        card = due[0]
        state = srs.CardState(card["ease"], card["interval"], card["reps"], card["lapses"])
        src = f"{card['filename'] or ''} {fmt_ref(card['source_unit'] or 'page', card['source_page'])}" \
            if card["source_page"] else ""
        st.caption(f"Card {1} of {len(due)} due · {card['origin']} · {src}")
        with st.container(border=True):
            markdown(card["front"].replace("\n", "  \n"))
            if ss.fc_revealed:
                st.divider()
                markdown(card["back"].replace("\n", "  \n"))
        if not ss.fc_revealed:
            if st.button("Show answer", type="primary"):
                ss.fc_revealed = True
                st.rerun()
        else:
            labels = srs.preview(state)
            cols = st.columns(4)
            for col, (name, grade) in zip(cols, srs.BUTTONS.items()):
                if col.button(f"{name}  ·  {labels[name]}", use_container_width=True,
                              type="primary" if name == "Good" else "secondary"):
                    new, due_date = srs.review(state, grade)
                    store.update_card(card["id"], new.ease, new.interval, new.reps, new.lapses, due_date.isoformat())
                    ss.fc_revealed = False
                    st.rerun()
            st.caption("Again = forgot · Hard = remembered with effort · Good = remembered · Easy = instant. "
                       "The label is when you will see the card again.")

# ---------------------------------------------------------------- add cards
with t_add:
    if not ss.selected_docs:
        st.info("Select a document in the sidebar.")
    else:
        doc = st.selectbox("Document", ss.selected_docs, format_func=ss.doc_labels.get, key="fc_doc") \
            if len(ss.selected_docs) > 1 else ss.selected_docs[0]
        fname = ss.doc_names[doc]
        a1, a2, a3 = st.columns(3)
        with a1:
            st.markdown("**📖 From the glossary**  \n:gray[Term on the front, definitions in English and Arabic "
                        "on the back.]")
            if st.button("Add glossary cards", use_container_width=True):
                if not store.latest_note("glossary", doc):
                    demo_guard(4)   # the glossary has to be built first
                bar, cb = progress_bar("Reading the glossary…")
                try:
                    st.toast(f"{cards.from_glossary(doc, fname, progress=cb)} new cards from the glossary")
                except LLMError as e:
                    st.error(str(e))
                bar.empty()
        with a2:
            st.markdown("**✍️ From the lecture**  \n:gray[The LLM writes one-fact cards from across the lecture.]")
            n = st.slider("Cards", 5, max(5, demo_cap(40)), min(15, demo_cap(40)), step=5, key="fc_n",
                          label_visibility="collapsed")
            if st.button("Generate cards", use_container_width=True):
                demo_guard(-(-n // 6))
                bar, cb = progress_bar("Writing cards…")
                try:
                    st.toast(f"{cards.generate_cards(doc, fname, n, progress=cb)} new cards generated")
                except LLMError as e:
                    st.error(str(e))
                bar.empty()
        with a3:
            st.markdown("**❌ From my mistakes**  \n:gray[Every question you last answered wrong, with the right "
                        "answer on the back.]")
            if st.button("Add mistake cards", use_container_width=True):
                st.toast(f"{cards.from_mistakes(ss.selected_docs)} new cards from your mistakes")
        st.caption("Cards that already exist are not added twice. New cards are due today.")

# ---------------------------------------------------------------- browse & export
with t_browse:
    if not all_cards:
        st.caption("No cards yet.")
    else:
        df = pd.DataFrame([{"id": c["id"], "Front": c["front"], "Back": c["back"], "Origin": c["origin"],
                            "Source": f"{c['filename'] or ''} {fmt_ref(c['source_unit'] or 'page', c['source_page'])}"
                            if c["source_page"] else "",
                            "Due": c["due"], "Interval (days)": c["interval"], "Ease": c["ease"]} for c in all_cards])
        picked = st.dataframe(df.drop(columns="id"), hide_index=True, use_container_width=True,
                              on_select="rerun", selection_mode="multi-row", key="fc_table")
        rows = picked.selection.rows if picked else []
        b1, b2, b3, _ = st.columns([1, 1, 1, 2])
        if b1.button(f"Delete selected ({len(rows)})", disabled=not rows, use_container_width=True):
            store.delete_cards([int(df.iloc[r]["id"]) for r in rows])
            st.rerun()
        deck = "StudyRAG::" + (" + ".join(sorted({c["filename"] for c in all_cards if c["filename"]})) or "cards")
        b2.download_button("Anki deck (.apkg)", anki_package(all_cards, deck), f"studyrag-{date.today()}.apkg",
                           "application/octet-stream", use_container_width=True)
        b3.download_button("CSV", cards_csv(all_cards), f"studyrag-cards-{date.today()}.csv", "text/csv",
                           use_container_width=True)
        st.caption("Anki: File → Import → choose the .apkg file. The deck keeps its name, so re-importing updates it.")
