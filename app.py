# app.py
import streamlit as st
import sys
import time
import datetime as dt
import random
import requests

from pdf_utils import extract_pdf_text
from llm import summarize_text
from auth_rest import (
    sign_in, sign_up, sign_out,
    save_item, list_items, get_item, move_item, delete_item,
    create_folder, list_folders, delete_folder
)

# -------------------- Page config (must be FIRST Streamlit call) --------------------
st.set_page_config(page_title="StudyBloom", page_icon="📚")
st.caption(f"Python: {sys.version.split()[0]} • Build: 2025-11-10-router")

# ============================ URL helpers (router) ============================
def _get_params() -> dict:
    """Read query params. Works on Streamlit >=1.28 via st.query_params; falls back otherwise."""
    try:
        return dict(st.query_params)
    except Exception:
        return st.experimental_get_query_params()

def _set_params(**kwargs):
    """Set query params."""
    try:
        st.query_params.clear()
        st.query_params.update(kwargs)
    except Exception:
        st.experimental_set_query_params(**kwargs)

def _clear_params():
    _set_params()

# ============================ Viewer helpers ============================
def render_summary(data: dict):
    st.subheader("📝 Notes")
    st.markdown(f"**TL;DR**: {data.get('tl_dr', '')}")

    for sec in (data.get("sections") or []):
        st.markdown(f"### {sec.get('heading', 'Section')}")
        for b in sec.get("bullets", []) or []:
            st.markdown(f"- {b}")

    kts = data.get("key_terms") or []
    if kts:
        st.markdown("## Key Terms")
        for kt in kts:
            st.markdown(f"- **{kt.get('term','')}** — {kt.get('definition','')}")

    forms = data.get("formulas") or []
    if forms:
        st.markdown("## Formulas")
        for f in forms:
            name = f.get("name","")
            expr = (f.get("latex") or f.get("expression") or "").strip()
            meaning = f.get("meaning","")
            if expr:
                # Render LaTeX when expression appears TeX-ish
                if any(s in expr for s in ["\\frac", "\\sqrt", "^", "_", "\\times", "\\cdot", "\\sum", "\\int", "\\left", "\\right"]):
                    if name or meaning:
                        st.markdown(f"**{name}** — {meaning}")
                    try:
                        st.latex(expr)
                    except Exception:
                        st.code(expr)
                else:
                    st.markdown(f"- **{name}**: `{expr}` — {meaning}")
            else:
                st.markdown(f"- **{name}** — {meaning}")

    exs = data.get("examples") or []
    if exs:
        st.markdown("## Worked Examples")
        for e in exs:
            st.markdown(f"- {e}")

    pits = data.get("common_pitfalls") or []
    if pits:
        st.markdown("## Common Pitfalls")
        for p in pits:
            st.markdown(f"- {p}")

def interactive_flashcards(flashcards, key_prefix="fc"):
    st.subheader("🧠 Flashcards (click Flip)")
    if not flashcards:
        st.caption("No flashcards found.")
        return

    st.session_state.setdefault(f"{key_prefix}_idx", 0)
    st.session_state.setdefault(f"{key_prefix}_revealed", False)
    st.session_state.setdefault(f"{key_prefix}_order", list(range(len(flashcards))))

    idx = st.session_state[f"{key_prefix}_idx"]
    revealed = st.session_state[f"{key_prefix}_revealed"]
    order = st.session_state[f"{key_prefix}_order"]

    colL, colR = st.columns([2, 1])
    with colL:
        st.progress((idx + 1) / len(order), text=f"Card {idx + 1} / {len(order)}")
    with colR:
        if st.button("🔀 Shuffle", key=f"{key_prefix}_shuffle"):
            new_order = list(range(len(flashcards)))
            random.shuffle(new_order)
            st.session_state[f"{key_prefix}_order"] = new_order
            st.session_state[f"{key_prefix}_idx"] = 0
            st.session_state[f"{key_prefix}_revealed"] = False
            st.rerun()

    card = flashcards[order[idx]]
    st.markdown("#### Front")
    st.info(card.get("front", ""))
    if revealed:
        st.markdown("#### Back")
        st.success(card.get("back", ""))

    c1, c2, c3 = st.columns(3)
    if c1.button("◀️ Prev", disabled=(idx == 0), key=f"{key_prefix}_prev"):
        st.session_state[f"{key_prefix}_idx"] = max(0, idx - 1)
        st.session_state[f"{key_prefix}_revealed"] = False
        st.rerun()

    if c2.button("🔁 Flip", key=f"{key_prefix}_flip"):
        st.session_state[f"{key_prefix}_revealed"] = not revealed
        st.rerun()

    if c3.button("Next ▶️", disabled=(idx == len(order) - 1), key=f"{key_prefix}_next"):
        st.session_state[f"{key_prefix}_idx"] = min(len(order) - 1, idx + 1)
        st.session_state[f"{key_prefix}_revealed"] = False
        st.rerun()

def interactive_quiz(questions, key_prefix="quiz"):
    st.subheader("🧪 Quiz (self-marked)")
    if not questions:
        st.caption("No questions found.")
        return

    st.session_state.setdefault(f"{key_prefix}_i", 0)
    st.session_state.setdefault(f"{key_prefix}_reveal", False)
    st.session_state.setdefault(f"{key_prefix}_correct", 0)
    st.session_state.setdefault(f"{key_prefix}_history", [])

    i = st.session_state[f"{key_prefix}_i"]
    reveal = st.session_state[f"{key_prefix}_reveal"]
    correct = st.session_state[f"{key_prefix}_correct"]
    hist = st.session_state[f"{key_prefix}_history"]

    st.progress((i + 1) / len(questions), text=f"Question {i + 1} / {len(questions)}")
    q = questions[i]
    st.markdown(f"### {q.get('question','')}")

    if not reveal:
        if st.button("👀 Show answer", key=f"{key_prefix}_show"):
            st.session_state[f"{key_prefix}_reveal"] = True
            st.rerun()
    else:
        with st.expander("Model answer", expanded=True):
            st.markdown(q.get("model_answer", ""))
            for pt in q.get("markscheme_points", []) or []:
                st.markdown(f"- {pt}")

        cc1, cc2 = st.columns(2)
        if cc1.button("✅ I got it", key=f"{key_prefix}_gotit"):
            if len(hist) <= i:
                hist.append({"correct": True, "qid": i})
            else:
                hist[i] = {"correct": True, "qid": i}
            st.session_state[f"{key_prefix}_correct"] = correct + 1
            st.session_state[f"{key_prefix}_reveal"] = False
            if i < len(questions) - 1:
                st.session_state[f"{key_prefix}_i"] = i + 1
            st.rerun()

        if cc2.button("❌ I need practice", key=f"{key_prefix}_wrong"):
            if len(hist) <= i:
                hist.append({"correct": False, "qid": i})
            else:
                hist[i] = {"correct": False, "qid": i}
            st.session_state[f"{key_prefix}_reveal"] = False
            if i < len(questions) - 1:
                st.session_state[f"{key_prefix}_i"] = i + 1
            st.rerun()

    c3, c4, c5 = st.columns(3)
    c3.metric("Score", f"{correct} / {len(questions)}")
    if c4.button("⏭️ Skip", key=f"{key_prefix}_skip", disabled=(i == len(questions) - 1)):
        st.session_state[f"{key_prefix}_reveal"] = False
        st.session_state[f"{key_prefix}_i"] = min(len(questions) - 1, i + 1)
        st.rerun()
    if c5.button("🔁 Restart", key=f"{key_prefix}_restart"):
        st.session_state[f"{key_prefix}_i"] = 0
        st.session_state[f"{key_prefix}_reveal"] = False
        st.session_state[f"{key_prefix}_correct"] = 0
        st.session_state[f"{key_prefix}_history"] = []
        st.rerun()

    if i == len(questions) - 1 and not reveal:
        st.markdown("---")
        st.markdown("### Review")
        if hist:
            for j, rec in enumerate(hist, 1):
                emoji = "✅" if rec.get("correct") else "❌"
                oq = questions[rec["qid"]]
                st.markdown(f"{emoji} **Q{j}:** {oq.get('question','')}")
        else:
            st.caption("Answer questions to see review here.")

# ============================ Sidebar: Auth & Folders ============================
st.sidebar.title("StudyBloom")
st.sidebar.caption("Log in to save, organize, and move your study materials.")

if "sb_user" not in st.session_state:
    st.sidebar.subheader("Sign in")
    login_email = st.sidebar.text_input("Email", key="login_email")
    login_pwd = st.sidebar.text_input("Password", type="password", key="login_pwd")
    if st.sidebar.button("Sign in", use_container_width=True):
        try:
            sign_in(login_email, login_pwd)
            st.rerun()
        except requests.HTTPError as e:
            st.sidebar.error(getattr(e.response, "text", str(e)))
        except Exception as e:
            st.sidebar.error(str(e))

    st.sidebar.subheader("Create account")
    reg_email = st.sidebar.text_input("New email", key="reg_email")
    reg_pwd = st.sidebar.text_input("New password", type="password", key="reg_pwd")
    if st.sidebar.button("Sign up", use_container_width=True):
        try:
            sign_up(reg_email, reg_pwd)
            st.sidebar.success("Account created. Check your email to confirm, then sign in above.")
        except requests.HTTPError as e:
            st.sidebar.error(getattr(e.response, "text", str(e)))
        except Exception as e:
            st.sidebar.error(str(e))
else:
    st.sidebar.success(f"Signed in as {st.session_state['sb_user']['email']}")
    if st.sidebar.button("Sign out", use_container_width=True):
        sign_out()
        st.rerun()

# Folder tree utilities
def build_tree(rows):
    nodes = {r["id"]: {**r, "children": []} for r in rows}
    roots = []
    for r in rows:
        pid = r.get("parent_id")
        if pid and pid in nodes:
            nodes[pid]["children"].append(nodes[r["id"]])
        else:
            roots.append(nodes[r["id"]])
    return roots, nodes

def id_to_name(fid, folders):
    if not fid:
        return None
    for f in folders:
        if f["id"] == fid:
            return f["name"]
    return None

# ============================ ROUTE: Item full-page view ============================
params = _get_params()
if "item" in params:
    # Dedicated item page (no tabs/expanders)
    item_id = params.get("item")
    if isinstance(item_id, list):
        item_id = item_id[0]
    try:
        full = get_item(item_id)
        kind = full.get("kind")
        title = full.get("title") or kind.title()
        st.title(title)

        if st.button("← Back", key="back_btn"):
            _clear_params()
            st.rerun()

        data = full.get("data") or {}
        if kind == "summary":
            render_summary(data or full)
        elif kind == "flashcards":
            interactive_flashcards((data.get("flashcards") or []), key_prefix=f"fc_{item_id}")
        elif kind == "quiz":
            interactive_quiz((data.get("questions") or []), key_prefix=f"quiz_{item_id}")
        else:
            st.write(data or full)
    except Exception as e:
        st.error(f"Could not load item: {e}")
        if st.button("← Back", key="back_btn_err"):
            _clear_params()
            st.rerun()
    st.stop()

# ============================ Normal app (no item param) ============================
selected_folder = None
all_folders = []
if "sb_user" in st.session_state:
    st.sidebar.markdown("---")
    st.sidebar.subheader("📂 Folders")

    try:
        all_folders = list_folders()
        tree, node_map = build_tree(all_folders)

        def render_tree(nodes, level=0):
            for n in nodes:
                label = (" " * level) + f"• {n['name']}"
            # simple click to focus folder
                if st.sidebar.button(label, key=f"folderbtn_{n['id']}"):
                    st.session_state["active_folder_id"] = n["id"]
                if n["children"]:
                    render_tree(n["children"], level + 1)

        render_tree(tree)
        selected_folder = st.session_state.get("active_folder_id")
        if selected_folder:
            cur = next((f for f in all_folders if f["id"] == selected_folder), None)
            if cur:
                st.sidebar.caption(f"Selected: **{cur['name']}**")

        with st.sidebar.expander("New Folder"):
            new_name = st.text_input("Folder name", key="new_folder_name")
            choices = {"(no parent)": None}
            for f in all_folders:
                choices[f"{f['name']}"] = f["id"]
            parent_name = st.selectbox("Parent", list(choices.keys()))
            parent = choices[parent_name]
            if st.button("Create folder", use_container_width=True):
                if not (new_name or "").strip():
                    st.warning("Enter a folder name.")
                else:
                    try:
                        created = create_folder(new_name.strip(), parent)
                        st.session_state["active_folder_id"] = created["id"]
                        st.success("Folder created.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Create failed: {e}")

        if selected_folder and st.sidebar.button("🗑️ Delete selected folder"):
            try:
                delete_folder(selected_folder)
                st.session_state.pop("active_folder_id", None)
                st.success("Folder deleted.")
                st.rerun()
            except Exception as e:
                st.sidebar.error(f"Delete failed: {e}")

    except Exception:
        st.sidebar.info("Create your first folder to organize notes.")

# ============================ Main Tabs ============================
tabs = st.tabs(["Exam Planner", "Quick Study", "Manage Items"])

# ---------- Tab 1: Exam Planner ----------
with tabs[0]:
    st.title("🗂️ Exam Planner")
    st.write(
        "Plan by **Subject → Exam → Topic**. Upload each topic; we'll summarize, "
        "build **flashcards**, generate a **quiz**, and file them neatly by topic."
    )

    if "sb_user" not in st.session_state:
        st.info("Log in (left) to use Exam Planner.")
    else:
        st.session_state.setdefault("ep_subject_id", None)
        st.session_state.setdefault("ep_exam_id", None)
        st.session_state.setdefault("ep_topic_id", None)

        # Subject
        subjects = [f for f in all_folders if not f.get("parent_id")]
        subj_names = [s["name"] for s in subjects]
        subj_index = 0
        if st.session_state["ep_subject_id"]:
            sel = id_to_name(st.session_state["ep_subject_id"], all_folders)
            if sel in subj_names:
                subj_index = subj_names.index(sel)

        c1, c2 = st.columns([3, 1])
        subject_choice = c1.selectbox("Subject folder", ["(create new)"] + subj_names,
                                      index=subj_index + 1 if subj_names else 0, key="ep_subj_choice")
        new_subject_name = c2.text_input("New", key="ep_new_subject_name", placeholder="e.g., A-Level Mathematics")
        if c2.button("Create Subject"):
            n = (new_subject_name or "").strip()
            if not n:
                st.warning("Enter a subject name.")
            else:
                try:
                    subj = create_folder(n, None)
                    st.session_state["ep_subject_id"] = subj["id"]
                    st.session_state["ep_exam_id"] = None
                    st.session_state["ep_topic_id"] = None
                    st.session_state["active_folder_id"] = subj["id"]
                    st.rerun()
                except Exception as e:
                    st.error(f"Create failed: {e}")

        if subject_choice != "(create new)" and subj_names:
            st.session_state["ep_subject_id"] = next(s["id"] for s in subjects if s["name"] == subject_choice)

        subject_id = st.session_state["ep_subject_id"]
        if not subject_id:
            st.stop()

        # Exam
        exams = [f for f in all_folders if f.get("parent_id") == subject_id]
        exam_names = [e["name"] for e in exams]
        exam_index = 0
        if st.session_state["ep_exam_id"]:
            sel = id_to_name(st.session_state["ep_exam_id"], all_folders)
            if sel in exam_names:
                exam_index = exam_names.index(sel)

        c1, c2 = st.columns([3, 1])
        exam_choice = c1.selectbox("Exam folder", ["(create new)"] + exam_names,
                                   index=exam_index + 1 if exam_names else 0, key="ep_exam_choice")
        new_exam_name = c2.text_input("New", key="ep_new_exam_name", placeholder="e.g., May 2026 Session")
        if c2.button("Create Exam"):
            n = (new_exam_name or "").strip()
            if not n:
                st.warning("Enter an exam name.")
            else:
                try:
                    ex = create_folder(n, subject_id)
                    st.session_state["ep_exam_id"] = ex["id"]
                    st.session_state["ep_topic_id"] = None
                    st.session_state["active_folder_id"] = ex["id"]
                    st.rerun()
                except Exception as e:
                    st.error(f"Create failed: {e}")

        if exam_choice != "(create new)" and exam_names:
            st.session_state["ep_exam_id"] = next(e["id"] for e in exams if e["name"] == exam_choice)

        exam_id = st.session_state["ep_exam_id"]
        if not exam_id:
            st.stop()

        # Topic
        topics = [f for f in all_folders if f.get("parent_id") == exam_id]
        topic_names = [t["name"] for t in topics]
        topic_index = 0
        if st.session_state["ep_topic_id"]:
            sel = id_to_name(st.session_state["ep_topic_id"], all_folders)
            if sel in topic_names:
                topic_index = topic_names.index(sel)

        c1, c2 = st.columns([3, 1])
        topic_choice = c1.selectbox("Topic folder", ["(create new)"] + topic_names,
                                    index=topic_index + 1 if topic_names else 0, key="ep_topic_choice")
        new_topic_name = c2.text_input("New", key="ep_new_topic_name", placeholder="e.g., Differentiation")
        if c2.button("Create Topic"):
            n = (new_topic_name or "").strip()
            if not n:
                st.warning("Enter a topic name.")
            else:
                try:
                    tp = create_folder(n, exam_id)
                    st.session_state["ep_topic_id"] = tp["id"]
                    st.session_state["active_folder_id"] = tp["id"]
                    st.rerun()
                except Exception as e:
                    st.error(f"Create failed: {e}")

        if topic_choice != "(create new)" and topic_names:
            st.session_state["ep_topic_id"] = next(t["id"] for t in topics if t["name"] == topic_choice)

        topic_id = st.session_state["ep_topic_id"]
        if not topic_id:
            st.stop()

        subject_name = id_to_name(st.session_state["ep_subject_id"], all_folders) or "General"

        st.markdown("---")
        st.subheader("Upload a PDF for this topic")
        audience_label = st.selectbox("Audience style", ["University", "A-Level / IB", "GCSE", "HKDSE"], index=0, key="aud_ep")
        audience = "university" if audience_label == "University" else "high school"
        detail = st.slider("Detail level", 1, 5, 3, key="detail_ep")
        uploaded = st.file_uploader("Upload PDF", type=["pdf"], key="u_ep")

        if uploaded and st.button("Generate Notes + Flashcards + Quiz", type="primary"):
            with st.spinner("Extracting text…"):
                pdf_bytes = uploaded.read()
                try:
                    text = extract_pdf_text(pdf_bytes, max_pages=30)
                except Exception as e:
                    st.error(f"PDF extraction failed: {e}")
                    st.stop()
            if not text.strip():
                st.error("No text found in this PDF.")
                st.stop()

            with st.spinner("Summarizing with AI…"):
                try:
                    data = summarize_text(text, audience=audience, detail=detail, subject=subject_name)
                except TypeError:
                    try:
                        data = summarize_text(text, audience=audience, detail=detail)
                    except TypeError:
                        data = summarize_text(text, audience=audience)
                except Exception as e:
                    st.error(f"Summarization failed: {e}")
                    st.stop()

            # Save three items and capture IDs
            summary_id = flash_id = quiz_id = None
            try:
                title = data.get("title") or f"{id_to_name(topic_id, all_folders) or 'Topic'} Summary"
                created_summary = save_item("summary", title, data, topic_id)
                summary_id = created_summary.get("id")

                fcs = data.get("flashcards") or []
                if fcs:
                    created_flash = save_item("flashcards", f"{title} • Flashcards", {"flashcards": fcs}, topic_id)
                    flash_id = created_flash.get("id")

                qs = data.get("exam_questions") or []
                if qs:
                    created_quiz = save_item("quiz", f"{title} • Quiz", {"questions": qs}, topic_id)
                    quiz_id = created_quiz.get("id")

                st.success("Saved: summary, flashcards, and quiz to this Topic folder ✅")
            except Exception as e:
                st.error(f"Save failed: {e}")

            # Open buttons (full-page)
            st.markdown("### Open Your Materials")
            oc1, oc2, oc3 = st.columns(3)
            if summary_id and oc1.button("Open Notes Page", type="primary"):
                _set_params(item=summary_id)
                st.rerun()
            if flash_id and oc2.button("Open Flashcards Page"):
                _set_params(item=flash_id)
                st.rerun()
            if quiz_id and oc3.button("Open Quiz Page"):
                _set_params(item=quiz_id)
                st.rerun()

# ---------- Tab 2: Quick Study ----------
with tabs[1]:
    st.title("⚡ Quick Study")
    st.write("Upload anything; we’ll create notes, flashcards, and a quiz, then you can file them in any folder.")

    if "sb_user" not in st.session_state:
        st.info("Log in (left) to save your study materials.")
    else:
        dest_id = None
        options = ["(no folder)"] + [f["name"] for f in all_folders]
        dest_pick = st.selectbox("Save to folder", options, index=0)
        if dest_pick != "(no folder)":
            dest_id = next(f["id"] for f in all_folders if f["name"] == dest_pick)

        audience_label = st.selectbox("Audience style", ["University", "A-Level / IB", "GCSE", "HKDSE"], index=0, key="aud_qs")
        audience = "university" if audience_label == "University" else "high school"
        detail = st.slider("Detail level", 1, 5, 3, key="detail_qs")
        subject_hint = st.text_input("Subject hint (optional, e.g., 'Mathematics')", key="subject_qs")

        uploaded = st.file_uploader("Upload PDF", type=["pdf"], key="u_qs")
        if uploaded and st.button("Generate & Save"):
            with st.spinner("Extracting text…"):
                pdf_bytes = uploaded.read()
                try:
                    text = extract_pdf_text(pdf_bytes, max_pages=30)
                except Exception as e:
                    st.error(f"PDF extraction failed: {e}")
                    st.stop()
            if not text.strip():
                st.error("No text found in this PDF.")
                st.stop()

            with st.spinner("Summarizing with AI…"):
                try:
                    data = summarize_text(text, audience=audience, detail=detail, subject=(subject_hint or "General"))
                except TypeError:
                    try:
                        data = summarize_text(text, audience=audience, detail=detail)
                    except TypeError:
                        data = summarize_text(text, audience=audience)
                except Exception as e:
                    st.error(f"Summarization failed: {e}")
                    st.stop()

            # Save & capture IDs
            summary_id = flash_id = quiz_id = None
            try:
                title = data.get("title") or "Untitled"
                created_summary = save_item("summary", title, data, dest_id)
                summary_id = created_summary.get("id")

                fcs = data.get("flashcards") or []
                if fcs:
                    created_flash = save_item("flashcards", f"{title} • Flashcards", {"flashcards": fcs}, dest_id)
                    flash_id = created_flash.get("id")

                qs = data.get("exam_questions") or []
                if qs:
                    created_quiz = save_item("quiz", f"{title} • Quiz", {"questions": qs}, dest_id)
                    quiz_id = created_quiz.get("id")

                st.success("Saved: summary, flashcards, and quiz ✅")
            except Exception as e:
                st.error(f"Save failed: {e}")

            st.markdown("### Open Your Materials")
            oc1, oc2, oc3 = st.columns(3)
            if summary_id and oc1.button("Open Notes Page", type="primary"):
                _set_params(item=summary_id)
                st.rerun()
            if flash_id and oc2.button("Open Flashcards Page"):
                _set_params(item=flash_id)
                st.rerun()
            if quiz_id and oc3.button("Open Quiz Page"):
                _set_params(item=quiz_id)
                st.rerun()

# ---------- Tab 3: Manage Items ----------
with tabs[2]:
    st.title("🧰 Manage Items")
    st.write("Open, move, or delete your notes, flashcards, and quizzes.")

    if "sb_user" not in st.session_state:
        st.info("Log in to manage your items.")
    else:
        all_opt = ["(all folders)"] + [f["name"] for f in all_folders]
        folder_filter = st.selectbox("Show items in", all_opt, index=0)
        filter_id = None if folder_filter == "(all folders)" else next(f["id"] for f in all_folders if f["name"] == folder_filter)

        try:
            items = list_items(filter_id, limit=200)
            if not items:
                st.caption("No items yet.")
            else:
                move_choices = {"(no folder)": None}
                for f in all_folders:
                    move_choices[f"{f['name']}"] = f["id"]

                for it in items:
                    with st.expander(f"📄 [{it['kind']}] {it['title']} — {it['created_at'][:16].replace('T',' ')}", expanded=False):
                        st.write(f"**Type**: {it['kind']}")
                        st.write(f"**Current folder**: {next((f['name'] for f in all_folders if f['id'] == it.get('folder_id')), '—')}")

                        cols = st.columns(4)

                        # Open (full page)
                        if cols[0].button("🔎 Open", key=f"open_{it['id']}"):
                            _set_params(item=it["id"])
                            st.rerun()

                        # Move
                        dest_name = cols[1].selectbox("Move to", list(move_choices.keys()), key=f"mv_{it['id']}")
                        if cols[2].button("Move", key=f"m_{it['id']}"):
                            try:
                                move_item(it["id"], move_choices[dest_name])
                                st.success("Moved.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Move failed: {e}")

                        # Delete
                        if cols[3].button("Delete", key=f"d_{it['id']}"):
                            try:
                                delete_item(it["id"])
                                st.success("Deleted.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Delete failed: {e}")
        except Exception as e:
            st.error(f"Load failed: {e}")





