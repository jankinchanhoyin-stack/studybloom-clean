# app.py
import streamlit as st
import sys, random, requests
from typing import Optional, List, Dict, Tuple

from pdf_utils import extract_any
from llm import summarize_text, grade_free_answer, generate_quiz_from_notes
from auth_rest import (
    sign_in, sign_up, sign_out, get_current_user, refresh_user,
    update_user_metadata, change_password,
    save_item, list_items, get_item, move_item, delete_item,
    create_folder, list_folders, delete_folder, list_child_folders,
    save_quiz_attempt, list_quiz_attempts, list_quiz_attempts_for_items,
    save_flash_review, list_flash_reviews_for_items
)

# ---------- Page config ----------
st.set_page_config(page_title="StudyBloom", page_icon="📚")
st.caption(f"Python: {sys.version.split()[0]} • Build: auth-modal-account-2025-11-11")

# ---------- URL helpers ----------
def _get_params() -> Dict[str, str]:
    try: return dict(st.query_params)
    except Exception: return st.experimental_get_query_params()

def _set_params(**kwargs):
    try: st.query_params.clear(); st.query_params.update(kwargs)
    except Exception: st.experimental_set_query_params(**kwargs)

def _clear_params(): _set_params()

# ---------- Layout: top bar ----------
def top_bar():
    cols = st.columns([6,2,2])
    with cols[0]:
        st.markdown("### 📚 StudyBloom")
    user = get_current_user()
    if user:
        with cols[1]:
            if st.button("My Account", key="btn_account"):
                _set_params(account="1"); st.rerun()
        with cols[2]:
            if st.button("Sign out", key="btn_signout"):
                sign_out(); _clear_params(); st.rerun()
    else:
        with cols[1]:
            if st.button("Log in", key="btn_login_open"):
                st.session_state["auth_modal_mode"] = "login"
                st.session_state["auth_modal_open"] = True
        with cols[2]:
            if st.button("Sign up", key="btn_signup_open"):
                st.session_state["auth_modal_mode"] = "signup"
                st.session_state["auth_modal_open"] = True

# ---------- Auth modal ----------
def show_auth_modal(default_mode: str = "login"):
    mode = st.session_state.get("auth_modal_mode", default_mode)
    @st.experimental_dialog("Welcome to StudyBloom")
    def _dialog():
        tabs = st.tabs(["Log in", "Sign up"])
        # Log in
        with tabs[0]:
            st.write("Log in to save, organize and track your study.")
            email = st.text_input("Email", key="dlg_login_email")
            pwd = st.text_input("Password", type="password", key="dlg_login_pwd")
            if st.button("Log in", type="primary", key="dlg_login_btn"):
                try:
                    sign_in(email, pwd)
                    st.success("Logged in!")
                    st.rerun()
                except requests.HTTPError as e:
                    st.error(getattr(e.response, "text", str(e)))
                except Exception as e:
                    st.error(str(e))
        # Sign up
        with tabs[1]:
            st.write("Create your account")
            disp = st.text_input("Display name", key="dlg_disp")
            uname = st.text_input("Username", key="dlg_uname")
            email2 = st.text_input("Email", key="dlg_email")
            pwd2 = st.text_input("Password", type="password", key="dlg_pwd")
            if st.button("Create account", type="primary", key="dlg_signup_btn"):
                try:
                    sign_up(email2, pwd2, display_name=disp or None, username=uname or None)
                    st.success("Sign-up successful. Check your email to confirm, then log in.")
                except requests.HTTPError as e:
                    st.error(getattr(e.response, "text", str(e)))
                except Exception as e:
                    st.error(str(e))
    _dialog()

# ---------- Common renderers (notes/flashcards/quiz/progress) ----------
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
            st.session_state[f"{key_prefix}_order"]=list(range(len(flashcards)))
            st.session_state[f"{key_prefix}_idx"]=0
            st.session_state[f"{key_prefix}_revealed"]=False
            st.session_state[f"{key_prefix}_wrong_counts"]={}
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
                        _set_params(item=created.get("id")); st.rerun()
                    else:
                        st.warning("Could not generate a new quiz from notes.")
                else:
                    st.info("No summary found in this folder to generate from.")
            else:
                st.info("Folder not found for this quiz.")
        except Exception as e:
            st.error(f"Re-generate failed: {e}")

# ---------- Progress computation ----------
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

# ---------- App routes ----------
top_bar()
params = _get_params()

# Show auth modal on entry if not logged in (and not on account page)
if not get_current_user() and params.get("account") is None and not st.session_state.get("auth_modal_shown_once"):
    st.session_state["auth_modal_shown_once"] = True
    st.session_state.setdefault("auth_modal_mode", "login")
    st.session_state["auth_modal_open"] = True

if st.session_state.get("auth_modal_open") and not get_current_user():
    show_auth_modal(st.session_state.get("auth_modal_mode","login"))
    st.session_state["auth_modal_open"] = False  # it will re-open if user clicks top-right buttons

# My Account page
if params.get("account"):
    st.markdown("#### ← Back")
    if st.button("Back", key="acc_back"):
        _clear_params(); st.rerun()
    user = get_current_user()
    if not user:
        st.info("Please log in first."); st.stop()
    meta = (user.get("user_metadata") or {})
    st.header("My Account")
    st.write(f"**Email:** {user.get('email','')}")

    disp = st.text_input("Display name", value=meta.get("display_name",""))
    uname = st.text_input("Username", value=meta.get("username",""))
    if st.button("Save profile"):
        try:
            update_user_metadata(display_name=disp, username=uname)
            st.success("Profile updated.")
        except requests.HTTPError as e:
            st.error(getattr(e.response, "text", str(e)))
        except Exception as e:
            st.error(str(e))

    st.subheader("Change password")
    p1 = st.text_input("New password", type="password")
    p2 = st.text_input("Confirm new password", type="password")
    if st.button("Update password"):
        if not p1 or p1 != p2:
            st.error("Passwords do not match.")
        else:
            try:
                change_password(p1)
                st.success("Password updated.")
            except requests.HTTPError as e:
                st.error(getattr(e.response, "text", str(e)))
            except Exception as e:
                st.error(str(e))
    st.stop()

# ---------------- Tabs ----------------
view = (_get_params().get("view") or [""])[0] if isinstance(_get_params().get("view"), list) else _get_params().get("view")
default_idx = 2 if view == "all" else (1 if view == "resources" else 0)
tabs = st.tabs(["Quick Study", "Resources", "All Resources"])

# ===== Quick Study =====
with tabs[0]:
    st.title("⚡ Quick Study")
    if "sb_user" not in st.session_state:
        st.info("Log in to save your study materials.")
    else:
        subjects = _roots(ALL_FOLDERS); subj_names = [s["name"] for s in subjects]

        # Subject
        st.markdown("### Subject")
        make_new_subject = st.checkbox("Create a new subject", key="qs_make_new_subject", value=False)
        subject_id = None
        if make_new_subject:
            new_subject = st.text_input("New subject name", placeholder="e.g., A-Level Mathematics", key="qs_new_subject")
            if st.button("Save subject", key="qs_save_subject_btn"):
                name = (new_subject or "").strip()
                if not name: st.warning("Enter a subject name.")
                elif name.lower() in {n.lower() for n in subj_names}:
                    st.error("This subject already exists. Please use a different name.")
                else:
                    create_folder(name, None); st.success("Subject created."); st.rerun()
        else:
            subj_pick = st.selectbox("Use existing subject", ["— select —"]+subj_names, key="qs_subject_pick")
            if subj_pick in subj_names:
                subject_id = next(s["id"] for s in subjects if s["name"]==subj_pick)

        # Exam
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
                    if not name: st.warning("Enter an exam name.")
                    elif name.lower() in {n.lower() for n in exam_names}:
                        st.error("This exam already exists under that subject.")
                    else:
                        create_folder(name, subject_id); st.success("Exam created."); st.rerun()
            else:
                ex_pick = st.selectbox("Use existing exam", ["— select —"]+exam_names, key="qs_exam_pick")
                if ex_pick in exam_names:
                    exam_id = next(e["id"] for e in exams if e["name"]==ex_pick)
        else:
            st.caption("Pick or create a Subject first to reveal Exams.")

        # Topic: Always new (no save yet)
        st.markdown("### Topic")
        topic_name_input = ""
        if exam_id:
            topic_name_input = st.text_input("New topic name", placeholder="e.g., Differentiation", key="qs_new_topic")
        else:
            st.caption("Pick or create an Exam first to add a Topic.")

        st.markdown("---")
        st.markdown("**Subject (free text, improves accuracy & quality):**")
        subject_hint = st.text_input(
            "e.g., Mathematics (Calculus), Biology (Cell Division), History (Cold War)",
            value="General",
            key="qs_subject_hint"
        )

        audience_label = st.selectbox("Audience", ["University","A-Level","IB","GCSE","HKDSE","Primary"], index=0, key="qs_audience_label")
        aud_map = {"University":"university","A-Level":"A-Level","IB":"A-Level","GCSE":"high school","HKDSE":"high school","Primary":"primary"}
        audience = aud_map.get(audience_label,"high school")
        detail = st.slider("Detail level", 1, 5, 3, key="qs_detail")

        st.markdown("### Quiz type")
        quiz_mode = st.radio("Choose quiz format", ["Free response", "Multiple choice"], index=0, horizontal=True, key="qs_quiz_mode")
        mcq_options = 4
        if quiz_mode == "Multiple choice":
            mcq_options = st.slider("MCQ options per question", 3, 6, 4, key="qs_mcq_opts")

        files = st.file_uploader("Upload files (PDF, PPTX, JPG, PNG, TXT)",
                                 type=["pdf","pptx","jpg","jpeg","png","txt"], accept_multiple_files=True, key="qs_files")

        # Gate the generate button until all required inputs exist
        has_subject = bool(subject_id or st.session_state.get("qs_new_subject"))
        has_exam = bool(exam_id or st.session_state.get("qs_new_exam"))
        has_topic_text = bool((st.session_state.get("qs_new_topic") or "").strip())
        has_files = bool(files)
        has_audience = bool(audience)

        can_generate = (subject_id is not None) and (exam_id is not None) and has_topic_text and has_files and has_audience

        gen_clicked = st.button(
            "Generate Notes + Flashcards + Quiz",
            type="primary",
            key="qs_generate_btn",
            disabled=not can_generate
        )

        if gen_clicked and can_generate:
            # Resolve destination folders freshly
            subjects = _roots(ALL_FOLDERS); subj_map = {s["name"]: s["id"] for s in subjects}
            subject_id = subj_map.get(st.session_state.get("qs_subject_pick"), subject_id)
            exams = [f for f in list_folders() if subject_id and f.get("parent_id")==subject_id]
            exam_map = {e["name"]: e["id"] for e in exams}
            exam_id = exam_map.get(st.session_state.get("qs_exam_pick"), exam_id)

            # Create the topic now (and catch clashes)
            topic_id = None
            topic_name = (st.session_state.get("qs_new_topic") or "").strip()
            if exam_id and topic_name:
                existing_topics = [f for f in list_folders() if f.get("parent_id")==exam_id]
                if topic_name.lower() in {t["name"].lower() for t in existing_topics}:
                    st.error("Topic already exists under this exam. Please choose a different name.")
                    st.stop()
                created = create_folder(topic_name, exam_id)
                topic_id = created["id"]
                topic_name = created["name"]

            dest_folder = topic_id or exam_id or subject_id or None

            # Base title (Topic > Exam > Subject > subject_hint)
            base_title = (
                topic_name
                or (next((e["name"] for e in exams if e["id"]==exam_id), None) if exam_id else None)
                or (next((s["name"] for s in subjects if s["id"]==subject_id), None) if subject_id else None)
                or (subject_hint or "Study Pack")
            )

            prog = st.progress(0, text="Starting…")
            try:
                prog.progress(10, text="Extracting text…")
                text = extract_any(files)
                if not text.strip():
                    st.error("No text detected in the uploaded files.")
                    st.stop()

                prog.progress(35, text="Summarising with AI…")
                data = summarize_text(text, audience=audience, detail=detail, subject=subject_hint)

                prog.progress(60, text="Generating flashcards & quiz…")
                cards = []
                try:
                    cards = generate_flashcards_from_notes(data, audience=audience)
                except Exception as e:
                    st.warning(f"Flashcards skipped: {e}")

                qs = generate_quiz_from_notes(
                    data, subject=subject_hint, audience=audience,
                    num_questions=8, mode=("mcq" if quiz_mode=="Multiple choice" else "free"),
                    mcq_options=mcq_options
                )

                prog.progress(85, text="Saving items…")
                title_notes = f"📄 {base_title} — Notes"
                title_flash = f"🧠 {base_title} — Flashcards"
                title_quiz  = f"🧪 {base_title} — Quiz"

                summary = save_item("summary", title_notes, data, dest_folder)
                summary_id = summary.get("id")
                flash_id = quiz_id = None

                if cards:
                    flash = save_item("flashcards", title_flash, {"flashcards": cards}, dest_folder)
                    flash_id = flash.get("id")

                quiz_payload = {"questions": qs}
                if quiz_mode == "Multiple choice":
                    quiz_payload["type"] = "mcq"
                quiz_item = save_item("quiz", title_quiz, quiz_payload, dest_folder)
                quiz_id = quiz_item.get("id")

                prog.progress(100, text="Done!")
                st.success("Saved ✅")

                st.markdown("### Open")
                c1,c2,c3 = st.columns(3)
                if summary_id and c1.button("Open Notes", type="primary", use_container_width=True, key="qs_open_notes"):
                    _set_params(item=summary_id, view="all"); st.rerun()
                if flash_id and c2.button("Open Flashcards", use_container_width=True, key="qs_open_flash"):
                    _set_params(item=flash_id, view="all"); st.rerun()
                if quiz_id  and c3.button("Open Quiz", use_container_width=True, key="qs_open_quiz"):
                    _set_params(item=quiz_id, view="all"); st.rerun()
            except Exception as e:
                st.error(f"Generation failed: {e}")

# ===== Resources =====
with tabs[1]:
    st.title("🧰 Resources")
    if "sb_user" not in st.session_state:
        st.info("Log in to view your resources.")
    else:
        try:
            ALL_FOLDERS = list_folders()
        except:
            pass

        # ---- SUBJECT row (inline actions) ----
        subjects = [r for r in ALL_FOLDERS if not r.get("parent_id")]
        subj_names = [s["name"] for s in subjects]
        csubj1, csubj2, csubj3 = st.columns([3, 0.9, 0.9])
        subj_pick = csubj1.selectbox("Subject", ["— select —"] + subj_names, key="res_subj_pick")
        subj_id = next((s["id"] for s in subjects if s["name"]==subj_pick), None) if subj_pick in subj_names else None

        if subj_id:
            if csubj2.button("Rename", key="rn_subj_btn", use_container_width=True):
                st.session_state["rn_subj_mode"] = True
            if csubj3.button("Delete", key="del_subj_btn", use_container_width=True):
                st.session_state["del_subj_confirm"] = True

            if st.session_state.get("rn_subj_mode"):
                newn = st.text_input("New subject name", value=subj_pick, key="rn_subj_name")
                rc1, rc2 = st.columns([1, 1])
                if rc1.button("Save", key="rn_subj_save"):
                    try:
                        rename_folder(subj_id, (newn or "").strip())
                        st.session_state.pop("rn_subj_mode", None)
                        st.success("Subject renamed.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Rename failed: {e}")
                if rc2.button("Cancel", key="rn_subj_cancel"):
                    st.session_state.pop("rn_subj_mode", None); st.rerun()

            if st.session_state.get("del_subj_confirm"):
                st.warning("Delete this subject and all nested exams/topics/items? This cannot be undone.")
                dc1, dc2 = st.columns(2)
                if dc1.button("Confirm delete", type="primary", key="del_subj_yes"):
                    try:
                        delete_folder(subj_id)
                        st.session_state.pop("del_subj_confirm", None)
                        st.success("Subject deleted."); st.rerun()
                    except Exception as e:
                        st.error(f"Delete failed: {e}")
                if dc2.button("Cancel", key="del_subj_no"):
                    st.session_state.pop("del_subj_confirm", None); st.rerun()
        else:
            st.caption("Pick a Subject to reveal Exams.")

        # ---- EXAM row (inline actions) ----
        exam_id = None
        if subj_id:
            exams = [f for f in ALL_FOLDERS if f.get("parent_id")==subj_id]
            ex_names = [e["name"] for e in exams]
            cex1, cex2, cex3 = st.columns([3, 0.9, 0.9])
            ex_pick = cex1.selectbox("Exam", ["— select —"] + ex_names, key="res_exam_pick")
            exam_id = next((e["id"] for e in exams if e["name"]==ex_pick), None) if ex_pick in ex_names else None

            if exam_id:
                if cex2.button("Rename", key="rn_exam_btn", use_container_width=True):
                    st.session_state["rn_exam_mode"] = True
                if cex3.button("Delete", key="del_exam_btn", use_container_width=True):
                    st.session_state["del_exam_confirm"] = True

                if st.session_state.get("rn_exam_mode"):
                    newn = st.text_input("New exam name", value=ex_pick, key="rn_exam_name")
                    rc1, rc2 = st.columns(2)
                    if rc1.button("Save", key="rn_exam_save"):
                        try:
                            rename_folder(exam_id, (newn or "").strip())
                            st.session_state.pop("rn_exam_mode", None)
                            st.success("Exam renamed."); st.rerun()
                        except Exception as e:
                            st.error(f"Rename failed: {e}")
                    if rc2.button("Cancel", key="rn_exam_cancel"):
                        st.session_state.pop("rn_exam_mode", None); st.rerun()

                if st.session_state.get("del_exam_confirm"):
                    st.warning("Delete this exam and all nested topics/items? This cannot be undone.")
                    dc1, dc2 = st.columns(2)
                    if dc1.button("Confirm delete", type="primary", key="del_exam_yes"):
                        try:
                            delete_folder(exam_id)
                            st.session_state.pop("del_exam_confirm", None)
                            st.success("Exam deleted."); st.rerun()
                        except Exception as e:
                            st.error(f"Delete failed: {e}")
                    if dc2.button("Cancel", key="del_exam_no"):
                        st.session_state.pop("del_exam_confirm", None); st.rerun()
        else:
            st.caption("Pick a Subject to reveal Exams.")

        # ---- TOPIC row (inline actions) ----
        topic_id = None
        if exam_id:
            topics = [f for f in ALL_FOLDERS if f.get("parent_id")==exam_id]
            tp_names = [t["name"] for t in topics]
            ctp1, ctp2, ctp3 = st.columns([3, 0.9, 0.9])
            tp_pick = ctp1.selectbox("Topic", ["— select —"] + tp_names, key="res_topic_pick")
            topic_id = next((t["id"] for t in topics if t["name"]==tp_pick), None) if tp_pick in tp_names else None

            if topic_id:
                if ctp2.button("Rename", key="rn_topic_btn", use_container_width=True):
                    st.session_state["rn_topic_mode"] = True
                if ctp3.button("Delete", key="del_topic_btn", use_container_width=True):
                    st.session_state["del_topic_confirm"] = True

                if st.session_state.get("rn_topic_mode"):
                    newn = st.text_input("New topic name", value=tp_pick, key="rn_topic_name")
                    rc1, rc2 = st.columns(2)
                    if rc1.button("Save", key="rn_topic_save"):
                        try:
                            rename_folder(topic_id, (newn or "").strip())
                            st.session_state.pop("rn_topic_mode", None)
                            st.success("Topic renamed."); st.rerun()
                        except Exception as e:
                            st.error(f"Rename failed: {e}")
                    if rc2.button("Cancel", key="rn_topic_cancel"):
                        st.session_state.pop("rn_topic_mode", None); st.rerun()

                if st.session_state.get("del_topic_confirm"):
                    st.warning("Delete this topic and all items? This cannot be undone.")
                    dc1, dc2 = st.columns(2)
                    if dc1.button("Confirm delete", type="primary", key="del_topic_yes"):
                        try:
                            delete_folder(topic_id)
                            st.session_state.pop("del_topic_confirm", None)
                            st.success("Topic deleted."); st.rerun()
                        except Exception as e:
                            st.error(f"Delete failed: {e}")
                    if dc2.button("Cancel", key="del_topic_no"):
                        st.session_state.pop("del_topic_confirm", None); st.rerun()
        else:
            st.caption("Pick an Exam to reveal Topics.")

        # Items inside Topic (neat buttons)
        if topic_id:
            st.progress(compute_topic_progress(topic_id), text="Topic progress")
            emoji = {"summary":"📄","flashcards":"🧠","quiz":"🧪"}
            try: items = list_items(topic_id, limit=200)
            except: items = []
            st.subheader("Resources in topic")
            if not items: st.caption("No items yet.")
            for it in items:
                icon = emoji.get(it["kind"], "📄")
                c0, c1, c2, c3 = st.columns([8,1.2,1.2,1.2])
                c0.markdown(f"{icon} **{it['title']}** — {it['created_at'][:16].replace('T',' ')}")
                with c1:
                    if st.button("Open", key=f"res_open_{it['id']}", use_container_width=True):
                        _set_params(item=it["id"], view="all"); st.rerun()
                with c2:
                    if not st.session_state.get(f"edit_item_{it['id']}", False):
                        if st.button("Rename", key=f"res_btn_rename_{it['id']}", use_container_width=True):
                            st.session_state[f"edit_item_{it['id']}"]=True; st.rerun()
                    else:
                        newt = st.text_input("New title", value=it["title"], key=f"res_rn_{it['id']}")
                        s1,s2 = st.columns(2)
                        if s1.button("Save", key=f"res_save_{it['id']}"):
                            try: rename_item(it["id"], newt.strip()); st.session_state[f"edit_item_{it['id']}"]=False; st.rerun()
                            except Exception as e: st.error(f"Rename failed: {e}")
                        if s2.button("Cancel", key=f"res_cancel_{it['id']}"):
                            st.session_state[f"edit_item_{it['id']}"]=False; st.rerun()
                with c3:
                    if st.button("Delete", key=f"res_del_{it['id']}", use_container_width=True):
                        try: delete_item(it["id"]); st.success("Deleted."); st.rerun()
                        except Exception as e: st.error(f"Delete failed: {e}")

# ===== All Resources (newest) =====
with tabs[2]:
    st.title("🗂️ All Resources (Newest)")
    if "sb_user" not in st.session_state:
        st.info("Log in to view your resources.")
    else:
        emoji = {"summary":"📄","flashcards":"🧠","quiz":"🧪"}
        try: all_items = list_items(None, limit=1000)
        except: all_items = []
        all_items.sort(key=lambda x: x.get("created_at",""), reverse=True)
        if not all_items: st.caption("Nothing yet — create something in Quick Study!")
        for it in all_items:
            icon = emoji.get(it["kind"], "📄")
            c0, c1, c2, c3 = st.columns([8,1.2,1.2,1.2])
            c0.markdown(f"{icon} **{it['title']}** — {it['created_at'][:16].replace('T',' ')}")
            with c1:
                if st.button("Open", key=f"all_open_{it['id']}", use_container_width=True):
                    _set_params(item=it['id'], view="all"); st.rerun()
            with c2:
                if not st.session_state.get(f"edit_item_all_{it['id']}", False):
                    if st.button("Rename", key=f"all_btn_rename_{it['id']}", use_container_width=True):
                        st.session_state[f"edit_item_all_{it['id']}"]=True; st.rerun()
                else:
                    newt = st.text_input("New title", value=it["title"], key=f"all_rn_{it['id']}")
                    s1,s2 = st.columns(2)
                    if s1.button("Save", key=f"all_save_{it['id']}"):
                        try: rename_item(it["id"], newt.strip()); st.session_state[f"edit_item_all_{it['id']}"]=False; st.rerun()
                        except Exception as e: st.error(f"Rename failed: {e}")
                    if s2.button("Cancel", key=f"all_cancel_{it['id']}"):
                        st.session_state[f"edit_item_all_{it['id']}"]=False; st.rerun()
            with c3:
                if st.button("Delete", key=f"all_del_{it['id']}", use_container_width=True):
                    try: delete_item(it["id"]); st.success("Deleted."); st.rerun()
                    except Exception as e: st.error(f"Delete failed: {e}")
