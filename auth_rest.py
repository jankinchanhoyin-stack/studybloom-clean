# auth_rest.py
import os
import requests
from typing import Optional, Tuple, List, Dict
import streamlit as st
from datetime import datetime, timedelta, timezone
import requests

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
def sign_in(email: str, password: str):
    url, _ = _get_keys()
    r = requests.post(
        f"{url}/auth/v1/token?grant_type=password",
        json={"email": email, "password": password},
        headers=_headers(), timeout=20
    )
    r.raise_for_status()
    data = r.json()
    st.session_state["sb_user"] = {"access_token": data["access_token"], "user": data["user"]}
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

def sb_list_friends_with_profiles() -> list[dict]:
    """
    Returns a list of {"friend_id", "username", "display_name"} for the current user’s friends.
    Expects a 'friends' table with (user_id, friend_user_id) and a 'profiles' table with (user_id, username, display_name).
    """
    # You need the current user id available here; adapt if you use a different getter.
    me = (st.session_state.get("sb_user") or {}).get("user") or {}
    me_id = me.get("id")
    if not me_id:
        return []

    url, headers = _sb_headers()

    # 1) fetch friend IDs
    r = requests.get(
        f"{url}/rest/v1/friends?user_id=eq.{me_id}&select=friend_user_id",
        headers=headers,
        timeout=20
    )
    if r.status_code != 200:
        return []

    friend_ids = [row.get("friend_user_id") for row in r.json() if row.get("friend_user_id")]
    if not friend_ids:
        return []

    # 2) fetch profile info for those IDs via IN filter
    # Build a comma-separated list wrapped in parentheses for 'in.()' filter
    in_clause = ",".join(friend_ids)
    r2 = requests.get(
        f"{url}/rest/v1/profiles?user_id=in.({in_clause})&select=user_id,username,display_name",
        headers=headers,
        timeout=20
    )
    if r2.status_code != 200:
        return []

    profiles = r2.json()
    # Normalize output shape
    out = []
    for p in profiles:
        out.append({
            "friend_id": p.get("user_id"),
            "username": p.get("username") or "",
            "display_name": p.get("display_name") or "",
        })
    return out


def sb_sum_xp_for_window(user_id: str, start_iso: str, end_iso: str) -> int:
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
    today = sb_sum_xp_for_window(user_id, _iso_start_of_today_utc(), _iso_start_of_tomorrow_utc())
    month = sb_sum_xp_for_window(user_id, _iso_start_of_month_utc(), _iso_start_of_next_month_utc())
    return {"today": today, "month": month}





