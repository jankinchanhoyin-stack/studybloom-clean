# auth_rest.py
import os
import requests
from typing import Optional, Tuple, List, Dict
import streamlit as st

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

def sign_up(email: str, password: str, display_name: Optional[str] = None, username: Optional[str] = None):
    url, _ = _get_keys()
    payload = {"email": email, "password": password}
    meta = {}
    if display_name: meta["display_name"] = display_name
    if username: meta["username"] = username
    if meta:
        payload["data"] = meta
    r = requests.post(f"{url}/auth/v1/signup", json=payload, headers=_headers(), timeout=20)
    r.raise_for_status()
    return r.json()

def sign_out():
    st.session_state.pop("sb_user", None)

def get_current_user() -> Optional[Dict]:
    u = st.session_state.get("sb_user")
    return u["user"] if (u and "user" in u) else None

def refresh_user():
    """Refetch the current user object (e.g., after updating user metadata)."""
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.get(f"{url}/auth/v1/user", headers=_headers(token), timeout=20)
    r.raise_for_status()
    user = r.json()
    st.session_state["sb_user"]["user"] = user
    return user

def update_user_metadata(display_name: Optional[str] = None, username: Optional[str] = None):
    url, _ = _get_keys()
    token, _ = _require_user()
    data = {"data": {}}
    if display_name is not None:
        data["data"]["display_name"] = display_name
    if username is not None:
        data["data"]["username"] = username
    r = requests.put(f"{url}/auth/v1/user", json=data, headers=_headers(token), timeout=20)
    r.raise_for_status()
    refresh_user()
    return r.json()

def change_password(new_password: str):
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.put(
        f"{url}/auth/v1/user",
        json={"password": new_password},
        headers=_headers(token),
        timeout=20
    )
    r.raise_for_status()
    return r.json()

# ---------- Content endpoints (unchanged from your app) ----------
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
    if not item_ids:
        return []
    url, _ = _get_keys()
    token, _ = _require_user()
    ids_csv = "(" + ","import os
import requests
from typing import Optional, Tuple, List, Dict
import streamlit as st

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

def sign_up(email: str, password: str, display_name: Optional[str] = None, username: Optional[str] = None):
    url, _ = _get_keys()
    payload = {"email": email, "password": password}
    meta = {}
    if display_name: meta["display_name"] = display_name
    if username: meta["username"] = username
    if meta:
        payload["data"] = meta
    r = requests.post(f"{url}/auth/v1/signup", json=payload, headers=_headers(), timeout=20)
    r.raise_for_status()
    return r.json()

def sign_out():
    st.session_state.pop("sb_user", None)

def get_current_user() -> Optional[Dict]:
    u = st.session_state.get("sb_user")
    return u["user"] if (u and "user" in u) else None

def refresh_user():
    """Refetch the current user object (e.g., after updating user metadata)."""
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.get(f"{url}/auth/v1/user", headers=_headers(token), timeout=20)
    r.raise_for_status()
    user = r.json()
    st.session_state["sb_user"]["user"] = user
    return user

def update_user_metadata(display_name: Optional[str] = None, username: Optional[str] = None):
    url, _ = _get_keys()
    token, _ = _require_user()
    data = {"data": {}}
    if display_name is not None:
        data["data"]["display_name"] = display_name
    if username is not None:
        data["data"]["username"] = username
    r = requests.put(f"{url}/auth/v1/user", json=data, headers=_headers(token), timeout=20)
    r.raise_for_status()
    refresh_user()
    return r.json()

def change_password(new_password: str):
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.put(
        f"{url}/auth/v1/user",
        json={"password": new_password},
        headers=_headers(token),
        timeout=20
    )
    r.raise_for_status()
    return r.json()

# ---------- Content endpoints (unchanged from your app) ----------
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

def save_flash_review(item_id: str, known: bool):
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



