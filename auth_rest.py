# auth_rest.py
import os
import requests
from typing import Optional, Tuple, List, Dict
import streamlit as st
import requests
import streamlit as st
from datetime import datetime, timezone, timedelta  # if you use the XP helpers here
from urllib.parse import quote_plus


def _get_keys() -> Tuple[str, str]:
    url = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("Supabase URL/key missing. Set SUPABASE_URL and SUPABASE_ANON_KEY.")
    return url, key

def _headers(token: str | None = None) -> Dict[str, str]:
    url, key = _get_keys()
    h = {
        "apikey": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h

def _require_user() -> Tuple[str, Dict]:
    user = st.session_state.get("sb_user")
    if not user or not user.get("access_token"):
        raise RuntimeError("Not signed in.")
    return user["access_token"], user["user"]

# ---------- Auth ----------
import requests
import streamlit as st

def _supabase_auth_headers_for_client():
    """
    Use the ANON key for auth endpoints (GoTrue). Do NOT send the service key here.
    """
    url = st.secrets.get("SUPABASE_URL")
    anon = st.secrets.get("SUPABASE_ANON_KEY") or st.secrets.get("SUPABASE_KEY")
    if not url or not anon:
        raise RuntimeError("Missing SUPABASE_URL and/or SUPABASE_ANON_KEY in secrets.")
    # GoTrue only needs apikey; do NOT include Authorization: Bearer <service> on auth endpoints.
    return url, {"apikey": anon, "Content-Type": "application/json"}

def sign_in(email: str, password: str):
    """
    Password grant sign in via GoTrue. Returns {'access_token':..., 'user':{...}, ...}
    Raises RuntimeError with a helpful message if it fails (e.g. email not confirmed).
    """
    url, headers = _supabase_auth_headers_for_client()
    email = (email or "").strip()

    payload = {
        "email": email,
        "password": password or "",
        "gotrue_meta_security": {}  # avoids some UA/CSRF heuristics on some deployments
    }

    r = requests.post(
        f"{url}/auth/v1/token?grant_type=password",
        headers=headers,
        json=payload,
        timeout=20,
    )
    # If invalid, GoTrue returns JSON like {"error":"invalid_grant","error_description":"Invalid login credentials"}
    if r.status_code >= 400:
        try:
            err = r.json()
        except Exception:
            err = {}
        code = err.get("error") or "auth_error"
        desc = err.get("error_description") or r.text
        raise RuntimeError(f"Sign-in failed ({code}): {desc}")

    data = r.json()  # includes access_token, refresh_token, token_type, user
    # Optional: normalize to your app’s session shape
    st.session_state["sb_user"] = {
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "user": data.get("user") or {},
        "session": data,
    }
    return data


def sign_up(email: str, password: str, display_name: str = "", username: str = ""):
    url, _ = _get_keys()
    r = requests.post(
        f"{url}/auth/v1/signup",
        json={
            "email": email,
            "password": password,
            "data": {"display_name": display_name, "username": username}
        },
        headers=_headers(),
        timeout=20
    )
    r.raise_for_status()
    return r.json()

def current_user() -> dict:
    """Fetch the authenticated user from Supabase Auth."""
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.get(f"{url}/auth/v1/user", headers=_headers(token), timeout=15)
    r.raise_for_status()
    return r.json()

def update_profile(display_name: Optional[str] = None, username: Optional[str] = None) -> dict:
    """Update user metadata (display_name, username)."""
    url, _ = _get_keys()
    token, _ = _require_user()
    data = {}
    if display_name is not None or username is not None:
        meta = {}
        if display_name is not None: meta["display_name"] = display_name
        if username is not None: meta["username"] = username
        data["data"] = meta
    if not data:
        raise RuntimeError("Nothing to update.")
    r = requests.put(f"{url}/auth/v1/user", headers=_headers(token), json=data, timeout=20)
    r.raise_for_status()
    return r.json()

def change_password(new_password: str) -> dict:
    """Change the authenticated user's password."""
    if not new_password:
        raise RuntimeError("New password cannot be empty.")
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.put(
        f"{url}/auth/v1/user",
        headers=_headers(token),
        json={"password": new_password},
        timeout=20
    )
    r.raise_for_status()
    return r.json()
def sign_out():
    # Revoke token server-side (best effort), then drop local session.
    try:
        url, _ = _get_keys()
        tok = (st.session_state.get("sb_user") or {}).get("access_token")
        if tok:
            requests.post(f"{url}/auth/v1/logout", headers=_headers(tok), timeout=10)
    except Exception:
        pass
    st.session_state.pop("sb_user", None)


# ---------- Folders & items ----------
def create_folder(name: str, parent_id: Optional[str]):
    url, _ = _get_keys()
    token, user = _require_user()
    payload = {"name": name, "parent_id": parent_id, "user_id": user["id"]}
    r = requests.post(
        f"{url}/rest/v1/folders",
        headers={**_headers(token), "Prefer": "return=representation"},
        json=payload, timeout=20
    )
    r.raise_for_status()
    return r.json()[0]

def list_folders() -> List[Dict]:
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.get(
        f"{url}/rest/v1/folders",
        headers=_headers(token),
        params={"select": "id,name,parent_id,created_at", "order": "created_at.asc"},
        timeout=20
    )
    r.raise_for_status()
    return r.json()

def list_child_folders(parent_id: Optional[str]):
    url, _ = _get_keys()
    token, _ = _require_user()
    params = {"select": "id,name,parent_id,created_at", "order": "created_at.asc"}
    if parent_id is None:
        params["parent_id"] = "is.null"
    else:
        params["parent_id"] = f"eq.{parent_id}"
    r = requests.get(f"{url}/rest/v1/folders", headers=_headers(token), params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def delete_folder(folder_id: str):
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.delete(
        f"{url}/rest/v1/folders",
        headers=_headers(token),
        params={"id": f"eq.{folder_id}"},
        timeout=20
    )
    r.raise_for_status()
    return True

def save_item(kind: str, title: str, data: dict, folder_id: Optional[str]):
    url, _ = _get_keys()
    token, user = _require_user()
    payload = {"kind": kind, "title": title, "data": data, "folder_id": folder_id, "user_id": user["id"]}
    r = requests.post(
        f"{url}/rest/v1/items",
        headers={**_headers(token), "Prefer": "return=representation"},
        json=payload, timeout=30
    )
    r.raise_for_status()
    return r.json()[0]

def list_items(folder_id: Optional[str] = None, limit: int = 100) -> List[Dict]:
    url, _ = _get_keys()
    token, _ = _require_user()
    params = {"select": "id,kind,title,data,folder_id,created_at", "order": "created_at.desc", "limit": str(limit)}
    if folder_id:
        params["folder_id"] = f"eq.{folder_id}"
    r = requests.get(f"{url}/rest/v1/items", headers=_headers(token), params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def get_item(item_id: str) -> Dict:
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.get(
        f"{url}/rest/v1/items",
        headers=_headers(token),
        params={"id": f"eq.{item_id}", "select": "id,kind,title,data,folder_id,created_at"},
        timeout=30
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise RuntimeError("Item not found")
    return rows[0]

def move_item(item_id: str, new_folder_id: Optional[str]):
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.patch(
        f"{url}/rest/v1/items",
        headers={**_headers(token), "Prefer": "return=representation"},
        params={"id": f"eq.{item_id}"},
        json={"folder_id": new_folder_id},
        timeout=20
    )
    r.raise_for_status()
    return r.json()[0]

def delete_item(item_id: str):
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.delete(
        f"{url}/rest/v1/items",
        headers=_headers(token),
        params={"id": f"eq.{item_id}"},
        timeout=20
    )
    r.raise_for_status()
    return True

# ---------- Quiz attempts ----------
def save_quiz_attempt(item_id: str, correct: int, total: int, history: list):
    url, _ = _get_keys()
    token, user = _require_user()
    payload = {"user_id": user["id"], "item_id": item_id, "correct": int(correct), "total": int(total), "history": history}
    r = requests.post(
        f"{url}/rest/v1/quiz_attempts",
        headers={**_headers(token), "Prefer": "return=representation"},
        json=payload, timeout=20
    )
    r.raise_for_status()
    return r.json()[0]

def list_quiz_attempts(item_id: Optional[str] = None, limit: int = 20):
    url, _ = _get_keys()
    token, _ = _require_user()
    params = {"select": "id,item_id,correct,total,created_at", "order": "created_at.desc", "limit": str(limit)}
    if item_id:
        params["item_id"] = f"eq.{item_id}"
    r = requests.get(f"{url}/rest/v1/quiz_attempts", headers=_headers(token), params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def list_quiz_attempts_for_items(item_ids: List[str]) -> List[Dict]:
    """Fetch attempts for multiple items (used for topic progress)."""
    if not item_ids:
        return []
    url, _ = _get_keys()
    token, _ = _require_user()
    ids_csv = "(" + ",".join(item_ids) + ")"
    params = {
        "select": "id,item_id,correct,total,created_at",
        "order": "created_at.desc",
        "item_id": f"in.{ids_csv}"
    }
    r = requests.get(f"{url}/rest/v1/quiz_attempts", headers=_headers(token), params=params, timeout=30)
    r.raise_for_status()
    return r.json()

# ---------- Flashcard reviews (✅/❌) ----------
def save_flash_review(item_id: str, known: bool):
    """
    Insert a flashcard review event.
    Table schema below in the SQL section.
    """
    url, _ = _get_keys()
    token, user = _require_user()
    payload = {"user_id": user["id"], "item_id": item_id, "known": bool(known)}
    r = requests.post(
        f"{url}/rest/v1/flashcard_reviews",
        headers={**_headers(token), "Prefer": "return=representation"},
        json=payload, timeout=15
    )
    r.raise_for_status()
    return r.json()[0]

def list_flash_reviews_for_items(item_ids: List[str]) -> List[Dict]:
    if not item_ids:
        return []
    url, _ = _get_keys()
    token, _ = _require_user()
    ids_csv = "(" + ",".join(item_ids) + ")"
    params = {
        "select": "id,item_id,known,created_at",
        "order": "created_at.desc",
        "item_id": f"in.{ids_csv}"
    }
    r = requests.get(f"{url}/rest/v1/flashcard_reviews", headers=_headers(token), params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def _current_user_id() -> Optional[str]:
    try:
        return st.session_state["sb_user"]["user"]["id"]
    except Exception:
        return None

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

def _sb_headers():
    url = st.secrets.get("SUPABASE_URL")
    key = (st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")  # if you have service role, prefer it for server-side
           or st.secrets.get("SUPABASE_KEY")
           or st.secrets.get("SUPABASE_ANON_KEY"))
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL / key in secrets.")
    return url, {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

def sb_find_profile_by_username(username: str) -> Optional[dict]:
    if not username:
        return None
    url, headers = _sb_headers()
    r = requests.get(
        f"{url}/rest/v1/profiles?username=eq.{requests.utils.quote(username)}&select=id,username",
        headers=headers,
        timeout=20,
    )
    if r.status_code == 200 and r.json():
        return r.json()[0]
    return None




def sb_add_friend(friend_username: str) -> tuple[bool, str]:
    me = _current_user_id()
    if not me:
        return False, "Please sign in first."

    prof = sb_find_profile_by_username((friend_username or "").strip())
    if not prof:
        return False, "No user found with that username."

    if prof["id"] == me:
        return False, "You can’t add yourself."

    url, headers = _sb_headers()
    payload = {"user_id": me, "friend_user_id": prof["id"]}
    r = requests.post(f"{url}/rest/v1/friends", headers=headers, json=payload, timeout=20)
    if r.status_code in (200, 201):
        return True, f"Added {prof['username']}."
    elif r.status_code == 409:
        return False, "Already added."
    else:
        try:
            msg = r.json()
        except Exception:
            msg = r.text
        return False, f"Could not add friend: {msg}"


def _sum_xp_from_core_tables(user_id: str, start_iso: str, end_iso: str) -> int:
    url, headers = _sb_headers()

    # Flash: 1 XP per known=True review
    fr = requests.get(
        f"{url}/rest/v1/flash_reviews"
        f"?user_id=eq.{user_id}"
        f"&created_at=gte.{start_iso}"
        f"&created_at=lt.{end_iso}"
        f"&known=is.true"
        f"&select=id",
        headers=headers, timeout=25
    )
    flash_xp = len(fr.json()) if fr.status_code == 200 else 0

    # Quiz: XP = sum(correct)
    qa = requests.get(
        f"{url}/rest/v1/quiz_attempts"
        f"?user_id=eq.{user_id}"
        f"&created_at=gte.{start_iso}"
        f"&created_at=lt.{end_iso}"
        f"&select=correct",
        headers=headers, timeout=25
    )
    quiz_xp = 0
    if qa.status_code == 200:
        try:
            quiz_xp = sum(int(row.get("correct") or 0) for row in qa.json() or [])
        except Exception:
            pass

    return flash_xp + quiz_xp

def sb_get_xp_totals_for_user(user_id: str) -> dict:
    today_start = _iso_start_of_today_utc()
    tomorrow_start = _iso_start_of_tomorrow_utc()
    month_start = _iso_start_of_month_utc()
    next_month_start = _iso_start_of_next_month_utc()
    return {
        "today": _sum_xp_from_core_tables(user_id, today_start, tomorrow_start),
        "month": _sum_xp_from_core_tables(user_id, month_start, next_month_start),
    }

# --- Add near the top if missing ---
import requests
import streamlit as st
from typing import List, Dict, Optional

# _sb_headers() must already be defined in this file:
# def _sb_headers(): ...  (returns (url, headers))

def _me_id() -> str | None:
    """Return current auth uid from session_state."""
    user = (st.session_state.get("sb_user") or {}).get("user") or {}
    return user.get("id") or user.get("user_id") or None

def sb_find_profile_by_username(username: str) -> dict | None:
    """
    Look up a profile row by username. Assumes profiles has columns:
      id (uuid auth.uid), username (text unique), display_name (text)
    """
    if not username:
        return None
    url, headers = _sb_headers()
    # Use params to avoid URL formatting bugs
    params = {
        "username": f"eq.{username}",
        "select": "id,username,display_name",
        "limit": 1,
    }
    r = requests.get(f"{url}/rest/v1/profiles", headers=headers, params=params, timeout=20)
    if r.status_code == 406:  # empty response handling
        return None
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None
    
def sb_is_already_friends(a_user_id: str, b_user_id: str) -> bool:
    url, headers = _sb_headers()
    q = f"{url}/rest/v1/friends?user_id=eq.{a_user_id}&friend_user_id=eq.{b_user_id}&select=id"
    r = requests.get(q, headers=headers, timeout=15)
    r.raise_for_status()
    return bool(r.json())
def _find_user_by_username_ci(username: str) -> Optional[dict]:
    url, headers = _sb_headers()
    # case-insensitive exact match via ilike
    # IMPORTANT: url-encode wildcard-free pattern (no *), keeps it exact-ish but CI.
    # PostgREST: column=ilike.value
    r = requests.get(
        f"{url}/rest/v1/profiles?username=ilike.{username}&select=id,username,display_name",
        headers=headers, timeout=20
    )
    if r.status_code == 200:
        rows = r.json() or []
        # Pick exact (case-insensitive) if multiple
        for row in rows:
            if (row.get("username") or "").lower() == username.lower():
                return row
        return rows[0] if rows else None
    return None


def sb_send_friend_request(username_or_handle: str) -> str:
    """
    Send a friend request to the user with the given username (with or without leading '@').
    Guards against: empty input, self-requests, already-friends, duplicates, and inverse pending requests.
    Returns a user-friendly status message.
    """
    # --- Resolve current user ---
    try:
        me = current_user()
    except Exception as e:
        return f"Please sign in first. ({e})"

    my_id = me.get("id") or (me.get("user") or {}).get("id")
    if not my_id:
        return "Please sign in first."

    # --- Normalize handle & find recipient (case-insensitive) ---
    handle = (username_or_handle or "").strip().lstrip("@")
    if not handle:
        return "Enter a username."

    you = _find_user_by_username_ci(handle)
    if not you:
        return "Username not found."

    you_id = you.get("id")
    you_un = (you.get("username") or "").strip() or handle

    # --- Prevent sending to self ---
    if you_id == my_id:
        return "You can’t add yourself."

    url, headers = _sb_headers()

    # --- Check existing relationships / requests (both directions) ---
    # We consider any row with status in (pending, accepted) between the two users.
    # PostgREST OR syntax: or=(and(a.eq.1,b.eq.2),and(a.eq.2,b.eq.1))
    status_list = "in.(pending,accepted)"
    or_param = (
        f"or=("
        f"and(requester_id.eq.{my_id},recipient_id.eq.{you_id}),"
        f"and(requester_id.eq.{you_id},recipient_id.eq.{my_id})"
        f")"
    )
    check = requests.get(
        f"{url}/rest/v1/friend_requests"
        f"?{or_param}"
        f"&status={status_list}"
        f"&select=id,requester_id,recipient_id,status,created_at",
        headers=headers,
        timeout=20,
    )
    if check.status_code == 200:
        rows = check.json() or []
        if rows:
            # If any accepted row exists, they are already friends.
            if any((r.get("status") or "").lower() == "accepted" for r in rows):
                return f"You’re already friends with @{you_un}."
            # Otherwise there is at least one pending row; determine direction.
            for r in rows:
                req, rec, st = r.get("requester_id"), r.get("recipient_id"), (r.get("status") or "").lower()
                if st == "pending":
                    if req == my_id and rec == you_id:
                        return f"Request already sent to @{you_un}."
                    if req == you_id and rec == my_id:
                        return f"@{you_un} has already sent you a request — check Incoming and accept it."
    else:
        # If the check failed for any reason, fail safe (don’t create duplicates).
        try:
            msg = check.json()
        except Exception:
            msg = check.text
        return f"Couldn’t verify existing requests ({msg}). Please try again."

    # --- Create a new pending request ---
    payload = {
        "requester_id": my_id,
        "recipient_id": you_id,
        "status": "pending",
    }
    create = requests.post(
        f"{url}/rest/v1/friend_requests",
        json=payload,
        headers=headers,
        timeout=20,
    )
    if create.status_code not in (200, 201):
        try:
            msg = create.json()
        except Exception:
            msg = create.text
        return f"Could not send request: {msg}"

    return f"Friend request sent to @{you_un}."


from typing import Optional, Dict, List, Tuple
import requests

def _profiles_by_ids_map(ids: List[str]) -> Dict[str, dict]:
    """Fetch profiles for a list of IDs -> {id: profile} (id, display_name)."""
    if not ids:
        return {}
    url, headers = _sb_headers()
    # PostgREST IN filter; chunk if needed
    CHUNK = 80
    out: Dict[str, dict] = {}
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i+CHUNK]
        q = (
            f"{url}/rest/v1/profiles"
            f"?id=in.({','.join(chunk)})"
            f"&select=id,display_name"
        )
        r = requests.get(q, headers=headers, timeout=20)
        if r.status_code == 200:
            for row in (r.json() or []):
                out[row.get("id")] = row
    return out


def _find_recipients_by_display_name(name: str) -> List[dict]:
    """
    Case-insensitive lookup by display_name.
    Returns a list of matches (id, display_name). If multiple, the caller should disambiguate.
    """
    url, headers = _sb_headers()
    needle = (name or "").strip()
    if not needle:
        return []

    # Try exact case-insensitive first (ilike + post-filter exact-lc)
    r = requests.get(
        f"{url}/rest/v1/profiles"
        f"?display_name=ilike.{needle}"
        f"&select=id,display_name",
        headers=headers,
        timeout=20,
    )
    if r.status_code != 200:
        return []

    rows = r.json() or []
    exact = [row for row in rows if (row.get("display_name") or "").lower() == needle.lower()]
    if exact:
        return exact

    # If no exact matches, try substring search to help the user pick
    r2 = requests.get(
        f"{url}/rest/v1/profiles"
        f"?display_name=ilike.*{needle}*"
        f"&select=id,display_name",
        headers=headers,
        timeout=20,
    )
    if r2.status_code != 200:
        return []

    return r2.json() or []


def sb_send_friend_request(display_name: str) -> str:
    """
    Create a pending friend request to a user identified by *display_name*.
    Guards: not signed in, empty name, self, duplicates, reverse-pending, already friends.
    """
    # --- Me ---
    try:
        me = current_user()
    except Exception as e:
        return f"Please sign in first. ({e})"

    my_id = me.get("id") or (me.get("user") or {}).get("id")
    if not my_id:
        return "Please sign in first."

    # --- Find recipient by display_name ---
    candidates = _find_recipients_by_display_name(display_name)
    if not candidates:
        return "Display name not found."

    if len(candidates) > 1:
        # Don’t auto-pick—force user to disambiguate
        preview = ", ".join([f"{c.get('display_name','(no name)')} (id: {c.get('id')})" for c in candidates[:5]])
        more = "…" if len(candidates) > 5 else ""
        return f"Multiple users match that display name. Please specify by id. Matches: {preview}{more}"

    you = candidates[0]
    you_id = you.get("id")
    you_name = you.get("display_name") or "(no name)"

    if you_id == my_id:
        return "You can’t add yourself."

    url, headers = _sb_headers()

    # --- Existing relationship check (both directions) ---
    # status in (pending, accepted)
    status_list = "in.(pending,accepted)"
    or_param = (
        f"or=("
        f"and(requester_id.eq.{my_id},recipient_id.eq.{you_id}),"
        f"and(requester_id.eq.{you_id},recipient_id.eq.{my_id})"
        f")"
    )
    check = requests.get(
        f"{url}/rest/v1/friend_requests"
        f"?{or_param}&status={status_list}"
        f"&select=id,requester_id,recipient_id,status",
        headers=headers,
        timeout=20,
    )
    if check.status_code != 200:
        try:
            err = check.json()
        except Exception:
            err = check.text
        return f"Couldn’t verify existing requests: {err}"

    rows = check.json() or []
    if rows:
        if any((r.get("status") or "").lower() == "accepted" for r in rows):
            return f"You’re already friends with {you_name}."
        for r in rows:
            st = (r.get("status") or "").lower()
            if st == "pending":
                if r.get("requester_id") == my_id:
                    return f"Request already sent to {you_name}."
                if r.get("requester_id") == you_id:
                    return f"{you_name} has already sent you a request — check Incoming and accept it."

    # --- Create pending request ---
    payload = {"requester_id": my_id, "recipient_id": you_id, "status": "pending"}
    create = requests.post(
        f"{url}/rest/v1/friend_requests",
        json=payload,
        headers=headers,
        timeout=20,
    )
    if create.status_code not in (200, 201):
        try:
            err = create.json()
        except Exception:
            err = create.text
        return f"Could not send request: {err}"

    return f"Friend request sent to {you_name}."


def sb_list_friend_requests(direction: str = "incoming") -> List[dict]:
    """
    Returns a normalized list of requests with embedded minimal profile info:
    - incoming: [{'id', 'status', 'requester': {id, display_name}, 'recipient': {…}}]
    - outgoing: same shape
    """
    try:
        me = current_user()
    except Exception:
        return []
    my_id = me.get("id") or (me.get("user") or {}).get("id")
    if not my_id:
        return []

    url, headers = _sb_headers()

    if direction == "incoming":
        q = f"{url}/rest/v1/friend_requests?recipient_id=eq.{my_id}&select=id,requester_id,recipient_id,status,created_at"
    else:
        q = f"{url}/rest/v1/friend_requests?requester_id=eq.{my_id}&select=id,requester_id,recipient_id,status,created_at"

    r = requests.get(q, headers=headers, timeout=20)
    if r.status_code != 200:
        return []

    rows = r.json() or []
    # Fetch both sides' profiles
    ids = set()
    for row in rows:
        if row.get("requester_id"):
            ids.add(str(row["requester_id"]))
        if row.get("recipient_id"):
            ids.add(str(row["recipient_id"]))
    prof_map = _profiles_by_ids_map(list(ids))

    out = []
    for row in rows:
        out.append({
            "id": row.get("id"),
            "status": row.get("status"),
            "created_at": row.get("created_at"),
            "requester": prof_map.get(str(row.get("requester_id")), {"id": row.get("requester_id"), "display_name": "(unknown)"}),
            "recipient": prof_map.get(str(row.get("recipient_id")), {"id": row.get("recipient_id"), "display_name": "(unknown)"}),
        })
    return out


def sb_respond_friend_request(request_id: str, action: str) -> str:
    """
    action: 'accept' or 'decline'
    """
    url, headers = _sb_headers()
    new_status = "accepted" if action == "accept" else "declined"
    r = requests.patch(
        f"{url}/rest/v1/friend_requests?id=eq.{request_id}",
        json={"status": new_status},
        headers=headers,
        timeout=20,
    )
    if r.status_code not in (200, 204):
        try:
            err = r.json()
        except Exception:
            err = r.text
        return f"Update failed: {err}"
    return f"Request {('accepted' if action=='accept' else 'declined')}."


def sb_cancel_outgoing_request(request_id: str) -> str:
    """Delete a pending outgoing request you sent."""
    url, headers = _sb_headers()
    r = requests.delete(
        f"{url}/rest/v1/friend_requests?id=eq.{request_id}&status=eq.pending",
        headers=headers,
        timeout=20,
    )
    if r.status_code not in (200, 204):
        try:
            err = r.json()
        except Exception:
            err = r.text
        return f"Cancel failed: {err}"
    return "Request cancelled"


def sb_respond_friend_request(req_id: str, accept: bool) -> str:
    """Accept or reject a friend request (only the recipient can act)."""
    me = _me_id()
    if not me:
        return "Please sign in first."
    url, headers = _sb_headers()
    # verify requester/recipient
    getr = requests.get(
        f"{url}/rest/v1/friend_requests",
        headers=headers,
        params={"id": f"eq.{req_id}", "select": "id,recipient_id,status"},
        timeout=20,
    )
    getr.raise_for_status()
    arr = getr.json()
    if not arr:
        return "Request not found."
    row = arr[0]
    if row["recipient_id"] != me:
        return "Only the recipient can respond to this request."
    if row.get("status") != "pending":
        return "This request is not pending."

    new_status = "accepted" if accept else "rejected"
    upd = requests.patch(
        f"{url}/rest/v1/friend_requests?id=eq.{quote_plus(req_id)}",
        headers=headers,
        json={"status": new_status},
        timeout=20,
    )
    upd.raise_for_status()
    return "Accepted." if accept else "Rejected."

def sb_list_friends_with_profiles() -> list[dict]:
    """
    Return accepted friends (two-way) as profile dicts:
      {id, username, display_name}
    We treat a 'friend' as any row in friend_requests where status='accepted'
    and either requester_id=me or recipient_id=me; the friend is the other side.
    """
    me = _me_id()
    if not me:
        return []
    url, headers = _sb_headers()

    # fetch accepted rows involving me
    params = {
        "or": f"(requester_id.eq.{me},recipient_id.eq.{me})",
        "status": "eq.accepted",
        "select": "id,requester_id,recipient_id,status",
        "limit": 1000,
    }
    r = requests.get(f"{url}/rest/v1/friend_requests", headers=headers, params=params, timeout=20)
    r.raise_for_status()
    rels = r.json()

    friend_ids = []
    for fr in rels:
        other = fr["recipient_id"] if fr["requester_id"] == me else fr["requester_id"]
        if other not in friend_ids:
            friend_ids.append(other)

    friends = []
    for uid in friend_ids:
        pr = requests.get(
            f"{url}/rest/v1/profiles",
            headers=headers,
            params={"id": f"eq.{uid}", "select": "id,username,display_name", "limit": 1},
            timeout=20,
        )
        pr.raise_for_status()
        arr = pr.json()
        if arr:
            friends.append(arr[0])
    return friends
    
def sb_cancel_outgoing_request(request_id: str) -> str:
    """Allow requester to cancel a pending outgoing request."""
    me = _me_id()
    if not me:
        return "Please sign in."
    url, headers = _sb_headers()
    # Verify it's mine and still pending
    r = requests.get(
        f"{url}/rest/v1/friend_requests?id=eq.{request_id}&select=id,requester_id,status",
        headers=headers, timeout=20
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return "Request not found."
    req = rows[0]
    if req.get("requester_id") != me:
        return "You can only cancel your own requests."
    if req.get("status") != "pending":
        return "Only pending requests can be cancelled."

    d = requests.delete(
        f"{url}/rest/v1/friend_requests?id=eq.{request_id}",
        headers=headers, timeout=20
    )
    d.raise_for_status()
    return "Request cancelled."

# ==== Flexible profiles + friends helpers (drop-in) ====
import streamlit as st
import requests
from typing import Optional, Tuple

# Reuse your existing _sb_headers or define if missing
def _sb_headers() -> Tuple[str, dict]:
    url = st.secrets.get("SUPABASE_URL")
    key = (st.secrets.get("SUPABASE_SERVICE_ROLE_KEY")
           or st.secrets.get("SUPABASE_KEY")
           or st.secrets.get("SUPABASE_ANON_KEY"))
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL / key in secrets.")
    return url, {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

def _me_id() -> Optional[str]:
    user = (st.session_state.get("sb_user") or {}).get("user") or {}
    return user.get("id") or user.get("user_id") or None

# ---- Detect profiles schema (cache in session_state) ----
def _detect_profile_columns() -> Tuple[str, str, str]:
    """
    Returns (id_col, username_col, display_col).
    Tries common variants: id/user_id, username/user_name, display_name/full_name/name.
    Cached in session_state to avoid repeated requests.
    """
    cache_key = "_profiles_schema_cols"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    url, headers = _sb_headers()
    # Fetch one row with select=*, limit=1 to learn the columns
    r = requests.get(
        f"{url}/rest/v1/profiles",
        headers=headers,
        params={"select": "*", "limit": 1},
        timeout=20,
    )
    if r.status_code in (401, 403):
        raise RuntimeError("RLS/policy blocks reading profiles. Allow SELECT to authenticated users.")
    r.raise_for_status()
    rows = r.json()
    cols = set(rows[0].keys()) if rows else set()  # if table empty, we still can't introspect exact names

    # heuristics
    id_col = "id" if "id" in cols else ("user_id" if "user_id" in cols else "id")
    username_col = (
        "username" if "username" in cols else
        ("user_name" if "user_name" in cols else "username")
    )
    display_col = (
        "display_name" if "display_name" in cols else
        ("full_name" if "full_name" in cols else
         ("name" if "name" in cols else username_col))
    )

    st.session_state[cache_key] = (id_col, username_col, display_col)
    return id_col, username_col, display_col

# ---------- PROFILES ----------
def sb_find_profile_by_username(username: str) -> Optional[dict]:
    """
    Look up a profile by username (column name auto-detected).
    Returns dict with the detected id/username/display fields.
    """
    if not username:
        return None
    url, headers = _sb_headers()
    id_col, username_col, display_col = _detect_profile_columns()

    params = {
        username_col: f"eq.{username}",
        "select": f"{id_col},{username_col},{display_col}",
        "limit": 1,
    }
    r = requests.get(f"{url}/rest/v1/profiles", headers=headers, params=params, timeout=20)
    if r.status_code in (401, 403):
        raise RuntimeError("RLS/policy blocks reading profiles. Allow SELECT to authenticated users.")
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else None

# ---------- FRIEND REQUESTS ----------
def sb_send_friend_request(target_username: str) -> str:
    """
    Create a 'pending' friend request to target_username.
    friend_requests: id, requester_id, recipient_id, status ('pending'|'accepted'|'rejected')
    """
    me = _me_id()
    if not me:
        return "Please sign in first."
    url, headers = _sb_headers()
    id_col, username_col, display_col = _detect_profile_columns()

    # 1) Find recipient by username
    target = sb_find_profile_by_username((target_username or "").strip())
    if not target:
        return "No user with that username."
    recipient_id = target[id_col]
    if recipient_id == me:
        return "You can’t send a request to yourself."

    # 2) Duplicate check for pending in either direction
    dup_params = {
        "or": f"(and(requester_id.eq.{me},recipient_id.eq.{recipient_id}),and(requester_id.eq.{recipient_id},recipient_id.eq.{me}))",
        "status": "eq.pending",
        "select": "id",
        "limit": 1,
    }
    dup = requests.get(f"{url}/rest/v1/friend_requests", headers=headers, params=dup_params, timeout=20)
    dup.raise_for_status()
    if dup.json():
        return "A pending request already exists."

    # 3) Insert pending
    payload = {"requester_id": me, "recipient_id": recipient_id, "status": "pending"}
    ins = requests.post(f"{url}/rest/v1/friend_requests", headers=headers, json=payload, timeout=20)
    if ins.status_code in (401, 403):
        return "You’re not allowed to create friend requests. Check RLS policies."
    ins.raise_for_status()
    return "Friend request sent."

def sb_list_friend_requests(direction: str = "incoming") -> list[dict]:
    """
    List pending friend requests for current user with basic profile info.
    direction: 'incoming' or 'outgoing'
    """
    me = _me_id()
    if not me:
        return []
    url, headers = _sb_headers()
    id_col, username_col, display_col = _detect_profile_columns()

    base = {"status": "eq.pending", "select": "id,status,requester_id,recipient_id"}
    if direction == "incoming":
        base["recipient_id"] = f"eq.{me}"
    else:
        base["requester_id"] = f"eq.{me}"

    r = requests.get(f"{url}/rest/v1/friend_requests", headers=headers, params=base, timeout=20)
    r.raise_for_status()
    rows = r.json()

    # enrich
    def _profile(uid: str) -> dict:
        pr = requests.get(
            f"{url}/rest/v1/profiles",
            headers=headers,
            params={"select": f"{id_col},{username_col},{display_col}", id_col: f"eq.{uid}", "limit": 1},
            timeout=20,
        )
        pr.raise_for_status()
        arr = pr.json()
        return arr[0] if arr else {id_col: uid, username_col: "unknown", display_col: ""}

    for row in rows:
        row["requester"] = _profile(row["requester_id"])
        row["recipient"] = _profile(row["recipient_id"])
    return rows

def sb_respond_friend_request(req_id: str, accept: bool) -> str:
    me = _me_id()
    if not me:
        return "Please sign in first."
    url, headers = _sb_headers()

    getr = requests.get(
        f"{url}/rest/v1/friend_requests",
        headers=headers,
        params={"id": f"eq.{req_id}", "select": "id,recipient_id,status", "limit": 1},
        timeout=20,
    )
    getr.raise_for_status()
    arr = getr.json()
    if not arr:
        return "Request not found."
    row = arr[0]
    if row["recipient_id"] != me:
        return "Only the recipient can respond."
    if row.get("status") != "pending":
        return "This request is not pending."

    new_status = "accepted" if accept else "rejected"
    upd = requests.patch(
        f"{url}/rest/v1/friend_requests",
        headers=headers,
        params={"id": f"eq.{req_id}"},
        json={"status": new_status},
        timeout=20,
    )
    upd.raise_for_status()
    return "Accepted." if accept else "Rejected."

def sb_list_friends_with_profiles() -> list[dict]:
    """
    Return accepted friends as profile dicts (id, username, display_name*).
    """
    me = _me_id()
    if not me:
        return []
    url, headers = _sb_headers()
    id_col, username_col, display_col = _detect_profile_columns()

    params = {
        "or": f"(requester_id.eq.{me},recipient_id.eq.{me})",
        "status": "eq.accepted",
        "select": "id,requester_id,recipient_id,status",
        "limit": 1000,
    }
    r = requests.get(f"{url}/rest/v1/friend_requests", headers=headers, params=params, timeout=20)
    r.raise_for_status()
    rels = r.json()

    friend_ids = []
    for fr in rels:
        other = fr["recipient_id"] if fr["requester_id"] == me else fr["requester_id"]
        if other not in friend_ids:
            friend_ids.append(other)

    friends = []
    for uid in friend_ids:
        pr = requests.get(
            f"{url}/rest/v1/profiles",
            headers=headers,
            params={"select": f"{id_col},{username_col},{display_col}", id_col: f"eq.{uid}", "limit": 1},
            timeout=20,
        )
        pr.raise_for_status()
        arr = pr.json()
        if arr:
            friends.append(arr[0])
    return friends
# ==== end block ====






