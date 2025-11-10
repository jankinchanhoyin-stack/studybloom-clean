import os
import requests
import streamlit as st

def _get_keys():
    # From Streamlit Secrets first, fallback to env (local dev)
    url = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
    anon = st.secrets.get("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not anon:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY in Secrets/env.")
    # normalize: remove trailing slash
    url = url.rstrip("/")
    return url, anon

def _headers(token: str | None = None):
    _, anon = _get_keys()
    h = {
        "apikey": anon,
        "Content-Type": "application/json",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h

# ---------- Auth ----------
def sign_up(email: str, password: str):
    url, _ = _get_keys()
    # Use the Streamlit URL as your default redirect
    app_url = st.secrets.get("APP_URL") or os.getenv("APP_URL")
    params = {}
    if app_url:
        params["redirect_to"] = app_url  # works with GoTrue v2 REST

    r = requests.post(
        f"{url}/auth/v1/signup",
        params=params,
        headers=_headers(),
        json={"email": email, "password": password},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()
    
def sign_in(email: str, password: str):
    url, _ = _get_keys()
    endpoint = f"{url}/auth/v1/token?grant_type=password"
    r = requests.post(endpoint, headers=_headers(), json={"email": email, "password": password}, timeout=20)
    r.raise_for_status()
    data = r.json()
    # store session in Streamlit
    access = data.get("access_token")
    user = data.get("user") or {}
    if not access:
        raise RuntimeError("No access_token in response.")
    st.session_state["sb_token"] = access
    st.session_state["sb_user"] = {"id": user.get("id"), "email": user.get("email")}
    return data

def sign_out():
    st.session_state.pop("sb_token", None)
    st.session_state.pop("sb_user", None)

def _require_user():
    token = st.session_state.get("sb_token")
    user = st.session_state.get("sb_user")
    if not token or not user:
        raise RuntimeError("Not signed in.")
    return token, user

# ---------- DB (PostgREST) ----------
def save_summary(title: str, tl_dr: str, data: dict):
    url, _ = _get_keys()
    token, user = _require_user()
    endpoint = f"{url}/rest/v1/summaries"
    payload = {
        "user_id": user["id"],
        "title": title or "Untitled",
        "tl_dr": tl_dr or "",
        "data": data,
    }
    r = requests.post(endpoint, headers={**_headers(token), "Prefer": "return=representation"}, json=payload, timeout=20)
    r.raise_for_status()
    return r.json()

def list_summaries(limit: int = 20):
    url, _ = _get_keys()
    token, _ = _require_user()
    # order=created_at.desc&limit=20
    endpoint = f"{url}/rest/v1/summaries"
    params = {
        "select": "id,title,created_at",
        "order": "created_at.desc",
        "limit": str(limit),
    }
    r = requests.get(endpoint, headers=_headers(token), params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def get_summary(summary_id: str):
    url, _ = _get_keys()
    token, _ = _require_user()
    endpoint = f"{url}/rest/v1/summaries"
    params = {"id": f"eq.{summary_id}", "select": "*"}
    r = requests.get(endpoint, headers=_headers(token), params=params, timeout=20)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise RuntimeError("Summary not found.")
    return rows[0]
