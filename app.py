# app.py
import streamlit as st
import sys, random, requests
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

# ---------- Page config ----------
st.set_page_config(page_title="StudyBloom", page_icon="📚")
st.caption(f"Python: {sys.version.split()[0]} • Build: 2025-11-10 auto-select folders")

# ---------- URL helpers ----------
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

# ---------- Rendering helpers ----------
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
        for e in data["examples"]: st.markdown(f"- {e}")
    if data.get("common_pitfalls"):
        st.markdown("## Common Pitfalls")
        for p in data["common_pitfalls"]: st.markdown(f"- {p}")

# ---- Flashcards with ✅/❌ + spaced re-ask + DB logging ----
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

# ---- Quiz with free-text grading ----
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
                        _set_params(item=created.get("id")); st.rerun()
                    else:
                        st.warning("Could not generate a new quiz from notes.")
                else:
                    st.info("No summary found in this folder to generate from.")
            else:
                st.info("Folder not found for this quiz.")
        except Exception as e:
            st.error(f"Re-generate failed: {e}")

    if item_id and "sb_user" in st.session_state:
        try:
            att = list_quiz_attempts(item_id, limit=5)
            if att:
                st.markdown("#### Recent Attempts")
                for a in att:
                    st.markdown(f"- {a['created_at'][:16].replace('T',' ')} — **{a['correct']}/{a['total']}**")
        except Exception:
            pass

# ---------- Auth sidebar ----------
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
    st.sidebar.markdown("---")
    st.sidebar.subheader("📂 Subjects")
    try:
        _sidebar_folders = list_folders()
        _roots = [f for f in _sidebar_folders if not f.get("parent_id")]
        for n in _roots:
            if st.sidebar.button(f"• {n['name']}", key=f"subject_{n['id']}"):
                _set_params(folder=n["id"]); st.rerun()
        with st.sidebar.expander("New Subject"):
            _new_subj = st.text_input("Subject name", key="new_root_name", placeholder="e.g., A-Level Mathematics")
            if st.button("Create subject", use_container_width=True, key="create_root_btn"):
                if not (_new_subj or "").strip(): st.warning("Enter a name.")
                else:
                    try:
                        created = create_folder(_new_subj.strip(), None)
                        _set_params(folder=created["id"]); st.rerun()
                    except Exception as e: st.error(f"Create failed: {e}")
    except Exception as e:
        st.sidebar.info("Create your first subject.")
        st.sidebar.caption(str(e))

# ---------- Folder helpers ----------
def build_tree(rows: List[dict]):
    nodes = {r["id"]:{**r,"children":[]} for r in rows}
    roots=[]
    for r in rows:
        pid = r.get("parent_id")
        if pid and pid in nodes: nodes[pid]["children"].append(nodes[r["id"]])
        else: roots.append(nodes[r["id"]])
    return roots, nodes

def compute_topic_progress(topic_folder_id: str) -> float:
    """0..1 based on quiz attempts + flashcard recall (40% flash, 60% quiz)."""
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

# ---------- ROUTES ----------
params = _get_params()

# Safe folder load
try:
    all_folders = list_folders() if "sb_user" in st.session_state else []
except Exception as e:
    all_folders = []
    if "sb_user" in st.session_state:
        st.warning(f"Could not load folders: {e}")

# Folder route
if "folder" in params and "sb_user" in st.session_state:
    folder_id = params.get("folder")
    if isinstance(folder_id, list): folder_id = folder_id[0]
    this = next((f for f in all_folders if f["id"]==folder_id), None)
    st.title(this["name"] if this else "Folder")
    if st.button("← Back to Home", key="folder_back_btn"): _clear_params(); st.rerun()

    # Subfolders
    try:
        subs = list_child_folders(folder_id)
        if subs:
            st.subheader("Subfolders")
            for s in subs:
                progress = compute_topic_progress(s["id"])
                c1,c2,c3 = st.columns([5,2,1])
                c1.markdown(f"📁 **{s['name']}**")
                c2.progress(progress, text=f"{int(progress*100)}%")
                if c3.button("Open", key=f"open_btn_{s['id']}"):
                    _set_params(folder=s["id"]); st.rerun()
    except Exception as e:
        st.info(f"No subfolders or error: {e}")

    # Items
    emoji = {"summary":"📄","flashcards":"🧠","quiz":"🧪"}
    try:
        items = list_items(folder_id, limit=200)
        st.subheader("Items")
        if not items: st.caption("No items yet.")
        for it in items:
            icon = emoji.get(it["kind"], "📄")
            cols = st.columns([6,1,1])
            cols[0].markdown(f"{icon} **{it['title']}** — {it['created_at'][:16].replace('T',' ')}")
            if cols[1].button("Open", key=f"open_item_{it['id']}"): _set_params(item=it["id"]); st.rerun()
            if cols[2].button("Delete", key=f"del_{it['id']}"):
                try: delete_item(it["id"]); st.success("Deleted."); st.rerun()
                except Exception as e: st.error(f"Delete failed: {e}")
    except Exception as e:
        st.error(f"Load failed: {e}")
    st.stop()

# Item route
if "item" in params and "sb_user" in st.session_state:
    item_id = params.get("item")
    if isinstance(item_id, list): item_id = item_id[0]
    try:
        full = get_item(item_id)
        kind = full.get("kind"); title = full.get("title") or kind.title()
        st.title(title)
        if st.button("← Back", key="item_back_btn"): _clear_params(); st.rerun()

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
        if st.button("← Back", key="item_back_btn2"): _clear_params(); st.rerun()
    st.stop()

# ---------- Home tabs ----------
tabs = st.tabs(["Exam Planner", "Quick Study", "Manage Items"])

# --- Exam Planner ---
with tabs[0]:
    st.title("🗂️ Exam Planner")
    if "sb_user" not in st.session_state:
        st.info("Log in to use Exam Planner.")
    else:
        roots, node_map = build_tree(all_folders)
        subjects = roots
        subj_names = [s["name"] for s in subjects]
        subj = st.selectbox("Subject", ["(create new)"] + subj_names, key="exam_subject")
        new_subject = st.text_input("New subject", placeholder="e.g., A-Level Mathematics", key="exam_new_subject")

        subject_id = None
        if subj != "(create new)" and subj_names:
            subject_id = next(s["id"] for s in subjects if s["name"] == subj)
        elif st.button("Create subject", key="exam_create_subject_btn"):
            if not (new_subject or "").strip():
                st.warning("Enter a subject name.")
            else:
                created = create_folder(new_subject.strip(), None)
                # Auto-select newly created subject (no page nav)
                st.session_state["exam_subject"] = created["name"]
                st.session_state["exam_exam"] = "(create new)"
                st.session_state["exam_topic"] = "(create new)"
                st.rerun()

        if subject_id:
            exams = [f for f in all_folders if f.get("parent_id")==subject_id]
            exam_names = [e["name"] for e in exams]
            ex = st.selectbox("Exam", ["(create new)"] + exam_names, key="exam_exam")
            new_exam = st.text_input("New exam", placeholder="e.g., May 2026", key="exam_new_exam")

            exam_id = None
            if ex != "(create new)" and exam_names:
                exam_id = next(e["id"] for e in exams if e["name"]==ex)
            elif st.button("Create exam", key="exam_create_exam_btn"):
                if not (new_exam or "").strip():
                    st.warning("Enter an exam name.")
                else:
                    created = create_folder(new_exam.strip(), subject_id)
                    # Auto-select new exam
                    st.session_state["exam_exam"] = created["name"]
                    st.session_state["exam_topic"] = "(create new)"
                    st.rerun()

            if exam_id:
                topics = [f for f in all_folders if f.get("parent_id")==exam_id]
                topic_names = [t["name"] for t in topics]
                tp = st.selectbox("Topic", ["(create new)"] + topic_names, key="exam_topic")
                new_topic = st.text_input("New topic", placeholder="e.g., Differentiation", key="exam_new_topic")

                topic_id = None
                if tp != "(create new)" and topic_names:
                    topic_id = next(t["id"] for t in topics if t["name"]==tp)
                elif st.button("Create topic", key="exam_create_topic_btn"):
                    if not (new_topic or "").strip():
                        st.warning("Enter a topic name.")
                    else:
                        created = create_folder(new_topic.strip(), exam_id)
                        # Auto-select new topic
                        st.session_state["exam_topic"] = created["name"]
                        st.rerun()

                if topic_id:
                    st.markdown("---")
                    audience_label = st.selectbox(
                        "Audience style",
                        ["University", "A-Level", "A-Level / IB", "GCSE", "HKDSE", "Primary"],
                        index=1,
                        key="exam_audience_label",
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
                    detail = st.slider("Detail level", 1, 5, 3, key="exam_detail")
                    files = st.file_uploader(
                        "Upload files (PDF, PPTX, JPG, PNG, TXT) for this topic",
                        type=["pdf","pptx","jpg","jpeg","png","txt"],
                        accept_multiple_files=True,
                        key="exam_files",
                    )

                    if files and st.button("Generate Notes + Flashcards + Quiz", type="primary", key="exam_generate_btn"):
                        with st.spinner("Extracting text…"):
                            try: text = extract_any(files)
                            except Exception as e: st.error(f"Extraction failed: {e}"); st.stop()
                        if not text.strip(): st.error("No text found."); st.stop()

                        subject_name = next((s["name"] for s in subjects if s["id"]==subject_id), "General")
                        with st.spinner("Summarizing with AI…"):
                            try:
                                data = summarize_text(text, audience=audience, detail=detail, subject=subject_name)
                            except TypeError:
                                try: data = summarize_text(text, audience=audience, detail=detail)
                                except TypeError: data = summarize_text(text, audience=audience)
                            except Exception as e:
                                st.error(f"Summarization failed: {e}"); st.stop()

                        emoji = {"summary":"📄","flashcards":"🧠","quiz":"🧪"}
                        summary_id = flash_id = quiz_id = None
                        try:
                            title = data.get("title") or f"{new_topic or tp or 'Topic'}"
                            summary_id = save_item("summary", f"{emoji['summary']} {title}", data, topic_id).get("id")
                            if data.get("flashcards"):
                                flash_id = save_item("flashcards", f"{emoji['flashcards']} {title} • Flashcards",
                                                     {"flashcards": data["flashcards"]}, topic_id).get("id")
                            if data.get("exam_questions"):
                                quiz_id = save_item("quiz", f"{emoji['quiz']} {title} • Quiz",
                                                    {"questions": data["exam_questions"]}, topic_id).get("id")
                            st.success("Saved to Topic ✅")
                        except Exception as e:
                            st.error(f"Save failed: {e}")

                        st.markdown("### Open Your Materials")
                        c1,c2,c3 = st.columns(3)
                        if summary_id and c1.button("Open Notes Page", type="primary", key="exam_open_notes"): _set_params(item=summary_id); st.rerun()
                        if flash_id and c2.button("Open Flashcards Page", key="exam_open_flash"): _set_params(item=flash_id); st.rerun()
                        if quiz_id and c3.button("Open Quiz Page", key="exam_open_quiz"): _set_params(item=quiz_id); st.rerun()

# --- Quick Study ---
with tabs[1]:
    st.title("⚡ Quick Study")
    if "sb_user" not in st.session_state:
        st.info("Log in to save your study materials.")
    else:
        dest_id=None
        options = ["(no folder)"] + [f["name"] for f in all_folders]
        pick = st.selectbox("Save to folder", options, index=0, key="qs_dest_folder")
        if pick != "(no folder)":
            dest_id = next(f["id"] for f in all_folders if f["name"]==pick)

        audience_label = st.selectbox("Audience", ["University", "A-Level", "A-Level / IB", "GCSE", "HKDSE", "Primary"], index=0, key="qs_audience_label")
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
        subject_hint = st.text_input("Subject (e.g., Mathematics)", value="General", key="qs_subject_hint")

        files = st.file_uploader(
            "Upload files (PDF, PPTX, JPG, PNG, TXT)",
            type=["pdf","pptx","jpg","jpeg","png","txt"],
            accept_multiple_files=True,
            key="qs_files",
        )
        if files and st.button("Generate & Save", type="primary", key="qs_generate_btn"):
            with st.spinner("Extracting text…"):
                try: text = extract_any(files)
                except Exception as e: st.error(f"Extraction failed: {e}"); st.stop()
            if not text.strip(): st.error("No text found."); st.stop()

            with st.spinner("Summarizing with AI…"):
                try:
                    data = summarize_text(text, audience=audience, detail=detail, subject=subject_hint)
                except TypeError:
                    try: data = summarize_text(text, audience=audience, detail=detail)
                    except TypeError: data = summarize_text(text, audience=audience)
                except Exception as e:
                    st.error(f"Summarization failed: {e}"); st.stop()

            emoji = {"summary":"📄","flashcards":"🧠","quiz":"🧪"}
            summary_id=flash_id=quiz_id=None
            try:
                title = data.get("title") or "Untitled"
                summary_id = save_item("summary", f"{emoji['summary']} {title}", data, dest_id).get("id")
                if data.get("flashcards"):
                    flash_id = save_item("flashcards", f"{emoji['flashcards']} {title} • Flashcards",
                                         {"flashcards": data["flashcards"]}, dest_id).get("id")
                if data.get("exam_questions"):
                    quiz_id = save_item("quiz", f"{emoji['quiz']} {title} • Quiz",
                                        {"questions": data["exam_questions"]}, dest_id).get("id")
                st.success("Saved ✅")
            except Exception as e:
                st.error(f"Save failed: {e}")

            st.markdown("### Open Your Materials")
            c1,c2,c3 = st.columns(3)
            if summary_id and c1.button("Open Notes Page", type="primary", key="qs_open_notes"): _set_params(item=summary_id); st.rerun()
            if flash_id and c2.button("Open Flashcards Page", key="qs_open_flash"): _set_params(item=flash_id); st.rerun()
            if quiz_id and c3.button("Open Quiz Page", key="qs_open_quiz"): _set_params(item=quiz_id); st.rerun()

# --- Manage Items ---
with tabs[2]:
    st.title("🧰 Manage Items")
    if "sb_user" not in st.session_state:
        st.info("Log in to manage.")
    else:
        emoji = {"summary":"📄","flashcards":"🧠","quiz":"🧪"}
        all_opt = ["(all folders)"] + [f["name"] for f in all_folders]
        folder_filter = st.selectbox("Show items in", all_opt, index=0, key="mi_filter_folder")
        filter_id = None if folder_filter=="(all folders)" else next(f["id"] for f in all_folders if f["name"]==folder_filter)
        try:
            items = list_items(filter_id, limit=200)
            if not items: st.caption("No items yet.")
            for it in items:
                icon = emoji.get(it["kind"], "📄")
                cols = st.columns([6,1,1])
                cols[0].markdown(f"{icon} **{it['title']}** — {it['created_at'][:16].replace('T',' ')}")
                if cols[1].button("Open", key=f"mi_open_{it['id']}"): _set_params(item=it["id"]); st.rerun()
                if cols[2].button("Delete", key=f"mi_del_{it['id']}"):
                    try: delete_item(it["id"]); st.success("Deleted."); st.rerun()
                    except Exception as e: st.error(f"Delete failed: {e}")
        except Exception as e:
            st.error(f"Load failed: {e}")
