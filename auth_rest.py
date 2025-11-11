import os
import re
import time
import uuid
import json
import requests
import streamlit as st
from typing import Optional, Tuple, Dict, Any, List

# ============ Base + headers ============

def _base() -> Tuple[str, str]:
    url = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = (
        st.secrets.get("SUPABASE_KEY")
        or st.secrets.get("SUPABASE_ANON_KEY")
        or os.getenv("SUPABASE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
    )
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL / SUPABASE_KEY (or ANON) in Secrets or env.")
    return url.rstrip("/"), key

def _rest_headers(api_key: str, auth_token: Optional[str] = None) -> Dict[str, str]:
    h = {
        "apikey": api_key,
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    h["Authorization"] = f"Bearer {auth_token or api_key}"
    return h

# ============ Session helpers ============

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
    u = st.session_state.get("sb_user") or {}
    return u.get("access_token") or (u.get("session") or {}).get("access_token")

def _current_user_id() -> Optional[str]:
    u = st.session_state.get("sb_user") or {}
    return (u.get("user") or {}).get("id")

# ============ Auth core ============

def sign_in(email: str, password: str) -> dict:
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

def sign_up(email: str, password: str) -> dict:
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

# ============ Profiles ============

def get_profile(user_id: str) -> Optional[dict]:
    if not user_id:
        return None
    url, key = _base()
    h = _rest_headers(key)
    try:
        r = requests.get(f"{url}/rest/v1/profiles?id=eq.{user_id}", headers=h, timeout=20)
        if r.status_code in (401, 403, 404, 406):
            return None
        if r.status_code >= 400:
            return None
        arr = r.json() or []
        return arr[0] if arr else None
    except Exception:
        return None

def username_exists(username: str) -> bool:
    url, key = _base()
    h = _rest_headers(key)
    r = requests.get(f"{url}/rest/v1/profiles?username=eq.{username}", headers=h, timeout=15)
    if r.status_code >= 400:
        # if table missing column, we don't block sign-up (but uniqueness will fail on DB side if unique)
        return False
    return bool(r.json())

def next_sequential_username() -> str:
    """
    Finds max userN and returns next: user{N+1}
    """
    url, key = _base()
    h = _rest_headers(key)
    r = requests.get(f"{url}/rest/v1/profiles?select=username&order=username.asc", headers=h, timeout=20)
    if r.status_code >= 400:
        return "user1"
    names = [row.get("username","") for row in (r.json() or []) if isinstance(row, dict)]
    maxn = 0
    for n in names:
        m = re.fullmatch(r"user(\d+)", n or "")
        if m:
            try:
                maxn = max(maxn, int(m.group(1)))
            except:
                pass
    return f"user{maxn+1}"

def upsert_profile(user_id: str, name: str = "", username: str = "", avatar_url: str = "") -> dict:
    """
    Creates or merges a profile row.
    Requires table `profiles(id uuid pk references auth.users(id), name text, username text unique, avatar_url text)`
    """
    if not user_id:
        raise RuntimeError("No user id")
    url, key = _base()
    h = _rest_headers(key)
    h["Prefer"] = "resolution=merge-duplicates,return=representation"
    payload = {"id": user_id}
    if name is not None: payload["name"] = name or None
    if username is not None: payload["username"] = username or None
    if avatar_url is not None: payload["avatar_url"] = avatar_url or None
    r = requests.post(f"{url}/rest/v1/profiles", headers=h, json=payload, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Upsert profile failed: {r.text}")
    data = r.json()
    return data[0] if isinstance(data, list) and data else payload

def set_missing_username_if_needed(user_id: str):
    """
    For legacy users without username, set to next 'userN' (unique).
    """
    prof = get_profile(user_id) or {}
    if prof.get("username"):
        return
    tries = 0
    while tries < 1000:
        cand = next_sequential_username()
        if not username_exists(cand):
            upsert_profile(user_id, name=prof.get("name",""), username=cand, avatar_url=prof.get("avatar_url",""))
            return
        tries += 1

def change_password(current_password: str, new_password: str):
    tok = _current_access_token()
    if not tok:
        raise RuntimeError("You must be signed in to change password.")
    if not new_password:
        raise RuntimeError("New password cannot be empty.")
    url, key = _base()
    h = _rest_headers(key, auth_token=tok)
    r = requests.patch(f"{url}/auth/v1/user", headers=h, json={"password": new_password}, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Change password failed: {r.text}")
    return r.json()

# ============ OAuth (Google) ============

def oauth_authorize_url(provider: str = "google") -> str:
    base_url, _ = _base()
    site = st.secrets.get("SITE_URL") or os.getenv("SITE_URL") or ""
    params = f"provider={provider}"
    if site:
        params += f"&redirect_to={site}"
    return f"{base_url}/auth/v1/authorize?{params}"

# ============ Storage: avatar upload ============

def _storage_headers(token: Optional[str] = None) -> Dict[str,str]:
    _, key = _base()
    return {"apikey": key, "Authorization": f"Bearer {token or key}"}

def upload_avatar_to_storage(file_bytes: bytes, content_type: str, user_id: str) -> str:
    base_url, _ = _base()
    bucket = st.secrets.get("AVATARS_BUCKET", "avatars")
    ext = "png"
    if content_type == "image/jpeg": ext = "jpg"
    elif content_type == "image/webp": ext = "webp"
    filename = f"{user_id}/{uuid.uuid4().hex}.{ext}"
    headers = _storage_headers()
    url = f"{base_url}/storage/v1/object/{bucket}/{filename}"
    r = requests.post(url, headers=headers, params={"upsert": "true"}, data=file_bytes)
    if r.status_code >= 400:
        raise RuntimeError(f"Avatar upload failed: {r.text}")
    public_url = f"{base_url}/storage/v1/object/public/{bucket}/{filename}"
    return public_url

# ============ Folders & Items (CRUD) ============

def _try_get_user_filter() -> Optional[str]:
    """If your schema has user scoping (user_id columns), return filter; else None."""
    uid = _current_user_id()
    return uid

def create_folder(name: str, parent_id: Optional[str]):
    if not name:
        raise RuntimeError("Folder name cannot be empty.")
    url, key = _base()
    h = _rest_headers(key)
    payload = {"name": name, "parent_id": parent_id}
    # if schema includes user_id, attach it
    uid = _try_get_user_filter()
    if uid: payload["user_id"] = uid
    r = requests.post(f"{url}/rest/v1/folders", headers=h, json=payload, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Create folder failed: {r.text}")
    data = r.json()
    return data[0] if isinstance(data, list) and data else payload

def list_folders() -> List[dict]:
    url, key = _base()
    h = _rest_headers(key)
    uid = _try_get_user_filter()

    # try user-scoped first
    try:
        if uid:
            q = f"{url}/rest/v1/folders?user_id=eq.{uid}&select=*&order=created_at.desc"
        else:
            q = f"{url}/rest/v1/folders?select=*&order=created_at.desc"
        r = requests.get(q, headers=h, timeout=30)
        if r.status_code < 400:
            return r.json() or []
    except Exception:
        pass

    # fallback (non-scoped)
    r = requests.get(f"{url}/rest/v1/folders?select=*&order=created_at.desc", headers=h, timeout=30)
    if r.status_code >= 400:
        # surface the error to UI
        raise RuntimeError(f"List folders failed: {r.text}")
    return r.json() or []

def list_child_folders(parent_id: Optional[str]) -> List[dict]:
    url, key = _base()
    h = _rest_headers(key)
    uid = _try_get_user_filter()
    try:
        if uid:
            if parent_id:
                q = f"{url}/rest/v1/folders?user_id=eq.{uid}&parent_id=eq.{parent_id}&select=*"
            else:
                q = f"{url}/rest/v1/folders?user_id=eq.{uid}&parent_id=is.null&select=*"
        else:
            if parent_id:
                q = f"{url}/rest/v1/folders?parent_id=eq.{parent_id}&select=*"
            else:
                q = f"{url}/rest/v1/folders?parent_id=is.null&select=*"
        r = requests.get(q, headers=h, timeout=30)
        if r.status_code < 400:
            return r.json() or []
    except Exception:
        pass
    # fallback
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
    uid = _try_get_user_filter()
    if uid: payload["user_id"] = uid
    r = requests.post(f"{url}/rest/v1/items", headers=h, json=payload, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Save item failed: {r.text}")
    arr = r.json()
    return arr[0] if isinstance(arr, list) and arr else payload

def list_items(folder_id: Optional[str], limit: int = 200) -> List[dict]:
    url, key = _base()
    h = _rest_headers(key)
    uid = _try_get_user_filter()

    # try user-scoped first
    try:
        if folder_id:
            if uid:
                q = f"{url}/rest/v1/items?user_id=eq.{uid}&folder_id=eq.{folder_id}&select=*&order=created_at.desc&limit={limit}"
            else:
                q = f"{url}/rest/v1/items?folder_id=eq.{folder_id}&select=*&order=created_at.desc&limit={limit}"
        else:
            if uid:
                q = f"{url}/rest/v1/items?user_id=eq.{uid}&select=*&order=created_at.desc&limit={limit}"
            else:
                q = f"{url}/rest/v1/items?select=*&order=created_at.desc&limit={limit}"
        r = requests.get(q, headers=h, timeout=30)
        if r.status_code < 400:
            return r.json() or []
    except Exception:
        pass

    # fallback (non-scoped)
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

# ============ Quiz + flashcard telemetry ============

def save_quiz_attempt(item_id: str, correct: int, total: int, details: Optional[List[dict]] = None):
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
    if not item_ids:
        return []
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




