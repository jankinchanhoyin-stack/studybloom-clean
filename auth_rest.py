# auth_rest.py
import os
from typing import Optional
import requests
import streamlit as st

# ---------- Config helpers ----------
def _get_keys():
    """
    Reads Supabase credentials (URL + anon key) from Streamlit Secrets first,
    then environment variables for local dev.
    """
    url = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
    anon = st.secrets.get("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not anon:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY in Secrets/env.")
    return url.rstrip("/"), anon

def _headers(token: Optional[str] = None):
    """
    Always include the anon 'apikey'. When a user is signed in, also include
    the user's JWT so Row Level Security (RLS) policies work server-side.
    """
    _, anon = _get_keys()
    h = {"apikey": anon, "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h

def _require_user():
    """
    Ensures there is a signed-in user in Streamlit session_state,
    returning (token, user_dict). Raises if not signed in.
    """
    token = st.session_state.get("sb_token")
    user = st.session_state.get("sb_user")
    if not token or not user:
        raise RuntimeError("Not signed in.")
    return token, user

# ---------- Auth ----------
def sign_up(email: str, password: str):
    """
    Sign up via GoTrue REST. Sends confirmation email depending on your Supabase settings.
    Back-compat: returns (None, response_json) so old 'a,b = sign_up(...)' code doesn't break.
    """
    url, _ = _get_keys()
    # Optional explicit redirect after email confirmation
    app_url = st.secrets.get("APP_URL") or os.getenv("APP_URL")
    params = {"redirect_to": app_url} if app_url else None

    r = requests.post(
        f"{url}/auth/v1/signup",
        headers=_headers(),
        params=params,
        json={"email": email, "password": password},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    # Backward-compat tuple
    return None, data

def sign_in(email: str, password: str):
    """
    Password sign-in. Stores user's access token and basic identity in session_state
    so subsequent DB calls include the JWT for RLS.
    Back-compat: returns (None, response_json).
    """
    url, _ = _get_keys()
    r = requests.post(
        f"{url}/auth/v1/token?grant_type=password",
        headers=_headers(),
        json={"email": email, "password": password},
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()

    access = data.get("access_token")
    user = data.get("user") or {}
    if not access:
        raise RuntimeError("No access_token in response from Supabase.")

    st.session_state["sb_token"] = access
    st.session_state["sb_user"] = {"id": user.get("id"), "email": user.get("email")}
    # Backward-compat tuple
    return None, data

def sign_out():
    """
    Local sign-out (clears session). Supabase REST doesn't require a server call here.
    """
    st.session_state.pop("sb_token", None)
    st.session_state.pop("sb_user", None)

# ---------- Database (PostgREST) ----------
def save_summary(title: str, tl_dr: str, data: dict):
    """
    Insert a summary row tied to the signed-in user.
    Requires RLS policies in Supabase that check auth.uid() = user_id.
    """
    url, _ = _get_keys()
    token, user = _require_user()
    payload = {
        "user_id": user["id"],
        "title": title or "Untitled",
        "tl_dr": tl_dr or "",
        "data": data,
    }
    r = requests.post(
        f"{url}/rest/v1/summaries",
        headers={**_headers(token), "Prefer": "return=representation"},
        json=payload,
        timeout=20,
    )
    r.raise_for_status()
    return r.json()

def list_summaries(limit: int = 20):
    """
    List recent summaries for the signed-in user.
    """
    url, _ = _get_keys()
    token, _ = _require_user()
    params = {
        "select": "id,title,created_at",
        "order": "created_at.desc",
        "limit": str(limit),
    }
    r = requests.get(
        f"{url}/rest/v1/summaries",
        headers=_headers(token),
        params=params,
        timeout=20,
    )
    r.raise_for_status()
    return r.json()

def get_summary(summary_id: str):
    """
    Fetch a single summary (must belong to the signed-in user due to RLS).
    """
    url, _ = _get_keys()
    token, _ = _require_user()
    params = {"id": f"eq.{summary_id}", "select": "*"}
    r = requests.get(
        f"{url}/rest/v1/summaries",
        headers=_headers(token),
        params=params,
        timeout=20,
    )
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise RuntimeError("Summary not found.")
    return rows[0]

# ---------- Folder & Item helpers (REST) ----------
def create_folder(name: str, parent_id: Optional[str] = None):
    url, _ = _get_keys()
    token, user = _require_user()
    payload = {"user_id": user["id"], "name": name}
    if parent_id:
        payload["parent_id"] = parent_id
    r = requests.post(f"{url}/rest/v1/folders",
                      headers={**_headers(token), "Prefer": "return=representation"},
                      json=payload, timeout=20)
    r.raise_for_status()
    return r.json()[0] if isinstance(r.json(), list) and r.json() else r.json()

def list_folders():
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.get(f"{url}/rest/v1/folders",
                     headers=_headers(token),
                     params={"select": "id,name,parent_id,created_at", "order": "created_at.asc"},
                     timeout=20)
    r.raise_for_status()
    return r.json()

def delete_folder(folder_id: str):
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.delete(f"{url}/rest/v1/folders",
                        headers=_headers(token),
                        params={"id": f"eq.{folder_id}"},
                        timeout=20)
    r.raise_for_status()
    return True

def save_item(kind: str, title: str, data: dict, folder_id: Optional[str]):
    url, _ = _get_keys()
    token, user = _require_user()
    payload = {
        "user_id": user["id"],
        "kind": kind,
        "title": title or "Untitled",
        "data": data,
        "folder_id": folder_id
    }
    r = requests.post(f"{url}/rest/v1/items",
                      headers={**_headers(token), "Prefer": "return=representation"},
                      json=payload, timeout=20)
    r.raise_for_status()
    return r.json()[0] if isinstance(r.json(), list) and r.json() else r.json()

def list_items(folder_id: Optional[str] = None, limit: int = 100):
    url, _ = _get_keys()
    token, _ = _require_user()
    params = {"select": "id,kind,title,created_at,folder_id", "order": "created_at.desc", "limit": str(limit)}
    if folder_id:
        params["folder_id"] = f"eq.{folder_id}"
    r = requests.get(f"{url}/rest/v1/items", headers=_headers(token), params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def get_item(item_id: str):
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.get(f"{url}/rest/v1/items",
                     headers=_headers(token),
                     params={"id": f"eq.{item_id}", "select": "*"},
                     timeout=20)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise RuntimeError("Item not found.")
    return rows[0]

def move_item(item_id: str, new_folder_id: Optional[str]):
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.patch(f"{url}/rest/v1/items",
                       headers={**_headers(token), "Prefer": "return=representation"},
                       params={"id": f"eq.{item_id}"},
                       json={"folder_id": new_folder_id},
                       timeout=20)
    r.raise_for_status()
    return r.json()[0] if isinstance(r.json(), list) and r.json() else r.json()

def delete_item(item_id: str):
    url, _ = _get_keys()
    token, _ = _require_user()
    r = requests.delete(f"{url}/rest/v1/items",
                        headers=_headers(token),
                        params={"id": f"eq.{item_id}"},
                        timeout=20)
    r.raise_for_status()
    return True

