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

def sb_send_friend_request(target_username: str) -> str:
    """
    Create a pending friend request to target_username.
    Table: friend_requests (id uuid, requester_id uuid, recipient_id uuid, status text)
    Valid statuses: 'pending','accepted','rejected'
    """
    me = _me_id()
    if not me:
        return "Please sign in first."

    # 1) Find recipient
    target = sb_find_profile_by_username((target_username or "").strip())
    if not target:
        return "No user with that username."
    recipient_id = target["id"]
    if recipient_id == me:
        return "You can’t send a request to yourself."

    url, headers = _sb_headers()

    # 2) Block duplicates in either direction while pending
    # requester_id=me,recipient_id=them OR requester_id=them,recipient_id=me with status pending
    params = {
        "or": f"(and(requester_id.eq.{me},recipient_id.eq.{recipient_id}),and(requester_id.eq.{recipient_id},recipient_id.eq.{me}))",
        "status": "eq.pending",
        "select": "id",
        "limit": 1,
    }
    check = requests.get(f"{url}/rest/v1/friend_requests", headers=headers, params=params, timeout=20)
    check.raise_for_status()
    exists = check.json()
    if exists:
        return "A pending request already exists."

    # 3) Insert pending request
    payload = {
        "requester_id": me,
        "recipient_id": recipient_id,
        "status": "pending",
    }
    r = requests.post(f"{url}/rest/v1/friend_requests", headers=headers, json=payload, timeout=20)
    r.raise_for_status()
    return "Friend request sent."


def sb_list_friend_requests(direction: str = "incoming") -> list[dict]:
    """
    List pending friend requests for the current user (incoming/outgoing).
    Returns rows with joined profile info for convenience.
    """
    me = _me_id()
    if not me:
        return []
    url, headers = _sb_headers()

    base_params = {"status": "eq.pending", "select": "id,status,requester_id,recipient_id"}
    if direction == "incoming":
        base_params["recipient_id"] = f"eq.{me}"
    else:
        base_params["requester_id"] = f"eq.{me}"

    r = requests.get(f"{url}/rest/v1/friend_requests", headers=headers, params=base_params, timeout=20)
    r.raise_for_status()
    rows = r.json()

    # enrich with usernames
    def _profile(id_):
        pr = requests.get(
            f"{url}/rest/v1/profiles",
            headers=headers,
            params={"id": f"eq.{id_}", "select": "id,username,display_name", "limit": 1},
            timeout=20,
        )
        pr.raise_for_status()
        arr = pr.json()
        return arr[0] if arr else {"id": id_, "username": "unknown", "display_name": ""}

    for row in rows:
        row["requester"] = _profile(row["requester_id"])
        row["recipient"] = _profile(row["recipient_id"])
    return rows

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





