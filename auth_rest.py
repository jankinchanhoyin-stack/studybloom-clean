# auth_rest.py — RLS-safe version: uses user access token + sets user_id on inserts
import os, time, json, base64, requests
from typing import Optional, Dict, List, Tuple

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE = os.environ.get("SUPABASE_SERVICE_ROLE", "")

_session = {
    "access_token": None,
    "refresh_token": None,
    "expires_at": 0,
    "user": None,  # expects dict with 'id'
}

def _base() -> Tuple[str,str]:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY")
    return SUPABASE_URL, SUPABASE_ANON_KEY

def _now() -> int:
    return int(time.time())

def _current_access_token() -> Optional[str]:
    tok = _session.get("access_token")
    exp = _session.get("expires_at", 0)
    if tok and exp and _now() < exp - 30:
        return tok
    return tok

def current_user_id() -> Optional[str]:
    u = _session.get("user") or {}
    return u.get("id")

def _rest_headers(api_key: str, auth_token: Optional[str] = None) -> Dict[str, str]:
    # Prefer the signed-in user's access token so RLS sees auth.uid()
    tok = auth_token or _current_access_token()
    h = {
        "apikey": api_key,
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    h["Authorization"] = f"Bearer {tok or api_key}"
    return h

def _storage_headers(token: Optional[str] = None) -> Dict[str,str]:
    _, key = _base()
    tok = token or _current_access_token()
    return {"apikey": key, "Authorization": f"Bearer {tok or key}"}

# ---------- Auth ----------
def sign_up(email: str, password: str) -> Dict:
    url, key = _base()
    r = requests.post(
        f"{url}/auth/v1/signup",
        headers={"apikey": key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Sign up failed: {r.status_code} {r.text}")
    return r.json()

def sign_in(email: str, password: str) -> Dict:
    url, key = _base()
    r = requests.post(
        f"{url}/auth/v1/token?grant_type=password",
        headers={"apikey": key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Sign in failed: {r.status_code} {r.text}")
    data = r.json()
    _session["access_token"] = data.get("access_token")
    _session["refresh_token"] = data.get("refresh_token")
    _session["expires_at"] = _now() + int(data.get("expires_in", 3600))
    _session["user"] = data.get("user")
    return data

def sign_out():
    _session.update({"access_token": None, "refresh_token": None, "expires_at": 0, "user": None})

def change_password(current_password: str, new_password: str) -> None:
    # Placeholder (typical flow is email OTP)
    raise RuntimeError("Password change via app is not enabled. Use the emailed reset link instead.")

# ---------- Profiles ----------
def upsert_profile(user_id: str, name: str, username: str, avatar_url: str = "") -> Dict:
    url, key = _base()
    payload = {"id": user_id, "name": name, "username": username, "avatar_url": avatar_url}
    r = requests.post(
        f"{url}/rest/v1/profiles",
        headers=_rest_headers(key),
        json=payload
    )
    if r.status_code >= 400:
        # Try update if exists
        r = requests.patch(
            f"{url}/rest/v1/profiles?id=eq.{user_id}",
            headers=_rest_headers(key),
            json={"name": name, "username": username, "avatar_url": avatar_url},
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Upsert profile failed: {r.status_code} {r.text}")
    return r.json()[0] if r.json() else payload

def get_profile(user_id: str) -> Dict:
    url, key = _base()
    r = requests.get(
        f"{url}/rest/v1/profiles?id=eq.{user_id}",
        headers=_rest_headers(key)
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Get profile failed: {r.status_code} {r.text}")
    arr = r.json()
    return arr[0] if arr else {}

def username_exists(username: str) -> bool:
    url, key = _base()
    r = requests.get(
        f"{url}/rest/v1/profiles?username=eq.{username}",
        headers=_rest_headers(key),
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Username check failed: {r.status_code} {r.text}")
    return len(r.json()) > 0

def set_missing_username_if_needed(user_id: str) -> None:
    prof = get_profile(user_id)
    if not prof.get("username"):
        base = "user"
        idx = 1
        while username_exists(f"{base}{idx}"):
            idx += 1
        upsert_profile(user_id, prof.get("name",""), f"{base}{idx}", prof.get("avatar_url",""))

# ---------- OAuth ----------
def oauth_authorize_url(provider: str = "google") -> str:
    url, _ = _base()
    return f"{url}/auth/v1/authorize?provider={provider}&redirect_to="

# ---------- Storage (avatar helper kept; safe to ignore if UI removed) ----------
def upload_avatar_to_storage(content: bytes, content_type: str, user_id: str) -> str:
    url, _ = _base()
    filename = f"{user_id}/avatar"
    put = requests.post(
        f"{url}/storage/v1/object/avatars/{filename}",
        headers={**_storage_headers(), "Content-Type": content_type},
        data=content
    )
    if put.status_code >= 400:
        raise RuntimeError(f"Upload avatar failed: {put.status_code} {put.text}")
    return f"{url}/storage/v1/object/public/avatars/{filename}"

# ---------- Folders & Items ----------
def create_folder(name: str, parent_id: Optional[str]) -> Dict:
    url, key = _base()
    uid = current_user_id()
    if not uid:
        raise RuntimeError("Create folder failed: no signed-in user")
    payload = {"name": name, "parent_id": parent_id, "user_id": uid}
    r = requests.post(f"{url}/rest/v1/folders", headers=_rest_headers(key), json=payload)
    if r.status_code >= 400:
        raise RuntimeError(f"Create folder failed: {r.status_code} {r.text}")
    return r.json()[0]

def list_folders(parent_id: Optional[str] = None) -> List[Dict]:
    url, key = _base()
    if parent_id is None:
        q = f"{url}/rest/v1/folders?parent_id=is.null&select=*"
    else:
        q = f"{url}/rest/v1/folders?parent_id=eq.{parent_id}&select=*"
    r = requests.get(q, headers=_rest_headers(key))
    if r.status_code >= 400:
        raise RuntimeError(f"List folders failed: {r.status_code} {r.text}")
    return r.json()

def save_item(folder_id: Optional[str], kind: str, title: str, data: dict) -> Dict:
    url, key = _base()
    uid = current_user_id()
    if not uid:
        raise RuntimeError("Save item failed: no signed-in user")
    payload = {"folder_id": folder_id, "kind": kind, "title": title, "data": data, "user_id": uid}
    r = requests.post(f"{url}/rest/v1/items", headers=_rest_headers(key), json=payload)
    if r.status_code >= 400:
        raise RuntimeError(f"Save item failed: {r.status_code} {r.text}")
    return r.json()[0]

def list_items(folder_id: Optional[str] = None) -> List[Dict]:
    url, key = _base()
    if folder_id is None:
        q = f"{url}/rest/v1/items?folder_id=is.null&select=*"
    else:
        q = f"{url}/rest/v1/items?folder_id=eq.{folder_id}&select=*"
    r = requests.get(q, headers=_rest_headers(key))
    if r.status_code >= 400:
        raise RuntimeError(f"List items failed: {r.status_code} {r.text}")
    return r.json()

def get_item(item_id: str) -> Dict:
    url, key = _base()
    r = requests.get(f"{url}/rest/v1/items?id=eq.{item_id}", headers=_rest_headers(key))
    if r.status_code >= 400:
        raise RuntimeError(f"Get item failed: {r.status_code} {r.text}")
    rows = r.json()
    return rows[0] if rows else {}

def move_item(item_id: str, new_folder_id: Optional[str]) -> None:
    url, key = _base()
    r = requests.patch(
        f"{url}/rest/v1/items?id=eq.{item_id}",
        headers=_rest_headers(key),
        json={"folder_id": new_folder_id},
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Move item failed: {r.status_code} {r.text}")

def delete_item(item_id: str) -> None:
    url, key = _base()
    r = requests.delete(
        f"{url}/rest/v1/items?id=eq.{item_id}",
        headers=_rest_headers(key)
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Delete item failed: {r.status_code} {r.text}")

def delete_folder(folder_id: str) -> None:
    url, key = _base()
    r = requests.delete(
        f"{url}/rest/v1/folders?id=eq.{folder_id}",
        headers=_rest_headers(key)
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Delete folder failed: {r.status_code} {r.text}")

