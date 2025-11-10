# app.py
import streamlit as st
import sys
import time
import datetime as dt
import requests

from pdf_utils import extract_pdf_text
from llm import summarize_text  # must be the fallback version I gave you
from auth_rest import (
    sign_in, sign_up, sign_out,
    save_item, list_items, get_item, move_item, delete_item,
    create_folder, list_folders, delete_folder
)

# -------------------- Page config (must be FIRST Streamlit call) --------------------
st.set_page_config(page_title="StudyBloom", page_icon="📚")
st.caption(f"Python: {sys.version.split()[0]} • Build: 2025-11-10-fviewer")

# ============================ Viewer helpers ============================
def render_summary(data: dict):
    st.subheader("📝 Notes")
    st.markdown(f"**TL;DR**: {data.get('tl_dr', '')}")
    sections = data.get("sections") or []
    for sec in sections:
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
            st.markdown(f"- **{f.get('name','')}**: `{f.get('expression','')}` — {f.get('meaning','')}")

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

def render_flashcards(data: dict):
    st.subheader("🧠 Flashcards")
    # handle both shapes: {"flashcards":[...]} OR full data with .flashcards
    flashcards = data.get("flashcards") or data.get("data", {}).get("flashcards") or []
    if not flashcards:
        st.caption("No flashcards found.")
        return
    for i, c in enumerate(flashcards, start=1):
        front = c.get("front", "")
        back = c.get("back", "")
        st.markdown(f"**{i}. Front:** {front}\n\n**Back:** {back}")

def render_quiz(data: dict):
    st.subheader("🧪 Quiz")
    # handle both shapes: {"questions":[...]} OR full data with .exam_questions
    qs = data.get("questions") or data.get("exam_questions") or data.get("data", {}).get("questions") or []
    if not qs:
        st.caption("No questions found.")
        return
    for i, q in enumerate(qs, start=1):
        st.markdown(f"**Q{i}. {q.get('question','')}**")
        st.markdown(f"**Model answer:** {q.get('model_answer','')}")
        for pt in q.get("markscheme_points", []) or []:
            st.markdown(f"- {pt}")
        st.markdown("---")

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
        "Plan by **Subject → Exam → Topic**. Upload each topic as you revise; we'll summarize, "
        "build **flashcards**, generate a **quiz**, and file them neatly by topic."
    )

    if "sb_user" not in st.session_state:
        st.info("Log in (left) to use Exam Planner.")
    else:
        # session selections
        st.session_state.setdefault("ep_subject_id", None)
        st.session_state.setdefault("ep_exam_id", None)
        st.session_state.setdefault("ep_topic_id", None)

        # ----- SUBJECT -----
        subjects = [f for f in all_folders if not f.get("parent_id")]
        subj_names = [s["name"] for s in subjects]
        subj_index = 0
        if st.session_state["ep_subject_id"]:
            sel = id_to_name(st.session_state["ep_subject_id"], all_folders)
            if sel in subj_names:
                subj_index = subj_names.index(sel)

        c1, c2 = st.columns([3, 1])
        subject_choice = c1.selectbox(
            "Subject folder", ["(create new)"] + subj_names,
            index=subj_index + 1 if subj_names else 0, key="ep_subj_choice"
        )
        new_subject_name = c2.text_input("New", key="ep_new_subject_name", placeholder="e.g., A-Level Physics")
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

        # ----- EXAM -----
        exams = [f for f in all_folders if f.get("parent_id") == subject_id]
        exam_names = [e["name"] for e in exams]
        exam_index = 0
        if st.session_state["ep_exam_id"]:
            sel = id_to_name(st.session_state["ep_exam_id"], all_folders)
            if sel in exam_names:
                exam_index = exam_names.index(sel)

        c1, c2 = st.columns([3, 1])
        exam_choice = c1.selectbox(
            "Exam folder", ["(create new)"] + exam_names,
            index=exam_index + 1 if exam_names else 0, key="ep_exam_choice"
        )
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

        # ----- TOPIC -----
        topics = [f for f in all_folders if f.get("parent_id") == exam_id]
        topic_names = [t["name"] for t in topics]
        topic_index = 0
        if st.session_state["ep_topic_id"]:
            sel = id_to_name(st.session_state["ep_topic_id"], all_folders)
            if sel in topic_names:
                topic_index = topic_names.index(sel)

        c1, c2 = st.columns([3, 1])
        topic_choice = c1.selectbox(
            "Topic folder", ["(create new)"] + topic_names,
            index=topic_index + 1 if topic_names else 0, key="ep_topic_choice"
        )
        new_topic_name = c2.text_input("New", key="ep_new_topic_name", placeholder="e.g., Kinematics")
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
                    data = summarize_text(text, audience=audience, detail=detail)
                except TypeError:
                    # if your summarize_text signature doesn’t accept detail
                    data = summarize_text(text, audience=audience)
                except Exception as e:
                    st.error(f"Summarization failed: {e}")
                    st.stop()

            # Save three items (summary, flashcards, quiz) under the Topic
            try:
                title = data.get("title") or f"{id_to_name(topic_id, all_folders) or 'Topic'} Summary"
                save_item("summary", title, data, topic_id)

                fcs = data.get("flashcards") or []
                if fcs:
                    save_item("flashcards", f"{title} • Flashcards", {"flashcards": fcs}, topic_id)

                qs = data.get("exam_questions") or []
                if qs:
                    save_item("quiz", f"{title} • Quiz", {"questions": qs}, topic_id)

                st.success("Saved: summary, flashcards, and quiz to this Topic folder ✅")
            except Exception as e:
                st.error(f"Save failed: {e}")

            # Show full viewers
            t1, t2, t3 = st.tabs(["📝 Notes", "🧠 Flashcards", "🧪 Quiz"])
            with t1:
                render_summary(data)
            with t2:
                render_flashcards({"flashcards": data.get("flashcards") or []})
            with t3:
                render_quiz({"questions": data.get("exam_questions") or []})

# ---------- Tab 2: Quick Study ----------
with tabs[1]:
    st.title("⚡ Quick Study")
    st.write("Just want to study? Upload anything; we’ll create notes, flashcards, and a quiz, then you can file them in any folder.")

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
                    data = summarize_text(text, audience=audience, detail=detail)
                except TypeError:
                    data = summarize_text(text, audience=audience)
                except Exception as e:
                    st.error(f"Summarization failed: {e}")
                    st.stop()

            try:
                title = data.get("title") or "Untitled"
                save_item("summary", title, data, dest_id)
                fcs = data.get("flashcards") or []
                if fcs:
                    save_item("flashcards", f"{title} • Flashcards", {"flashcards": fcs}, dest_id)
                qs = data.get("exam_questions") or []
                if qs:
                    save_item("quiz", f"{title} • Quiz", {"questions": qs}, dest_id)
                st.success("Saved: summary, flashcards, and quiz ✅")
            except Exception as e:
                st.error(f"Save failed: {e}")

            # View immediately
            t1, t2, t3 = st.tabs(["📝 Notes", "🧠 Flashcards", "🧪 Quiz"])
            with t1:
                render_summary(data)
            with t2:
                render_flashcards({"flashcards": data.get("flashcards") or []})
            with t3:
                render_quiz({"questions": data.get("exam_questions") or []})

# ---------- Tab 3: Manage Items ----------
with tabs[2]:
    st.title("🧰 Manage Items")
    st.write("View, move, or delete your notes, flashcards, and quizzes.")

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
                    with st.expander(f"📄 [{it['kind']}] {it['title']} — {it['created_at'][:16].replace('T',' ')}"):
                        st.write(f"**Type**: {it['kind']}")
                        st.write(f"**Current folder**: {next((f['name'] for f in all_folders if f['id'] == it.get('folder_id')), '—')}")

                        cols = st.columns(4)

                        # View
                        if cols[0].button("👁️ View", key=f"v_{it['id']}"):
                            try:
                                full = get_item(it["id"])  # includes 'data'
                                kind = full.get("kind")
                                payload = full.get("data") or {}
                                if kind == "summary":
                                    render_summary(payload or full)
                                elif kind == "flashcards":
                                    render_flashcards(payload)
                                elif kind == "quiz":
                                    render_quiz(payload)
                                else:
                                    st.write(payload or full)
                            except Exception as e:
                                st.error(f"View failed: {e}")

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



