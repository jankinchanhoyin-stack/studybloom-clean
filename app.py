# app.py
# ------------- MUST be first -------------
import streamlit as st
st.set_page_config(page_title="StudyBloom", page_icon="📚")

# ------------- Imports -------------
import sys, requests
from typing import Optional, List, Dict, Tuple

from pdf_utils import extract_any
from llm import summarize_text, grade_free_answer, generate_quiz_from_notes
from auth_rest import (
    sign_in, sign_up, sign_out,
    save_item, list_items, get_item, move_item, delete_item,
    create_folder, list_folders, delete_folder, list_child_folders,
    save_quiz_attempt, list_quiz_attempts, list_quiz_attempts_for_items,
    save_flash_review, list_flash_reviews_for_items
)

st.caption(f"Python {sys.version.split()[0]} • Build: resources-dropdowns + topic-progress + unique-names")

# ------------- URL helpers -------------
def _get_params() -> Dict[str, str]:
    try:
        return dict(st.query_params)
    except Exception:
        return st.experimental_get_query_params()

def _set_params(**kwargs):
    try:
        st.query_params.clear()
        st.query_params.update(kwargs)
    except Exception:
        st.experimental_set_query_params(**kwargs)

def _clear_params():
    _set_params()

# ------------- Supabase REST helpers (rename) -------------
def _sb_headers():
    url = st.secrets.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL / SUPABASE_KEY in secrets.")
    return url, {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

def rename_folder(folder_id: str, new_name: str) -> dict:
    url, headers = _sb_headers()
    r = requests.patch(
        f"{url}/rest/v1/folders?id=eq.{folder_id}",
        json={"name": new_name},
        headers=headers,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    return data[0] if isinstance(data, list) and data else {}

def rename_item(item_id: str, new_title: str) -> dict:
    url, headers = _sb_headers()
    r = requests.patch(
        f"{url}/rest/v1/items?id=eq.{item_id}",
        json={"title": new_title},
        headers=headers,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    return data[0] if isinstance(data, list) and data else {}

# ------------- Progress -------------
def compute_topic_progress(topic_folder_id: str) -> float:
    """0.0 ~ 1.0 based on latest quiz ratios (60%) + flashcard known rate (40%)."""
    try:
        items = list_items(topic_folder_id, limit=500)
        quiz_ids = [it["id"] for it in items if it["kind"] == "quiz"]
        flash_ids = [it["id"] for it in items if it["kind"] == "flashcards"]

        quiz_score = 0.0
        if quiz_ids:
            attempts = list_quiz_attempts_for_items(quiz_ids)
            latest_per_quiz: Dict[str, Tuple[int, int]] = {}
            for at in attempts:
                qid = at["item_id"]
                # assuming list returned newest first; first seen = latest
                if qid not in latest_per_quiz:
                    latest_per_quiz[qid] = (at["correct"], at["total"])
            if latest_per_quiz:
                ratios = [(c / t) if t else 0 for (c, t) in latest_per_quiz.values()]
                quiz_score = sum(ratios) / len(ratios)

        flash_score = 0.0
        if flash_ids:
            reviews = list_flash_reviews_for_items(flash_ids)
            if reviews:
                known = sum(1 for r in reviews if r.get("known"))
                flash_score = known / max(1, len(reviews))

        return 0.6 * quiz_score + 0.4 * flash_score
    except Exception:
        return 0.0

# ------------- Renderers -------------
def render_summary(data: dict):
    st.subheader("📝 Notes")
    st.markdown(f"**TL;DR**: {data.get('tl_dr', '')}")
    for sec in (data.get("sections") or []):
        st.markdown(f"### {sec.get('heading','Section')}")
        for b in sec.get("bullets", []) or []:
            st.markdown(f"- {b}")
    if data.get("key_terms"):
        st.markdown("## Key Terms")
        for kt in data["key_terms"]:
            st.markdown(f"- **{kt.get('term','')}** — {kt.get('definition','')}")
    if data.get("formulas"):
        st.markdown("## Formulas")
        for f in data["formulas"]:
            name = f.get("name","")
            expr = (f.get("latex") or f.get("expression") or "").strip()
            meaning = f.get("meaning","")
            if any(s in expr for s in ["\\frac","\\sqrt","^","_","\\times","\\cdot","\\sum","\\int","\\left","\\right"]):
                if name or meaning: st.markdown(f"**{name}** — {meaning}")
                try: st.latex(expr)
                except Exception: st.code(expr)
            else:
                st.markdown(f"- **{name}**: `{expr}` — {meaning}")
    if data.get("examples"):
        st.markdown("## Worked Examples")
        for e in data["examples"]:
            st.markdown(f"- {e}")
    if data.get("common_pitfalls"):
        st.markdown("## Common Pitfalls")
        for p in data["common_pitfalls"]:
            st.markdown(f"- {p}")

def interactive_flashcards(flashcards: List[dict], item_id: Optional[str] = None, key_prefix: str = "fc"):
    st.subheader("🧠 Flashcards")
    if not flashcards:
        st.caption("No flashcards found."); return
    st.session_state.setdefault(f"{key_prefix}_idx", 0)
    st.session_state.setdefault(f"{key_prefix}_revealed", False)
    st.session_state.setdefault(f"{key_prefix}_order", list(range(len(flashcards))))
    st.session_state.setdefault(f"{key_prefix}_wrong_counts", {})
    order = st.session_state[f"{key_prefix}_order"]
    if not order:
        st.success("Deck complete — nice work!")
        if st.button("🔁 Restart", key=f"{key_prefix}_restart_all"):
            st.session_state[f"{key_prefix}_order"] = list(range(len(flashcards)))
            st.session_state[f"{key_prefix}_idx"] = 0
            st.session_state[f"{key_prefix}_revealed"] = False
            st.session_state[f"{key_prefix}_wrong_counts"] = {}
            st.rerun()
        return
    idx = st.session_state[f"{key_prefix}_idx"]
    idx = max(0, min(idx, len(order)-1))
    st.session_state[f"{key_prefix}_idx"] = idx
    revealed = st.session_state[f"{key_prefix}_revealed"]
    wrong_counts = st.session_state[f"{key_prefix}_wrong_counts"]
    st.progress((idx+1)/len(order), text=f"Card {idx+1}/{len(order)}")
    orig_i = order[idx]
    card = flashcards[orig_i]
    st.markdown("#### Front")
    st.info(card.get("front",""))
    if revealed:
        st.markdown("#### Back")
        st.success(card.get("back",""))
    c1,c2,c3,c4 = st.columns(4)
    if c1.button("◀️ Prev", disabled=(idx==0), key=f"{key_prefix}_prev"):
        st.session_state[f"{key_prefix}_idx"]=idx-1
        st.session_state[f"{key_prefix}_revealed"]=False
        st.rerun()
    if c2.button("🔁 Flip", key=f"{key_prefix}_flip"):
        st.session_state[f"{key_prefix}_revealed"]=not revealed
        st.rerun()
    if c3.button("✅ Knew it", key=f"{key_prefix}_ok"):
        if item_id and "sb_user" in st.session_state:
            try: save_flash_review(item_id, True)
            except Exception: pass
        st.session_state[f"{key_prefix}_order"].pop(idx)
        st.session_state[f"{key_prefix}_revealed"]=False
        if idx >= len(st.session_state[f"{key_prefix}_order"]):
            st.session_state[f"{key_prefix}_idx"] = max(0, len(st.session_state[f"{key_prefix}_order"])-1)
        st.rerun()
    if c4.button("❌ Again", key=f"{key_prefix}_bad"):
        if item_id and "sb_user" in st.session_state:
            try: save_flash_review(item_id, False)
            except Exception: pass
        count = wrong_counts.get(orig_i, 0)
        if count < 2:
            insert_at = min(len(order), idx + 4)
            st.session_state[f"{key_prefix}_order"].insert(insert_at, orig_i)
            wrong_counts[orig_i] = count + 1
        st.session_state[f"{key_prefix}_wrong_counts"]=wrong_counts
        st.session_state[f"{key_prefix}_revealed"]=False
        if idx < len(st.session_state[f"{key_prefix}_order"]) - 1:
            st.session_state[f"{key_prefix}_idx"]=idx+1
        st.rerun()

def interactive_quiz(questions: List[dict], item_id: Optional[str]=None, key_prefix="quiz", subject_hint="General"):
    st.subheader("🧪 Quiz")
    if not questions:
        st.caption("No questions found."); return
    st.session_state.setdefault(f"{key_prefix}_i", 0)
    st.session_state.setdefault(f"{key_prefix}_graded", False)
    st.session_state.setdefault(f"{key_prefix}_feedback", "")
    st.session_state.setdefault(f"{key_prefix}_mark_last", (0, 0))
    st.session_state.setdefault(f"{key_prefix}_history", [])
    i = st.session_state[f"{key_prefix}_i"]
    i = max(0, min(i, len(questions)-1))
    st.session_state[f"{key_prefix}_i"] = i
    q = questions[i]
    st.progress((i+1)/len(questions), text=f"Question {i+1}/{len(questions)}")
    st.markdown(f"### {q.get('question','')}")
    ans = st.text_area("Your answer", key=f"{key_prefix}_ans_{i}", height=120, placeholder="Type your working/answer here…")
    colg1, colg2, colg3, colg4 = st.columns(4)
    if colg1.button("Submit", key=f"{key_prefix}_submit"):
        try:
            result = grade_free_answer(
                question=q.get("question",""),
                model_answer=q.get("model_answer",""),
                markscheme=q.get("markscheme_points", []) or [],
                user_answer=ans or "",
                subject=subject_hint or "General",
            )
            st.session_state[f"{key_prefix}_graded"] = True
            st.session_state[f"{key_prefix}_feedback"] = result.get("feedback","")
            last = (result.get("score",0), result.get("max_points",10))
            st.session_state[f"{key_prefix}_mark_last"] = last
            hist = st.session_state[f"{key_prefix}_history"]
            if len(hist) <= i:
                hist.append({"score": last[0], "max": last[1]})
            else:
                hist[i] = {"score": last[0], "max": last[1]}
        except Exception as e:
            st.error(f"Grading failed: {e}")
    if st.session_state[f"{key_prefix}_graded"]:
        sc, mx = st.session_state[f"{key_prefix}_mark_last"]
        st.success(f"Score for this question: {sc} / {mx}")
        with st.expander("Model answer & mark scheme", expanded=False):
            st.markdown(q.get("model_answer",""))
            for pt in q.get("markscheme_points", []) or []:
                st.markdown(f"- {pt}")
        if st.session_state[f"{key_prefix}_feedback"]:
            st.info(st.session_state[f"{key_prefix}_feedback"])
    c1,c2,c3,c4 = st.columns(4)
    if c1.button("◀️ Prev", disabled=(i==0), key=f"{key_prefix}_prev"):
        st.session_state[f"{key_prefix}_i"]=i-1
        st.session_state[f"{key_prefix}_graded"]=False
        st.session_state[f"{key_prefix}_feedback"]=""
        st.rerun()
    if c2.button("Next ▶️", disabled=(i==len(questions)-1), key=f"{key_prefix}_next"):
        st.session_state[f"{key_prefix}_i"]=i+1
        st.session_state[f"{key_prefix}_graded"]=False
        st.session_state[f"{key_prefix}_feedback"]=""
        st.rerun()
    total_sc = sum(h.get("score",0) for h in st.session_state[f"{key_prefix}_history"])
    total_mx = sum(h.get("max",0) for h in st.session_state[f"{key_prefix}_history"])
    st.metric("Total so far", f"{total_sc} / {total_mx or (len(questions)*10)}")
    if c3.button("✅ Finish & Save", key=f"{key_prefix}_finish"):
        if item_id and "sb_user" in st.session_state:
            try:
                correct = sum(1 for h in st.session_state[f"{key_prefix}_history"] if h.get("max",0) and h.get("score",0) >= 0.7*h["max"])
                total = len(questions)
                save_quiz_attempt(item_id, correct, total, st.session_state[f"{key_prefix}_history"])
                st.success(f"Attempt saved: {correct}/{total}")
            except Exception:
                st.info("Attempt not saved (check quiz_attempts table).")
    if c4.button("🎲 New quiz", key=f"{key_prefix}_regen") and item_id:
        try:
            quiz_item = get_item(item_id)
            folder_id = quiz_item.get("folder_id")
            subject = subject_hint or "General"
            if folder_id:
                siblings = list_items(folder_id, limit=200)
                summary = next((s for s in siblings if s.get("kind")=="summary"), None)
                if summary and summary.get("data"):
                    new_qs = generate_quiz_from_notes(summary["data"], subject=subject, audience="high school", num_questions=8)
                    if new_qs:
                        created = save_item("quiz", f"{summary['title']} • Quiz (new)", {"questions": new_qs}, folder_id)
                        st.success("New quiz created.")
                        _set_params(item=created.get("id"), resources=1); st.rerun()
                    else:
                        st.warning("Could not generate a new quiz from notes.")
                else:
                    st.info("No summary found in this folder to generate from.")
            else:
                st.info("Folder not found for this quiz.")
        except Exception as e:
            st.error(f"Re-generate failed: {e}")

# ------------- Sidebar: Auth only -------------
st.sidebar.title("StudyBloom")
st.sidebar.caption("Log in to save & organize.")
if "sb_user" not in st.session_state:
    st.sidebar.subheader("Sign in")
    email = st.sidebar.text_input("Email", key="login_email")
    pwd = st.sidebar.text_input("Password", type="password", key="login_pwd")
    if st.sidebar.button("Sign in", use_container_width=True, key="login_btn"):
        try: sign_in(email, pwd); st.rerun()
        except Exception as e: st.sidebar.error(str(e))
    st.sidebar.subheader("Create account")
    remail = st.sidebar.text_input("New email", key="reg_email")
    rpwd = st.sidebar.text_input("New password", type="password", key="reg_pwd")
    if st.sidebar.button("Sign up", use_container_width=True, key="reg_btn"):
        try: sign_up(remail, rpwd); st.sidebar.success("Check email to confirm, then sign in.")
        except Exception as e: st.sidebar.error(str(e))
else:
    st.sidebar.success(f"Signed in as {st.session_state['sb_user']['user'].get('email','account')}")
    if st.sidebar.button("Sign out", use_container_width=True, key="logout_btn"):
        sign_out(); st.rerun()

# ------------- Fetch folders for the session -------------
if "sb_user" in st.session_state:
    try:
        ALL_FOLDERS = list_folders()
    except Exception as e:
        ALL_FOLDERS = []
        st.warning(f"Could not load folders: {e}")
else:
    ALL_FOLDERS = []

def _roots(rows):  # subjects
    return [r for r in rows if not r.get("parent_id")]

# ------------- Dedicated Resources PAGE (route) -------------
params = _get_params()
if "resources" in params and "item" not in params and "sb_user" in st.session_state:
    st.title("🧰 Study Resources")

    # Dropdowns: Subject → Exam → Topic
    subjects = _roots(ALL_FOLDERS)
    subj_names = [s["name"] for s in subjects]
    subj_pick = st.selectbox("Subject", ["— select —"]+subj_names, key="res_subj_pick")
    subj_id = None
    if subj_pick in subj_names:
        subj_id = next(s["id"] for s in subjects if s["name"]==subj_pick)

    exam_id = None
    if subj_id:
        exams = [f for f in ALL_FOLDERS if f.get("parent_id")==subj_id]
        ex_names = [e["name"] for e in exams]
        ex_pick = st.selectbox("Exam", ["— select —"]+ex_names, key="res_exam_pick")
        if ex_pick in ex_names:
            exam_id = next(e["id"] for e in exams if e["name"]==ex_pick)

    topic_id = None
    if exam_id:
        topics = [f for f in ALL_FOLDERS if f.get("parent_id")==exam_id]
        tp_names = [t["name"] for t in topics]
        tp_pick = st.selectbox("Topic", ["— select —"]+tp_names, key="res_topic_pick")
        if tp_pick in tp_names:
            topic_id = next(t["id"] for t in topics if t["name"]==tp_pick)

    if topic_id:
        # Progress bar per topic
        prog = compute_topic_progress(topic_id)
        st.progress(prog, text=f"Topic progress: {int(prog*100)}%")

        # Items list under topic
        emoji = {"summary":"📄","flashcards":"🧠","quiz":"🧪"}
        try:
            items = list_items(topic_id, limit=200)
        except Exception:
            items = []
        st.subheader("Resources")
        if not items:
            st.caption("No items yet.")
        for it in items:
            icon = emoji.get(it["kind"], "📄")
            cols = st.columns([7,1,1,1])
            cols[0].markdown(f"{icon} **{it['title']}** — {it['created_at'][:16].replace('T',' ')}")
            if cols[1].button("Open", key=f"res_open_{it['id']}"):
                _set_params(item=it["id"], resources=1); st.rerun()
            # Compact rename (✏) and delete (🗑)
            if not st.session_state.get(f"edit_item_{it['id']}", False):
                if cols[2].button("✏", key=f"res_btn_rename_{it['id']}"):
                    st.session_state[f"edit_item_{it['id']}"]=True; st.rerun()
            else:
                newt = st.text_input("New title", value=it["title"], key=f"res_rn_{it['id']}")
                c1,c2 = st.columns(2)
                if c1.button("Save", key=f"res_save_{it['id']}"):
                    try: rename_item(it["id"], newt.strip()); st.session_state[f"edit_item_{it['id']}"]=False; st.rerun()
                    except Exception as e: st.error(f"Rename failed: {e}")
                if c2.button("Cancel", key=f"res_cancel_{it['id']}"):
                    st.session_state[f"edit_item_{it['id']}"]=False; st.rerun()
            if cols[3].button("🗑", key=f"res_del_{it['id']}"):
                try: delete_item(it["id"]); st.success("Deleted."); st.rerun()
                except Exception as e: st.error(f"Delete failed: {e}")

    st.stop()

# ------------- Item PAGE (full page) -------------
if "item" in params and "sb_user" in st.session_state:
    item_id = params.get("item")
    if isinstance(item_id, list): item_id = item_id[0]
    try:
        full = get_item(item_id)
        kind = full.get("kind"); title = full.get("title") or kind.title()
        st.title(title)

        # Back goes to Resources page
        if st.button("← Back to Study Resources", key="item_back_btn"):
            _set_params(resources=1); st.rerun()

        data = full.get("data") or {}
        subject_hint = st.text_input("Subject (affects grading & new quizzes)", value="General", key=f"subj_{item_id}")
        if kind == "summary":
            render_summary(data or full)
        elif kind == "flashcards":
            interactive_flashcards(data.get("flashcards") or [], item_id=item_id, key_prefix=f"fc_{item_id}")
        elif kind == "quiz":
            interactive_quiz(data.get("questions") or [], item_id=item_id, key_prefix=f"quiz_{item_id}", subject_hint=subject_hint)
        else:
            st.write(data or full)
    except Exception as e:
        st.error(f"Could not load item: {e}")
        if st.button("← Back to Study Resources", key="item_back_btn2"):
            _set_params(resources=1); st.rerun()
    st.stop()

# ------------- Tabs (Quick Study + Study Resources only) -------------
tabs = st.tabs(["Quick Study", "Study Resources"])

# ===== Quick Study =====
with tabs[0]:
    st.title("⚡ Quick Study")
    if "sb_user" not in st.session_state:
        st.info("Log in to save your study materials.")
    else:
        subjects = _roots(ALL_FOLDERS)
        subj_names = [s["name"] for s in subjects]

        # SUBJECT: choose existing OR create new (unique)
        st.markdown("### Subject")
        make_new_subject = st.checkbox("Create a new subject", key="qs_make_new_subject", value=False)
        subject_id = None
        if make_new_subject:
            new_subject = st.text_input("New subject name", placeholder="e.g., A-Level Mathematics", key="qs_new_subject")
            if st.button("Save subject", key="qs_save_subject_btn"):
                name = (new_subject or "").strip()
                if not name:
                    st.warning("Enter a subject name.")
                elif name in subj_names:
                    st.error("This subject already exists. Please use a different name.")
                else:
                    created = create_folder(name, None)
                    st.success("Subject created.")
                    st.rerun()
        else:
            subj_pick = st.selectbox("Use existing subject", ["— select —"]+subj_names, key="qs_subject_pick")
            if subj_pick in subj_names:
                subject_id = next(s["id"] for s in subjects if s["name"]==subj_pick)

        # EXAM: choose existing OR create new (unique within subject)
        st.markdown("### Exam")
        exam_id = None
        if subject_id:
            exams = [f for f in ALL_FOLDERS if f.get("parent_id")==subject_id]
            exam_names = [e["name"] for e in exams]
            make_new_exam = st.checkbox("Create a new exam", key="qs_make_new_exam", value=False)
            if make_new_exam:
                new_exam = st.text_input("New exam name", placeholder="e.g., IGCSE May 2026", key="qs_new_exam")
                if st.button("Save exam", key="qs_save_exam_btn"):
                    name = (new_exam or "").strip()
                    if not name:
                        st.warning("Enter an exam name.")
                    elif name in exam_names:
                        st.error("This exam already exists in the selected subject. Please use a different name.")
                    else:
                        create_folder(name, subject_id)
                        st.success("Exam created.")
                        st.rerun()
            else:
                ex_pick = st.selectbox("Use existing exam", ["— select —"]+exam_names, key="qs_exam_pick")
                if ex_pick in exam_names:
                    exam_id = next(e["id"] for e in exams if e["name"]==ex_pick)
        else:
            st.caption("Pick or create a Subject first to reveal the Exam chooser.")

        # TOPIC: ALWAYS CREATE NEW (unique within exam)
        st.markdown("### Topic")
        topic_id = None
        if exam_id:
            topics = [f for f in ALL_FOLDERS if f.get("parent_id")==exam_id]
            topic_names = [t["name"] for t in topics]
            new_topic = st.text_input("New topic name", placeholder="e.g., Differentiation", key="qs_new_topic")
            if st.button("Save topic", key="qs_save_topic_btn"):
                name = (new_topic or "").strip()
                if not name:
                    st.warning("Enter a topic name.")
                elif name in topic_names:
                    st.error("This topic already exists under the selected exam. Please use a different name.")
                else:
                    created = create_folder(name, exam_id)
                    topic_id = created["id"]
                    st.success("Topic created.")
                    st.rerun()
        else:
            st.caption("Pick or create an Exam first to add a Topic.")

        st.markdown("---")
        st.markdown("**Subject (free text, improves accuracy & quality):**")
        subject_hint = st.text_input(
            "e.g., Mathematics (Calculus), Biology (Cell Division), History (Cold War)",
            value="General",
            key="qs_subject_hint"
        )
        audience_label = st.selectbox(
            "Audience",
            ["University", "A-Level", "A-Level / IB", "GCSE", "HKDSE", "Primary"],
            index=0,
            key="qs_audience_label"
        )
        aud_map = {
            "University": "university",
            "A-Level": "A-Level",
            "A-Level / IB": "A-Level",
            "GCSE": "high school",
            "HKDSE": "high school",
            "Primary": "primary"
        }
        audience = aud_map.get(audience_label, "high school")
        detail = st.slider("Detail level", 1, 5, 3, key="qs_detail")

        files = st.file_uploader(
            "Upload files (PDF, PPTX, JPG, PNG, TXT)",
            type=["pdf","pptx","jpg","jpeg","png","txt"],
            accept_multiple_files=True,
            key="qs_files",
        )

        if files and st.button("Generate Notes + Flashcards + Quiz", type="primary", key="qs_generate_btn"):
            # Choose destination: prefer latest created Topic if present, else Exam/Subject
            # (Users are prompted to create a Topic; if they skipped, still save under exam/subject.)
            subjects = _roots(ALL_FOLDERS)
            # Recompute ids in case of new creations after rerun:
            subject_id = next((s["id"] for s in subjects if s["name"] == st.session_state.get("qs_subject_pick")), subject_id)
            if subject_id:
                exams = [f for f in list_folders() if f.get("parent_id")==subject_id]
            else:
                exams = []
            if subject_id and st.session_state.get("qs_exam_pick") in [e["name"] for e in exams]:
                exam_id = next(e["id"] for e in exams if e["name"]==st.session_state.get("qs_exam_pick"))
                topics = [f for f in list_folders() if f.get("parent_id")==exam_id]
            else:
                topics = []

            # No old topics selectable — but if user created one just now, grab it by exact name:
            created_topic_name = st.session_state.get("qs_new_topic")
            topic_id = None
            if created_topic_name:
                for t in topics:
                    if t["name"] == created_topic_name:
                        topic_id = t["id"]; break

            dest_folder = topic_id or exam_id or subject_id or None

            progress = st.progress(0, text="Starting…")
            try:
                progress.progress(10, text="Extracting text…")
                text = extract_any(files)
                if not text.strip():
                    st.error("No text detected."); st.stop()

                progress.progress(35, text="Summarising with AI…")
                try:
                    data = summarize_text(text, audience=audience, detail=detail, subject=subject_hint)
                except TypeError:
                    try: data = summarize_text(text, audience=audience, detail=detail)
                    except TypeError: data = summarize_text(text, audience=audience)

                progress.progress(75, text="Saving items…")
                emoji = {"summary":"📄","flashcards":"🧠","quiz":"🧪"}
                title = data.get("title") or "Untitled"

                summary = save_item("summary", f"{emoji['summary']} {title}", data, dest_folder)
                summary_id = summary.get("id")
                flash_id = quiz_id = None

                if data.get("flashcards"):
                    flash = save_item("flashcards", f"{emoji['flashcards']} {title} • Flashcards",
                                      {"flashcards": data["flashcards"]}, dest_folder)
                    flash_id = flash.get("id")
                if data.get("exam_questions"):
                    quiz = save_item("quiz", f"{emoji['quiz']} {title} • Quiz",
                                     {"questions": data["exam_questions"]}, dest_folder)
                    quiz_id = quiz.get("id")

                progress.progress(100, text="Done!")
                st.success("Saved ✅")

                st.markdown("### Open")
                c1,c2,c3 = st.columns(3)
                if summary_id and c1.button("Open Notes", type="primary", key="qs_open_notes"):
                    _set_params(item=summary_id, resources=1); st.rerun()
                if flash_id and c2.button("Open Flashcards", key="qs_open_flash"):
                    _set_params(item=flash_id, resources=1); st.rerun()
                if quiz_id and c3.button("Open Quiz", key="qs_open_quiz"):
                    _set_params(item=quiz_id, resources=1); st.rerun()
            except Exception as e:
                st.error(f"Generation failed: {e}")

# ===== Study Resources =====
with tabs[1]:
    # Direct to the route page for consistency (so back buttons work everywhere)
    if st.button("Open Study Resources page", key="open_resources_tab"):
        _set_params(resources=1); st.rerun()
    st.caption("Use the button above to manage and open your saved resources with dropdown filters.")

