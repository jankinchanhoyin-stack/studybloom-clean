import streamlit as st
import sys  # optional for version caption

# >>> MUST be first Streamlit call <<<
st.set_page_config(page_title="StudyBloom", page_icon="📚")

# (Optional: debug Python version so we can see it on the page)
st.caption(f"Python: {sys.version.split()[0]}")

# now do other imports that may *reference* streamlit,
# but they must not call st.* at import time
import time
import datetime as dt
import requests

from pdf_utils import extract_pdf_text
from llm import summarize_text
from auth_rest import (
    sign_in, sign_up, sign_out,
    save_item, list_items, get_item, move_item, delete_item,
    create_folder, list_folders, delete_folder
)


from auth_rest import (
    sign_in, sign_up, sign_out,
    save_item, list_items, get_item, move_item, delete_item,
    create_folder, list_folders, delete_folder
)

st.set_page_config(page_title="StudyBloom", page_icon="📚")
st.caption("Build tag: folders-2025-11-10")

# ---------------- Sidebar: Auth ----------------
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
            st.sidebar.error(f"{e}")

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
            st.sidebar.error(f"{e}")
else:
    st.sidebar.success(f"Signed in as {st.session_state['sb_user']['email']}")
    if st.sidebar.button("Sign out", use_container_width=True):
        sign_out()
        st.rerun()

# ---------------- Sidebar: Folder Tree ----------------
def build_tree(rows):
    # id -> node
    nodes = {r["id"]: {**r, "children": []} for r in rows}
    roots = []
    for r in rows:
        pid = r.get("parent_id")
        if pid and pid in nodes:
            nodes[pid]["children"].append(nodes[r["id"]])
        else:
            roots.append(nodes[r["id"]])
    return roots, nodes

selected_folder = None
all_folders = []
if "sb_user" in st.session_state:
    st.sidebar.markdown("---")
    st.sidebar.subheader("📂 Folders")
    try:
        all_folders = list_folders()
        tree, node_map = build_tree(all_folders)

        # Simple tree display + select
        def render_tree(nodes, level=0):
            global selected_folder
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

        # Quick create controls
        with st.sidebar.expander("New Folder"):
            new_name = st.text_input("Folder name", key="new_folder_name")
            parent = None
            choices = {"(no parent)": None}
            for f in all_folders:
                choices[f"{f['name']}"] = f["id"]
            parent_name = st.selectbox("Parent", list(choices.keys()))
            parent = choices[parent_name]
            if st.button("Create folder", use_container_width=True):
                if not new_name.strip():
                    st.warning("Enter a folder name.")
                else:
                    try:
                        create_folder(new_name.strip(), parent)
                        st.success("Folder created.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Create failed: {e}")

        # Delete current folder
        if selected_folder:
            if st.sidebar.button("🗑️ Delete selected folder"):
                try:
                    delete_folder(selected_folder)
                    st.session_state.pop("active_folder_id", None)
                    st.success("Folder deleted.")
                    st.rerun()
                except Exception as e:
                    st.sidebar.error(f"Delete failed: {e}")

    except Exception as e:
        st.sidebar.info("Create your first folder to organize notes.")

# ---------------- Main Tabs ----------------
tabs = st.tabs(["Exam Planner", "Quick Study", "Manage Items"])

# ---------- Tab 1: Exam Planner ----------
with tabs[0]:
    st.title("🗂️ Exam Planner")
    st.write(
        "Plan by **Subject → Exam → Topic**. Upload each topic as you revise; we'll summarize, "
        "make **flashcards**, and **quiz questions**, and file them neatly by topic."
    )

    if "sb_user" not in st.session_state:
        st.info("Log in (left) to use Exam Planner.")
    else:
        # ensure keys
        st.session_state.setdefault("ep_subject_id", None)
        st.session_state.setdefault("ep_exam_id", None)
        st.session_state.setdefault("ep_topic_id", None)

        def id_to_name(fid):
            if not fid:
                return None
            for f in all_folders:
                if f["id"] == fid:
                    return f["name"]
            return None

        st.subheader("Choose destination folders")

        # ----- SUBJECT -----
        subjects = [f for f in all_folders if not f.get("parent_id")]
        subj_names = [s["name"] for s in subjects]
        # pre-select by session
        subj_index = 0
        if st.session_state["ep_subject_id"]:
            sel_name = id_to_name(st.session_state["ep_subject_id"])
            if sel_name in subj_names:
                subj_index = subj_names.index(sel_name)

        cols = st.columns([3,1])
        subject_choice = cols[0].selectbox("Subject folder", ["(create new)"] + subj_names, index=subj_index+1 if subj_names else 0, key="ep_subj_choice")
        if cols[1].button("New subject"):
            new_subject = st.text_input("New subject name", key="ep_new_subject_name", placeholder="e.g., A-Level Physics")
            create_click = st.button("Create Subject", key="ep_create_subject_go")
            if create_click:
                n = st.session_state.get("ep_new_subject_name", "").strip()
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
            sel_name = id_to_name(st.session_state["ep_exam_id"])
            if sel_name in exam_names:
                exam_index = exam_names.index(sel_name)

        cols = st.columns([3,1])
        exam_choice = cols[0].selectbox("Exam folder", ["(create new)"] + exam_names, index=exam_index+1 if exam_names else 0, key="ep_exam_choice")
        if cols[1].button("New exam"):
            new_exam = st.text_input("New exam name", key="ep_new_exam_name", placeholder="e.g., May 2026 Session")
            create_exam_click = st.button("Create Exam", key="ep_create_exam_go")
            if create_exam_click:
                n = st.session_state.get("ep_new_exam_name", "").strip()
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
            sel_name = id_to_name(st.session_state["ep_topic_id"])
            if sel_name in topic_names:
                topic_index = topic_names.index(sel_name)

        cols = st.columns([3,1])
        topic_choice = cols[0].selectbox("Topic folder", ["(create new)"] + topic_names, index=topic_index+1 if topic_names else 0, key="ep_topic_choice")
        if cols[1].button("New topic"):
            new_topic = st.text_input("New topic name", key="ep_new_topic_name", placeholder="e.g., Kinematics")
            create_topic_click = st.button("Create Topic", key="ep_create_topic_go")
            if create_topic_click:
                n = st.session_state.get("ep_new_topic_name", "").strip()
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

        if uploaded:
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

            if st.button("Generate Notes + Flashcards + Quiz", type="primary"):
                with st.spinner("Summarizing with AI…"):
                    try:
                        data = summarize_text(text, audience=audience, detail=detail)
                    except TypeError:
                        data = summarize_text(text, audience=audience)
                    except Exception as e:
                        st.error(f"Summarization failed: {e}")
                        st.stop()

                try:
                    title = data.get("title") or f"{id_to_name(topic_id) or 'Topic'} Summary"
                    save_item("summary", title, data, topic_id)

                    fcs = data.get("flashcards", [])
                    if fcs:
                        save_item("flashcards", f"{title} • Flashcards", {"flashcards": fcs}, topic_id)

                    qs = data.get("exam_questions", [])
                    if qs:
                        save_item("quiz", f"{title} • Quiz", {"questions": qs}, topic_id)

                    st.success("Saved: summary, flashcards, and quiz to this Topic folder ✅")
                except Exception as e:
                    st.error(f"Save failed: {e}")

                st.markdown("### Preview")
                st.markdown(f"**TL;DR**: {data.get('tl_dr','')}")
                if data.get("sections"):
                    st.markdown("#### Sections")
                    for sec in data["sections"]:
                        st.markdown(f"- **{sec.get('heading','Section')}**")
                if data.get("flashcards"):
                    st.markdown(f"**Flashcards:** {len(data['flashcards'])}")
                if data.get("exam_questions"):
                    st.markdown(f**"**Quiz questions:** {len(data['exam_questions'])}")


# ---------- Tab 2: Quick Study ----------
with tabs[1]:
    st.title("⚡ Quick Study")
    st.write("Just want to study? Upload anything, we’ll create notes, flashcards, and a quiz, and you can file them in any folder.")

    if "sb_user" not in st.session_state:
        st.info("Log in (left) to save your study materials.")
    else:
        # Choose a destination folder (any)
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
                fcs = data.get("flashcards", [])
                if fcs:
                    save_item("flashcards", f"{title} • Flashcards", {"flashcards": fcs}, dest_id)
                qs = data.get("exam_questions", [])
                if qs:
                    save_item("quiz", f"{title} • Quiz", {"questions": qs}, dest_id)
                st.success("Saved: summary, flashcards, and quiz ✅")
            except Exception as e:
                st.error(f"Save failed: {e}")

# ---------- Tab 3: Manage Items ----------
with tabs[2]:
    st.title("🧰 Manage Items")
    st.write("View, move, or delete your notes, flashcards, and quizzes.")

    if "sb_user" not in st.session_state:
        st.info("Log in to manage your items.")
    else:
        # Filter by folder
        all_opt = ["(all folders)"] + [f["name"] for f in all_folders]
        folder_filter = st.selectbox("Show items in", all_opt, index=0)
        filter_id = None if folder_filter == "(all folders)" else next(f["id"] for f in all_folders if f["name"] == folder_filter)

        try:
            items = list_items(filter_id, limit=200)
            if not items:
                st.caption("No items yet.")
            else:
                # destination for move
                move_choices = {"(no folder)": None}
                for f in all_folders:
                    move_choices[f"{f['name']}"] = f["id"]

                for it in items:
                    with st.expander(f"📄 [{it['kind']}] {it['title']} — {it['created_at'][:16].replace('T',' ')}"):
                        st.write(f"**Type**: {it['kind']}")
                        st.write(f"**Current folder**: {next((f['name'] for f in all_folders if f['id']==it.get('folder_id')), '—')}")
                        cols = st.columns(3)
                        # Move
                        dest_name = cols[0].selectbox("Move to", list(move_choices.keys()), key=f"mv_{it['id']}")
                        if cols[1].button("Move", key=f"m_{it['id']}"):
                            try:
                                move_item(it["id"], move_choices[dest_name])
                                st.success("Moved.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Move failed: {e}")
                        # Delete
                        if cols[2].button("Delete", key=f"d_{it['id']}"):
                            try:
                                delete_item(it["id"])
                                st.success("Deleted.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Delete failed: {e}")
        except Exception as e:
            st.error(f"Load failed: {e}")


