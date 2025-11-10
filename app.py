# app.py
import streamlit as st
import sys, random, requests
from typing import Optional

from pdf_utils import extract_any
from llm import summarize_text
from auth_rest import (
    sign_in, sign_up, sign_out,
    save_item, list_items, get_item, move_item, delete_item,
    create_folder, list_folders, delete_folder, list_child_folders,
    save_quiz_attempt, list_quiz_attempts
)

# ------- Page config --------
st.set_page_config(page_title="StudyBloom", page_icon="📚")
st.caption(f"Python: {sys.version.split()[0]} • Build: 2025-11-10-folders+ocr+scores")

# ====================== URL router helpers ======================
def _get_params() -> dict:
    try: return dict(st.query_params)
    except Exception: return st.experimental_get_query_params()

def _set_params(**kwargs):
    try:
        st.query_params.clear(); st.query_params.update(kwargs)
    except Exception:
        st.experimental_set_query_params(**kwargs)

def _clear_params(): _set_params()

# ====================== Render helpers ======================
def render_summary(data: dict):
    st.subheader("📝 Notes")
    st.markdown(f"**TL;DR**: {data.get('tl_dr', '')}")
    for sec in (data.get("sections") or []):
        st.markdown(f"### {sec.get('heading','Section')}")
        for b in sec.get("bullets", []) or []:
            st.markdown(f"- {b}")
    if data.get("key_terms"):
        st.markdown("## Key Terms")
        for kt in data["key_terms"]:
            st.markdown(f"- **{kt.get('term','')}** — {kt.get('definition','')}")
    if data.get("formulas"):
        st.markdown("## Formulas")
        for f in data["formulas"]:
            name = f.get("name",""); expr = (f.get("latex") or f.get("expression") or "").strip()
            meaning = f.get("meaning","")
            if any(s in expr for s in ["\\frac","\\sqrt","^","_","\\times","\\cdot","\\sum","\\int","\\left","\\right"]):
                if name or meaning: st.markdown(f"**{name}** — {meaning}")
                try: st.latex(expr)
                except Exception: st.code(expr)
            else:
                st.markdown(f"- **{name}**: `{expr}` — {meaning}")
    if data.get("examples"):
        st.markdown("## Worked Examples")
        for e in data["examples"]: st.markdown(f"- {e}")
    if data.get("common_pitfalls"):
        st.markdown("## Common Pitfalls")
        for p in data["common_pitfalls"]: st.markdown(f"- {p}")

def interactive_flashcards(flashcards, key_prefix="fc"):
    st.subheader("🧠 Flashcards")
    if not flashcards:
        st.caption("No flashcards found."); return
    st.session_state.setdefault(f"{key_prefix}_idx", 0)
    st.session_state.setdefault(f"{key_prefix}_revealed", False)
    st.session_state.setdefault(f"{key_prefix}_order", list(range(len(flashcards))))
    idx = st.session_state[f"{key_prefix}_idx"]; revealed = st.session_state[f"{key_prefix}_revealed"]
    order = st.session_state[f"{key_prefix}_order"]
    st.progress((idx+1)/len(order), text=f"Card {idx+1}/{len(order)}")
    card = flashcards[order[idx]]
    st.markdown("#### Front"); st.info(card.get("front",""))
    if revealed: st.markdown("#### Back"); st.success(card.get("back",""))
    c1,c2,c3,c4 = st.columns(4)
    if c1.button("◀️ Prev", disabled=(idx==0), key=f"{key_prefix}_prev"):
        st.session_state[f"{key_prefix}_idx"]=max(0,idx-1); st.session_state[f"{key_prefix}_revealed"]=False; st.rerun()
    if c2.button("🔁 Flip", key=f"{key_prefix}_flip"):
        st.session_state[f"{key_prefix}_revealed"]=not revealed; st.rerun()
    if c3.button("Next ▶️", disabled=(idx==len(order)-1), key=f"{key_prefix}_next"):
        st.session_state[f"{key_prefix}_idx"]=min(len(order)-1,idx+1); st.session_state[f"{key_prefix}_revealed"]=False; st.rerun()
    if c4.button("🔀 Shuffle", key=f"{key_prefix}_shuf"):
        new = list(range(len(flashcards))); random.shuffle(new)
        st.session_state[f"{key_prefix}_order"]=new; st.session_state[f"{key_prefix}_idx"]=0; st.session_state[f"{key_prefix}_revealed"]=False; st.rerun()

def interactive_quiz(questions, item_id: Optional[str]=None, key_prefix="quiz"):
    st.subheader("🧪 Quiz")
    if not questions:
        st.caption("No questions found."); return
    st.session_state.setdefault(f"{key_prefix}_i", 0)
    st.session_state.setdefault(f"{key_prefix}_reveal", False)
    st.session_state.setdefault(f"{key_prefix}_correct", 0)
    st.session_state.setdefault(f"{key_prefix}_history", [])
    i = st.session_state[f"{key_prefix}_i"]; reveal = st.session_state[f"{key_prefix}_reveal"]
    correct = st.session_state[f"{key_prefix}_correct"]; hist = st.session_state[f"{key_prefix}_history"]
    st.progress((i+1)/len(questions), text=f"Question {i+1}/{len(questions)}")
    q = questions[i]; st.markdown(f"### {q.get('question','')}")
    if not reveal:
        if st.button("👀 Show answer", key=f"{key_prefix}_show"): st.session_state[f"{key_prefix}_reveal"]=True; st.rerun()
    else:
        with st.expander("Model answer", expanded=True):
            st.markdown(q.get("model_answer",""))
            for pt in q.get("markscheme_points",[]) or []: st.markdown(f"- {pt}")
        cc1,cc2=st.columns(2)
        if cc1.button("✅ I got it", key=f"{key_prefix}_ok"):
            if len(hist)<=i: hist.append({"correct":True,"qid":i})
            else: hist[i]={"correct":True,"qid":i}
            st.session_state[f"{key_prefix}_correct"]=correct+1; st.session_state[f"{key_prefix}_reveal"]=False
            if i<len(questions)-1: st.session_state[f"{key_prefix}_i"]=i+1
            st.rerun()
        if cc2.button("❌ I need practice", key=f"{key_prefix}_bad"):
            if len(hist)<=i: hist.append({"correct":False,"qid":i})
            else: hist[i]={"correct":False,"qid":i}
            st.session_state[f"{key_prefix}_reveal"]=False
            if i<len(questions)-1: st.session_state[f"{key_prefix}_i"]=i+1
            st.rerun()
    # footer
    c3,c4,c5 = st.columns(3)
    c3.metric("Score", f"{correct}/{len(questions)}")
    if c4.button("⏭️ Skip", key=f"{key_prefix}_skip", disabled=(i==len(questions)-1)):
        st.session_state[f"{key_prefix}_reveal"]=False; st.session_state[f"{key_prefix}_i"]=min(len(questions)-1,i+1); st.rerun()
    if c5.button("🔁 Restart", key=f"{key_prefix}_restart"):
        st.session_state[f"{key_prefix}_i"]=0; st.session_state[f"{key_prefix}_reveal"]=False; st.session_state[f"{key_prefix}_correct"]=0; st.session_state[f"{key_prefix}_history"]=[]; st.rerun()

    # Save attempt when finished (i at last Q and user hides reveal)
    finished = (i == len(questions)-1) and (not reveal)
    if finished and item_id and "sb_user" in st.session_state:
        try:
            save_quiz_attempt(item_id, correct, len(questions), hist)
            st.success("Attempt saved ✅")
        except Exception as e:
            st.info("Could not save attempt (table missing?). See README.")
    # Recent attempts
    if item_id and "sb_user" in st.session_state:
        try:
            att = list_quiz_attempts(item_id, limit=5)
            if att:
                st.markdown("#### Recent Attempts")
                for a in att:
                    st.markdown(f"- {a['created_at'][:16].replace('T',' ')} — **{a['correct']}/{a['total']}**")
        except Exception:
            pass

# ====================== Auth UI ======================
st.sidebar.title("StudyBloom")
st.sidebar.caption("Log in to save & organize.")
if "sb_user" not in st.session_state:
    st.sidebar.subheader("Sign in")
    email = st.sidebar.text_input("Email", key="login_email")
    pwd = st.sidebar.text_input("Password", type="password", key="login_pwd")
    if st.sidebar.button("Sign in", use_container_width=True):
        try: sign_in(email, pwd); st.rerun()
        except requests.HTTPError as e: st.sidebar.error(getattr(e.response,"text",str(e)))
        except Exception as e: st.sidebar.error(str(e))
    st.sidebar.subheader("Create account")
    remail = st.sidebar.text_input("New email", key="reg_email")
    rpwd = st.sidebar.text_input("New password", type="password", key="reg_pwd")
    if st.sidebar.button("Sign up", use_container_width=True):
        try: sign_up(remail, rpwd); st.sidebar.success("Check email to confirm, then sign in.")
        except requests.HTTPError as e: st.sidebar.error(getattr(e.response,"text",str(e)))
        except Exception as e: st.sidebar.error(str(e))
else:
    st.sidebar.success(f"Signed in as {st.session_state['sb_user']['email']}")
    if st.sidebar.button("Sign out", use_container_width=True): sign_out(); st.rerun()

# ====================== Folder tree + sidebar click opens folder page ======================
def build_tree(rows):
    nodes = {r["id"]:{**r,"children":[]} for r in rows}
    roots=[]
    for r in rows:
        pid = r.get("parent_id")
        if pid and pid in nodes: nodes[pid]["children"].append(nodes[r["id"]])
        else: roots.append(nodes[r["id"]])
    return roots, nodes

all_folders=[]
if "sb_user" in st.session_state:
    st.sidebar.markdown("---"); st.sidebar.subheader("📂 Folders")
    try:
        all_folders = list_folders()
        tree, _ = build_tree(all_folders)

        def render_tree(nodes, level=0):
            for n in nodes:
                label = (" "*level) + f"• {n['name']}"
                if st.sidebar.button(label, key=f"folderbtn_{n['id']}"):
                    _set_params(folder=n["id"])
                    st.rerun()
                if n["children"]: render_tree(n["children"], level+1)

        render_tree(tree)
        with st.sidebar.expander("New Folder"):
            new_name = st.text_input("Folder name", key="new_folder_name")
            parent_options = {"(no parent)": None}
            for f in all_folders: parent_options[f["name"]] = f["id"]
            psel = st.selectbox("Parent", list(parent_options.keys()))
            pid = parent_options[psel]
            if st.button("Create", use_container_width=True):
                if not (new_name or "").strip(): st.warning("Enter a name.")
                else:
                    try:
                        created = create_folder(new_name.strip(), pid); _set_params(folder=created["id"]); st.rerun()
                    except Exception as e: st.error(f"Create failed: {e}")
    except Exception:
        st.sidebar.info("Create your first folder.")

# ====================== ROUTES ======================
params = _get_params()

# ---- Folder page ----
if "folder" in params and "sb_user" in st.session_state:
    folder_id = params.get("folder")
    if isinstance(folder_id, list): folder_id = folder_id[0]
    # Header + back
    this = next((f for f in all_folders if f["id"]==folder_id), None)
    st.title(this["name"] if this else "Folder")
    if st.button("← Back to Home"): _clear_params(); st.rerun()

    # Subfolders
    try:
        subs = list_child_folders(folder_id)  # children
        if subs:
            st.subheader("Subfolders")
            for s in subs:
                if st.button(f"📁 {s['name']}", key=f"open_{s['id']}"):
                    _set_params(folder=s["id"]); st.rerun()
    except Exception: pass

    # Items in this folder
    try:
        items = list_items(folder_id, limit=200)
        st.subheader("Items")
        if not items: st.caption("No items yet.")
        for it in items:
            cols = st.columns([5,1,1,1])
            cols[0].markdown(f"**[{it['kind']}]** {it['title']} — {it['created_at'][:16].replace('T',' ')}")
            if cols[1].button("Open", key=f"open_item_{it['id']}"): _set_params(item=it["id"]); st.rerun()
            if cols[2].button("Move", key=f"move_{it['id']}"):
                st.session_state["move_item_id"]=it["id"]
            if cols[3].button("Delete", key=f"del_{it['id']}"):
                try: delete_item(it["id"]); st.success("Deleted."); st.rerun()
                except Exception as e: st.error(f"Delete failed: {e}")
        # Inline mover
        if st.session_state.get("move_item_id"):
            mv_id = st.session_state["move_item_id"]
            st.info("Choose destination folder:")
            dest_map = {"(no folder)": None}
            for f in all_folders: dest_map[f["name"]] = f["id"]
            dest = st.selectbox("Destination", list(dest_map.keys()))
            if st.button("Confirm move"):
                try: move_item(mv_id, dest_map[dest]); st.success("Moved."); st.session_state.pop("move_item_id",None); st.rerun()
                except Exception as e: st.error(f"Move failed: {e}")
    except Exception as e:
        st.error(f"Load failed: {e}")
    st.stop()

# ---- Item full-page route ----
if "item" in params and "sb_user" in st.session_state:
    item_id = params.get("item")
    if isinstance(item_id, list): item_id = item_id[0]
    try:
        full = get_item(item_id); kind = full.get("kind"); title = full.get("title") or kind.title()
        st.title(title)
        if st.button("← Back"): _clear_params(); st.rerun()
        data = full.get("data") or {}
        if kind == "summary":
            render_summary(data or full)
        elif kind == "flashcards":
            interactive_flashcards(data.get("flashcards") or [], key_prefix=f"fc_{item_id}")
        elif kind == "quiz":
            interactive_quiz(data.get("questions") or [], item_id=item_id, key_prefix=f"quiz_{item_id}")
        else:
            st.write(data or full)
    except Exception as e:
        st.error(f"Could not load item: {e}")
        if st.button("← Back"): _clear_params(); st.rerun()
    st.stop()

# ====================== Main Tabs (home) ======================
tabs = st.tabs(["Exam Planner", "Quick Study", "Manage Items"])

# ---- Exam Planner (unchanged except multi-file + subject-aware) ----
with tabs[0]:
    st.title("🗂️ Exam Planner")
    if "sb_user" not in st.session_state:
        st.info("Log in (left) to use Exam Planner.")
    else:
        # You already had subject/exam/topic selectors — keep your working version here
        st.write("Go to a folder in the sidebar to upload into that Topic, or use Quick Study.")
        st.info("Tip: Click a folder on the left to open a dedicated page showing its subfolders and items.")

# ---- Quick Study (multi-file upload + OCR + LaTeX bias) ----
with tabs[1]:
    st.title("⚡ Quick Study")
    if "sb_user" not in st.session_state:
        st.info("Log in to save your study materials.")
    else:
        # Destination
        dest_id=None
        options = ["(no folder)"] + [f["name"] for f in all_folders]
        pick = st.selectbox("Save to folder", options, index=0)
        if pick != "(no folder)": dest_id = next(f["id"] for f in all_folders if f["name"]==pick)

        audience_label = st.selectbox("Audience", ["University", "A-Level / IB", "GCSE", "HKDSE"], index=0)
        audience = "university" if audience_label=="University" else "high school"
        detail = st.slider("Detail level", 1, 5, 3)
        subject_hint = st.text_input("Subject (e.g., Mathematics) for subject-specific questions", value="General")

        files = st.file_uploader("Upload files (PDF, PPTX, JPG, PNG, TXT)", type=["pdf","pptx","jpg","jpeg","png","txt"], accept_multiple_files=True)
        if files and st.button("Generate & Save", type="primary"):
            with st.spinner("Extracting text from all files…"):
                try:
                    text = extract_any(files)
                except Exception as e:
                    st.error(f"Extraction failed: {e}"); st.stop()
            if not text.strip(): st.error("No text found."); st.stop()

            with st.spinner("Summarizing with AI…"):
                try:
                    data = summarize_text(text, audience=audience, detail=detail, subject=subject_hint)
                except TypeError:
                    try: data = summarize_text(text, audience=audience, detail=detail)
                    except TypeError: data = summarize_text(text, audience=audience)
                except Exception as e:
                    st.error(f"Summarization failed: {e}"); st.stop()

            # Save three items
            summary_id=flash_id=quiz_id=None
            try:
                title = data.get("title") or "Untitled"
                summary_id = save_item("summary", title, data, dest_id).get("id")
                if data.get("flashcards"):
                    flash_id = save_item("flashcards", f"{title} • Flashcards", {"flashcards": data["flashcards"]}, dest_id).get("id")
                if data.get("exam_questions"):
                    quiz_id = save_item("quiz", f"{title} • Quiz", {"questions": data["exam_questions"]}, dest_id).get("id")
                st.success("Saved ✅")
            except Exception as e:
                st.error(f"Save failed: {e}")

            # Open buttons
            st.markdown("### Open Your Materials")
            c1,c2,c3 = st.columns(3)
            if summary_id and c1.button("Open Notes Page", type="primary"): _set_params(item=summary_id); st.rerun()
            if flash_id and c2.button("Open Flashcards Page"): _set_params(item=flash_id); st.rerun()
            if quiz_id and c3.button("Open Quiz Page"): _set_params(item=quiz_id); st.rerun()

# ---- Manage Items (unchanged, now with Open → new page) ----
with tabs[2]:
    st.title("🧰 Manage Items")
    if "sb_user" not in st.session_state:
        st.info("Log in to manage.")
    else:
        all_opt = ["(all folders)"] + [f["name"] for f in all_folders]
        folder_filter = st.selectbox("Show items in", all_opt, index=0)
        filter_id = None if folder_filter=="(all folders)" else next(f["id"] for f in all_folders if f["name"]==folder_filter)
        try:
            items = list_items(filter_id, limit=200)
            if not items: st.caption("No items yet.")
            for it in items:
                cols = st.columns([6,1,1])
                cols[0].markdown(f"**[{it['kind']}]** {it['title']} — {it['created_at'][:16].replace('T',' ')}")
                if cols[1].button("Open", key=f"open_{it['id']}"): _set_params(item=it["id"]); st.rerun()
                if cols[2].button("Delete", key=f"del_{it['id']}"):
                    try: delete_item(it["id"]); st.success("Deleted."); st.rerun()
                    except Exception as e: st.error(f"Delete failed: {e}")
        except Exception as e:
            st.error(f"Load failed: {e}")


