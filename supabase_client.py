import os
import streamlit as st
from supabase import create_client, Client

def _get_supabase_keys():
    url = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_ANON_KEY in Streamlit secrets or env.")
    return url, key

def get_client(with_token: bool = True) -> Client:
    url, key = _get_supabase_keys()
    sb = create_client(url, key)
    if with_token:
        token = st.session_state.get("sb_token")
        if token:
            # attach the user's access token so RLS policies work
            sb.postgrest.auth(token)
    return sb

def sign_up(email: str, password: str):
    sb = get_client(with_token=False)
    res = sb.auth.sign_up({"email": email, "password": password})
    return sb, res

def sign_in(email: str, password: str):
    sb = get_client(with_token=False)
    res = sb.auth.sign_in_with_password({"email": email, "password": password})
    # attach token for DB calls
    token = getattr(res.session, "access_token", None) or getattr(res, "access_token", None)
    if token:
        st.session_state["sb_token"] = token
        sb.postgrest.auth(token)
    user = getattr(res, "user", None) or getattr(res.session, "user", None)
    if user:
        st.session_state["sb_user"] = {"id": user.id, "email": user.email}
    return sb, res

def sign_out():
    sb = get_client(with_token=True)
    try:
        sb.auth.sign_out()
    except Exception:
        pass
    st.session_state.pop("sb_token", None)
    st.session_state.pop("sb_user", None)

def save_summary(title: str, tl_dr: str, data: dict):
    sb = get_client(with_token=True)
    user = st.session_state.get("sb_user")
    if not user:
        raise RuntimeError("Not signed in.")
    payload = {"user_id": user["id"], "title": title or "Untitled", "tl_dr": tl_dr, "data": data}
    return sb.table("summaries").insert(payload).execute()

def list_summaries(limit: int = 20):
    sb = get_client(with_token=True)
    return sb.table("summaries").select("id,title,created_at").order("created_at", desc=True).limit(limit).execute()

def get_summary(summary_id: str):
    sb = get_client(with_token=True)
    return sb.table("summaries").select("*").eq("id", summary_id).single().execute()
