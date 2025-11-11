import os
import re
import time
import json
import uuid
import requests
import streamlit as st
from typing import Optional, List, Dict

# ---------- Base config ----------
def _base():
    url = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = (
        st.secrets.get("SUPABASE_KEY")
        or st.secrets.get("SUPABASE_ANON_KEY")
        or os.getenv("SUPABASE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
    )
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL / SUPABASE_KEY in Secrets or env.")
    return url.rstrip("/"), key

def _rest_headers(api_key: str, auth_token: str | None = None):
    h = {
        "apikey": api_key,
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    # Use user token if provided, else anon/service key
    h["Authorization"] = f"Bearer {auth_token or api_key}"
    return h

# ---------- Session helpers ----------
def _set_user_session(session_json: dict):
    st.session_state["sb_user"] = {
        "user": session_json.get("user") or {},
        "access_token": session_json.get("access_token"),
        "token_type": session_json.get("token_type"),
        "expires_in": session_json.get("expires_in"),
        "obtained_at": int(time.time()),
        "session": session_json,
    }

def _current_access_token() -> Optional[str]:
    u = st.session_state.get("sb_user")
    if not u: return None
    return u.get("access_token") or (u.get("session") or {}).get("access_token")

def _current_user_id() -> Optional[str]:
    u = st.session_state.get("sb_user")
    if not u: return None
    return (u.get("user") or {}).get("id")

# ---------- Auth: Email/password & OAuth ----------
def sign_in(email: str, password: str):
    url, key = _base()
    r = requests.post(
        f"{url}/auth/v1/token?grant_type=password",
        headers=_rest_headers(key),
        json={"email": email, "password": password},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Sign-in failed: {r.text}")
    data = r.json()
    _set_user_session(data)
    return data

def sign_up(email: str, password: str):
    url, key = _base()
    r = requests.post(
        f"{url}/auth/v1/signup",
        headers=_rest_headers(key),
        json={"email": email, "password": password},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Sign-up failed: {r.text}")
    return r.json()

def sign_out():
    st.session_state.pop("sb_user", None)

def oauth_authorize_url(provider: str = "google", redirect_to: Optional[str] = None, scopes: Optional[str] = None) -> str:
    url, key = _base()
    params = f"provider={provider}"
    if redirect_to:
        params += f"&redirect_to={requests.utils.quote(redirect_to, safe='')}"
    if scopes:
        params += f"&scopes={requests.utils.quote(scopes, safe='')}"
    return f"{url}/auth/v1/authorize?{params}"

def exchange_hash_session(access_token: str) -> dict:
    """
    When Google redirects back to APP_BASE_URL with tokens in the URL hash,
    call /auth/v1/user with Authorization: Bearer <access_token> to get user.
    """
    url, key = _base()
    h = _rest_headers(key, auth_token=access_token)
    r = requests.get(f"{url}/auth/v1/user", headers=h, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"OAuth exchange failed: {r.text}")
    user = r.json()
    sess = {"access_token": access_token, "user": user, "token_type": "bearer", "expires_in": 3600}
    _set_user_session(sess)
    return sess

# ---------- Profiles ----------
def get_profile(user_id: str) -> Optional[dict]:
    if not user_id:
        return None
    url, key = _base()
    h = _rest_headers(key)
    try:
        r = requests.get(f"{url}/rest/v1/profiles?id=eq.{user_id}", headers=h, timeout=20)
        if r.status_code in (401,403,404,406): return None
        if r.status_code >= 400: return None
        arr = r.json() or []
        return arr[0] if arr else None
    except Exception:
        return None

def username_exists(username: str) -> bool:
    url, key = _base()
    h = _rest_headers(key)
    r = requests.get(f"{url}/rest/v1/profiles?username=eq.{requests.utils.quote(username, safe='')}", headers=h, timeout=15)
    if r.status_code >= 400: return False
    arr = r.json() or []
    return len(arr) > 0

def next_default_username() -> str:
    """
    Scan existing 'userN' usernames and pick next N.
    """
    url, key = _base()
    h = _rest_headers(key)
    r = requests.get(f"{url}/rest/v1/profiles?select=username", headers=h, timeout=20)
    if r.status_code >= 400:
        return "user1"
    arr = r.json() or []
    nums = []
    for row in arr:
        u = (row.get("username") or "").strip().lower()
        m = re.match(r"user(\d+)$", u)
        if m:
            nums.append(int(m.group(1)))
    n = max(nums) + 1 if nums else 1
    candidate = f"user{n}"
    while username_exists(candidate):
        n += 1
        candidate = f"user{n}"
    return candidate

def upsert_profile(user_id: str, name: str = "", username: str = "", avatar_url: str = "") -> dict:
    if not user_id:
        raise RuntimeError("No user id")
    url, key = _base()
    h = _rest_headers(key)
    h["Prefer"] = "resolution=merge-duplicates,return=representation"
    payload = {"id": user_id}
    if name is not None: payload["name"] = (name or None)
    if username is not None: payload["username"] = (username or None)
    if avatar_url is not None: payload["avatar_url"] = (avatar_url or None)
    r = requests.post(f"{url}/rest/v1/profiles", headers=h, json=payload, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Upsert profile failed: {r.text}")
    data = r.json()
    return data[0] if isinstance(data, list) and data else payload

def ensure_profile_with_username(user_id: str, desired_username: Optional[str] = None, name: str = "") -> dict:
    prof = get_profile(user_id)
    if prof and prof.get("username"):
        return prof
    # Decide username
    if desired_username:
        u = desired_username.strip()
        if username_exists(u):
            raise RuntimeError("Username already taken. Please choose another.")
        username = u
    else:
        username = next_default_username()
    return upsert_profile(user_id, name=name or (prof or {}).get("name",""), username=username, avatar_url=(prof or {}).get("avatar_url",""))

def change_password(current_password: str, new_password: str):
    tok = _current_access_token()
    if not tok:
        raise RuntimeError("You must be signed in to change password.")
    url, key = _base()
    h = _rest_headers(key, auth_token=tok)
    r = requests.patch(f"{url}/auth/v1/user", headers=h, json={"password": new_password}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Change password failed: {r.text}")
    return r.json()

# ---------- Storage: avatar upload ----------
def upload_avatar_to_bucket(file_bytes: bytes, filename: str, content_type: str) -> str:
    """
    Uploads to 'avatars' bucket at path <user_id>/<uuid>-<filename>
    Returns public URL.
    Requires bucket 'avatars' to be public or RLS allowing public read.
    """
    uid = _current_user_id()
    tok = _current_access_token()
    if not uid or not tok:
        raise RuntimeError("You must be signed in to upload an avatar.")
    url, key = _base()
    headers = {
        "Authorization": f"Bearer {tok}",
        "apikey": key,
        "Content-Type": content_type or "application/octet-stream",
        "x-upsert": "true",
    }
    path = f"{uid}/{uuid.uuid4().hex}-{filename}"
    put = requests.put(f"{url}/storage/v1/object/avatars/{path}", headers=headers, data=file_bytes, timeout=60)
    if put.status_code >= 400:
        raise RuntimeError(f"Avatar upload failed: {put.text}")
    # Public URL (assuming public bucket)
    public_url = f"{url}/storage/v1/object/public/avatars/{path}"
    # Save to profile
    upsert_profile(uid, avatar_url=public_url)
    return public_url

# ---------- Folders & items (CRUD) ----------
def create_folder(name: str, parent_id: Optional[str]):
    if not name: raise RuntimeError("Folder name cannot be empty.")
    url, key = _base()
    h = _rest_headers(key)
    payload = {"name": name, "parent_id": parent_id}
    r = requests.post(f"{url}/rest/v1/folders", headers=h, json=payload, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Create folder failed: {r.text}")
    data = r.json()
    return data[0] if isinstance(data, list) and data else payload

def list_folders():
    url, key = _base()
    h = _rest_headers(key)
    r = requests.get(f"{url}/rest/v1/folders?select=*&order=created_at.desc", headers=h, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"List folders failed: {r.text}")
    return r.json() or []

def list_child_folders(parent_id: Optional[str]):
    url, key = _base()
    h = _rest_headers(key)
    if parent_id:
        q = f"{url}/rest/v1/folders?parent_id=eq.{parent_id}&select=*"
    else:
        q = f"{url}/rest/v1/folders?parent_id=is.null&select=*"
    r = requests.get(q, headers=h, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"List child folders failed: {r.text}")
    return r.json() or []

def delete_folder(folder_id: str):
    url, key = _base()
    h = _rest_headers(key)
    r = requests.delete(f"{url}/rest/v1/folders?id=eq.{folder_id}", headers=h, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Delete folder failed: {r.text}")
    return True

def save_item(kind: str, title: str, data: dict, folder_id: Optional[str]):
    url, key = _base()
    h = _rest_headers(key)
    payload = {"kind": kind, "title": title, "data": data, "folder_id": folder_id}
    r = requests.post(f"{url}/rest/v1/items", headers=h, json=payload, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Save item failed: {r.text}")
    arr = r.json()
    return arr[0] if isinstance(arr, list) and arr else payload

def list_items(folder_id: Optional[str], limit: int = 200):
    url, key = _base()
    h = _rest_headers(key)
    if folder_id:
        q = f"{url}/rest/v1/items?folder_id=eq.{folder_id}&select=*&order=created_at.desc&limit={limit}"
    else:
        q = f"{url}/rest/v1/items?select=*&order=created_at.desc&limit={limit}"
    r = requests.get(q, headers=h, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"List items failed: {r.text}")
    return r.json() or []

def get_item(item_id: str):
    url, key = _base()
    h = _rest_headers(key)
    r = requests.get(f"{url}/rest/v1/items?id=eq.{item_id}&select=*", headers=h, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Get item failed: {r.text}")
    arr = r.json() or []
    return arr[0] if arr else {}

def move_item(item_id: str, new_folder_id: Optional[str]):
    url, key = _base()
    h = _rest_headers(key)
    r = requests.patch(f"{url}/rest/v1/items?id=eq.{item_id}", headers=h, json={"folder_id": new_folder_id}, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Move item failed: {r.text}")
    return r.json()

def delete_item(item_id: str):
    url, key = _base()
    h = _rest_headers(key)
    r = requests.delete(f"{url}/rest/v1/items?id=eq.{item_id}", headers=h, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Delete item failed: {r.text}")
    return True

# ---------- Quiz & Flash telemetry ----------
def save_quiz_attempt(item_id: str, correct: int, total: int, details: List[dict] | None = None):
    url, key = _base()
    h = _rest_headers(key)
    uid = _current_user_id()
    payload = {"user_id": uid, "item_id": item_id, "correct": correct, "total": total, "details": details or []}
    r = requests.post(f"{url}/rest/v1/quiz_attempts", headers=h, json=payload, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Save attempt failed: {r.text}")
    return r.json()

def list_quiz_attempts(item_id: str):
    url, key = _base()
    h = _rest_headers(key)
    r = requests.get(f"{url}/rest/v1/quiz_attempts?item_id=eq.{item_id}&select=*&order=created_at.desc", headers=h, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"List attempts failed: {r.text}")
    return r.json() or []

def list_quiz_attempts_for_items(item_ids: List[str]):
    if not item_ids: return []
    url, key = _base()
    h = _rest_headers(key)
    idlist = ",".join(item_ids)
    r = requests.get(f"{url}/rest/v1/quiz_attempts?item_id=in.({idlist})&select=*&order=created_at.desc", headers=h, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"List attempts (batch) failed: {r.text}")
    return r.json() or []

def save_flash_review(item_id: str, known: bool):
    url, key = _base()
    h = _rest_headers(key)
    uid = _current_user_id()
    payload = {"user_id": uid, "item_id": item_id, "known": bool(known)}
    r = requests.post(f"{url}/rest/v1/flash_reviews", headers=h, json=payload, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Save flash review failed: {r.text}")
    return r.json()

def list_flash_reviews_for_items(item_ids: List[str]):
    if not item_ids: return []
    url, key = _base()
    h = _rest_headers(key)
    idlist = ",".join(item_ids)
    r = requests.get(f"{url}/rest/v1/flash_reviews?item_id=in.({idlist})&select=*&order=created_at.desc", headers=h, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"List flash reviews failed: {r.text}")
    return r.json() or []



