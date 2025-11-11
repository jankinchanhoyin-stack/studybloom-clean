import sys, importlib.util, os
import re

def extract_verbatim_definitions(raw_text: str, max_defs: int = 120) -> list[dict]:
    """
    Naive extractor for 'Term: definition' or 'Term - definition' lines.
    Keeps the EXACT wording after the delimiter. Returns list of {"term","definition"}.
    """
    defs = []
    seen = set()
    # Work line by line to keep original wording / punctuation
    for line in raw_text.splitlines():
        l = line.strip()
        if not l or len(l) < 5:
            continue
        # Common patterns: Term: Def..., Term - Def..., Term — Def...
        m = re.match(r"^(.{2,100}?)\s*[:\-–—]\s*(.+)$", l)
        if m:
            term = m.group(1).strip()
            definition = m.group(2).strip()
            # Heuristics to avoid false positives
            if len(term) <= 80 and len(definition) >= 3:
                key = term.lower()
                if key not in seen:
                    seen.add(key)
                    defs.append({"term": term, "definition": definition})
                    if len(defs) >= max_defs:
                        break
    return defs


def _import_local_or_data(mod_name: str, filename: str):
    """Try regular import; if it fails, load from /mnt/data/<filename>."""
    try:
        return __import__(mod_name)
    except Exception:
        candidate = os.path.join("/mnt/data", filename)
        if os.path.exists(candidate):
            spec = importlib.util.spec_from_file_location(mod_name, candidate)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore
            sys.modules[mod_name] = mod
            return mod
        raise  # neither local nor /mnt/data available

# make sure the app folder is in path, then attempt imports with fallback
if "/mount/src/studybloom-clean" not in sys.path:
    sys.path.append("/mount/src/studybloom-clean")

pdf_utils = _import_local_or_data("pdf_utils", "pdf_utils.py")
llm       = _import_local_or_data("llm", "llm.py")

# now import the actual functions you need from the loaded modules
from pdf_utils import extract_any
from llm import (
    summarize_text,
    generate_quiz_from_notes,
    generate_flashcards_from_notes,
    grade_free_answer,
)

import streamlit as st

st.markdown("""
<style>
.stButton > button { white-space: nowrap !important; padding: .35rem .65rem !important; line-height: 1.1 !important; }
.small-btn .stButton > button { padding: .25rem .5rem !important; font-size: .9rem !important; }
</style>
""", unsafe_allow_html=True)

import sys, requests, time, copy
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timedelta, timezone
import requests

from pdf_utils import extract_any
from llm import (
    summarize_text,
    generate_quiz_from_notes,
    generate_flashcards_from_notes,
    grade_free_answer,
)
from auth_rest import (
    # auth + items + folders
    sign_in, sign_up, sign_out,
    save_item, list_items, get_item, move_item, delete_item,
    create_folder, list_folders, delete_folder, list_child_folders,

    # quiz/flash progress
    save_quiz_attempt, list_quiz_attempts, list_quiz_attempts_for_items,
    save_flash_review, list_flash_reviews_for_items,

    # profile
    current_user, update_profile, change_password,

    # community (friend requests + friends)
    sb_send_friend_request,
    sb_list_friend_requests,
    sb_respond_friend_request,
    sb_cancel_outgoing_request,
    sb_list_friends_with_profiles,

    # XP totals
    sb_get_xp_totals_for_user,
)


# --- Add these imports at the top of auth_rest.py ---
import requests
import streamlit as st
from datetime import datetime, timedelta, timezone

# --- Supabase REST headers (local to this module) ---
def _sb_headers():
    """
    Returns (base_url, headers) for Supabase REST calls using anon/service key.
    Keep this local so helpers below don't depend on app.py.
    """
    url = st.secrets.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_ANON_KEY") or st.secrets.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL / SUPABASE_ANON_KEY (or SUPABASE_KEY).")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    return url, headers

# --- Time window helpers (UTC) ---
def _iso_start_of_today_utc() -> str:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return start.isoformat()

def _iso_start_of_tomorrow_utc() -> str:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)
    return start.isoformat()

def _iso_start_of_month_utc() -> str:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    return start.isoformat()

def _iso_start_of_next_month_utc() -> str:
    now = datetime.now(timezone.utc)
    month = now.month + 1
    year = now.year + (1 if month == 13 else 0)
    month = 1 if month == 13 else month
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    return start.isoformat()

# --- XP aggregation helpers ---
def sb_sum_xp_for_window(user_id: str, start_iso: str, end_iso: str) -> int:
    """
    Sum xp from xp_events for a user in [start_iso, end_iso).
    """
    url, headers = _sb_headers()
    q = (
        f"{url}/rest/v1/xp_events"
        f"?user_id=eq.{user_id}"
        f"&occurred_at=gte.{start_iso}"
        f"&occurred_at=lt.{end_iso}"
        f"&select=xp"
    )
    r = requests.get(q, headers=headers, timeout=25)
    if r.status_code != 200:
        return 0
    try:
        return int(sum(int(row.get("xp") or 0) for row in r.json()))
    except Exception:
        return 0

def sb_get_xp_totals_for_user(user_id: str) -> dict:
    """
    Returns {"today": int, "month": int}
    """
    today = sb_sum_xp_for_window(user_id, _iso_start_of_today_utc(), _iso_start_of_tomorrow_utc())
    month = sb_sum_xp_for_window(user_id, _iso_start_of_month_utc(), _iso_start_of_next_month_utc())
    return {"today": today, "month": month}

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

# --- Write pending cookies (set by login dialog) BEFORE restore ---
if cookies and st.session_state.get("pending_cookie_token"):
    cookies["sb_access"] = st.session_state.pop("pending_cookie_token") or ""
    cookies["sb_email"]  = st.session_state.pop("pending_cookie_email", "") or ""
    cookies.save()
    # if we had a 'just_logged_out' guard lingering, clear it
    st.session_state.pop("just_logged_out", None)

# Restore session from cookie if present (BEFORE any auto-prompt)
if "sb_user" not in st.session_state and cookies and not st.session_state.get("just_logged_out"):
    tok = cookies.get("sb_access") or ""
    if tok:
        user = _fetch_user_from_token(tok)
        if user:
            st.session_state["sb_user"] = {"user": user, "access_token": tok}

# clear the guard after we pass the restore point
if st.session_state.get("just_logged_out"):
    st.session_state.pop("just_logged_out")

from datetime import datetime, timedelta, timezone
from typing import Tuple

def _parse_iso(ts: str) -> datetime:
    try:
        if ts.endswith("Z"):
            return datetime.fromisoformat(ts[:-1]).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(ts).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)

def _window_bounds(kind: str) -> Tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if kind == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start, end
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end

def compute_xp(period: str = "today") -> Tuple[int, int]:
    """
    Returns (flash_known_count, quiz_correct_count) for the given period.
    Uses list_items(), list_flash_reviews_for_items(), and list_quiz_attempts_for_items().
    """
    if "sb_user" not in st.session_state:
        return 0, 0

    try:
        items = list_items(None, limit=2000)
    except Exception:
        items = []

    quiz_ids = [it["id"] for it in items if it.get("kind") == "quiz"]
    flash_ids = [it["id"] for it in items if it.get("kind") == "flashcards"]

    start, end = _window_bounds("today" if period == "today" else "month")

    flash_known = 0
    try:
        if flash_ids:
            reviews = list_flash_reviews_for_items(flash_ids) or []
            for r in reviews:
                ts = _parse_iso(r.get("created_at", ""))
                if start <= ts < end and r.get("known") is True:
                    flash_known += 1
    except Exception:
        pass

    quiz_correct = 0
    try:
        if quiz_ids:
            attempts = list_quiz_attempts_for_items(quiz_ids) or []
            for a in attempts:
                ts = _parse_iso(a.get("created_at", ""))
                if start <= ts < end:
                    quiz_correct += int(a.get("correct", 0) or 0)
    except Exception:
        pass

    return flash_known, quiz_correct


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
def move_folder_parent(folder_id: str, new_parent_id: Optional[str]) -> dict:
    """Move a folder to a new parent (subjects have parent_id=None)."""
    url, headers = _sb_headers()
    payload = {"parent_id": new_parent_id}  # can be None for a root Subject
    resp = requests.patch(
        f"{url}/rest/v1/folders?id=eq.{folder_id}",
        json=payload,
        headers=headers,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
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
                # Capture the response for a reliable token source
                sess = sign_in(email, pwd)  # make sure auth_rest.sign_in returns the session dict
                # Try multiple spots for the token
                token = (
                    (st.session_state.get("sb_user") or {}).get("access_token")
                    or (st.session_state.get("sb_user") or {}).get("session", {}).get("access_token")
                    or (sess or {}).get("access_token")
                    or (sess or {}).get("session", {}).get("access_token")
                )
        
                # Prefer writing via the top-level hook (works even if cookies is None here)
                if remember:
                    st.session_state["pending_cookie_token"] = token or ""
                    st.session_state["pending_cookie_email"] = email or ""
        
                # Mark we handled auth so we don't pop the dialog again
                st.session_state["auth_prompted"] = True
                # Defensive: clear any logout guard
                st.session_state.pop("just_logged_out", None)
        
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
# ---------- Top bar ----------
def _topbar():
    left, right = st.columns([8, 4])
    with left:
        st.markdown("<h1 style='margin:0;'>StudyBloom</h1>", unsafe_allow_html=True)

    # If not signed in, show Sign in / Sign up on the right
    with right:
        if "sb_user" not in st.session_state:
            c1, c2 = st.columns(2)
            if c1.button("Sign in", key="hdr_signin"):
                if st_dialog is not None:
                    login_dialog()
                else:
                    st.warning("Sign-in dialog not supported in this environment.")
            if c2.button("Sign up", key="hdr_signup"):
                if st_dialog is not None:
                    signup_dialog()
                else:
                    st.warning("Sign-up dialog not supported in this environment.")
        else:
            # Signed-in header can stay clean since Profile/Sign out live elsewhere
            pass


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
                try:
                    sess = sign_in(email, pwd)  # will raise with detailed message on failure
                except Exception as e:
                    st.error(str(e))
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

from typing import Dict, List, Optional

def compute_topic_stats(topic_id: Optional[str]) -> Dict[str, float]:
    """
    Aggregates latest quiz performance and flashcard 'known' ratio for a topic.
    Returns:
      progress   : blended 0..1 (60% quiz avg + 40% flash known)
      quiz_avg   : latest-per-quiz average percent (0..1)
      quiz_count : number of quizzes counted
      flash_known: share of reviews marked 'known' (0..1)
      flash_reviews: number of flash reviews counted
    """
    if not topic_id:
        return {"progress": 0.0, "quiz_avg": 0.0, "quiz_count": 0,
                "flash_known": 0.0, "flash_reviews": 0}

    try:
        items = list_items(topic_id, limit=500)
    except Exception:
        items = []

    quiz_ids  = [it["id"] for it in items if it.get("kind") == "quiz"]
    flash_ids = [it["id"] for it in items if it.get("kind") == "flashcards"]

    # ---- Quiz: take the latest attempt per quiz, then average their % scores
    quiz_avg = 0.0
    quiz_count = 0
    if quiz_ids:
        try:
            attempts = list_quiz_attempts_for_items(quiz_ids)  # multiple rows per quiz
        except Exception:
            attempts = []

        latest_by_quiz: Dict[str, dict] = {}
        for at in attempts:
            iid = at.get("item_id")
            if not iid:
                continue
            if (iid not in latest_by_quiz) or (at.get("created_at", "") > latest_by_quiz[iid].get("created_at", "")):
                latest_by_quiz[iid] = at

        if latest_by_quiz:
            pct_values: List[float] = []
            for a in latest_by_quiz.values():
                c, t = a.get("correct", 0), a.get("total", 0)
                pct_values.append((c / t) if t else 0.0)
            quiz_count = len(pct_values)
            quiz_avg = sum(pct_values) / quiz_count if quiz_count else 0.0

    # ---- Flashcards: overall known ratio across reviews
    flash_known = 0.0
    flash_reviews = 0
    if flash_ids:
        try:
            reviews = list_flash_reviews_for_items(flash_ids)
        except Exception:
            reviews = []
        flash_reviews = len(reviews)
        if flash_reviews:
            flash_known = sum(1 for r in reviews if r.get("known")) / flash_reviews

    progress = 0.6 * quiz_avg + 0.4 * flash_known
    return {
        "progress": progress,
        "quiz_avg": quiz_avg,
        "quiz_count": quiz_count,
        "flash_known": flash_known,
        "flash_reviews": flash_reviews,
    }



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
    if not flashcards:
        st.caption("No flashcards found.")
        return

    # ---------- Session state ----------
    # Full queue of remaining indices (we'll pop from here), but keep a fixed total.
    st.session_state.setdefault(f"{key_prefix}_order", list(range(len(flashcards))))
    st.session_state.setdefault(f"{key_prefix}_revealed", False)
    st.session_state.setdefault(f"{key_prefix}_total", len(flashcards))
    st.session_state.setdefault(f"{key_prefix}_known_set", set())   # unique known card indices
    st.session_state.setdefault(f"{key_prefix}_again_set", set())   # unique "don't know" indices
    st.session_state.setdefault(f"{key_prefix}_idx", 0)             # pointer in current order

    order = st.session_state[f"{key_prefix}_order"]
    total = st.session_state[f"{key_prefix}_total"]
    known_set: set = st.session_state[f"{key_prefix}_known_set"]
    again_set: set = st.session_state[f"{key_prefix}_again_set"]
    revealed = st.session_state[f"{key_prefix}_revealed"]

    # If the queue is empty, we're done
    if not order:
        known = len(known_set)
        dontknow = len(again_set - known_set)  # anything not later upgraded to known
        st.success("Deck complete — nice work!")
        st.metric("Known", f"{known}/{total}")
        st.metric("Don't know", f"{dontknow}/{total}")
        # Completion bar (100%)
        st.progress(1.0, text="Complete")
        if st.button("🔁 Restart", key=f"{key_prefix}_restart_all"):
            st.session_state[f"{key_prefix}_order"] = list(range(total))
            st.session_state[f"{key_prefix}_revealed"] = False
            st.session_state[f"{key_prefix}_idx"] = 0
            st.session_state[f"{key_prefix}_known_set"] = set()
            st.session_state[f"{key_prefix}_again_set"] = set()
            st.rerun()
        return

    # Clamp idx to valid range
    idx = st.session_state[f"{key_prefix}_idx"]
    if idx >= len(order): idx = len(order) - 1
    if idx < 0: idx = 0
    st.session_state[f"{key_prefix}_idx"] = idx

    # Current card
    orig_i = order[idx]
    card = flashcards[orig_i]

    # ---------- Progress ----------
    # "Done" = how many unique cards have been judged (known or again at least once)
    judged = len(known_set | again_set)
    # Current position number should be judged + 1 (the one we're currently on),
    # but cap at total (when returning to old cards).
    current_num = min(judged + 1, total)
    # Top progress bar: overall completion (judged / total)
    st.progress(judged / max(1, total), text=f"Card {current_num}/{total}")
    # Small stats row
    cstat1, cstat2, cstat3 = st.columns(3)
    with cstat1:
        st.metric("Known", f"{len(known_set)}/{total}")
    with cstat2:
        st.metric("Don't know", f"{len(again_set - known_set)}/{total}")
    with cstat3:
        st.metric("Remaining", f"{total - len(known_set | again_set)}")

    # ---------- Card UI ----------
    st.markdown("#### Front")
    st.info(card.get("front", ""))
    if revealed:
        st.markdown("#### Back")
        st.success(card.get("back", ""))

    # ---------- Controls ----------
    c1, c2, c3, c4 = st.columns(4)

    # Prev: move pointer back within current queue (doesn't change judged counts)
    if c1.button("◀️ Prev", disabled=(idx == 0), key=f"{key_prefix}_prev"):
        st.session_state[f"{key_prefix}_idx"] = max(0, idx - 1)
        st.session_state[f"{key_prefix}_revealed"] = False
        st.rerun()

    # Flip
    if c2.button("🔁 Flip", key=f"{key_prefix}_flip"):
        st.session_state[f"{key_prefix}_revealed"] = not revealed
        st.rerun()

    # Known
    if c3.button("✅ Knew it", key=f"{key_prefix}_ok"):
        # If this card had previously been "again", upgrade it to known
        if orig_i in again_set:
            again_set.discard(orig_i)
        known_set.add(orig_i)

        # Optional: persist a positive review
        if item_id and "sb_user" in st.session_state:
            try:
                save_flash_review(item_id, True)
            except Exception:
                pass

        # Remove this card from the queue so we don't see it again this run
        order.pop(idx)
        # Keep pointer on next card (same idx now points to the following card)
        if idx >= len(order):
            st.session_state[f"{key_prefix}_idx"] = max(0, len(order) - 1)
        st.session_state[f"{key_prefix}_revealed"] = False
        st.rerun()

    # Again
    if c4.button("❌ Again", key=f"{key_prefix}_bad"):
        # Count once (unique). If later "Known", we'll move it.
        if orig_i not in known_set:
            again_set.add(orig_i)

        # Optional: persist a negative review
        if item_id and "sb_user" in st.session_state:
            try:
                save_flash_review(item_id, False)
            except Exception:
                pass

        # Re-queue this card a few ahead (spaced repetition lite)
        # Move pointer to next and insert this index again later
        order.pop(idx)
        insert_at = min(len(order), idx + 4)
        order.insert(insert_at, orig_i)

        # Pointer stays at same idx to show the next card
        if idx >= len(order):
            st.session_state[f"{key_prefix}_idx"] = max(0, len(order) - 1)
        st.session_state[f"{key_prefix}_revealed"] = False
        st.rerun()

def interactive_quiz(questions: List[dict], item_id: Optional[str]=None, key_prefix="quiz", subject_hint="General"):
    st.subheader("🧪 Quiz")
    if not questions:
        st.caption("No questions found.")
        return

    # ---------- Session state ----------
    total = len(questions)
    st.session_state.setdefault(f"{key_prefix}_i", 0)                 # current index pointer
    st.session_state.setdefault(f"{key_prefix}_graded", False)        # whether the current Q has been graded
    st.session_state.setdefault(f"{key_prefix}_feedback", "")
    st.session_state.setdefault(f"{key_prefix}_mark_last", (0, 0))    # (score, max)
    st.session_state.setdefault(f"{key_prefix}_history", [])          # per-Q {score,max}
    st.session_state.setdefault(f"{key_prefix}_answered_set", set())  # indices answered at least once
    st.session_state.setdefault(f"{key_prefix}_correct_set", set())   # indices currently judged correct (unique)

    i = st.session_state[f"{key_prefix}_i"]
    i = max(0, min(i, total - 1))
    st.session_state[f"{key_prefix}_i"] = i

    q = questions[i]
    is_mcq = "options" in q and isinstance(q.get("options"), list)

    # ---------- Progress (global) ----------
    answered_set: set = st.session_state[f"{key_prefix}_answered_set"]
    correct_set: set  = st.session_state[f"{key_prefix}_correct_set"]

    answered = len(answered_set)
    correct  = len(correct_set)
    incorrect = max(0, answered - correct)
    remaining = total - answered

    # Progress bar shows answered/total; text displays correctness counts
    st.progress(
        answered / max(1, total),
        text=f"Question {i+1}/{total} • ✅ {correct}  ❌ {incorrect}  • Remaining {remaining}"
    )

    # ---------- Render current question ----------
    st.markdown(f"### {q.get('question','')}")

    def _mark_and_record(score: int, max_points: int, was_correct: bool):
        """Update per-question state + history + sets."""
        # Mark graded + last mark
        st.session_state[f"{key_prefix}_graded"] = True
        st.session_state[f"{key_prefix}_mark_last"] = (score, max_points)

        # Update answered/correct sets for this index
        answered_set.add(i)
        if was_correct:
            correct_set.add(i)
        else:
            if i in correct_set:
                correct_set.discard(i)

        # Ensure history has an entry for this index
        hist = st.session_state[f"{key_prefix}_history"]
        entry = {"score": score, "max": max_points, "correct": bool(was_correct)}
        if len(hist) <= i:
            # pad with blanks if needed
            hist.extend([{} for _ in range(i - len(hist) + 1)])
            hist[i] = entry
        else:
            hist[i] = entry

    if is_mcq:
        options = q.get("options") or []
        # Use a unique key per question so Streamlit keeps selections per index
        choice = st.radio("Choose one", options, key=f"{key_prefix}_mcq_{i}", index=None)

        col1, col2, col3 = st.columns(3)

        if col1.button("Submit", key=f"{key_prefix}_mcq_submit"):
            if choice is None:
                st.warning("Pick an option first.")
            else:
                correct_idx = q.get("correct_index", -1)
                is_correct = (options.index(choice) == correct_idx)
                _mark_and_record(score=(10 if is_correct else 0), max_points=10, was_correct=is_correct)

                st.success("Correct! ✅" if is_correct else "Not quite. ❌")
                if q.get("explanation"):
                    st.info(q["explanation"])

        if col2.button("◀️ Prev", disabled=(i == 0), key=f"{key_prefix}_prev"):
            st.session_state[f"{key_prefix}_i"] = i - 1
            st.session_state[f"{key_prefix}_graded"] = False
            st.rerun()

        if col3.button("Next ▶️", disabled=(i == total - 1), key=f"{key_prefix}_next"):
            st.session_state[f"{key_prefix}_i"] = i + 1
            st.session_state[f"{key_prefix}_graded"] = False
            st.rerun()

    else:
        ans = st.text_area(
            "Your answer",
            key=f"{key_prefix}_ans_{i}",
            height=120,
            placeholder="Type your working/answer here…"
        )

        colg1, colg2, colg3, colg4 = st.columns(4)

        if colg1.button("Submit", key=f"{key_prefix}_submit"):
            try:
                result = grade_free_answer(
                    q.get("question",""),
                    q.get("model_answer",""),
                    q.get("markscheme_points",[]) or [],
                    ans or "",
                    subject_hint or "General"
                )
                score = int(result.get("score", 0) or 0)
                maxp  = int(result.get("max_points", 10) or 10)
                # same rule you use when saving attempts (>=70% is "correct")
                is_correct = (maxp > 0 and score >= 0.7 * maxp)

                _mark_and_record(score=score, max_points=maxp, was_correct=is_correct)

                st.success(f"Score for this question: {score} / {maxp}")
                if result.get("feedback"):
                    st.info(result["feedback"])
            except Exception as e:
                st.error(f"Grading failed: {e}")

        if st.session_state[f"{key_prefix}_graded"]:
            sc, mx = st.session_state[f"{key_prefix}_mark_last"]
            with st.expander("Model answer & mark scheme", expanded=False):
                st.markdown(q.get("model_answer",""))
                for pt in q.get("markscheme_points",[]) or []:
                    st.markdown(f"- {pt}")

        if colg2.button("◀️ Prev", disabled=(i == 0), key=f"{key_prefix}_prev"):
            st.session_state[f"{key_prefix}_i"] = i - 1
            st.session_state[f"{key_prefix}_graded"] = False
            st.session_state[f"{key_prefix}_feedback"] = ""
            st.rerun()

        if colg3.button("Next ▶️", disabled=(i == total - 1), key=f"{key_prefix}_next"):
            st.session_state[f"{key_prefix}_i"] = i + 1
            st.session_state[f"{key_prefix}_graded"] = False
            st.session_state[f"{key_prefix}_feedback"] = ""
            st.rerun()

    # ---------- Totals + Save ----------
    total_sc = sum((h.get("score", 0) or 0) for h in st.session_state[f"{key_prefix}_history"] if isinstance(h, dict))
    total_mx = sum((h.get("max",   0) or 0) for h in st.session_state[f"{key_prefix}_history"] if isinstance(h, dict))

    m1, m2, m3 = st.columns(3)
    m1.metric("Answered", f"{answered}/{total}")
    m2.metric("Correct", f"{correct}/{answered or 1}")
    m3.metric("Score", f"{total_sc} / {total_mx or (total*10)}")

    # Save attempt (uses your existing rule: correct if >=70% of marks for FR; true/false for MCQ)
    if st.button("✅ Finish & Save", key=f"{key_prefix}_finish"):
        if item_id and "sb_user" in st.session_state:
            try:
                # If history has explicit "correct", use it. Otherwise fallback to score rule.
                hist = st.session_state[f"{key_prefix}_history"]
                corr = 0
                tot  = 0
                for idx, h in enumerate(hist):
                    if not isinstance(h, dict) or ("score" not in h and "correct" not in h):
                        continue
                    tot += 1
                    if "correct" in h:
                        if h["correct"]: corr += 1
                    else:
                        sc = h.get("score", 0) or 0
                        mx = h.get("max", 10) or 10
                        if mx > 0 and sc >= 0.7 * mx:
                            corr += 1
                # If some questions weren’t answered, tot may be less than total; still save what we have
                save_quiz_attempt(item_id, corr, (tot or total), hist)
                st.success(f"Attempt saved: {corr}/{tot or total}")
            except Exception:
                st.info("Attempt not saved (check quiz_attempts table).")


# ---------------- Load folders ----------------
if "sb_user" in st.session_state:
    try: ALL_FOLDERS = list_folders()
    except: ALL_FOLDERS = []; st.warning("Could not load folders.")
else:
    ALL_FOLDERS = []

def _roots(rows): return [r for r in rows if not r.get("parent_id")]  # subjects

# ================================
# My Account / Profile Page (Full)
# ================================

# --- Minimal CSS for this page ---
st.markdown("""
<style>
#acct_signout button {
  border: 1px solid #ef4444 !important;
  color: #b91c1c !important;
  background: #fff !important;
  border-radius: 10px !important;
  font-weight: 600 !important;
  padding: .4rem .9rem !important;
}
#acct_signout button:hover { background: #fee2e2 !important; }
.xp-box {
  border: 1px solid rgba(0,0,0,0.08);
  border-radius: 10px;
  padding: .8rem .9rem;
  background: #fff;
}
</style>
""", unsafe_allow_html=True)

# Route check
params = _get_params()
is_account = (params.get("view") == "account") or (
    isinstance(params.get("view"), list) and params.get("view")[0] == "account"
)

if is_account:
    # Top row: Back
    back_col, _ = st.columns([1, 9])
    if back_col.button("← Back", key="acct_back"):
        _set_params(view=None)
        st.rerun()

    st.title("My Account")

    # Must be signed in
    if "sb_user" not in st.session_state:
        st.info("Please sign in first.")
        st.stop()

    # Load current user
    try:
        u = current_user()
    except Exception as e:
        st.error(f"Could not load account: {e}")
        st.stop()

    meta = (u.get("user_metadata") or {})
    curr_display = meta.get("display_name", "")
    curr_username = meta.get("username", "")
    curr_email   = u.get("email", "")

    # ==============
    # Profile & Auth
    # ==============
    cL, cR = st.columns([3, 2], gap="large")

    with cL:
        st.subheader("Profile")
        nd = st.text_input("Display name", value=curr_display, key="acct_disp")
        nu = st.text_input("Username", value=curr_username, key="acct_uname")
        st.text_input("Email", value=curr_email, key="acct_email", disabled=True)

        if st.button("Save profile", type="primary", key="acct_save_profile"):
            try:
                update_profile(display_name=nd, username=nu)
                # refresh in-memory user so header/places reflect changes
                st.session_state["sb_user"]["user"]["user_metadata"] = {
                    **(st.session_state["sb_user"]["user"].get("user_metadata") or {}),
                    "display_name": nd,
                    "username": nu,
                }
                st.success("Profile updated.")
            except Exception as e:
                st.error(f"Update failed: {e}")

    with cR:
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

    st.divider()

    # =======
    # XP Area
    # =======
    
    st.subheader("✨ Your XP")
    
    # XP counts
    fc_today, qz_today   = compute_xp("today")
    fc_month, qz_month   = compute_xp("month")
    xp_today  = fc_today + qz_today
    xp_month  = fc_month + qz_month
    
    # Targets
    DAILY_XP_GOAL   = 60
    MONTHLY_XP_GOAL = 3000
    
    # Checkpoints with emojis
    CHECKPOINTS = [
        (1000, "📘 Rising Scholar"),
        (2000, "🎓 Seasoned Scholar"),
        (3000, "🏆 Master Scholar"),
    ]
    
    # Ratios (bars cap at 100%)
    daily_ratio   = min(1.0, xp_today / max(1, DAILY_XP_GOAL))
    monthly_ratio = min(1.0, xp_month / max(1, MONTHLY_XP_GOAL))
    
    # --- CSS tweaks ---

    st.markdown("""
    <style>
    /* ---- XP Progress Bars ---- */
    .stProgress > div[data-testid="stProgressBar"] {
        background-color: rgba(255,255,255,0.08) !important; /* subtle track for dark bg */
        border-radius: 10px !important;
    }
    .stProgress > div[data-testid="stProgressBar"] > div {
        background-color: #3B82F6 !important; /* clean Tailwind blue-500 fill */
        border-radius: 10px !important;
    }
    
    /* ---- XP Container Styling ---- */
    .xp-box {
        border: 1px solid rgba(255,255,255,0.07);
        border-radius: 10px;
        padding: .9rem 1rem;
        background: rgba(255,255,255,0.04); /* gentle contrast on #0E1117 */
        color: #E5E7EB; /* text-light */
    }
    
    /* ---- General Background ---- */
    body, .stApp {
        background-color: #0E1117 !important;
        color: #E5E7EB !important;
    }
    
    /* ---- Font Improvements ---- */
    h1, h2, h3, h4, h5, h6, .stMarkdown p {
        font-family: 'Inter', 'Segoe UI', sans-serif !important;
        letter-spacing: 0.2px;
    }
    
    /* ---- Metrics (Flashcards / Quiz) ---- */
    [data-testid="stMetricValue"] {
        color: #E5E7EB !important;
    }
    [data-testid="stMetricLabel"] {
        color: #9CA3AF !important;
        font-weight: 600;
    }
    </style>
    """, unsafe_allow_html=True)
    
        
    # --- Layout ---
    with st.container():
        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown("<div class='xp-box'>", unsafe_allow_html=True)
            label = f"**Today's XP:** {xp_today} / {DAILY_XP_GOAL}"
            if xp_today > DAILY_XP_GOAL:
                label += f"  •  🎉 exceeded by {xp_today - DAILY_XP_GOAL}"
            st.write(label)
            st.progress(daily_ratio)
            st.caption("Daily XP = Flashcards you marked **Knew it** + Quiz questions answered correctly today.")
            st.markdown("</div>", unsafe_allow_html=True)
        with c2:
            st.metric("Flashcards ✅ today", fc_today)
            st.metric("Quiz correct today", qz_today)
    
        st.markdown("")
    
        c3, c4 = st.columns([3, 2])
        with c3:
            st.markdown("<div class='xp-box'>", unsafe_allow_html=True)
            label = f"**This Month's XP:** {xp_month} / {MONTHLY_XP_GOAL}"
            if xp_month > MONTHLY_XP_GOAL:
                label += f"  •  🎉 exceeded by {xp_month - MONTHLY_XP_GOAL}"
            st.write(label)
            st.progress(monthly_ratio)
    
            reached = [(cp, name) for cp, name in CHECKPOINTS if xp_month >= cp]
            if reached:
                last_cp, last_name = reached[-1]
                st.success(f"Checkpoint reached: **{last_name}** ({last_cp} XP).")
            next_cp = next(((cp, name) for cp, name in CHECKPOINTS if xp_month < cp), None)
            if next_cp:
                cp_val, cp_name = next_cp
                st.caption(f"Next checkpoint: **{cp_name}** at {cp_val} XP — {max(0, cp_val - xp_month)} to go.")
            else:
                st.caption("All monthly checkpoints achieved — incredible progress! 🥳")
            st.markdown("</div>", unsafe_allow_html=True)
        with c4:
            st.metric("Flashcards ✅ this month", fc_month)
            st.metric("Quiz correct this month", qz_month)
    
        st.divider()

    # =========
    # Sign out
    # =========
    _, sign_col = st.columns([6, 1])
    with sign_col:
        if st.button("Sign out", key="acct_signout"):
            # Clear cookies
            try:
                if cookies:
                    if "sb_access" in cookies:
                        del cookies["sb_access"]
                    if "sb_email" in cookies:
                        del cookies["sb_email"]
                    cookies.save()
            except Exception:
                pass

            # Clear session + prevent immediate auto-restore
            st.session_state.pop("sb_user", None)
            st.session_state.pop("pending_cookie_token", None)
            st.session_state.pop("pending_cookie_email", None)
            st.session_state["just_logged_out"] = True

            # Best-effort server sign-out
            try:
                sign_out()
            except Exception:
                pass

            # Go home
            _set_params(view=None)
            st.rerun()

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
        if st.button("← Back to Resources", key="item_back_btn"):
            _set_params(view="all")
            st.rerun()

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
        if st.button("← Back to Resources", key="item_back_btn"):
            _set_params(view="all")
            st.rerun()
    st.stop()

# ---------------- Router: open a specific tab by URL param ----------------
_view_param = _get_params().get("view")
view_param = (_view_param[0] if isinstance(_view_param, list) else _view_param) or ""

def render_community_page():
    # --- Back to home ---
    top_l, _ = st.columns([1, 9])
    if top_l.button("← Back", key="comm_back"):
        _set_params(view=None)
        st.rerun()

    # Optional: guard if not signed in
    if "sb_user" not in st.session_state:
        st.info("Please sign in to use Community features.")
        return
    st.markdown("## 🌐 Community")

    if "sb_user" not in st.session_state:
        st.info("Sign in to use community features.")
        return

    # --- Add friend by username ---
    st.markdown("### Add a friend")
    add_c1, add_c2 = st.columns([4, 1.3])
    with add_c1:
        new_friend_username = st.text_input("Friend’s username", key="comm_add_username", placeholder="e.g., alex_123")
    with add_c2:
        if st.button("Send request", key="comm_send_req"):
            from auth_rest import sb_send_friend_request
            msg = sb_send_friend_request((new_friend_username or "").strip())
            if msg.lower().startswith(("error", "no user", "please sign in", "you can’t", "you can't")):
                st.error(msg)
            else:
                st.success(msg)
                st.rerun()

    st.divider()

    # --- Requests tabs ---
    st.markdown("### Requests")
    t1, t2 = st.tabs(["Incoming", "Outgoing"])

    from auth_rest import (
        sb_list_friend_requests, sb_respond_friend_request, sb_cancel_outgoing_request,
        sb_list_friends_with_profiles, sb_get_xp_totals_for_user
    )

    with t1:
        incoming = sb_list_friend_requests("incoming")
        if not incoming:
            st.caption("No incoming requests.")
        else:
            for r in incoming:
                u = r["requester"]
                line = f"**@{u.get('username','')}** ({u.get('display_name','')})  — _{r['status']}_"
                cA, cB, cC = st.columns([6, 1, 1])
                cA.markdown(line)
                if r["status"] == "pending":
                    if cB.button("Accept", key=f"req_acc_{r['id']}"):
                        msg = sb_respond_friend_request(r["id"], "accept")
                        (st.success if msg.startswith("Request accepted") else st.error)(msg)
                        st.rerun()
                    if cC.button("Decline", key=f"req_dec_{r['id']}"):
                        msg = sb_respond_friend_request(r["id"], "decline")
                        (st.success if msg.startswith("Request declined") else st.error)(msg)
                        st.rerun()

    with t2:
        outgoing = sb_list_friend_requests("outgoing")
        if not outgoing:
            st.caption("No outgoing requests.")
        else:
            for r in outgoing:
                u = r["recipient"]
                line = f"To **@{u.get('username','')}** ({u.get('display_name','')})  — _{r['status']}_"
                cA, cB = st.columns([6, 1])
                cA.markdown(line)
                if r["status"] == "pending":
                    if cB.button("Cancel", key=f"req_cancel_{r['id']}"):
                        msg = sb_cancel_outgoing_request(r["id"])
                        (st.success if msg.startswith("Request cancelled") else st.error)(msg)
                        st.rerun()

    st.divider()

    # --- Friends leaderboard (today/month) ---
    st.markdown("### Friends’ XP")

    try:
        me = current_user()  # returns the Supabase user object
    except Exception as e:
        st.error(f"Could not load your profile: {e}")
        return
    
    # robustly get the ID and a display name
    me_id = me.get("id") or (me.get("user") or {}).get("id")
    me_meta = (me.get("user_metadata") or {})
    me_name = me_meta.get("display_name") or me.get("email") or "me"
    if not me_id:
        st.error("Could not resolve your account ID. Try reloading or signing in again.")
        return

    rows = []
    my_xp = sb_get_xp_totals_for_user(me_id)
    rows.append({"User": f"🧠 {me_name} (you)", "Today XP": my_xp["today"], "Month XP": my_xp["month"]})

    for f in sb_list_friends_with_profiles():
        xp = sb_get_xp_totals_for_user(f["id"])
        uname = f.get("username") or "friend"
        dname = f.get("display_name") or uname
        rows.append({"User": f"👤 {dname} (@{uname})", "Today XP": xp["today"], "Month XP": xp["month"]})

    # Simple table
    import pandas as pd
    if rows:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("No friends to show yet — send a request above!")


def render_resources_page():
    # ← Home (top-left)
    bcol, _ = st.columns([1, 9])
    if bcol.button("← Home", key="res_back_home"):
        _set_params(view=None)
        st.rerun()

    st.markdown("## 🧭 Resources — Folder Explorer")

    if "sb_user" not in st.session_state:
        st.info("Log in to view your resources.")
        return

    # ---------- load data ----------
    try:
        ALL_FOLDERS = list_folders()
    except Exception:
        ALL_FOLDERS = []
    try:
        ALL_ITEMS = list_items(None, limit=2000)
    except Exception:
        ALL_ITEMS = []

    # ---------- utils ----------
    def roots(rows): return [r for r in rows if not r.get("parent_id")]                # Subjects
    def children(rows, pid): return [r for r in rows if r.get("parent_id") == pid]     # Exams/Topics

    def count_items_in_folder(fid: str) -> dict:
        # Count ONLY direct items in folder (not deep)
        d = {"summary":0, "flashcards":0, "quiz":0}
        for it in ALL_ITEMS:
            if it.get("folder_id") == fid:
                k = it.get("kind")
                if k in d: d[k] += 1
        return d

    def folder_card(folder: dict, level: str, key_prefix: str, move_targets: list):
        """Render one folder card with actions (no nested columns-in-columns)."""
        import datetime as _dt
    
        cont = st.container()
    
        name = folder.get("name", "Untitled")
        when = (folder.get("created_at", "")[:16].replace("T", " "))
        if level == "topic":
            try:
                s = compute_topic_stats(folder["id"])
                cont.progress(s["progress"], text=f"{int(s['progress']*100)}%")
            except Exception:
                pass
        cnt = count_items_in_folder(folder["id"])
    
        # Row 1: title + meta (two columns, single nesting level)
        left, right = cont.columns([7.5, 4.5])
        with left:
            cont.markdown(
                f"**{name}**  <span style='opacity:.6'>— {when}</span><br>"
                f"<span style='opacity:.8'> </span>",
                unsafe_allow_html=True,
            )
        with right:
            # put nothing here; actions go in a new row at container level
    
            pass
    
        # Row 2: actions (create columns at container level, not inside 'right')
        a1, a2, a3, a4 = cont.columns([1.1, 1.1, 1.8, 1.2])
    
        # Open (go to All Resources)
        if a1.button("Open", key=f"{key_prefix}_open_{folder['id']}", use_container_width=True):
            _set_params(view="all")
            st.rerun()
    
        # Rename inline
        edit_key = f"{key_prefix}_edit_{folder['id']}"
        if not st.session_state.get(edit_key):
            if a2.button("Rename", key=f"{key_prefix}_rn_btn_{folder['id']}", use_container_width=True):
                st.session_state[edit_key] = True
                st.rerun()
        else:
            newn = cont.text_input("New name", value=name, key=f"{key_prefix}_rn_val_{folder['id']}")
            s1, s2 = cont.columns(2)
            if s1.button("Save", key=f"{key_prefix}_rn_save_{folder['id']}"):
                try:
                    rename_folder(folder["id"], (newn or "").strip())
                    st.session_state[edit_key] = False
                    st.success("Renamed."); st.rerun()
                except Exception as e:
                    st.error(f"Rename failed: {e}")
            if s2.button("Cancel", key=f"{key_prefix}_rn_cancel_{folder['id']}"):
                st.session_state[edit_key] = False; st.rerun()
    
        # Move (simulate drag) — only for exams/topics
        if level in ("exam", "topic"):
            target_map = {f["name"]: f["id"] for f in move_targets}
            target_names = sorted(target_map.keys(), key=str.lower)
            tgt = a3.selectbox("Move to…", ["—"] + target_names, key=f"{key_prefix}_move_{folder['id']}")
            if tgt != "—":
                try:
                    move_folder_parent(folder["id"], target_map[tgt])
                    st.success("Moved."); st.rerun()
                except Exception as e:
                    st.error(f"Move failed: {e}")
        else:
            a3.write("")  # spacer for subjects
    
        # Delete with confirm
        del_key = f"{key_prefix}_del_{folder['id']}"
        if not st.session_state.get(del_key):
            if a4.button("Delete", key=f"{key_prefix}_del_btn_{folder['id']}", use_container_width=True):
                st.session_state[del_key] = True; st.rerun()
        else:
            cont.warning("Delete this folder and all nested content? This cannot be undone.")
            d1, d2 = cont.columns(2)
            if d1.button("Confirm", type="primary", key=f"{key_prefix}_del_yes_{folder['id']}"):
                try:
                    delete_folder(folder["id"]); st.success("Deleted."); st.rerun()
                except Exception as e:
                    st.error(f"Delete failed: {e}")
            if d2.button("Cancel", key=f"{key_prefix}_del_no_{folder['id']}"):
                st.session_state[del_key] = False; st.rerun()
    
        cont.markdown("---")


    # ---------- left controls (create + search) ----------
    toolbar_l, toolbar_r = st.columns([6, 4])
    with toolbar_l:
        st.markdown("#### Create")
        new_subj = st.text_input("New Subject", key="fx_new_subject", placeholder="e.g., A-Level Mathematics")
        if st.button("Add Subject", key="fx_add_subject", disabled=not (new_subj or "").strip()):
            try:
                create_folder(new_subj.strip(), None); st.success("Subject created."); st.rerun()
            except Exception as e:
                st.error(f"Create failed: {e}")

    with toolbar_r:
        st.markdown("#### Find")
        q = st.text_input("Search folders", key="fx_folder_search", placeholder="Type to filter…")

    # ---------- selections ----------
    st.session_state.setdefault("fx_sel_subject_id", None)
    st.session_state.setdefault("fx_sel_exam_id", None)

    # ---------- columns layout ----------
    colS, colE, colT = st.columns(3)

    # SUBJECTS
    with colS:
        st.markdown("### 📚 Subjects")
        S = roots(ALL_FOLDERS)
        if q: S = [s for s in S if q.lower() in s.get("name","").lower()]
        S.sort(key=lambda r: r.get("name","").lower())

        # Selection dropdown to drive middle column
        subj_names = [s["name"] for s in S]
        current_subj = next((s for s in S if s["id"] == st.session_state["fx_sel_subject_id"]), None)
        sel_label = current_subj["name"] if current_subj else "— select —"
        picked = st.selectbox("Select Subject", ["— select —"] + subj_names, index=0, key="fx_pick_subject")
        if picked in subj_names:
            st.session_state["fx_sel_subject_id"] = next(s["id"] for s in S if s["name"] == picked)

        st.markdown("---")
        for s in S:
            folder_card(s, level="subject", key_prefix=f"s_{s['id']}", move_targets=[])

    # EXAMS (of selected subject)
    with colE:
        st.markdown("### 🧪 Exams")
        sid = st.session_state.get("fx_sel_subject_id")
        if not sid:
            st.caption("Pick a Subject to see its Exams.")
        else:
            # create exam
            new_exam = st.text_input("New Exam", key="fx_new_exam", placeholder="e.g., IGCSE May 2026")
            if st.button("Add Exam", key="fx_add_exam", disabled=not (new_exam or "").strip()):
                try:
                    create_folder(new_exam.strip(), sid); st.success("Exam created."); st.rerun()
                except Exception as e:
                    st.error(f"Create failed: {e}")

            E = children(ALL_FOLDERS, sid)
            if q: E = [e for e in E if q.lower() in e.get("name","").lower()]
            E.sort(key=lambda r: r.get("name","").lower())

            # selection to drive topics
            exam_names = [e["name"] for e in E]
            current_exam = next((e for e in E if e["id"] == st.session_state["fx_sel_exam_id"]), None)
            ex_label = current_exam["name"] if current_exam else "— select —"
            ex_pick = st.selectbox("Select Exam", ["— select —"] + exam_names, index=0, key="fx_pick_exam")
            if ex_pick in exam_names:
                st.session_state["fx_sel_exam_id"] = next(e["id"] for e in E if e["name"] == ex_pick)

            st.markdown("---")
            # move targets for exams = all subjects (including same)
            move_targets_for_exam = roots(ALL_FOLDERS)
            for e in E:
                folder_card(e, level="exam", key_prefix=f"e_{e['id']}", move_targets=move_targets_for_exam)

    # TOPICS (of selected exam)
    with colT:
        st.markdown("### 🧩 Topics")
        eid = st.session_state.get("fx_sel_exam_id")
        if not eid:
            st.caption("Pick an Exam to see its Topics.")
        else:
            # create topic
            new_topic = st.text_input("New Topic", key="fx_new_topic", placeholder="e.g., Differentiation")
            if st.button("Add Topic", key="fx_add_topic", disabled=not (new_topic or "").strip()):
                try:
                    create_folder(new_topic.strip(), eid); st.success("Topic created."); st.rerun()
                except Exception as e:
                    st.error(f"Create failed: {e}")

            T = children(ALL_FOLDERS, eid)
            if q: T = [t for t in T if q.lower() in t.get("name","").lower()]
            T.sort(key=lambda r: r.get("name","").lower())

            # move targets for topics = all exams under current subject (or all exams globally if you prefer)
            # to keep it simple & safe: exams under the selected subject
            exams_under_subject = children(ALL_FOLDERS, st.session_state.get("fx_sel_subject_id"))
            for t in T:
                folder_card(t, level="topic", key_prefix=f"t_{t['id']}", move_targets=exams_under_subject)


def render_all_resources_page():
    # --------- Header / Back ---------
    top_l, _ = st.columns([1, 9])
    if top_l.button("← Home", key="all_back_home"):
        _set_params(view=None); st.rerun()

    st.markdown("## 🗂️ All Resources (Newest)")

    if "sb_user" not in st.session_state:
        st.info("Log in to view your resources."); return

    # --------- Load data ---------
    try:
        folders = list_folders()  # includes subjects/exams/topics
    except Exception:
        folders = []
    try:
        items = list_items(None, limit=1000)  # newest first later
    except Exception:
        items = []

    # Maps for quick lookup
    folder_by_id = {f["id"]: f for f in folders}

    def _folder_path(fid: Optional[str]) -> str:
        # Build "Subject / Exam / Topic" path
        if not fid:
            return "Unfiled"
        parts = []
        cur = folder_by_id.get(fid)
        while cur:
            parts.append(cur.get("name",""))
            pid = cur.get("parent_id")
            cur = folder_by_id.get(pid) if pid else None
        parts.reverse()
        return " / ".join([p for p in parts if p]) or "Unfiled"

    def _kind_icon(kind: str) -> str:
        return {"summary":"📄", "flashcards":"🧠", "quiz":"🧪"}.get(kind, "📄")

    # --------- Controls ---------
    ctl1, ctl2, ctl3, ctl4 = st.columns([4, 4, 2.2, 2.2])
    q = ctl1.text_input("Search titles", key="all_search", placeholder="e.g., Factorisation, Cold War…")
    kind_pick = ctl2.multiselect(
        "Filter by type",
        ["Notes","Flashcards","Quiz"],
        default=["Notes","Flashcards","Quiz"],
        key="all_kind"
    )
    sort_pick = ctl3.selectbox("Sort", ["Newest", "Oldest", "Title A→Z"], index=0, key="all_sort")
    grouped = ctl4.checkbox("Group by Topic", value=True, key="all_group")

    # Normalize kinds
    kind_map = {"Notes":"summary", "Flashcards":"flashcards", "Quiz":"quiz"}
    allowed_kinds = {kind_map[k] for k in kind_pick}

    # --------- Filter + sort ---------
    rows = [it for it in items if it.get("kind") in allowed_kinds]

    if q:
        ql = q.strip().lower()
        rows = [it for it in rows if ql in (it.get("title","").lower())]

    if sort_pick == "Newest":
        rows.sort(key=lambda r: r.get("created_at",""), reverse=True)
    elif sort_pick == "Oldest":
        rows.sort(key=lambda r: r.get("created_at",""))
    else:
        rows.sort(key=lambda r: r.get("title","").lower())

    # --------- UI helpers ---------
    def _row_actions(it, suffix="all"):
        c0, c1, c2, c3 = st.columns([7.5, 1.1, 1.1, 1.1])
        # title (click to open)
        title = it.get("title","Untitled")
        when = (it.get("created_at","")[:16].replace("T"," "))
        meta = f" — {when}" if when else ""
        c0.markdown(f"**{_kind_icon(it['kind'])} {title}**<span style='opacity:.6'>{meta}</span>", unsafe_allow_html=True)

        # Open
        if c1.button("Open", key=f"{suffix}_open_{it['id']}", use_container_width=True):
            _set_params(item=it['id'], view="all"); st.rerun()

        # Rename (inline)
        edit_key = f"{suffix}_edit_{it['id']}"
        if not st.session_state.get(edit_key, False):
            if c2.button("Rename", key=f"{suffix}_rn_btn_{it['id']}", use_container_width=True):
                st.session_state[edit_key] = True; st.rerun()
        else:
            newt = st.text_input("New title", value=title, key=f"{suffix}_rn_val_{it['id']}")
            s1, s2 = st.columns(2)
            if s1.button("Save", key=f"{suffix}_rn_save_{it['id']}"):
                try:
                    rename_item(it["id"], (newt or "").strip())
                    st.session_state[edit_key] = False
                    st.success("Renamed."); st.rerun()
                except Exception as e:
                    st.error(f"Rename failed: {e}")
            if s2.button("Cancel", key=f"{suffix}_rn_cancel_{it['id']}"):
                st.session_state[edit_key] = False; st.rerun()

        # Delete (confirm)
        del_key = f"{suffix}_del_{it['id']}"
        if not st.session_state.get(del_key, False):
            if c3.button("Delete", key=f"{suffix}_del_btn_{it['id']}", use_container_width=True):
                st.session_state[del_key] = True; st.rerun()
        else:
            st.warning("Delete this item? This cannot be undone.")
            d1, d2 = st.columns(2)
            if d1.button("Confirm", type="primary", key=f"{suffix}_del_yes_{it['id']}"):
                try:
                    delete_item(it["id"]); st.success("Deleted."); st.rerun()
                except Exception as e:
                    st.error(f"Delete failed: {e}")
            if d2.button("Cancel", key=f"{suffix}_del_no_{it['id']}"):
                st.session_state[del_key] = False; st.rerun()

    # --------- Render ---------
    if not rows:
        st.caption("No items match your filters.")
        return

    if not grouped:
        st.markdown("#### Flat list")
        for it in rows:
            _row_actions(it, suffix="flat")
        return

    # --------- Group by TOPIC folder and show progress ----------
    from collections import defaultdict
    bucket_by_topic: Dict[Optional[str], List[dict]] = defaultdict(list)
    for it in rows:
        bucket_by_topic[it.get("folder_id")].append(it)

    def _topic_sort_key(tid: Optional[str]) -> str:
        return (_folder_path(tid) or "Unfiled").lower()

    for topic_id in sorted(bucket_by_topic.keys(), key=_topic_sort_key):
        group_items = bucket_by_topic[topic_id]
        path = _folder_path(topic_id) or "Unfiled"

        # counts per kind
        notes_n = sum(1 for x in group_items if x["kind"]=="summary")
        flash_n = sum(1 for x in group_items if x["kind"]=="flashcards")
        quiz_n  = sum(1 for x in group_items if x["kind"]=="quiz")
        badge = f" | 📄 {notes_n}  🧠 {flash_n}  🧪 {quiz_n}"

        # compute stats/progress for the topic
        stats = compute_topic_stats(topic_id)
        pct = int(round(stats["progress"] * 100))
        quiz_pct = int(round(stats["quiz_avg"] * 100))
        flash_pct = int(round(stats["flash_known"] * 100))

        with st.expander(f"📁 {path}{badge} — {pct}% complete", expanded=False):
            st.progress(stats["progress"], text=f"Quiz avg: {quiz_pct}% • Flash known: {flash_pct}%")
            st.caption(f"Based on {stats['quiz_count']} quiz(es) and {stats['flash_reviews']} flash review(s).")

            for it in group_items:
                _row_actions(it, suffix=f"group_{topic_id or 'unfiled'}")


# If a view is requested, render that page directly and stop
if view_param == "resources":
    render_resources_page(); st.stop()
elif view_param == "all":
    render_all_resources_page(); st.stop()
elif view_param == "community":
    render_community_page()
    st.stop()

# ===========================
# Thin Icon Sidebar + Router
# ===========================

# ----------------- Slim, uniform rectangular sidebar -----------------
st.markdown("""
<style>
[data-testid="collapsedControl"] { display: none !important; }

/* make sidebar narrower overall */
section[data-testid="stSidebar"] {
  width: 170px !important;           /* adjust sidebar width */
  min-width: 170px !important;
}

/* inner padding */
section[data-testid="stSidebar"] .block-container {
  padding: 12px 10px 12px 10px;
}

/* vertical stack of rows */
.nav-stack {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  align-items: stretch;
}

/* each nav row same rectangular button size */
.nav-row { width: 100%; }
.nav-row .stButton > button {
  width: 100% !important;
  height: 44px !important;
  display: flex !important;
  align-items: center !important;
  justify-content: flex-start !important;
  gap: 10px !important;
  padding: 6px 10px !important;
  border-radius: 10px !important;
  font-size: 15px !important;
  line-height: 1 !important;
  font-weight: 500 !important;
}

/* icon text styling */
.nav-row .stButton > button span {
  font-size: 18px !important;
}

/* active & hover states */
.nav-row.active .stButton > button {
  border: 2px solid #f87171 !important;
  background-color: rgba(248,113,113,0.12) !important;
}
.nav-row .stButton > button:hover {
  background-color: rgba(255,255,255,0.07) !important;
}
</style>
""", unsafe_allow_html=True)


# ---- Sidebar FIRST, then router ----
def _nav_row(label_with_emoji: str, target_view: str|None, key: str, active: bool=False):
    cls = "nav-row active" if active else "nav-row"
    st.markdown(f"<div class='{cls}'>", unsafe_allow_html=True)
    if st.button(label_with_emoji, key=key):
        _set_params(view=target_view); st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

_v = _get_params().get("view")
view_param = (_v[0] if isinstance(_v, list) else _v) or ""

with st.sidebar:
    st.markdown("<div class='nav-list'>", unsafe_allow_html=True)
    for label, icon, page in [
        ("Quick Study", "⚡", "home"),
        ("Resources", "🧭", "resources"),
        ("All", "📁", "all"),
        ("Community", "🌐", "community"),
        ("My Profile","👤","account")
    ]:
        st.markdown("<div class='nav-btn'>", unsafe_allow_html=True)
        if st.button(f"{icon}  {label}", key=f"nav_{page}"):
            _set_params(view=page)
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# ======================================================
# QUICK STUDY (Home)
# ======================================================

def _autosize_counts(text: str, detail: int, quiz_mode: str) -> tuple[int, int]:
    """Heuristic: size outputs to input length + detail level."""
    n_words = max(1, len(text.split()))
    # buckets
    if n_words < 800:
        base_fc, base_q = 10, 6
    elif n_words < 2500:
        base_fc, base_q = 18, 10
    elif n_words < 6000:
        base_fc, base_q = 28, 14
    else:
        base_fc, base_q = 40, 20

    # detail influence (1..5) ~ +/- 30%
    scale = 1.0 + (detail - 3) * 0.15
    fc = int(round(base_fc * scale))
    q  = int(round(base_q  * scale))

    # MCQs need a few more to feel meaty
    if quiz_mode == "Multiple choice":
        q = int(round(q * 1.15))

    # clamp
    fc = max(8, min(fc, 60))
    q  = max(6, min(q, 40))
    return fc, q


def render_quick_study_page():
    st.title("⚡ Quick Study")

    # Require sign-in (to save results under folders)
    if "sb_user" not in st.session_state:
        st.info("Please sign in to create and save study materials.")
        return

    # ---------- Bootstrap state (consume “just created” IDs BEFORE widgets) ----------
    st.session_state.setdefault("qs_subject_id", None)
    st.session_state.setdefault("qs_exam_id", None)

    if "__qs_new_subject_id" in st.session_state:
        st.session_state["qs_subject_id"] = st.session_state.pop("__qs_new_subject_id")
        # Clear the “create new” checkbox so it doesn't stick
        if "qs_make_new_subject" in st.session_state:
            del st.session_state["qs_make_new_subject"]
        # Reset downstream
        st.session_state["qs_exam_id"] = None

    if "__qs_new_exam_id" in st.session_state:
        st.session_state["qs_exam_id"] = st.session_state.pop("__qs_new_exam_id")
        if "qs_make_new_exam" in st.session_state:
            del st.session_state["qs_make_new_exam"]

    # Refresh folders (subjects/exams) for pickers
    try:
        ALL_FOLDERS_LOCAL = list_folders()
    except Exception:
        ALL_FOLDERS_LOCAL = []
        st.warning("Could not load folders.")

    def _roots(rows):  # subjects
        return [r for r in rows if not r.get("parent_id")]

    subjects = _roots(ALL_FOLDERS_LOCAL)
    subj_names = [s["name"] for s in subjects]
    subj_by_id = {s["id"]: s for s in subjects}

    # ---------- SUBJECT ----------
    st.markdown("### Subject")
    make_new_subject = st.checkbox("Create a new subject", key="qs_make_new_subject", value=False)

    subject_id = st.session_state.get("qs_subject_id")
    if make_new_subject:
        new_subject = st.text_input("New subject name", placeholder="e.g., A-Level Mathematics", key="qs_new_subject")
        if st.button("Save subject", key="qs_save_subject_btn"):
            name = (new_subject or "").strip()
            if not name:
                st.warning("Enter a subject name.")
            elif name.lower() in {n.lower() for n in subj_names}:
                st.error("This subject already exists. Please use a different name.")
            else:
                try:
                    created = create_folder(name, None)
                    # Stash new subject -> select on next run
                    st.session_state["__qs_new_subject_id"] = created["id"]
                    st.rerun()
                except Exception as e:
                    st.error(f"Create failed: {e}")
    else:
        # Existing subject picker
        label = "— select —"
        if subject_id and subject_id in subj_by_id:
            label = subj_by_id[subject_id]["name"]
        pick = st.selectbox("Use existing subject", ["— select —"] + subj_names, index=0, key="qs_subject_pick")
        if pick in subj_names:
            st.session_state["qs_subject_id"] = next(s["id"] for s in subjects if s["name"] == pick)
            subject_id = st.session_state["qs_subject_id"]

    # ---------- EXAM ----------
    st.markdown("### Exam")
    exam_id = st.session_state.get("qs_exam_id")
    exams = []
    if subject_id:
        exams = [f for f in ALL_FOLDERS_LOCAL if f.get("parent_id") == subject_id]
        exam_names = [e["name"] for e in exams]
        make_new_exam = st.checkbox("Create a new exam", key="qs_make_new_exam", value=False)

        if make_new_exam:
            new_exam = st.text_input("New exam name", placeholder="e.g., IGCSE May 2026", key="qs_new_exam")
            if st.button("Save exam", key="qs_save_exam_btn"):
                name = (new_exam or "").strip()
                if not name:
                    st.warning("Enter an exam name.")
                elif name.lower() in {n.lower() for n in exam_names}:
                    st.error("This exam already exists under that subject.")
                else:
                    try:
                        created = create_folder(name, subject_id)
                        st.session_state["__qs_new_exam_id"] = created["id"]
                        st.rerun()
                    except Exception as e:
                        st.error(f"Create failed: {e}")
        else:
            # existing exam picker
            label = "— select —"
            if exam_id and any(e["id"] == exam_id for e in exams):
                label = next(e["name"] for e in exams if e["id"] == exam_id)
            pick = st.selectbox("Use existing exam", ["— select —"] + exam_names, index=0, key="qs_exam_pick")
            if pick in exam_names:
                st.session_state["qs_exam_id"] = next(e["id"] for e in exams if e["name"] == pick)
                exam_id = st.session_state["qs_exam_id"]
    else:
        st.caption("Pick or create a Subject first to reveal Exams.")

    # ---------- TOPIC ----------
    st.markdown("### Topic")
    topic_name = ""
    if exam_id:
        topic_name = st.text_input("New topic name", placeholder="e.g., Differentiation", key="qs_new_topic")
    else:
        st.caption("Pick or create an Exam first to add a Topic.")

    st.markdown("---")

    # ---------- EXTRA CONTEXT ----------
    st.markdown("**Subject (free text, improves accuracy & quality):**")
    subject_hint = st.text_input(
        "e.g., Mathematics (Calculus), Biology (Cell Division), History (Cold War)",
        value="General",
        key="qs_subject_hint"
    )

    audience_label = st.selectbox(
        "Audience",
        ["University", "A-Level", "IB", "GCSE", "HKDSE", "Primary"],
        index=0,
        key="qs_audience_label"
    )
    aud_map = {
        "University": "university",
        "A-Level": "A-Level",
        "IB": "A-Level",
        "GCSE": "high school",
        "HKDSE": "high school",
        "Primary": "primary"
    }
    audience = aud_map.get(audience_label, "high school")
    detail = st.slider("Detail level", 1, 5, 3, key="qs_detail")

    st.markdown("### Quiz type")
    quiz_mode = st.radio("Choose quiz format", ["Free response", "Multiple choice"], index=0, horizontal=True, key="qs_quiz_mode")
    mcq_options = 4
    if quiz_mode == "Multiple choice":
        mcq_options = st.slider("MCQ options per question", 3, 6, 4, key="qs_mcq_opts")

    files = st.file_uploader(
        "Upload files (PDF, PPTX, JPG, PNG, TXT)",
        type=["pdf", "pptx", "jpg", "jpeg", "png", "txt"],
        accept_multiple_files=True,
        key="qs_files",
    )

    # ---------- What to generate ----------
    st.markdown("### What to generate")
    sel_notes = st.checkbox("📄 Notes", value=True, key="qs_sel_notes")
    sel_flash = st.checkbox("🧠 Flashcards", value=True, key="qs_sel_flash")
    sel_quiz  = st.checkbox("🧪 Quiz", value=True, key="qs_sel_quiz")

    # ---------- Gate Generate button ----------
    has_topic_text = bool((st.session_state.get("qs_new_topic") or "").strip())
    has_files = bool(files)
    has_selection = sel_notes or sel_flash or sel_quiz
    can_generate = bool(subject_id and exam_id and has_topic_text and has_files and has_selection)

    gen_clicked = st.button("Generate", type="primary", key="qs_generate_btn", disabled=not can_generate)

    if gen_clicked and can_generate:
        # Resolve subject/exam from current selections
        subjects_now = _roots(list_folders())
        subj_map_now = {s["name"]: s["id"] for s in subjects_now}
        subject_id = subject_id or subj_map_now.get(st.session_state.get("qs_subject_pick"))

        exams_now = [f for f in list_folders() if subject_id and f.get("parent_id") == subject_id]
        exam_map_now = {e["name"]: e["id"] for e in exams_now}
        exam_id = exam_id or exam_map_now.get(st.session_state.get("qs_exam_pick"))

        # Create the topic folder (prevent duplicate name under exam)
        topic_id = None
        topic_name_in = (st.session_state.get("qs_new_topic") or "").strip()
        if exam_id and topic_name_in:
            existing_topics = [f for f in list_folders() if f.get("parent_id") == exam_id]
            if topic_name_in.lower() in {t["name"].lower() for t in existing_topics}:
                st.error("Topic already exists under this exam. Please choose a different name.")
                st.stop()
            created = create_folder(topic_name_in, exam_id)
            topic_id = created["id"]
            topic_name_in = created["name"]

        dest_folder = topic_id or exam_id or subject_id or None
        base_title = (
            topic_name_in
            or (next((e["name"] for e in exams_now if e["id"] == exam_id), None) if exam_id else None)
            or (next((s["name"] for s in subjects_now if s["id"] == subject_id), None) if subject_id else None)
            or (subject_hint or "Study Pack")
        )

        prog = st.progress(0, text="Starting…")
        try:
            prog.progress(10, text="Extracting text…")
            text = extract_any(files)
            if not text.strip():
                st.error("No text detected in the uploaded files.")
                st.stop()

            # Decide sizes automatically
            auto_fc, auto_qs = _autosize_counts(text, detail, quiz_mode)

            prog.progress(35, text="Summarising with AI…")
            data = summarize_text(text, audience=audience, detail=detail, subject=subject_hint)

            summary_id = flash_id = quiz_id = None

            if sel_flash:
                prog.progress(55, text=f"Generating ~{auto_fc} flashcards…")
                try:
                    cards = generate_flashcards_from_notes(data, audience=audience, target_count=auto_fc)
                except Exception as e:
                    st.warning(f"Flashcards skipped: {e}")
                    cards = []

            if sel_quiz:
                prog.progress(70, text=f"Generating ~{auto_qs} quiz questions…")
                qs = generate_quiz_from_notes(
                    data,
                    subject=subject_hint,
                    audience=audience,
                    num_questions=auto_qs,
                    mode=("mcq" if quiz_mode == "Multiple choice" else "free"),
                    mcq_options=mcq_options,
                )
            else:
                qs = None

            prog.progress(85, text="Saving selected items…")

            if sel_notes:
                title_notes = f"📄 {base_title} — Notes"
                summary = save_item("summary", title_notes, data, dest_folder)
                summary_id = summary.get("id")

            if sel_flash and 'cards' in locals() and cards:
                title_flash = f"🧠 {base_title} — Flashcards"
                flash = save_item("flashcards", title_flash, {"flashcards": cards}, dest_folder)
                flash_id = flash.get("id")

            if sel_quiz and qs:
                title_quiz = f"🧪 {base_title} — Quiz"
                quiz_payload = {"questions": qs}
                if quiz_mode == "Multiple choice":
                    quiz_payload["type"] = "mcq"
                    quiz_payload["mcq_options"] = mcq_options
                quiz_item = save_item("quiz", title_quiz, quiz_payload, dest_folder)
                quiz_id = quiz_item.get("id")

            prog.progress(100, text="Done!")
            st.success("Saved ✅")

            st.session_state["qs_created_summary_id"] = summary_id or None
            st.session_state["qs_created_flash_id"] = flash_id or None
            st.session_state["qs_created_quiz_id"] = quiz_id or None

        except Exception as e:
            st.error(f"Generation failed: {e}")

    # ---------- Show “Open” buttons if something was created ----------
    sid = st.session_state.get("qs_created_summary_id")
    fid = st.session_state.get("qs_created_flash_id")
    qid = st.session_state.get("qs_created_quiz_id")

    if sid or fid or qid:
        st.markdown("### Open")
        c1, c2, c3 = st.columns(3)
        if sid and c1.button("Open Notes", type="primary", use_container_width=True, key="qs_open_notes"):
            _set_params(item=sid, view="all"); st.rerun()
        if fid and c2.button("Open Flashcards", use_container_width=True, key="qs_open_flash"):
            _set_params(item=fid, view="all"); st.rerun()
        if qid and c3.button("Open Quiz", use_container_width=True, key="qs_open_quiz"):
            _set_params(item=qid, view="all"); st.rerun()


# ---- Call it when on Home / default ----
if view_param in ("", "home", None):
    render_quick_study_page()
    st.stop()

