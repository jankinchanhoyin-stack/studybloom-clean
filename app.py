# app.py
# ---------------- Page config MUST be first ----------------
import streamlit as st
st.set_page_config(page_title="StudyBloom", page_icon="📚")

# ---------------- Imports ----------------
import sys, requests
from typing import Optional, List, Dict, Tuple

# local modules
from pdf_utils import extract_any
from llm import summarize_text, grade_free_answer, generate_quiz_from_notes
from auth_rest import (
    sign_in, sign_up, sign_out,
    save_item, list_items, get_item, move_item, delete_item,
    create_folder, list_folders, delete_folder, list_child_folders,
    save_quiz_attempt, list_quiz_attempts, list_quiz_attempts_for_items,
    save_flash_review, list_flash_reviews_for_items
)

st.caption(f"Python {sys.version.split()[0]} • Build: back-to-resources + sidebar-no-rename + open-fixes")

# ---------------- URL helpers ----------------
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

# ---------------- Supabase REST helpers (rename for main pages only) ----------------
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

# ---------------- Rendering helpers ----------------
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
        if st.button("🔁 Restart deck", key=f"{key_prefix}_restart_all"):
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
    if c3.button("✅ I knew it", key=f"{key_prefix}_ok"):
        if item_id and "sb_user" in st.session_state:
            try: save_flash_review(item_id, True)
            except Exception: pass
        st.session_state[f"{key_prefix}_order"].pop(idx)
        st.session_state[f"{key_prefix}_revealed"]=False
        if idx >= len(st.session_state[f"{key_prefix}_order"]):
            st.session_state[f"{key_prefix}_idx"] = max(0, len(st.session_state[f"{key_prefix}_order"])-1)
        st.rerun()
    if c4.button("❌ Show me again", key=f"{key_prefix}_bad"):
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
    if colg1.button("Submit answer", key=f"{key_prefix}_submit"):
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
    if c4.button("🎲 Generate another quiz", key=f"{key_prefix}_regen") and item_id:
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

# ---------------- Folder & progress utils ----------------
def build_tree(rows: List[dict]):
    nodes = {r["id"]:{**r,"children":[]} for r in rows}
    roots=[]
    for r in rows:
        pid = r.get("parent_id")
        if pid and pid in nodes: nodes[pid]["children"].append(nodes[r["id"]])
        else: roots.append(nodes[r["id"]])
    return roots, nodes

def compute_topic_progress(topic_folder_id: str) -> float:
    try:
        items = list_items(topic_folder_id, limit=500)
        quiz_ids = [it["id"] for it in items if it["kind"]=="quiz"]
        flash_ids = [it["id"] for it in items if it["kind"]=="flashcards"]
        quiz_score = 0.0
        if quiz_ids:
            attempts = list_quiz_attempts_for_items(quiz_ids)
            latest_per_quiz: Dict[str, Tuple[int,int]] = {}
            for at in attempts:
                qid = at["item_id"]
                if qid not in latest_per_quiz:
                    latest_per_quiz[qid] = (at["correct"], at["total"])
            if latest_per_quiz:
                ratios = [(c/t) if t else 0 for (c,t) in latest_per_quiz.values()]
                quiz_score = sum(ratios)/len(ratios)
        flash_score = 0.0
        if flash_ids:
            reviews = list_flash_reviews_for_items(flash_ids)
            if reviews:
                known = sum(1 for r in reviews if r.get("known"))
                flash_score = known / max(1, len(reviews))
        return 0.6*quiz_score + 0.4*flash_score
    except Exception:
        return 0.0

# ---------------- Sidebar: Auth ----------------
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

# ---------------- Fetch folders ----------------
if "sb_user" in st.session_state:
    try:
        ALL_FOLDERS = list_folders()
    except Exception as e:
        ALL_FOLDERS = []
        st.warning(f"Could not load folders: {e}")
else:
    ALL_FOLDERS = []

# ---------------- Sidebar tree (NO RENAME BUTTONS) ----------------
if "sb_user" in st.session_state and ALL_FOLDERS:
    st.sidebar.markdown("---")
    st.sidebar.subheader("📂 Your Library")
    emoji_item = {"summary": "📄", "flashcards": "🧠", "quiz": "🧪"}

    roots = [f for f in ALL_FOLDERS if not f.get("parent_id")]  # subjects
    for subj in roots:
        with st.sidebar.expander(f"📁 {subj['name']}", expanded=False):
            if st.button("Open subject", key=f"sb_open_subject_{subj['id']}"):
                _set_params(folder=subj["id"]); st.rerun()

            exams = [f for f in ALL_FOLDERS if f.get("parent_id") == subj["id"]]
            for ex in exams:
                ex_open = st.checkbox(f"🗂️ {ex['name']}", key=f"sb_toggle_exam_{ex['id']}", value=False)
                if st.button("Open exam", key=f"sb_open_exam_{ex['id']}"):
                    _set_params(folder=ex["id"]); st.rerun()

                if ex_open:
                    topics = [f for f in ALL_FOLDERS if f.get("parent_id") == ex["id"]]
                    for tp in topics:
                        st.markdown("&nbsp;&nbsp;&nbsp;&nbsp;🏷️ **{}**".format(tp["name"]), unsafe_allow_html=True)
                        if st.button("Open topic", key=f"sb_open_topic_{tp['id']}"):
                            _set_params(folder=tp["id"]); st.rerun()

                        # Items under topic
                        try:
                            its = list_items(tp["id"], limit=200)
                        except Exception:
                            its = []
                        for it in its:
                            icon = emoji_item.get(it["kind"], "📄")
                            st.markdown("&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{} {}".format(icon, it["title"]), unsafe_allow_html=True)
                            if st.button(f"Open {it['id']}", key=f"sb_open_item_{it['id']}"):
                                _set_params(item=it["id"], resources=1); st.rerun()

# ---------------- Route: dedicated Study Resources PAGE (so Back works) ----------------
params = _get_params()
if "resources" in params and "item" not in params and "sb_user" in st.session_state:
    # This renders the same content as the Study Resources tab, but as its own page.
    st.title("🧰 Study Resources")
    emoji = {"summary":"📄","flashcards":"🧠","quiz":"🧪"}
    try:
        ALL_FOLDERS = list_folders()
    except Exception:
        ALL_FOLDERS = []
    all_opt = ["(all folders)"] + [f["name"] for f in ALL_FOLDERS]
    folder_filter = st.selectbox("Show items in", all_opt, index=0, key="page_mi_filter_folder")
    filter_id = None if folder_filter=="(all folders)" else next(f["id"] for f in ALL_FOLDERS if f["name"]==folder_filter)
    try:
        items = list_items(filter_id, limit=200)
        if not items: st.caption("No items yet.")
        for it in items:
            icon = emoji.get(it["kind"], "📄")
            cols = st.columns([6,1,1])
            cols[0].markdown(f"{icon} **{it['title']}** — {it['created_at'][:16].replace('T',' ')}")
            if cols[1].button("Open", key=f"page_mi_open_{it['id']}"):
                _set_params(item=it["id"], resources=1); st.rerun()
            if cols[2].button("Delete", key=f"page_mi_del_{it['id']}"):
                try: delete_item(it["id"]); st.success("Deleted."); st.rerun()
                except Exception as e: st.error(f"Delete failed: {e}")
    except Exception as e:
        st.error(f"Load failed: {e}")
    st.stop()

# ---------------- Item PAGE (full page) ----------------
if "item" in params and "sb_user" in st.session_state:
    item_id = params.get("item")
    if isinstance(item_id, list): item_id = item_id[0]
    try:
        full = get_item(item_id)
        kind = full.get("kind"); title = full.get("title") or kind.title()
        st.title(title)

        # Back goes to dedicated Study Resources page
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

# ---------------- Tabs (order requested) ----------------
tabs = st.tabs(["Quick Study", "Subjects", "Exams", "Topics", "Study Resources"])

# ========== Quick Study ==========
with tabs[0]:
    st.title("⚡ Quick Study")
    if "sb_user" not in st.session_state:
        st.info("Log in to save your study materials.")
    else:
        def _roots(rows): return [r for r in rows if not r.get("parent_id")]
        subjects = _roots(ALL_FOLDERS)
        subj_names = [s["name"] for s in subjects]

        # SUBJECT (create vs existing)
        st.markdown("### Subject")
        pending_subject = st.session_state.pop("pending_subject", None)
        create_new_subject = st.checkbox("Create a new subject", key="qs_make_new_subject", value=False)

        subject_id = None
        if create_new_subject:
            new_subject = st.text_input("New subject name", placeholder="e.g., A-Level Mathematics", key="qs_new_subject")
            c1,c2 = st.columns([1,1])
            if c1.button("Save subject", key="qs_save_subject_btn"):
                name = (new_subject or "").strip()
                if not name:
                    st.warning("Enter a subject name.")
                else:
                    created = create_folder(name, None)
                    st.session_state["pending_subject"] = created["name"]
                    st.session_state["qs_make_new_subject"] = False
                    st.rerun()
            if c2.button("Cancel", key="qs_cancel_subject_btn"):
                st.session_state["qs_make_new_subject"] = False; st.rerun()
        else:
            subj_index = 0
            subject_options = ["— select a subject —"] + subj_names
            if pending_subject and pending_subject in subj_names:
                subj_index = 1 + subj_names.index(pending_subject)
            subj_pick = st.selectbox("Use existing subject", subject_options,
                                     index=subj_index if subj_names else 0, key="qs_subject_pick")
            if subj_pick != "— select a subject —" and subj_pick in subj_names:
                subject_id = next(s["id"] for s in subjects if s["name"] == subj_pick)

        # EXAM
        st.markdown("### Exam")
        exam_id = None
        if subject_id:
            exams = [f for f in ALL_FOLDERS if f.get("parent_id")==subject_id]
            exam_names = [e["name"] for e in exams]
            pending_exam = st.session_state.pop("pending_exam", None)
            create_new_exam = st.checkbox("Create a new exam", key="qs_make_new_exam", value=False)
            if create_new_exam:
                new_exam = st.text_input("New exam name", placeholder="e.g., IGCSE May 2026", key="qs_new_exam")
                c1,c2 = st.columns([1,1])
                if c1.button("Save exam", key="qs_save_exam_btn"):
                    name = (new_exam or "").strip()
                    if not name:
                        st.warning("Enter an exam name.")
                    else:
                        created = create_folder(name, subject_id)
                        st.session_state["pending_exam"] = created["name"]
                        st.session_state["qs_make_new_exam"] = False
                        st.rerun()
                if c2.button("Cancel", key="qs_cancel_exam_btn"):
                    st.session_state["qs_make_new_exam"] = False; st.rerun()
            else:
                exam_index = 0
                exam_options = ["— select an exam —"] + exam_names
                if pending_exam and pending_exam in exam_names:
                    exam_index = 1 + exam_names.index(pending_exam)
                ex_pick = st.selectbox("Use existing exam", exam_options,
                                       index=exam_index if exam_names else 0, key="qs_exam_pick")
                if ex_pick != "— select an exam —" and ex_pick in exam_names:
                    exam_id = next(e["id"] for e in exams if e["name"]==ex_pick)
        else:
            st.caption("Pick or create a Subject first to reveal the Exam chooser.")

        # TOPIC
        st.markdown("### Topic")
        topic_id = None
        if exam_id:
            topics = [f for f in ALL_FOLDERS if f.get("parent_id")==exam_id]
            topic_names = [t["name"] for t in topics]
            pending_topic = st.session_state.pop("pending_topic", None)
            create_new_topic = st.checkbox("Create a new topic", key="qs_make_new_topic", value=False)
            if create_new_topic:
                new_topic = st.text_input("New topic name", placeholder="e.g., Differentiation", key="qs_new_topic")
                c1,c2 = st.columns([1,1])
                if c1.button("Save topic", key="qs_save_topic_btn"):
                    name = (new_topic or "").strip()
                    if not name:
                        st.warning("Enter a topic name.")
                    else:
                        created = create_folder(name, exam_id)
                        st.session_state["pending_topic"] = created["name"]
                        st.session_state["qs_make_new_topic"] = False
                        st.rerun()
                if c2.button("Cancel", key="qs_cancel_topic_btn"):
                    st.session_state["qs_make_new_topic"] = False; st.rerun()
            else:
                topic_index = 0
                topic_options = ["— select a topic —"] + topic_names
                if pending_topic and pending_topic in topic_names:
                    topic_index = 1 + topic_names.index(pending_topic)
                tp_pick = st.selectbox("Use existing topic", topic_options,
                                       index=topic_index if topic_names else 0, key="qs_topic_pick")
                if tp_pick != "— select a topic —" and tp_pick in topic_names:
                    topic_id = next(t["id"] for t in topics if t["name"]==tp_pick)
        else:
            st.caption("Pick or create an Exam first to reveal the Topic chooser.")

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
            if not (subject_id or exam_id or topic_id):
                st.info("Tip: create/select a Subject → Exam → Topic for tidy organisation.")
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

                # destination priority: Topic > Exam > Subject > None
                dest_folder = topic_id or exam_id or subject_id or None

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

# ========== Subjects ==========
with tabs[1]:
    st.title("📚 Subjects")
    if "sb_user" not in st.session_state:
        st.info("Log in to manage subjects.")
    else:
        roots, _ = build_tree(ALL_FOLDERS)
        if not roots:
            st.caption("No subjects yet. Create one in Quick Study.")
        for s in roots:
            cols = st.columns([6,1,1])
            cols[0].markdown(f"📁 **{s['name']}**")
            if cols[1].button("Open", key=f"subj_open_{s['id']}"):
                _set_params(folder=s["id"]); st.rerun()
            # optional rename on main tabs (not sidebar)
            if not st.session_state.get(f"edit_folder_{s['id']}", False):
                if cols[2].button("Rename", key=f"subj_btn_rename_{s['id']}"):
                    st.session_state[f"edit_folder_{s['id']}"]=True; st.rerun()
            else:
                newn = st.text_input("New subject name", value=s["name"], key=f"subj_rn_{s['id']}")
                c1,c2 = st.columns(2)
                if c1.button("Save", key=f"subj_save_{s['id']}"):
                    try: rename_folder(s["id"], newn.strip()); st.session_state[f"edit_folder_{s['id']}"]=False; st.rerun()
                    except Exception as e: st.error(f"Rename failed: {e}")
                if c2.button("Cancel", key=f"subj_cancel_{s['id']}"):
                    st.session_state[f"edit_folder_{s['id']}"]=False; st.rerun()

# ========== Exams ==========
with tabs[2]:
    st.title("📝 Exams")
    if "sb_user" not in st.session_state:
        st.info("Log in to manage exams.")
    else:
        roots, _ = build_tree(ALL_FOLDERS)
        subj_names = [s["name"] for s in roots]
        if not subj_names:
            st.caption("Create a subject first in Quick Study.")
        else:
            subj_pick = st.selectbox("Subject", subj_names, key="exams_subj_pick")
            subj_id = next(s["id"] for s in roots if s["name"]==subj_pick)
            exams = [f for f in ALL_FOLDERS if f.get("parent_id")==subj_id]
            if not exams:
                st.caption("No exams under this subject yet (add via Quick Study).")
            for e in exams:
                cols = st.columns([6,1,1])
                cols[0].markdown(f"🗂️ **{e['name']}**")
                if cols[1].button("Open", key=f"exam_open_{e['id']}"):
                    _set_params(folder=e["id"]); st.rerun()
                if not st.session_state.get(f"edit_folder_{e['id']}", False):
                    if cols[2].button("Rename", key=f"exam_btn_rename_{e['id']}"):
                        st.session_state[f"edit_folder_{e['id']}"]=True; st.rerun()
                else:
                    newn = st.text_input("New exam name", value=e["name"], key=f"exam_rn_{e['id']}")
                    c1,c2 = st.columns(2)
                    if c1.button("Save", key=f"exam_save_{e['id']}"):
                        try: rename_folder(e["id"], newn.strip()); st.session_state[f"edit_folder_{e['id']}"]=False; st.rerun()
                        except Exception as ex: st.error(f"Rename failed: {ex}")
                    if c2.button("Cancel", key=f"exam_cancel_{e['id']}"):
                        st.session_state[f"edit_folder_{e['id']}"]=False; st.rerun()

# ========== Topics ==========
with tabs[3]:
    st.title("🏷️ Topics")
    if "sb_user" not in st.session_state:
        st.info("Log in to manage topics.")
    else:
        roots, _ = build_tree(ALL_FOLDERS)
        subj_names = [s["name"] for s in roots]
        if not subj_names:
            st.caption("Create a subject first in Quick Study.")
        else:
            subj_pick = st.selectbox("Subject", subj_names, key="topics_subj_pick")
            subj_id = next(s["id"] for s in roots if s["name"]==subj_pick)
            exams = [f for f in ALL_FOLDERS if f.get("parent_id")==subj_id]
            exam_names = [e["name"] for e in exams]
            if not exam_names:
                st.caption("Create an exam under this subject first.")
            else:
                ex_pick = st.selectbox("Exam", exam_names, key="topics_exam_pick")
                exam_id = next(e["id"] for e in exams if e["name"]==ex_pick)
                topics = [f for f in ALL_FOLDERS if f.get("parent_id")==exam_id]
                if not topics:
                    st.caption("No topics yet. Add one in Quick Study (Topic section).")
                for t in topics:
                    cols = st.columns([6,1,1])
                    cols[0].markdown(f"🏷️ **{t['name']}**")
                    if cols[1].button("Open", key=f"topic_open_{t['id']}"):
                        _set_params(folder=t["id"]); st.rerun()
                    if not st.session_state.get(f"edit_folder_{t['id']}", False):
                        if cols[2].button("Rename", key=f"topic_btn_rename_{t['id']}"):
                            st.session_state[f"edit_folder_{t['id']}"]=True; st.rerun()
                    else:
                        newn = st.text_input("New topic name", value=t["name"], key=f"topic_rn_{t['id']}")
                        c1,c2 = st.columns(2)
                        if c1.button("Save", key=f"topic_save_{t['id']}"):
                            try: rename_folder(t["id"], newn.strip()); st.session_state[f"edit_folder_{t['id']}"]=False; st.rerun()
                            except Exception as ex: st.error(f"Rename failed: {ex}")
                        if c2.button("Cancel", key=f"topic_cancel_{t['id']}"):
                            st.session_state[f"edit_folder_{t['id']}"]=False; st.rerun()

# ========== Study Resources ==========
with tabs[4]:
    st.title("🧰 Study Resources")
    if "sb_user" not in st.session_state:
        st.info("Log in to manage resources.")
    else:
        emoji = {"summary":"📄","flashcards":"🧠","quiz":"🧪"}
        all_opt = ["(all folders)"] + [f["name"] for f in ALL_FOLDERS]
        folder_filter = st.selectbox("Show items in", all_opt, index=0, key="mi_filter_folder")
        filter_id = None if folder_filter=="(all folders)" else next(f["id"] for f in ALL_FOLDERS if f["name"]==folder_filter)
        try:
            items = list_items(filter_id, limit=200)
            if not items: st.caption("No items yet.")
            for it in items:
                icon = emoji.get(it["kind"], "📄")
                cols = st.columns([6,1,1,1])
                cols[0].markdown(f"{icon} **{it['title']}** — {it['created_at'][:16].replace('T',' ')}")
                if cols[1].button("Open", key=f"mi_open_{it['id']}"):
                    _set_params(item=it["id"], resources=1); st.rerun()
                if not st.session_state.get(f"edit_item_{it['id']}", False):
                    if cols[2].button("Rename", key=f"mi_btn_rename_{it['id']}"):
                        st.session_state[f"edit_item_{it['id']}"]=True; st.rerun()
                else:
                    newt = st.text_input("New title", value=it["title"], key=f"mi_rn_{it['id']}")
                    c1,c2 = st.columns(2)
                    if c1.button("Save", key=f"mi_save_{it['id']}"):
                        try: rename_item(it["id"], newt.strip()); st.session_state[f"edit_item_{it['id']}"]=False; st.rerun()
                        except Exception as e: st.error(f"Rename failed: {e}")
                    if c2.button("Cancel", key=f"mi_cancel_{it['id']}"):
                        st.session_state[f"edit_item_{it['id']}"]=False; st.rerun()
                if cols[3].button("Delete", key=f"mi_del_{it['id']}"):
                    try: delete_item(it["id"]); st.success("Deleted."); st.rerun()
                    except Exception as e: st.error(f"Delete failed: {e}")
        except Exception as e:
            st.error(f"Load failed: {e}")
