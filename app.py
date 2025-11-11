import streamlit as st
st.set_page_config(page_title="StudyBloom", page_icon="📚")


# ---- CSS: compact action buttons, avoid wrapping ----
st.markdown("""
<style>
.stButton > button { white-space: nowrap !important; padding: .35rem .65rem !important; line-height: 1.1 !important; }
.small-btn .stButton > button { padding: .25rem .5rem !important; font-size: .9rem !important; }
</style>
""", unsafe_allow_html=True)

import sys, requests
from typing import Optional, List, Dict, Tuple

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
    current_user, update_profile, change_password
)

st.caption(f"Python {sys.version.split()[0]} • Build: inline-actions + gated-generate")

# ---- Cookies (define BEFORE any dialog uses it) ----
COOKIE_PASSWORD = st.secrets.get("COOKIE_PASSWORD", "change_me_please")
cookies = None
try:
    from streamlit_cookies_manager import EncryptedCookieManager
    cookies = EncryptedCookieManager(prefix="studybloom.", password=COOKIE_PASSWORD)
    if not cookies.ready():
        st.stop()
except Exception:
    cookies = None  # proceed without cookies if not installed

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

# Restore session from cookie if present (BEFORE any auto-prompt)
if "sb_user" not in st.session_state and cookies:
    tok = cookies.get("sb_access")
    if tok:
        user = _fetch_user_from_token(tok)
        if user:
            st.session_state["sb_user"] = {"user": user, "access_token": tok}

# ---------------- Query helpers (needed by top bar) ----------------
def _get_params() -> Dict[str, str]:
    try: return dict(st.query_params)
    except: return st.experimental_get_query_params()

def _set_params(**kwargs):
    try:
        st.query_params.clear()
        st.query_params.update(kwargs)
    except Exception:
        st.experimental_set_query_params(**kwargs)

# ---------------- Supabase REST helpers ----------------
def _sb_headers():
    url = st.secrets.get("SUPABASE_URL")
    key = (st.secrets.get("SUPABASE_ANON_KEY") or st.secrets.get("SUPABASE_KEY"))
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL / SUPABASE_ANON_KEY (or SUPABASE_KEY).")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    return url, headers

def rename_item(item_id: str, new_title: str) -> dict:
    url, headers = _sb_headers()
    resp = requests.patch(f"{url}/rest/v1/items?id=eq.{item_id}",
                          json={"title": new_title}, headers=headers, timeout=20)
    resp.raise_for_status(); data = resp.json()
    return data[0] if isinstance(data, list) and data else {}

def rename_folder(folder_id: str, new_name: str) -> dict:
    url, headers = _sb_headers()
    resp = requests.patch(f"{url}/rest/v1/folders?id=eq.{folder_id}",
                          json={"name": new_name}, headers=headers, timeout=20)
    resp.raise_for_status(); data = resp.json()
    return data[0] if isinstance(data, list) and data else {}

# ---------- Dialog capability ----------
HAS_DIALOG = hasattr(st, "experimental_dialog")
st_dialog = st.experimental_dialog if HAS_DIALOG else None

# ---------- Auth dialogs (define ONCE) ----------
def _open_dialog(fn): fn()

if st_dialog:
    @st_dialog("Sign in")
    def login_dialog():
        st.write("Welcome back! Please sign in.")
        email = st.text_input("Email", key="dlg_login_email")
        pwd   = st.text_input("Password", type="password", key="dlg_login_pwd")
        remember = st.checkbox("Stay signed in", value=True, key="dlg_login_remember")
        c1, c2 = st.columns(2)
        if c1.button("Sign in", type="primary", key="dlg_login_btn"):
            try:
                sign_in(email, pwd)  # sets st.session_state["sb_user"]
                if remember and cookies and "sb_user" in st.session_state:
                    tok = st.session_state["sb_user"].get("access_token") or st.session_state["sb_user"].get("session",{}).get("access_token")
                    if tok:
                        cookies["sb_access"] = tok
                        cookies["sb_email"] = email or ""
                        cookies.save()
                st.session_state["auth_prompted"] = True
                st.rerun()
            except Exception as e:
                st.error(str(e))
        if c2.button("Sign Up", key="dlg_to_signup"):
            st.session_state["want_dialog"] = "signup"
            st.rerun()

    @st_dialog("Create account")
    def signup_dialog():
        st.write("Create your StudyBloom account.")
        disp  = st.text_input("Display name", key="dlg_sign_display")
        uname = st.text_input("Username", key="dlg_sign_username")
        email = st.text_input("Email", key="dlg_sign_email")
        pwd   = st.text_input("Password", type="password", key="dlg_sign_pwd")
        c1, c2 = st.columns(2)
        if c1.button("Sign up", type="primary", key="dlg_signup_btn"):
            try:
                sign_up(email, pwd, disp, uname)
                st.success("Check your email to confirm, then sign in.")
                _open_dialog(login_dialog)
            except Exception as e:
                st.error(str(e))
        if c2.button("Have an account? Sign in", key="dlg_to_login"):
            st.session_state["want_dialog"] = "login"
            st.rerun()
else:
    def login_dialog(): st.warning("Dialog not supported in this environment.")
    def signup_dialog(): st.warning("Dialog not supported in this environment.")

# ---------- Top bar ----------
def _topbar():
    left, right = st.columns([6, 4])
    with left:
        st.markdown("### StudyBloom")
    with right:
        if "sb_user" not in st.session_state:
            c1, c2 = st.columns(2)
            if c1.button("Log in", key="top_login"):
                if st_dialog is not None: login_dialog()
                else: st.warning("Pop-up dialog not supported here.")
            if c2.button("Sign up", key="top_signup"):
                if st_dialog is not None: signup_dialog()
                else: st.warning("Pop-up dialog not supported here.")
        else:
            user_email = st.session_state["sb_user"]["user"].get("email","account")
            disp = st.session_state["sb_user"]["user"].get("user_metadata",{}).get("display_name") or ""
            label = disp or user_email
            c1, c2 = st.columns(2)
            if c1.button("My account", key="top_account"):
                _set_params(view="account"); st.rerun()
            if c2.button("Sign out", key="top_logout"):
                if cookies:
                    if "sb_access" in cookies: del cookies["sb_access"]
                    if "sb_email" in cookies: del cookies["sb_email"]
                    cookies.save()
                sign_out(); st.rerun()

_topbar()
st.divider()

# ---------- Open requested dialog (avoids nested dialogs) ----------
def _maybe_open_requested_dialog():
    if st_dialog is None:
        return
    want = st.session_state.pop("want_dialog", None)
    if ("sb_user" not in st.session_state) and want:
        if want == "login":
            login_dialog()
        elif want == "signup":
            signup_dialog()

_maybe_open_requested_dialog()


# ---------------- Query helpers ----------------
def _get_params() -> Dict[str, str]:
    try: return dict(st.query_params)
    except: return st.experimental_get_query_params()

def _set_params(**kwargs):
    try:
        st.query_params.clear()
        st.query_params.update(kwargs)
    except Exception:
        st.experimental_set_query_params(**kwargs)

# ---------------- Supabase REST helpers ----------------
def _sb_headers():
    """
    Returns (base_url, headers) for Supabase REST calls using the anon/service key.
    Matches auth_rest's key selection so both paths work the same.
    """
    url = st.secrets.get("SUPABASE_URL")
    # Use the same env precedence as auth_rest: ANON first, then KEY
    key = (
        st.secrets.get("SUPABASE_ANON_KEY")
        or st.secrets.get("SUPABASE_KEY")
    )
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL / SUPABASE_ANON_KEY (or SUPABASE_KEY).")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    return url, headers

def rename_item(item_id: str, new_title: str) -> dict:
    url, headers = _sb_headers()
    resp = requests.patch(
        f"{url}/rest/v1/items?id=eq.{item_id}",
        json={"title": new_title},
        headers=headers,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return data[0] if isinstance(data, list) and data else {}

def rename_folder(folder_id: str, new_name: str) -> dict:
    url, headers = _sb_headers()
    resp = requests.patch(
        f"{url}/rest/v1/folders?id=eq.{folder_id}",
        json={"name": new_name},
        headers=headers,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    return data[0] if isinstance(data, list) and data else {}
# ---- cookie-based “stay signed in” (optional, safe import) ----
COOKIE_PASSWORD = st.secrets.get("COOKIE_PASSWORD", "change_me_please")
cookies = None
try:
    from streamlit_cookies_manager import EncryptedCookieManager
    cookies = EncryptedCookieManager(prefix="studybloom.", password=COOKIE_PASSWORD)
    if not cookies.ready():
        st.stop()
except Exception:
    cookies = None  # proceed without cookies if not installed
# Auto prompt login on entry (once per session) if not logged in
if "sb_user" not in st.session_state and not st.session_state.get("auth_prompted") and st_dialog:
    st.session_state["auth_prompted"] = True
    login_dialog()


# ---------- Auth dialogs ----------
def _open_dialog(fn):
    # Tiny helper to open the dialog function immediately
    fn()

try:
    # Streamlit 1.39 supports experimental_dialog decorator
    st_dialog = st.experimental_dialog
except AttributeError:
    st_dialog = None  # Fallback: render inline if dialog unavailable

if st_dialog:
    @st_dialog("Sign in")
    def login_dialog():
        st.write("Welcome back! Please sign in.")
        email = st.text_input("Email", key="dlg_login_email")
        pwd   = st.text_input("Password", type="password", key="dlg_login_pwd")
        remember = st.checkbox("Stay signed in", value=True, key="dlg_login_remember")
        c1, c2 = st.columns([1,1])
        if c1.button("Sign in", type="primary", key="dlg_login_btn"):
            try:
                sign_in(email, pwd)
                if remember and cookies and "sb_user" in st.session_state:
                    tok = st.session_state["sb_user"].get("access_token") or st.session_state["sb_user"].get("session",{}).get("access_token")
                    if tok:
                        cookies["sb_access"] = tok
                        cookies["sb_email"] = email or ""
                        cookies.save()
                st.session_state["auth_prompted"] = True
                st.rerun()
            except Exception as e:
                st.error(str(e))
        if c2.button("Use sign up instead", key="dlg_to_signup"):
            _open_dialog(signup_dialog)

    @st_dialog("Create account")
    def signup_dialog():
        st.write("Create your StudyBloom account.")
        disp = st.text_input("Display name", key="dlg_sign_display")
        uname = st.text_input("Username", key="dlg_sign_username")
        email = st.text_input("Email", key="dlg_sign_email")
        pwd   = st.text_input("Password", type="password", key="dlg_sign_pwd")
        c1, c2 = st.columns([1,1])
        if c1.button("Sign up", type="primary", key="dlg_signup_btn"):
            try:
                sign_up(email, pwd, disp, uname)
                st.success("Check your email to confirm, then sign in.")
                _open_dialog(login_dialog)
            except Exception as e:
                st.error(str(e))
        if c2.button("Have an account? Sign in", key="dlg_to_login"):
            _open_dialog(login_dialog)
else:
    # Fallback (no dialog): render inline messages
    def login_dialog():
        st.warning("Dialog not available in this environment. Use the top-right buttons.")
    def signup_dialog():
        st.warning("Dialog not available in this environment. Use the top-right buttons.")


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

# Restore session from cookie if present
if "sb_user" not in st.session_state and cookies:
    tok = cookies.get("sb_access")
    if tok:
        user = _fetch_user_from_token(tok)
        if user:
            st.session_state["sb_user"] = {"user": user, "access_token": tok}

# ---------------- Progress calc ----------------
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

# ---------------- Renderers ----------------
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
    if not flashcards: st.caption("No flashcards found."); return
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
    if not questions: st.caption("No questions found."); return
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
    if new_col.button("🎲 New quiz", key=f"{key_prefix}_regen") and item_id:
        try:
            quiz_item = get_item(item_id); folder_id = quiz_item.get("folder_id"); subject = subject_hint or "General"
            if folder_id:
                siblings = list_items(folder_id, limit=200); summary = next((s for s in siblings if s.get("kind")=="summary"), None)
                if summary and summary.get("data"):
                    new_qs = generate_quiz_from_notes(summary["data"], subject=subject, audience="high school", num_questions=8, mode="free")
                    created = save_item("quiz", f"{summary['title']} • Quiz (new)", {"questions": new_qs}, folder_id)
                    st.success("New quiz created."); _set_params(item=created.get("id"), view="all"); st.rerun()
                else: st.info("No summary found in this folder to generate from.")
            else:     st.info("Folder not found for this quiz.")
        except Exception as e:
            st.error(f"Re-generate failed: {e}")

# ---------------- Load folders ----------------
if "sb_user" in st.session_state:
    try: ALL_FOLDERS = list_folders()
    except: ALL_FOLDERS = []; st.warning("Could not load folders.")
else:
    ALL_FOLDERS = []

def _roots(rows): return [r for r in rows if not r.get("parent_id")]  # subjects

# ------------- Account Page -------------
params = _get_params()
if (params.get("view") == "account") or (isinstance(params.get("view"), list) and params.get("view")[0] == "account"):
    st.markdown("#### ")
    # Back, top-left
    bcol, _ = st.columns([1,9])
    if bcol.button("← Back", key="acct_back"):
        _set_params(view=None)
        st.rerun()

    st.title("My Account")
    if "sb_user" not in st.session_state:
        st.info("Please sign in first.")
        st.stop()

    # Show current values
    from auth_rest import current_user, update_profile, change_password
    try:
        u = current_user()
    except Exception as e:
        st.error(f"Could not load account: {e}")
        st.stop()

    meta = (u.get("user_metadata") or {})
    curr_display = meta.get("display_name","")
    curr_username = meta.get("username","")
    curr_email = u.get("email","")

    st.subheader("Profile")
    nd = st.text_input("Display name", value=curr_display, key="acct_disp")
    nu = st.text_input("Username", value=curr_username, key="acct_uname")
    st.text_input("Email", value=curr_email, key="acct_email", disabled=True)

    if st.button("Save profile", type="primary", key="acct_save_profile"):
        try:
            update_profile(display_name=nd, username=nu)
            st.success("Profile updated.")
            # Refresh in-memory user so top bar reflects changes
            st.session_state["sb_user"]["user"]["user_metadata"] = {**(st.session_state["sb_user"]["user"].get("user_metadata") or {}), "display_name": nd, "username": nu}
        except Exception as e:
            st.error(f"Update failed: {e}")

    st.subheader("Change password")
    np1 = st.text_input("New password", type="password", key="acct_pwd1")
    np2 = st.text_input("Confirm new password", type="password", key="acct_pwd2")
    if st.button("Change password", key="acct_change_pwd"):
        if not np1 or not np2:
            st.warning("Enter and confirm your new password.")
        elif np1 != np2:
            st.error("Passwords do not match.")
        else:
            try:
                change_password(np1)
                st.success("Password changed.")
            except Exception as e:
                st.error(f"Password change failed: {e}")

    st.stop()


# ---------------- Item PAGE ----------------
params = _get_params()
if "item" in params and "sb_user" in st.session_state:
    item_id = params.get("item")
    if isinstance(item_id, list): item_id = item_id[0]
    try:
        full  = get_item(item_id)
        kind  = full.get("kind")
        title = full.get("title") or kind.title()
        st.title(title)

        # Back -> All Resources
        if st.button("← Back to All Resources", key="item_back_btn"):
            _set_params(view="all"); st.rerun()

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
            _set_params(view="all"); st.rerun()
    st.stop()

# ---------------- Tabs ----------------
view = (_get_params().get("view") or [""])[0] if isinstance(_get_params().get("view"), list) else _get_params().get("view")
default_idx = 2 if view == "all" else (1 if view == "resources" else 0)
tabs = st.tabs(["Quick Study", "Resources", "All Resources"])

# ===== Quick Study (Step-by-step) =====
with tabs[0]:
    st.title("⚡ Quick Study")

    if "sb_user" not in st.session_state:
        st.info("Log in to save your study materials.")
    else:
        # -------- state setup --------
        st.session_state.setdefault("qs_step", 0)                  # 0..5
        st.session_state.setdefault("qs_subject_id", None)
        st.session_state.setdefault("qs_exam_id", None)

        try:
            ALL_FOLDERS = list_folders()
        except Exception:
            ALL_FOLDERS = []

        def _roots(rows): return [r for r in rows if not r.get("parent_id")]
        subjects = _roots(ALL_FOLDERS)
        subj_names = [s["name"] for s in subjects]

        # convenience resolvers
        def _subject_id_from_name(name: str | None) -> str | None:
            if not name: return None
            row = next((s for s in subjects if s["name"] == name), None)
            return row["id"] if row else None

        def _exams_for_subject(sid: str | None):
            if not sid: return []
            return [f for f in ALL_FOLDERS if f.get("parent_id") == sid]

        # ------- step UI helpers -------
        def _nav(prev_ok=True, next_ok=True, show_back=True, show_next=True, next_label="Next"):
            step = st.session_state["qs_step"]
            c1, c2 = st.columns([1,1])
        
            # Back
            if show_back:
                if c1.button("← Back", use_container_width=True, key=f"qs_back_{step}"):
                    st.session_state["qs_step"] = max(0, step - 1)
                    st.rerun()
            else:
                c1.write("")
        
            # Next
            if show_next:
                next_disabled = not next_ok
                if c2.button(
                    next_label,
                    type="primary",
                    use_container_width=True,
                    disabled=next_disabled,
                    key=f"qs_next_{step}",
                ):
                    st.session_state["qs_step"] = step + 1
                    st.rerun()

        # ---------- STEP 0: Subject ----------
        if st.session_state["qs_step"] == 0:
            st.header("1) Subject")
            mode_new = st.radio("Choose how to set the subject", ["Use existing", "Create new"], horizontal=True, key="qs_mode_subject", index=0)

            subject_ok = False
            if mode_new == "Create new":
                new_subject = st.text_input("New subject name", placeholder="e.g., A-Level Mathematics", key="qs_new_subject")
                subject_ok = bool((new_subject or "").strip())
                _nav(prev_ok=False, next_ok=subject_ok, show_back=False)
                if subject_ok:
                    # don’t create yet; just remember the intention
                    st.session_state["qs_subject_id"] = None
            else:
                subj_pick = st.selectbox("Use existing subject", ["— select —"] + subj_names, key="qs_subject_pick")
                subject_ok = subj_pick in subj_names
                if subject_ok:
                    st.session_state["qs_subject_id"] = _subject_id_from_name(subj_pick)
                _nav(prev_ok=False, next_ok=subject_ok, show_back=False)

        # ---------- STEP 1: Exam ----------
        elif st.session_state["qs_step"] == 1:
            st.header("2) Exam")
            # Resolve subject ID if user chose "new" last step — we still allow deferring creation to generation time
            sid = st.session_state.get("qs_subject_id")
            subj_is_new = (st.session_state.get("qs_mode_subject") == "Create new")
            if not sid and not subj_is_new:
                st.warning("Please pick or create a subject first.")
                st.session_state["qs_step"] = 0
                st.rerun()

            exams = _exams_for_subject(sid) if sid else []
            exam_names = [e["name"] for e in exams]
            mode_new_exam = st.radio("Choose how to set the exam", ["Use existing", "Create new"], horizontal=True, key="qs_mode_exam", index=0 if exam_names else 1)

            exam_ok = False
            if mode_new_exam == "Create new":
                new_exam = st.text_input("New exam name", placeholder="e.g., IGCSE May 2026", key="qs_new_exam")
                exam_ok = bool((new_exam or "").strip())
                _nav(next_ok=exam_ok)
                if exam_ok:
                    st.session_state["qs_exam_id"] = None
            else:
                ex_pick = st.selectbox("Use existing exam", ["— select —"] + exam_names, key="qs_exam_pick")
                exam_ok = ex_pick in exam_names
                if exam_ok:
                    st.session_state["qs_exam_id"] = next(e["id"] for e in exams if e["name"]==ex_pick)
                _nav(next_ok=exam_ok)

        # ---------- STEP 2: Topic ----------
        elif st.session_state["qs_step"] == 2:
            st.header("3) Topic")
            topic_ok = False
            topic_name_input = st.text_input("New topic name", placeholder="e.g., Differentiation", key="qs_new_topic")
            topic_ok = bool((topic_name_input or "").strip())
            _nav(next_ok=topic_ok)

        # ---------- STEP 3: Subject hint / audience / detail ----------
        elif st.session_state["qs_step"] == 3:
            st.header("4) Subject context")
            subject_hint = st.text_input("Subject (free text, improves quality)", value=st.session_state.get("qs_subject_hint", "General"), key="qs_subject_hint")
            audience_label = st.selectbox("Audience", ["University","A-Level","IB","GCSE","HKDSE","Primary"], index={"University":0,"A-Level":1,"IB":2,"GCSE":3,"HKDSE":4,"Primary":5}[st.session_state.get("qs_audience_label","University")], key="qs_audience_label")
            detail = st.slider("Detail level", 1, 5, st.session_state.get("qs_detail", 3), key="qs_detail")
            # basic validation: any non-empty hint is fine
            _nav(next_ok=bool((subject_hint or "").strip()))

        # ---------- STEP 4: Quiz type ----------
        elif st.session_state["qs_step"] == 4:
            st.header("5) Quiz settings")
            quiz_mode = st.radio("Choose quiz format", ["Free response", "Multiple choice"], index=0 if st.session_state.get("qs_quiz_mode","Free response")=="Free response" else 1, horizontal=True, key="qs_quiz_mode")
            if quiz_mode == "Multiple choice":
                st.slider("MCQ options per question", 3, 6, st.session_state.get("qs_mcq_opts",4), key="qs_mcq_opts")
            _nav(next_ok=True)

        # ---------- STEP 5: Files + Generate ----------
        elif st.session_state["qs_step"] == 5:
            st.header("6) Upload & Generate")
        
            files = st.file_uploader(
                "Upload files (PDF, PPTX, JPG, PNG, TXT)",
                type=["pdf", "pptx", "jpg", "jpeg", "png", "txt"],
                accept_multiple_files=True,
                key="qs_files",
            )
        
            # Enable generate as soon as at least one file is uploaded
            has_files = bool(files)
            gen_clicked = st.button(
                "Generate Notes + Flashcards + Quiz",
                type="primary",
                disabled=not has_files,
                key="qs_generate_btn",
            )
        
            if gen_clicked and has_files:
                try:
                    # ------- resolve / create folders on demand -------
                    try:
                        all_folders = list_folders()
                    except Exception:
                        all_folders = []
        
                    def _roots(rows): return [r for r in rows if not r.get("parent_id")]
                    def _children(rows, pid): return [r for r in rows if r.get("parent_id") == pid]
        
                    # Subject: use existing id or create from saved name
                    subj_new   = (st.session_state.get("qs_mode_subject") == "Create new")
                    subject_id = st.session_state.get("qs_subject_id")
                    if subj_new:
                        subj_name = (st.session_state.get("qs_new_subject") or "").strip()
                        existing_subjects = _roots(all_folders)
                        subj_names_lower = {s["name"].lower(): s["id"] for s in existing_subjects}
                        if subj_name and subj_name.lower() in subj_names_lower:
                            subject_id = subj_names_lower[subj_name.lower()]
                        else:
                            if not subj_name:
                                st.error("Missing subject name. Go back one step and enter it.")
                                st.stop()
                            created = create_folder(subj_name, None)
                            subject_id = created["id"]
                    if not subject_id:
                        st.error("Could not resolve Subject. Please go back and try again.")
                        st.stop()
        
                    # Exam: use existing id or create from saved name
                    exam_new = (st.session_state.get("qs_mode_exam") == "Create new")
                    exam_id  = st.session_state.get("qs_exam_id")
                    if exam_new:
                        exam_name = (st.session_state.get("qs_new_exam") or "").strip()
                        existing_exams = _children(list_folders(), subject_id)
                        exam_names_lower = {e["name"].lower(): e["id"] for e in existing_exams}
                        if exam_name and exam_name.lower() in exam_names_lower:
                            exam_id = exam_names_lower[exam_name.lower()]
                        else:
                            if not exam_name:
                                st.error("Missing exam name. Go back one step and enter it.")
                                st.stop()
                            created = create_folder(exam_name, subject_id)
                            exam_id = created["id"]
                    if not exam_id:
                        st.error("Could not resolve Exam. Please go back and try again.")
                        st.stop()
        
                    # Topic: always create here (prevent dup)
                    topic_name = (st.session_state.get("qs_new_topic") or "").strip()
                    if not topic_name:
                        st.error("Missing topic name. Go back one step and enter it.")
                        st.stop()
                    existing_topics = _children(list_folders(), exam_id)
                    if topic_name.lower() in {t["name"].lower() for t in existing_topics}:
                        st.error("Topic already exists under this exam. Please choose a different name.")
                        st.stop()
                    created_topic = create_folder(topic_name, exam_id)
                    topic_id = created_topic["id"]
                    topic_name = created_topic["name"]
        
                    # ------- context / settings -------
                    aud_map = {
                        "University": "university",
                        "A-Level": "A-Level",
                        "IB": "A-Level",
                        "GCSE": "high school",
                        "HKDSE": "high school",
                        "Primary": "primary",
                    }
                    audience     = aud_map.get(st.session_state.get("qs_audience_label", "University"), "high school")
                    subject_hint = st.session_state.get("qs_subject_hint", "General")
                    detail       = st.session_state.get("qs_detail", 3)
                    quiz_mode    = st.session_state.get("qs_quiz_mode", "Free response")
                    mcq_options  = st.session_state.get("qs_mcq_opts", 4)
        
                    # ------- pipeline -------
                    prog = st.progress(0, text="Starting…")
        
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
                        data,
                        subject=subject_hint,
                        audience=audience,
                        num_questions=8,
                        mode=("mcq" if quiz_mode == "Multiple choice" else "free"),
                        mcq_options=mcq_options,
                    )
        
                    prog.progress(85, text="Saving items…")
                    base_title  = topic_name or subject_hint or "Study Pack"
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
                    c1, c2, c3 = st.columns(3)
                    if summary_id and c1.button("Open Notes", type="primary", use_container_width=True, key="qs_open_notes"):
                        _set_params(item=summary_id, view="all"); st.rerun()
                    if flash_id and c2.button("Open Flashcards", use_container_width=True, key="qs_open_flash"):
                        _set_params(item=flash_id, view="all"); st.rerun()
                    if quiz_id and c3.button("Open Quiz", use_container_width=True, key="qs_open_quiz"):
                        _set_params(item=quiz_id, view="all"); st.rerun()
        
                except Exception as e:
                    st.error(f"Generation failed: {e}")
        
            # Only show Back on the final step (hide Next)
            _nav(show_next=False)


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

