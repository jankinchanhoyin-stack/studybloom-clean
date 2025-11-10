# app.py
import streamlit as st
import sys, random, requests
from typing import Optional

from pdf_utils import extract_any
from llm import summarize_text, grade_free_answer, generate_quiz_from_notes
from auth_rest import (
    sign_in, sign_up, sign_out,
    save_item, list_items, get_item, move_item, delete_item,
    create_folder, list_folders, delete_folder, list_child_folders,
    save_quiz_attempt, list_quiz_attempts
)

# ------- Page config --------
st.set_page_config(page_title="StudyBloom", page_icon="📚")
st.caption(f"Python: {sys.version.split()[0]} • Build: 2025-11-10-flip+grade+folders")

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

# ---- Flashcards with ✅/❌ and re-ask scheduling ----
def interactive_flashcards(flashcards, key_prefix="fc"):
    st.subheader("🧠 Flashcards")
    if not flashcards:
        st.caption("No flashcards found."); return

    # State: order is a queue of original indices; wrong_counts tracks how many times a card has been requeued
    st.session_state.setdefault(f"{key_prefix}_idx", 0)
    st.session_state.setdefault(f"{key_prefix}_revealed", False)
    st.session_state.setdefault(f"{key_prefix}_order", list(range(len(flashcards))))
    st.session_state.setdefault(f"{key_prefix}_wrong_counts", {})  # {orig_index: count}

    order = st.session_state[f"{key_prefix}_order"]
    idx = st.session_state[f"{key_prefix}_idx"]
    revealed = st.session_state[f"{key_prefix}_revealed"]
    wrong_counts = st.session_state[f"{key_prefix}_wrong_counts"]

    # Guard
    if not order:
        st.success("Deck complete — nice work!")
        if st.button("🔁 Restart deck"):
            st.session_state[f"{key_prefix}_order"] = list(range(len(flashcards)))
            st.session_state[f"{key_prefix}_idx"] = 0
            st.session_state[f"{key_prefix}_revealed"] = False
            st.session_state[f"{key_prefix}_wrong_counts"] = {}
            st.rerun()
        return

    # Clamp idx
    idx = max(0, min(idx, len(order)-1))
    st.session_state[f"{key_prefix}_idx"] = idx

    st.progress((idx+1)/len(order), text=f"Card {idx+1}/{len(order)}")

    orig_i = order[idx]
    card = flashcards[orig_i]

    st.markdown("#### Front")
    st.info(card.get("front",""))
    if revealed:
        st.markdown("#### Back")
        st.success(card.get("back",""))

    c1,c2,c3,c4 = st.columns(4)
    if c1.button("◀️ Prev", disabled=(idx==0), key=f"{key_prefix}_prev"):
        st.session_state[f"{key_prefix}_idx"]=idx-1
        st.session_state[f"{key_prefix}_revealed"]=False
        st.rerun()
    if c2.button("🔁 Flip", key=f"{key_prefix}_flip"):
        st.session_state[f"{key_prefix}_revealed"]=not revealed
        st.rerun()

    # ✅ / ❌ with re-ask: if ❌, requeue this original index ~3 cards later (cap repeats at 2)
    if c3.button("✅ I knew it", key=f"{key_prefix}_ok"):
        # Remove future duplicates of this orig index (clean up queue)
        st.session_state[f"{key_prefix}_order"] = [o for k, o in enumerate(order) if not (k>idx and o==orig_i)]
        # Advance
        if idx >= len(st.session_state[f"{key_prefix}_order"]) - 1:
            # at end → remove current and reset pointer
            st.session_state[f"{key_prefix}_order"].pop(idx)
            st.session_state[f"{key_prefix}_idx"] = 0
        else:
            st.session_state[f"{key_prefix}_idx"]=idx+1
            st.session_state[f"{key_prefix}_order"].pop(idx)
        st.session_state[f"{key_prefix}_revealed"]=False
        st.rerun()

    if c4.button("❌ I need to see it again", key=f"{key_prefix}_bad"):
        count = wrong_counts.get(orig_i, 0)
        if count < 2:
            insert_at = min(len(order), idx + 4)  # re-ask after ~3 other cards
            st.session_state[f"{key_prefix}_order"].insert(insert_at, orig_i)
            wrong_counts[orig_i] = count + 1
        # Move on from current position
        st.session_state[f"{key_prefix}_wrong_counts"] = wrong_counts
        st.session_state[f"{key_prefix}_revealed"]=False
        if idx < len(st.session_state[f"{key_prefix}_order"]) - 1:
            st.session_state[f"{key_prefix}_idx"]=idx+1
        st.rerun()

    # Shuffle / Restart row
    s1,s2 = st.columns(2)
    if s1.button("🔀 Shuffle deck", key=f"{key_prefix}_shuf"):
        new = list(range(len(flashcards))); random.shuffle(new)
        st.session_state[f"{key_prefix}_order"]=new
        st.session_state[f"{key_prefix}_idx"]=0
        st.session_state[f"{key_prefix}_revealed"]=False
        st.session_state[f"{key_prefix}_wrong_counts"]={}
        st.rerun()
    if s2.button("🔁 Restart deck", key=f"{key_prefix}_restart"):
        st.session_state[f"{key_prefix}_order"]=list(range(len(flashcards)))
        st.session_state[f"{key_prefix}_idx"]=0
        st.session_state[f"{key_prefix}_revealed"]=False
        st.session_state[f"{key_prefix}_wrong_counts"]={}
        st.rerun()

# ---- Quiz with free-text grading + save score + re-generate ----
def interactive_quiz(questions, item_id: Optional[str]=None, key_prefix="quiz", subject_hint: str="General"):
    st.subheader("🧪 Quiz")
    if not questions:
        st.caption("No questions found."); return

    # State
    st.session_state.setdefault(f"{key_prefix}_i", 0)
    st.session_state.setdefault(f"{key_prefix}_score", 0)
    st.session_state.setdefault(f"{key_prefix}_graded", False)  # whether current Q has been graded
    st.session_state.setdefault(f"{key_prefix}_feedback", "")
    st.session_state.setdefault(f"{key_prefix}_mark_last", (0, 0))  # (score,max)
    st.session_state.setdefault(f"{key_prefix}_history", [])  # per question records

    i = st.session_state[f"{key_prefix}_i"]
    i = max(0, min(i, len(questions)-1))
    st.session_state[f"{key_prefix}_i"] = i

    q = questions[i]
    st.progress((i+1)/len(questions), text=f"Question {i+1}/{len(questions)}")
    st.markdown(f"### {q.get('question','')}")

    # Answer box
    ans = st.text_area("Your answer", key=f"{key_prefix}_ans_{i}", height=120, placeholder="Type your working/answer here…")

    # Grade
    colg1, colg2, colg3 = st.columns([1,1,1])
    if colg1.button("Submit answer", key=f"{key_prefix}_submit"):
        try:
            result = grade_free_answer(
                question=q.get("question",""),
                model_answer=q.get("model_answer",""),
                markscheme=q.get("markscheme_points", []) or [],
                user_answer=ans or "",
                subject=subject_hint or "General",
            )
            st.session_state[f"{key_prefix}_graded"] = True
            st.session_state[f"{key_prefix}_feedback"] = result.get("feedback","")
            last = (result.get("score",0), result.get("max_points",10))
            st.session_state[f"{key_prefix}_mark_last"] = last

            # Save per-question history
            hist = st.session_state[f"{key_prefix}_history"]
            if len(hist) <= i:
                hist.append({"score": last[0], "max": last[1]})
            else:
                hist[i] = {"score": last[0], "max": last[1]}

        except Exception as e:
            st.error(f"Grading failed: {e}")

    if st.session_state[f"{key_prefix}_graded"]:
        sc, mx = st.session_state[f"{key_prefix}_mark_last"]
        st.success(f"Score for this question: {sc} / {mx}")
        with st.expander("Model answer & mark scheme", expanded=False):
            st.markdown(q.get("model_answer",""))
            for pt in q.get("markscheme_points", []) or []:
                st.markdown(f"- {pt}")
        if st.session_state[f"{key_prefix}_feedback"]:
            st.info(st.session_state[f"{key_prefix}_feedback"])

    # Nav / totals
    c1,c2,c3,c4 = st.columns(4)
    if c1.button("◀️ Prev", disabled=(i==0), key=f"{key_prefix}_prev"):
        st.session_state[f"{key_prefix}_i"]=i-1
        st.session_state[f"{key_prefix}_graded"]=False
        st.session_state[f"{key_prefix}_feedback"]=""
        st.rerun()
    if c2.button("Next ▶️", disabled=(i==len(questions)-1), key=f"{key_prefix}_next"):
        st.session_state[f"{key_prefix}_i"]=i+1
        st.session_state[f"{key_prefix}_graded"]=False
        st.session_state[f"{key_prefix}_feedback"]=""
        st.rerun()

    # Compute running total
    total_sc = sum(h.get("score",0) for h in st.session_state[f"{key_prefix}_history"])
    total_mx = sum(h.get("max",0) for h in st.session_state[f"{key_prefix}_history"])
    st.metric("Total so far", f"{total_sc} / {total_mx or (len(questions)*10)}")

    # Finish/save attempt
    if c3.button("✅ Finish & Save", key=f"{key_prefix}_finish"):
        if item_id and "sb_user" in st.session_state:
            try:
                # Convert to simple correct/total using >=70% per question as 'correct'
                correct = sum(1 for h in st.session_state[f"{key_prefix}_history"] if h.get("max",0) and h.get("score",0) >= 0.7*h["max"])
                total = len(questions)
                save_quiz_attempt(item_id, correct, total, st.session_state[f"{key_prefix}_history"])
                st.success(f"Attempt saved: {correct}/{total}")
            except Exception:
                st.info("Attempt not saved (check quiz_attempts table).")

    # Generate another quiz from notes in same folder
    if c4.button("🎲 Generate another quiz", key=f"{key_prefix}_regen") and item_id:
        try:
            # find sibling summary in same folder
            quiz_item = get_item(item_id)
            folder_id = quiz_item.get("folder_id")
            subject = subject_hint or "General"
            if folder_id:
                siblings = list_items(folder_id, limit=200)
                summary = next((s for s in siblings if s.get("kind")=="summary"), None)
                if summary and summary.get("data"):
                    new_qs = generate_quiz_from_notes(summary["data"], subject=subject, audience="high school", num_questions=8)
                    if new_qs:
                        created = save_item("quiz", f"{summary['title']} • Quiz (new)", {"questions": new_qs}, folder_id)
                        st.success("New quiz created.")
                        _set_params(item=created.get("id")); st.rerun()
                    else:
                        st.warning("Could not generate a new quiz from notes.")
                else:
                    st.info("No summary found in this folder to generate from.")
            else:
                st.info("Folder not found for this quiz.")
        except Exception as e:
            st.error(f"Re-generate failed: {e}")

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

# ====================== Sidebar shows ONLY subject (root) folders ======================
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
    st.sidebar.markdown("---"); st.sidebar.subheader("📂 Subjects")
    try:
        all_folders = list_folders()
        roots, _ = build_tree(all_folders)     # only show roots here
        for n in roots:
            if st.sidebar.button(f"• {n['name']}", key=f"subject_{n['id']}"):
                _set_params(folder=n["id"]); st.rerun()
        with st.sidebar.expander("New Subject"):
            new_name = st.text_input("Subject name", key="new_root_name", placeholder="e.g., A-Level Mathematics")
            if st.button("Create", use_container_width=True, key="create_root"):
                if not (new_name or "").strip(): st.warning("Enter a name.")
                else:
                    try:
                        created = create_folder(new_name.strip(), None); _set_params(folder=created["id"]); st.rerun()
                    except Exception as e: st.error(f"Create failed: {e}")
    except Exception:
        st.sidebar.info("Create your first subject.")

# ====================== ROUTES ======================
params = _get_params()

# ---- Folder page ----
if "folder" in params and "sb_user" in st.session_state:
    folder_id = params.get("folder")
    if isinstance(folder_id, list): folder_id = folder_id[0]
    this = next((f for f in all_folders if f["id"]==folder_id), None)
    st.title(this["name"] if this else "Folder")
    if st.button("← Back to Home"): _clear_params(); st.rerun()

    # Subfolders
    try:
        subs = list_child_folders(folder_id)
        if subs:
            st.subheader("Subfolders")
            for s in subs:
                if st.button(f"📁 {s['name']}", key=f"open_{s['id']}"):
                    _set_params(folder=s["id"]); st.rerun()
    except Exception: pass

    # Items in this folder (with emojis)
    emoji = {"summary":"📄","flashcards":"🧠","quiz":"🧪"}
    try:
        items = list_items(folder_id, limit=200)
        st.subheader("Items")
        if not items: st.caption("No items yet.")
        for it in items:
            icon = emoji.get(it["kind"], "📄")
            cols = st.columns([6,1,1])
            cols[0].markdown(f"{icon} **{it['title']}** — {it['created_at'][:16].replace('T',' ')}")
            if cols[1].button("Open", key=f"open_item_{it['id']}"): _set_params(item=it["id"]); st.rerun()
            if cols[2].button("Delete", key=f"del_{it['id']}"):
                try: delete_item(it["id"]); st.success("Deleted."); st.rerun()
                except Exception as e: st.error(f"Delete failed: {e}")
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
        subject_hint = st.text_input("Subject (affects grading & new quizzes)", value="General", key=f"subj_{item_id}")
        if kind == "summary":
            render_summary(data or full)
        elif kind == "flashcards":
            interactive_flashcards(data.get("flashcards") or [], key_prefix=f"fc_{item_id}")
        elif kind == "quiz":
            interactive_quiz(data.get("questions") or [], item_id=item_id, key_prefix=f"quiz_{item_id}", subject_hint=subject_hint)
        else:
            st.write(data or full)
    except Exception as e:
        st.error(f"Could not load item: {e}")
        if st.button("← Back"): _clear_params(); st.rerun()
    st.stop()

# ====================== Home tabs (kept light; you can keep your Exam Planner selectors here) ======================
tabs = st.tabs(["Quick Study", "Manage Items"])

# ---- Quick Study (multi-file) ----
with tabs[0]:
    st.title("⚡ Quick Study")
    if "sb_user" not in st.session_state:
        st.info("Log in to save your study materials.")
    else:
        # Choose destination folder (optional)
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
            from pdf_utils import extract_any
            with st.spinner("Extracting text…"):
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
            emoji = {"summary":"📄","flashcards":"🧠","quiz":"🧪"}
            summary_id=flash_id=quiz_id=None
            try:
                title = data.get("title") or "Untitled"
                summary_id = save_item("summary", f"{emoji['summary']} {title}", data, dest_id).get("id")
                if data.get("flashcards"):
                    flash_id = save_item("flashcards", f"{emoji['flashcards']} {title} • Flashcards", {"flashcards": data["flashcards"]}, dest_id).get("id")
                if data.get("exam_questions"):
                    quiz_id = save_item("quiz", f"{emoji['quiz']} {title} • Quiz", {"questions": data["exam_questions"]}, dest_id).get("id")
                st.success("Saved ✅")
            except Exception as e:
                st.error(f"Save failed: {e}")

            # Open buttons
            st.markdown("### Open Your Materials")
            c1,c2,c3 = st.columns(3)
            if summary_id and c1.button("Open Notes Page", type="primary"): _set_params(item=summary_id); st.rerun()
            if flash_id and c2.button("Open Flashcards Page"): _set_params(item=flash_id); st.rerun()
            if quiz_id and c3.button("Open Quiz Page"): _set_params(item=quiz_id); st.rerun()

# ---- Manage Items (emoji titles & open) ----
with tabs[1]:
    st.title("🧰 Manage Items")
    if "sb_user" not in st.session_state:
        st.info("Log in to manage.")
    else:
        emoji = {"summary":"📄","flashcards":"🧠","quiz":"🧪"}
        all_opt = ["(all folders)"] + [f["name"] for f in all_folders]
        folder_filter = st.selectbox("Show items in", all_opt, index=0)
        filter_id = None if folder_filter=="(all folders)" else next(f["id"] for f in all_folders if f["name"]==folder_filter)
        try:
            items = list_items(filter_id, limit=200)
            if not items: st.caption("No items yet.")
            for it in items:
                icon = emoji.get(it["kind"], "📄")
                cols = st.columns([6,1,1])
                cols[0].markdown(f"{icon} **{it['title']}** — {it['created_at'][:16].replace('T',' ')}")
                if cols[1].button("Open", key=f"open_{it['id']}"): _set_params(item=it["id"]); st.rerun()
                if cols[2].button("Delete", key=f"del_{it['id']}"):
                    try: delete_item(it["id"]); st.success("Deleted."); st.rerun()
                    except Exception as e: st.error(f"Delete failed: {e}")
        except Exception as e:
            st.error(f"Load failed: {e}")

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


