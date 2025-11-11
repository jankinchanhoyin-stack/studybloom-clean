# auth_rest.py — streamlined Supabase auth + profile helpers (RLS-safe)
import os, time, requests
from typing import Optional, Dict, List, Tuple

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

_session = {
    "access_token": None,
    "refresh_token": None,
    "expires_at": 0,
    "user": None,  # expects {"id":…, "email":…}
}

# ---------- internal helpers ----------
def _get_keys() -> Tuple[str, str]:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY")
    return SUPABASE_URL, SUPABASE_ANON_KEY

def _now() -> int: return int(time.time())

def _current_access_token() -> Optional[str]:
    tok, exp = _session.get("access_token"), _session.get("expires_at", 0)
    if tok and exp and _now() < exp - 30: return tok
    return tok

def _require_user() -> Tuple[str, Dict]:
    tok = _current_access_token()
    user = _session.get("user")
    if not tok or not user: raise RuntimeError("Not signed in")
    return tok, user

def _headers(token: Optional[str] = None) -> Dict[str, str]:
    _, key = _get_keys()
    tok = token or _current_access_token()
    return {"apikey": key, "Authorization": f"Bearer {tok or key}",
            "Content-Type": "application/json"}

# ---------- auth ----------
def sign_up(email: str, password: str) -> Dict:
    url, _ = _get_keys()
    r = requests.post(f"{url}/auth/v1/signup",
                      headers={"apikey": SUPABASE_ANON_KEY,
                               "Content-Type": "application/json"},
                      json={"email": email, "password": password}, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Sign-up failed: {r.status_code} {r.text}")
    return r.json()

def sign_in(email: str, password: str) -> Dict:
    url, _ = _get_keys()
    r = requests.post(f"{url}/auth/v1/token?grant_type=password",
                      headers={"apikey": SUPABASE_ANON_KEY,
                               "Content-Type": "application/json"},
                      json={"email": email, "password": password}, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Sign-in failed: {r.status_code} {r.text}")
    d = r.json()
    _session.update({
        "access_token": d.get("access_token"),
        "refresh_token": d.get("refresh_token"),
        "expires_at": _now() + int(d.get("expires_in", 3600)),
        "user": d.get("user"),
    })
    return d

def sign_out(): _session.update({"access_token": None, "refresh_token": None,
                                 "expires_at": 0, "user": None})

# ---------- profiles ----------
def get_profile() -> Dict:
    url, _ = _get_keys()
    tok, user = _require_user(); uid = user["id"]
    r = requests.get(f"{url}/rest/v1/profiles",
                     headers=_headers(tok),
                     params={"id": f"eq.{uid}", "select": "id,name,username"},
                     timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Get profile failed: {r.status_code} {r.text}")
    rows = r.json()
    base = rows[0] if rows else {"id": uid, "name": "", "username": ""}
    base["email"] = user.get("email", "")
    return base

def username_exists(username: str) -> bool:
    url, _ = _get_keys()
    tok, _ = _require_user()
    r = requests.get(f"{url}/rest/v1/profiles",
                     headers=_headers(tok),
                     params={"username": f"eq.{username}", "select": "id",
                             "limit": "1"}, timeout=15)
    if r.status_code >= 400:
        raise RuntimeError(f"Username check failed: {r.status_code} {r.text}")
    return bool(r.json())

def upsert_profile(name: str, username: str) -> Dict:
    url, _ = _get_keys()
    tok, user = _require_user(); uid = user["id"]
    payload = {"id": uid, "name": name, "username": username}
    r = requests.post(f"{url}/rest/v1/profiles",
                      headers={**_headers(tok),
                               "Prefer": "resolution=merge-duplicates,return=representation"},
                      json=payload, timeout=20)
    if 200 <= r.status_code < 300:
        data = r.json(); return data[0] if isinstance(data, list) and data else payload
    up = requests.patch(f"{url}/rest/v1/profiles",
                        headers={**_headers(tok), "Prefer": "return=representation"},
                        params={"id": f"eq.{uid}"}, json=payload, timeout=20)
    if up.status_code >= 400:
        raise RuntimeError(f"Update profile failed: {up.status_code} {up.text}")
    data = up.json(); return data[0] if isinstance(data, list) and data else payload

def change_password(new_password: str) -> bool:
    url, _ = _get_keys(); tok, _ = _require_user()
    r = requests.put(f"{url}/auth/v1/user",
                     headers=_headers(tok),
                     json={"password": new_password}, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Change password failed: {r.status_code} {r.text}")
    return True

# ---------- folders / items ----------
def create_folder(name: str, parent_id: Optional[str]) -> Dict:
    url, _ = _get_keys(); tok, user = _require_user()
    payload = {"name": name, "parent_id": parent_id, "user_id": user["id"]}
    r = requests.post(f"{url}/rest/v1/folders",
                      headers=_headers(tok), json=payload, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Create folder failed: {r.status_code} {r.text}")
    return r.json()[0]

def list_folders(parent_id: Optional[str] = None) -> List[Dict]:
    url, _ = _get_keys(); tok, _ = _require_user()
    if parent_id is None:
        r = requests.get(f"{url}/rest/v1/folders?parent_id=is.null&select=*",
                         headers=_headers(tok), timeout=20)
    else:
        r = requests.get(f"{url}/rest/v1/folders?parent_id=eq.{parent_id}&select=*",
                         headers=_headers(tok), timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"List folders failed: {r.status_code} {r.text}")
    return r.json()

def save_item(folder_id: Optional[str], kind: str, title: str, data: dict) -> Dict:
    url, _ = _get_keys(); tok, user = _require_user()
    payload = {"folder_id": folder_id, "kind": kind, "title": title,
               "data": data, "user_id": user["id"]}
    r = requests.post(f"{url}/rest/v1/items",
                      headers=_headers(tok), json=payload, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"Save item failed: {r.status_code} {r.text}")
    return r.json()[0]



