import streamlit as st
st.set_page_config(page_title="StudyBloom", page_icon="📚")

# ---------- CSS tweaks ----------
st.markdown("""
<style>
/* Compact, no-wrap buttons */
.stButton > button { white-space: nowrap; padding:.35rem .65rem; line-height:1.1; }
.small-btn .stButton > button { padding:.25rem .5rem; font-size:.9rem; }
/* Top bar */
.topbar { display:flex; align-items:center; justify-content:space-between; gap:0.5rem; }
.topbar .brand { font-weight:700; font-size:1.05rem; display:flex; align-items:center; gap:.5rem; }
.topbar .actions { display:flex; gap:.5rem; align-items:center; }
.avatar { width:34px; height:34px; border-radius:50%; object-fit:cover; border:1px solid #ddd; }
.linklike { color: var(--text-color, #444); text-decoration: none; }
hr.sep { margin: .35rem 0 .85rem 0; }
</style>
""", unsafe_allow_html=True)

import sys, requests
from typing import Optional, List, Dict, Tuple

# ---------- local modules ----------
from pdf_utils import extract_any
from llm import (
    summarize_text,
    generate_quiz_from_notes,
    generate_flashcards_from_notes,
    grade_free_answer,
)
from auth_rest import (
    sign_in, sign_up, sign_out,
    save_item, list_items, get_item, move_item, delete_item,
    create_folder, list_folders, delete_folder, list_child_folders,
    save_quiz_attempt, list_quiz_attempts, list_quiz_attempts_for_items,
    save_flash_review, list_flash_reviews_for_items,
    upsert_profile, get_profile, change_password,
)

st.caption(f"Python {sys.version.split()[0]} • Build: topbar+no-stop")

# ---------- URL/query helpers ----------
def _get_params() -> Dict[str, str]:
    try: return dict(st.query_params)
    except: return st.experimental_get_query_params()

def _set_params(**kwargs):
    try:
        st.query_params.clear()
        st.query_params.update(kwargs)
    except Exception:
        st.experimental_set_query_params(**kwargs)

# ---------- Supabase REST helpers (rename endpoints) ----------
def _sb_headers():
    url = st.secrets.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_ANON_KEY")
    if not url or not key: raise RuntimeError("Missing SUPABASE_URL / SUPABASE_KEY")
    return url, {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type":"application/json", "Prefer":"return=representation"}

def rename_item(item_id: str, new_title: str) -> dict:
    url, h = _sb_headers()
    r = requests.patch(f"{url}/rest/v1/items?id=eq.{item_id}", json={"title": new_title}, headers=h, timeout=20)
    r.raise_for_status(); data = r.json()
    return data[0] if isinstance(data, list) and data else {}

def rename_folder(folder_id: str, new_name: str) -> dict:
    url, h = _sb_headers()
    r = requests.patch(f"{url}/rest/v1/folders?id=eq.{folder_id}", json={"name": new_name}, headers=h, timeout=20)
    r.raise_for_status(); data = r.json()
    return data[0] if isinstance(data, list) and data else {}

# ---------- cookies (optional) ----------
COOKIE_PASSWORD = st.secrets.get("COOKIE_PASSWORD", "change_me_please")
cookies = None
try:
    from streamlit_cookies_manager import EncryptedCookieManager
    cookies = EncryptedCookieManager(prefix="studybloom.", password=COOKIE_PASSWORD)
    if not cookies.ready():
        st.stop()
except Exception:
    cookies = None

def _fetch_user_from_token(access_token: str) -> Optional[dict]:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets.get("SUPABASE_KEY") or st.secrets.get("SUPABASE_ANON_KEY")
        h = {"apikey": key, "Authorization": f"Bearer {access_token}"}
        r = requests.get(f"{url}/auth/v1/user", headers=h, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

# restore from cookie if exists
if "sb_user" not in st.session_state and cookies:
    tok = cookies.get("sb_access")
    if tok:
        user = _fetch_user_from_token(tok)
        if user:
            st.session_state["sb_user"] = {"user": user, "access_token": tok}

# ---------- app state ----------
def _route_set(route: str):
    st.session_state["route"] = route

route = st.session_state.get("route", "home")

# ---------- progress calc ----------
def compute_topic_progress(topic_folder_id: str) -> float:
    try:
        items = list_items(topic_folder_id, limit=500)
        quiz_ids = [it["id"] for it in items if it["kind"]=="quiz"]
        flash_ids = [it["id"] for it in items if it["kind"]=="flashcards"]

        quiz_score = 0.0
        if quiz_ids:
            attempts = list_quiz_attempts_for_items(quiz_ids)
            latest: Dict[str, Tuple[int,int]] = {}
            for at in attempts:
                qid = at["item_id"]
                if qid not in latest: latest[qid] = (at["correct"], at["total"])
            if latest:
                ratios = [(c/t) if t else 0 for (c,t) in latest.values()]
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

# ---------- renderers ----------
def render_summary(data: dict):
    st.subheader("📝 Notes")
    st.markdown(f"**TL;DR**: {data.get('tl_dr','')}")
    for sec in (data.get("sections") or []):
        st.markdown(f"### {sec.get('heading','Section')}")
        for b in sec.get("bullets",[]) or []:
            st.markdown(f"- {b}")
    if data.get("key_terms"):
        st.markdown("## Key Terms")
        for kt in data["key_terms"]:
            st.markdown(f"- **{kt.get('term','')}** — {kt.get('definition','')}")
    if data.get("formulas"):
        st.markdown("## Formulas")
        for f in data["formulas"]:
            name, expr, meaning = f.get("name",""), (f.get("latex") or f.get("expression") or "").strip(), f.get("meaning","")
            if any(s in expr for s in ["\\frac","\\sqrt","^","_","\\times","\\cdot","\\sum","\\int","\\left","\\right"]):
                if name or meaning: st.markdown(f"**{name}** — {meaning}")
                try: st.latex(expr)
                except: st.code(expr)
            else:
                st.markdown(f"- **{name}**: `{expr}` — {meaning}")

def interactive_flashcards(flashcards: List[dict], item_id: Optional[str]=None, key_prefix="fc"):
    st.subheader("🧠 Flashcards")
    if not flashcards:
        st.caption("No flashcards found.")
        return
    st.session_state.setdefault(f"{key_prefix}_idx",0)
    st.session_state.setdefault(f"{key_prefix}_revealed",False)
    st.session_state.setdefault(f"{key_prefix}_order", list(range(len(flashcards))))
    st.session_state.setdefault(f"{key_prefix}_wrong_counts",{})
    order = st.session_state[f"{key_prefix}_order"]
    if not order:
        st.success("Deck complete — nice work!")
        if st.button("🔁 Restart", key=f"{key_prefix}_restart_all"):
            st.session_state[f"{key_prefix}_order"]=list(range(len(flashcards)))
            st.session_state[f"{key_prefix}_idx"]=0
            st.session_state[f"{key_prefix}_revealed"]=False
            st.session_state[f"{key_prefix}_wrong_counts"]={}
            st.rerun()
        return
    idx = st.session_state[f"{key_prefix}_idx"]; idx = max(0, min(idx, len(order)-1))
    st.session_state[f"{key_prefix}_idx"]=idx
    revealed = st.session_state[f"{key_prefix}_revealed"]; wrong = st.session_state[f"{key_prefix}_wrong_counts"]
    st.progress((idx+1)/len(order), text=f"Card {idx+1}/{len(order)}")
    orig_i = order[idx]; card = flashcards[orig_i]
    st.markdown("#### Front"); st.info(card.get("front",""))
    if revealed: st.markdown("#### Back"); st.success(card.get("back",""))
    c1,c2,c3,c4 = st.columns(4)
    if c1.button("◀️ Prev", disabled=(idx==0), key=f"{key_prefix}_prev"):
        st.session_state[f"{key_prefix}_idx"]=idx-1; st.session_state[f"{key_prefix}_revealed"]=False; st.rerun()
    if c2.button("🔁 Flip", key=f"{key_prefix}_flip"):
        st.session_state[f"{key_prefix}_revealed"]=not revealed; st.rerun()
    if c3.button("✅ Knew it", key=f"{key_prefix}_ok"):
        if item_id and "sb_user" in st.session_state:
            try: save_flash_review(item_id, True)
            except: pass
        st.session_state[f"{key_prefix}_order"].pop(idx)
        st.session_state[f"{key_prefix}_revealed"]=False
        if idx >= len(st.session_state[f"{key_prefix}_order"]): st.session_state[f"{key_prefix}_idx"]=max(0,len(st.session_state[f"{key_prefix}_order"])-1)
        st.rerun()
    if c4.button("❌ Again", key=f"{key_prefix}_bad"):
        if item_id and "sb_user" in st.session_state:
            try: save_flash_review(item_id, False)
            except: pass
        cnt = wrong.get(orig_i,0)
        if cnt < 2:
            st.session_state[f"{key_prefix}_order"].insert(min(len(order), idx+4), orig_i)
            wrong[orig_i]=cnt+1
        st.session_state[f"{key_prefix}_revealed"]=False
        if idx < len(st.session_state[f"{key_prefix}_order"])-1: st.session_state[f"{key_prefix}_idx"]=idx+1
        st.rerun()

def interactive_quiz(questions: List[dict], item_id: Optional[str]=None, key_prefix="quiz", subject_hint="General"):
    st.subheader("🧪 Quiz")
    if not questions:
        st.caption("No questions found.")
        return
    st.session_state.setdefault(f"{key_prefix}_i",0)
    st.session_state.setdefault(f"{key_prefix}_graded",False)
    st.session_state.setdefault(f"{key_prefix}_feedback","")
    st.session_state.setdefault(f"{key_prefix}_mark_last",(0,0))
    st.session_state.setdefault(f"{key_prefix}_history",[])
    i = st.session_state[f"{key_prefix}_i"]; i = max(0, min(i, len(questions)-1)); st.session_state[f"{key_prefix}_i"]=i
    q = questions[i]
    is_mcq = "options" in q and isinstance(q.get("options"), list)

    st.progress((i+1)/len(questions), text=f"Question {i+1}/{len(questions)}")
    st.markdown(f"### {q.get('question','')}")

    if is_mcq:
        options = q.get("options") or []
        choice = st.radio("Choose one", options, key=f"{key_prefix}_mcq_{i}", index=None)
        col1,col2,col3 = st.columns(3)
        if col1.button("Submit", key=f"{key_prefix}_mcq_submit"):
            if choice is None:
                st.warning("Pick an option first.")
            else:
                correct = options.index(choice) == q.get("correct_index", -1)
                st.session_state[f"{key_prefix}_graded"]=True
                sc = 10 if correct else 0
                st.session_state[f"{key_prefix}_mark_last"]=(sc,10)
                hist = st.session_state[f"{key_prefix}_history"]
                if len(hist)<=i: hist.append({"score": sc, "max":10})
                else: hist[i]={"score": sc, "max":10}
                st.success("Correct! ✅" if correct else "Not quite. ❌")
                if q.get("explanation"): st.info(q["explanation"])
        if col2.button("◀️ Prev", disabled=(i==0), key=f"{key_prefix}_prev"):
            st.session_state[f"{key_prefix}_i"]=i-1; st.session_state[f"{key_prefix}_graded"]=False; st.rerun()
        if col3.button("Next ▶️", disabled=(i==len(questions)-1), key=f"{key_prefix}_next"):
            st.session_state[f"{key_prefix}_i"]=i+1; st.session_state[f"{key_prefix}_graded"]=False; st.rerun()
    else:
        ans = st.text_area("Your answer", key=f"{key_prefix}_ans_{i}", height=120, placeholder="Type your working/answer here…")
        colg1, colg2, colg3, colg4 = st.columns(4)
        if colg1.button("Submit", key=f"{key_prefix}_submit"):
            try:
                result = grade_free_answer(
                    q.get("question",""), q.get("model_answer",""),
                    q.get("markscheme_points",[]) or [], ans or "", subject_hint or "General"
                )
                st.session_state[f"{key_prefix}_graded"]=True
                st.session_state[f"{key_prefix}_mark_last"]=(result.get("score",0), result.get("max_points",10))
                st.session_state[f"{key_prefix}_feedback"]=result.get("feedback","")
                hist = st.session_state[f"{key_prefix}_history"]
                if len(hist)<=i: hist.append({"score": result.get("score",0), "max": result.get("max_points",10)})
                else: hist[i]={"score": result.get("score",0), "max": result.get("max_points",10)}
            except Exception as e:
                st.error(f"Grading failed: {e}")
        if st.session_state[f"{key_prefix}_graded"]:
            sc, mx = st.session_state[f"{key_prefix}_mark_last"]
            st.success(f"Score for this question: {sc} / {mx}")
            with st.expander("Model answer & mark scheme", expanded=False):
                st.markdown(q.get("model_answer",""))
                for pt in q.get("markscheme_points",[]) or []: st.markdown(f"- {pt}")
            if st.session_state[f"{key_prefix}_feedback"]:
                st.info(st.session_state[f"{key_prefix}_feedback"])
        if colg2.button("◀️ Prev", disabled=(i==0), key=f"{key_prefix}_prev"):
            st.session_state[f"{key_prefix}_i"]=i-1; st.session_state[f"{key_prefix}_graded"]=False; st.session_state[f"{key_prefix}_feedback"]=""; st.rerun()
        if colg3.button("Next ▶️", disabled=(i==len(questions)-1), key=f"{key_prefix}_next"):
            st.session_state[f"{key_prefix}_i"]=i+1; st.session_state[f"{key_prefix}_graded"]=False; st.session_state[f"{key_prefix}_feedback"]=""; st.rerun()

    total_sc = sum(h.get("score",0) for h in st.session_state[f"{key_prefix}_history"])
    total_mx = sum(h.get("max",0)  for h in st.session_state[f"{key_prefix}_history"])
    st.metric("Total so far", f"{total_sc} / {total_mx or (len(questions)*10)}")
    save_col, new_col = st.columns(2)
    if save_col.button("✅ Finish & Save", key=f"{key_prefix}_finish"):
        if item_id and "sb_user" in st.session_state:
            try:
                correct = sum(1 for h in st.session_state[f"{key_prefix}_history"] if h.get("max",0) and h.get("score",0) >= 0.7*h["max"])
                total = len(questions); save_quiz_attempt(item_id, correct, total, st.session_state[f"{key_prefix}_history"])
                st.success(f"Attempt saved: {correct}/{total}")
            except Exception:
                st.info("Attempt not saved (check quiz_attempts table).")

    # --- New quiz with loading screen ---
    if new_col.button("🎲 New quiz", key=f"{key_prefix}_regen") and item_id:
        with st.status("Generating a fresh quiz…", expanded=True) as status:
            try:
                status.update(label="Fetching current quiz + folder…")
                quiz_item = get_item(item_id)
                folder_id = quiz_item.get("folder_id")
                subject = subject_hint or "General"

                if not folder_id:
                    status.update(state="error", label="Folder not found for this quiz.")
                else:
                    status.update(label="Finding summary in this topic…")
                    siblings = list_items(folder_id, limit=200)
                    summary = next((s for s in siblings if s.get("kind") == "summary"), None)
                    if not (summary and summary.get("data")):
                        status.update(state="error", label="No summary found in this folder to generate from.")
                    else:
                        status.update(label="Asking AI to build new questions…")
                        new_qs = generate_quiz_from_notes(
                            summary["data"], subject=subject, audience="high school",
                            num_questions=8, mode="free",
                        )
                        status.update(label="Saving quiz…")
                        created = save_item(
                            "quiz",
                            f"{summary['title']} • Quiz (new)",
                            {"questions": new_qs},
                            folder_id,
                        )
                        status.update(state="complete", label="New quiz created 🎉")
                        _set_params(item=created.get("id"), view="all")
                        st.rerun()
            except Exception as e:
                status.update(state="error", label=f"Re-generate failed: {e}")

# ---------- top bar ----------
def topbar():
    st.markdown('<div class="topbar">', unsafe_allow_html=True)
    colA, colB = st.columns([1,1])
    with colA:
        st.markdown('<div class="brand">📚 StudyBloom</div>', unsafe_allow_html=True)
    with colB:
        inner = st.container()
        with inner:
            c1, c2, c3 = st.columns([0.55, 0.2, 0.25])
            c1.write("")  # spacer
            if "sb_user" not in st.session_state:
                if c2.button("Log in", key="tb_login"):
                    _route_set("login")
                if c3.button("Sign up", key="tb_signup"):
                    _route_set("signup")
            else:
                user = st.session_state["sb_user"]["user"]
                prof = get_profile(user.get("id")) or {}
                avatar = prof.get("avatar_url") or "https://placehold.co/64x64?text=U"
                # avatar as button:
                if c3.button("My Account", key="tb_myacct"):
                    _route_set("account")
                c2.image(avatar, caption="", width=34)
    st.markdown('<hr class="sep"/>', unsafe_allow_html=True)

# ---------- auth pages ----------
def page_login():
    st.title("Log in")
    email = st.text_input("Email", key="login_email_top")
    pwd   = st.text_input("Password", type="password", key="login_pwd_top")
    remember = st.checkbox("Stay signed in", value=True, key="remember_me_top")
    c1, c2 = st.columns([1,1])
    if c1.button("Log in", type="primary"):
        try:
            sign_in(email, pwd)
            if remember and cookies and "sb_user" in st.session_state:
                tok = st.session_state["sb_user"].get("access_token") or st.session_state["sb_user"].get("session",{}).get("access_token")
                if tok:
                    cookies["sb_access"] = tok
                    cookies["sb_email"] = email or ""
                    cookies.save()
            _route_set("home"); st.rerun()
        except Exception as e:
            st.error(str(e))
    if c2.button("Back"):
        _route_set("home"); st.rerun()

def page_signup():
    st.title("Create account")
    remail = st.text_input("Email", key="reg_email_top")
    rpwd   = st.text_input("Password", type="password", key="reg_pwd_top")
    c1, c2 = st.columns([1,1])
    if c1.button("Sign up", type="primary"):
        try:
            sign_up(remail, rpwd)
            st.success("Check your email to confirm, then log in.")
        except Exception as e:
            st.error(str(e))
    if c2.button("Back"):
        _route_set("home"); st.rerun()

def page_account():
    st.title("My account")
    if "sb_user" not in st.session_state:
        st.info("Please log in."); return
    user = st.session_state["sb_user"]["user"]
    uid = user.get("id")
    prof = get_profile(uid) or {}
    name = st.text_input("Display name", value=prof.get("name",""))
    username = st.text_input("Username", value=prof.get("username",""))
    avatar_url = st.text_input("Avatar URL", value=prof.get("avatar_url",""))
    if st.button("Save profile", type="primary"):
        try:
            upsert_profile(uid, name=name, username=username, avatar_url=avatar_url)
            st.success("Profile saved.")
        except Exception as e:
            st.error(f"Save failed: {e}")
    st.markdown("---")
    st.subheader("Change password")
    p1 = st.text_input("Current password", type="password")
    p2 = st.text_input("New password", type="password")
    if st.button("Update password"):
        try:
            change_password(p1, p2)
            st.success("Password updated.")
        except Exception as e:
            st.error(str(e))
    st.markdown("---")
    if st.button("Sign out"):
        if cookies:
            if "sb_access" in cookies: del cookies["sb_access"]
            if "sb_email" in cookies: del cookies["sb_email"]
            cookies.save()
        sign_out(); _route_set("home"); st.rerun()

# ---------- item page (notes/quiz/flashcards) ----------
def page_item():
    params = _get_params()
    item_id = params.get("item")
    if isinstance(item_id, list): item_id = item_id[0]
    if not item_id: return
    try:
        full  = get_item(item_id)
        kind  = full.get("kind")
        title = full.get("title") or kind.title()
        st.title(title)

        # Back -> All Resources
        if st.button("← Back to All Resources", key="item_back_btn"):
            _set_params(view="all"); _route_set("home"); st.rerun()

        data = full.get("data") or {}

        if kind == "summary":
            render_summary(data or full)
        elif kind == "flashcards":
            interactive_flashcards(data.get("flashcards") or [], item_id=item_id, key_prefix=f"fc_{item_id}")
        elif kind == "quiz":
            interactive_quiz(data.get("questions") or [], item_id=item_id, key_prefix=f"quiz_{item_id}", subject_hint="General")
        else:
            st.write(data or full)
    except Exception as e:
        st.error(f"Could not load item: {e}")
        if st.button("← Back to All Resources", key="item_back_btn2"):
            _set_params(view="all"); _route_set("home"); st.rerun()

# ---------- tabs ----------
def page_home():
    # tabs
    params = _get_params()
    view = (params.get("view") or [""])[0] if isinstance(params.get("view"), list) else params.get("view")
    default_idx = 2 if view == "all" else (1 if view == "resources" else 0)
    tabs = st.tabs(["Quick Study", "Resources", "All Resources"])

    # ===== Quick Study (progressive, no st.stop) =====
    with tabs[0]:
        st.title("⚡ Quick Study")
        if "sb_user" not in st.session_state:
            st.info("Log in to save your study materials.")
        else:
            ALL_FOLDERS = list_folders()
            subjects = [r for r in ALL_FOLDERS if not r.get("parent_id")]
            subj_names = [s["name"] for s in subjects]

            # Step 1: Subject
            st.subheader("1) Subject")
            s1a, s1b = st.columns([1.4, 1])
            subj_mode = s1a.radio("How do you want to pick a subject?", ["Select existing", "Create new"], horizontal=True, key="qs_subj_mode")

            subject_id = None
            subject_name_selected = None
            if subj_mode == "Select existing":
                subject_name_selected = s1b.selectbox("Subject", ["— select —"] + subj_names, key="qs_subject_pick")
                if subject_name_selected in subj_names:
                    subject_id = next(s["id"] for s in subjects if s["name"] == subject_name_selected)
            else:
                new_subject = s1b.text_input("New subject name", placeholder="e.g., A-Level Mathematics", key="qs_new_subject_name")

            ready_subject = bool(subject_id) or bool((st.session_state.get("qs_new_subject_name") or "").strip())
            if not ready_subject:
                st.caption("Select or enter a subject to continue.")
            else:
                # Create subject just-in-time if needed
                if not subject_id and (st.session_state.get("qs_new_subject_name") or "").strip():
                    name = st.session_state["qs_new_subject_name"].strip()
                    if name.lower() in {n.lower() for n in subj_names}:
                        st.error("This subject already exists. Choose it from 'Select existing'.")
                        return
                    created = create_folder(name, None)
                    subject_id = created["id"]
                    subject_name_selected = created["name"]
                    ALL_FOLDERS = list_folders()

                st.success(f"Subject: {subject_name_selected or next(s['name'] for s in ALL_FOLDERS if s['id']==subject_id)}")

                # Step 2: Exam
                st.subheader("2) Exam")
                exams = [f for f in ALL_FOLDERS if f.get("parent_id") == subject_id]
                ex_names = [e["name"] for e in exams]
                e2a, e2b = st.columns([1.4, 1])
                exam_mode = e2a.radio("How do you want to pick an exam?", ["Select existing", "Create new"], horizontal=True, key="qs_exam_mode")

                exam_id = None
                exam_name_selected = None

                if exam_mode == "Select existing":
                    exam_name_selected = e2b.selectbox("Exam", ["— select —"] + ex_names, key="qs_exam_pick")
                    if exam_name_selected in ex_names:
                        exam_id = next(e["id"] for e in exams if e["name"] == exam_name_selected)
                else:
                    new_exam = e2b.text_input("New exam name", placeholder="e.g., IGCSE May 2026", key="qs_new_exam_name")

                ready_exam = bool(exam_id) or bool((st.session_state.get("qs_new_exam_name") or "").strip())
                if not ready_exam:
                    st.caption("Select or enter an exam to continue.")
                else:
                    if not exam_id and (st.session_state.get("qs_new_exam_name") or "").strip():
                        name = st.session_state["qs_new_exam_name"].strip()
                        if name.lower() in {n.lower() for n in ex_names}:
                            st.error("This exam already exists under the selected subject.")
                            return
                        created = create_folder(name, subject_id)
                        exam_id = created["id"]
                        exam_name_selected = created["name"]
                        ALL_FOLDERS = list_folders()

                    st.success(f"Exam: {exam_name_selected or next(e['name'] for e in ALL_FOLDERS if e['id']==exam_id)}")

                    # Step 3: Topic (text only, created on Generate)
                    st.subheader("3) Topic")
                    topic_text = st.text_input("New topic name", placeholder="e.g., Differentiation", key="qs_new_topic")
                    if not (topic_text or "").strip():
                        st.caption("Enter a topic to continue.")
                    else:
                        # Step 4: Subject hint
                        st.subheader("4) Subject context (improves quality)")
                        subject_hint = st.text_input(
                            "e.g., Mathematics (Calculus), Biology (Cell Division), History (Cold War)",
                            value="General", key="qs_subject_hint"
                        )
                        if not (subject_hint or "").strip():
                            st.caption("Provide subject context to continue.")
                        else:
                            # Step 5: Audience & detail
                            st.subheader("5) Audience & detail")
                            audience_label = st.selectbox("Audience", ["University","A-Level","A-Level / IB","GCSE","HKDSE","Primary"], index=0, key="qs_audience_label")
                            aud_map = {"University":"university","A-Level":"A-Level","A-Level / IB":"A-Level","GCSE":"high school","HKDSE":"high school","Primary":"primary"}
                            audience = aud_map.get(audience_label,"high school")
                            detail = st.slider("Detail level", 1, 5, 3, key="qs_detail")

                            # Step 6: Quiz type
                            st.subheader("6) Quiz type")
                            quiz_mode = st.radio("Choose quiz format", ["Free response", "Multiple choice"], index=0, horizontal=True, key="qs_quiz_mode")
                            mcq_options = 4
                            if quiz_mode == "Multiple choice":
                                mcq_options = st.slider("MCQ options per question", 3, 6, 4, key="qs_mcq_opts")

                            # Step 7: Upload
                            st.subheader("7) Upload files")
                            files = st.file_uploader("Upload files (PDF, PPTX, JPG, PNG, TXT)",
                                                    type=["pdf","pptx","jpg","jpeg","png","txt"],
                                                    accept_multiple_files=True, key="qs_files")

                            # Button enabled only when files present
                            can_generate = bool(files)
                            gen_clicked = st.button("Generate Notes + Flashcards + Quiz", type="primary", disabled=not can_generate, key="qs_generate_btn")

                            if gen_clicked and can_generate:
                                # Create topic now (clash check)
                                ALL_FOLDERS = list_folders()
                                existing_topics = [f for f in ALL_FOLDERS if f.get("parent_id") == exam_id]
                                if topic_text.strip().lower() in {t["name"].lower() for t in existing_topics}:
                                    st.error("Topic already exists under this exam. Please choose a different name.")
                                else:
                                    created = create_folder(topic_text.strip(), exam_id)
                                    topic_id = created["id"]
                                    topic_name = created["name"]

                                    base_title = topic_name or (exam_name_selected or "Study Pack")
                                    prog = st.progress(0, text="Starting…")
                                    try:
                                        prog.progress(10, text="Extracting text…")
                                        text = extract_any(files)
                                        if not text.strip():
                                            st.error("No text detected in the uploaded files.")
                                        else:
                                            prog.progress(35, text="Summarising with AI…")
                                            data = summarize_text(text, audience=audience, detail=detail, subject=subject_hint)

                                            prog.progress(60, text="Generating flashcards & quiz…")
                                            try:
                                                cards = generate_flashcards_from_notes(data, audience=audience)
                                            except Exception as e:
                                                cards = []
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

                                            summary = save_item("summary", title_notes, data, topic_id)
                                            summary_id = summary.get("id")
                                            flash_id = quiz_id = None

                                            if cards:
                                                flash = save_item("flashcards", title_flash, {"flashcards": cards}, topic_id)
                                                flash_id = flash.get("id")

                                            quiz_payload = {"questions": qs}
                                            if quiz_mode == "Multiple choice":
                                                quiz_payload["type"] = "mcq"
                                            quiz_item = save_item("quiz", title_quiz, quiz_payload, topic_id)
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
                ALL_FOLDERS = []

            # SUBJECT row
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
                        try: rename_folder(subj_id, (newn or "").strip()); st.session_state.pop("rn_subj_mode", None); st.success("Subject renamed."); st.rerun()
                        except Exception as e: st.error(f"Rename failed: {e}")
                    if rc2.button("Cancel", key="rn_subj_cancel"):
                        st.session_state.pop("rn_subj_mode", None); st.rerun()

                if st.session_state.get("del_subj_confirm"):
                    st.warning("Delete this subject and all nested exams/topics/items? This cannot be undone.")
                    dc1, dc2 = st.columns(2)
                    if dc1.button("Confirm delete", type="primary", key="del_subj_yes"):
                        try: delete_folder(subj_id); st.session_state.pop("del_subj_confirm", None); st.success("Subject deleted."); st.rerun()
                        except Exception as e: st.error(f"Delete failed: {e}")
                    if dc2.button("Cancel", key="del_subj_no"):
                        st.session_state.pop("del_subj_confirm", None); st.rerun()
            else:
                st.caption("Pick a Subject to reveal Exams.")

            # EXAM row
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
                            try: rename_folder(exam_id, (newn or "").strip()); st.session_state.pop("rn_exam_mode", None); st.success("Exam renamed."); st.rerun()
                            except Exception as e: st.error(f"Rename failed: {e}")
                        if rc2.button("Cancel", key="rn_exam_cancel"):
                            st.session_state.pop("rn_exam_mode", None); st.rerun()

                    if st.session_state.get("del_exam_confirm"):
                        st.warning("Delete this exam and all nested topics/items? This cannot be undone.")
                        dc1, dc2 = st.columns(2)
                        if dc1.button("Confirm delete", type="primary", key="del_exam_yes"):
                            try: delete_folder(exam_id); st.session_state.pop("del_exam_confirm", None); st.success("Exam deleted."); st.rerun()
                            except Exception as e: st.error(f"Delete failed: {e}")
                        if dc2.button("Cancel", key="del_exam_no"):
                            st.session_state.pop("del_exam_confirm", None); st.rerun()
            else:
                st.caption("Pick a Subject to reveal Exams.")

            # TOPIC row
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
                            try: rename_folder(topic_id, (newn or "").strip()); st.session_state.pop("rn_topic_mode", None); st.success("Topic renamed."); st.rerun()
                            except Exception as e: st.error(f"Rename failed: {e}")
                        if rc2.button("Cancel", key="rn_topic_cancel"):
                            st.session_state.pop("rn_topic_mode", None); st.rerun()

                    if st.session_state.get("del_topic_confirm"):
                        st.warning("Delete this topic and all items? This cannot be undone.")
                        dc1, dc2 = st.columns(2)
                        if dc1.button("Confirm delete", type="primary", key="del_topic_yes"):
                            try: delete_folder(topic_id); st.session_state.pop("del_topic_confirm", None); st.success("Topic deleted."); st.rerun()
                            except Exception as e: st.error(f"Delete failed: {e}")
                        if dc2.button("Cancel", key="del_topic_no"):
                            st.session_state.pop("del_topic_confirm", None); st.rerun()
            else:
                st.caption("Pick an Exam to reveal Topics.")

            # Items in topic
            if topic_id:
                st.progress(compute_topic_progress(topic_id), text="Topic progress")
                emoji = {"summary":"📄","flashcards":"🧠","quiz":"🧪"}
                try: items = list_items(topic_id, limit=200)
                except: items = []
                st.subheader("Resources in topic")
                if not items:
                    st.caption("No items yet.")
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

    # ===== All Resources =====
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

# ---------- main routing ----------
topbar()
params = _get_params()
if "item" in params and "sb_user" in st.session_state:
    page_item()
elif route == "login":
    page_login()
elif route == "signup":
    page_signup()
elif route == "account":
    page_account()
else:
    page_home()
