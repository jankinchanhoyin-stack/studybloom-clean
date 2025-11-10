import streamlit as st
from pdf_utils import extract_pdf_text
from llm import summarize_text
from auth_rest import sign_in, sign_up, sign_out, save_summary, list_summaries, get_summary

st.set_page_config(page_title="StudyBloom • Summarizer", page_icon="📚")

# ---------------- Sidebar: Auth ----------------
st.sidebar.title("StudyBloom")
st.sidebar.caption("Log in to save notes & track progress.")

if "sb_user" not in st.session_state:
    st.sidebar.subheader("Sign in")
    login_email = st.sidebar.text_input("Email", key="login_email")
    login_pwd = st.sidebar.text_input("Password", type="password", key="login_pwd")
    if st.sidebar.button("Sign in", use_container_width=True):
        try:
            sign_in(login_email, login_pwd)
            st.rerun()
        except Exception as e:
            st.sidebar.error(f"Sign-in failed: {e}")

    st.sidebar.subheader("Create account")
    reg_email = st.sidebar.text_input("New email", key="reg_email")
    reg_pwd = st.sidebar.text_input("New password", type="password", key="reg_pwd")
    if st.sidebar.button("Sign up", use_container_width=True):
        try:
            sign_up(reg_email, reg_pwd)
            st.sidebar.success("Account created. Check your inbox if confirmation is required, then sign in above.")
        except Exception as e:
            st.sidebar.error(f"Sign-up failed: {e}")
else:
    st.sidebar.success(f"Signed in as {st.session_state['sb_user']['email']}")
    if st.sidebar.button("Sign out", use_container_width=True):
        sign_out()
        st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.subheader("My Summaries")
    try:
        rows = list_summaries(limit=25)
        for row in rows:
            label = f"• {row['title']}  ({row['created_at'][:10]})"
            if st.sidebar.button(label, key=row["id"]):
                try:
                    doc = get_summary(row["id"])
                    st.session_state["loaded_summary"] = doc
                    st.rerun()
                except Exception as e:
                    st.sidebar.error(f"Load failed: {e}")
    except Exception:
        st.sidebar.info("No saved summaries yet.")

# ---------------- Main UI ----------------
st.title("📚 StudyBloom — PDF Summarizer")
st.caption("Upload a lecture PDF → get focused study notes, key terms, pitfalls, exam-style Qs, and flashcards.")

audience_label = st.selectbox("Audience style", ["University", "A-Level / IB", "GCSE", "HKDSE"], index=0)
audience = "university" if audience_label == "University" else "high school"
detail = st.slider("Detail level (more = longer output)", 1, 5, 3)

uploaded = st.file_uploader("Upload PDF", type=["pdf"])

# If a summary was loaded from sidebar, render it
if "loaded_summary" in st.session_state and not uploaded:
    data = st.session_state.pop("loaded_summary")
    st.subheader(data.get("title", "Summary"))
    st.markdown(f"**TL;DR**: {data.get('tl_dr', '')}")
    for sec in data.get("sections", []):
        st.markdown(f"### {sec.get('heading','Section')}")
        for b in sec.get("bullets", []):
            st.markdown(f"- {b}")
    if data.get("key_terms"):
        st.markdown("## Key Terms")
        for kt in data["key_terms"]:
            st.markdown(f"- **{kt.get('term','')}** — {kt.get('definition','')}")
    if data.get("formulas"):
        st.markdown("## Formulas")
        for f in data["formulas"]:
            st.markdown(f"- **{f.get('name','')}**: `{f.get('expression','')}` — {f.get('meaning','')}")
    if data.get("examples"):
        st.markdown("## Worked Examples")
        for e in data["examples"]:
            st.markdown(f"- {e}")
    if data.get("common_pitfalls"):
        st.markdown("## Common Pitfalls")
        for p in data["common_pitfalls"]:
            st.markdown(f"- {p}")
    if data.get("exam_questions"):
        st.markdown("## Exam-Style Questions")
        for q in data["exam_questions"]:
            st.markdown(f"**Q:** {q.get('question','')}")
            st.markdown(f"**Model answer:** {q.get('model_answer','')}")
            for pt in q.get("markscheme_points", []):
                st.markdown(f"- {pt}")
            st.markdown("---")
    if data.get("flashcards"):
        st.markdown("## Flashcards")
        for c in data["flashcards"]:
            st.markdown(f"- **Front:** {c.get('front','')}\n\n  **Back:** {c.get('back','')}")

if uploaded:
    with st.spinner("Extracting text…"):
        pdf_bytes = uploaded.read()
        try:
            text = extract_pdf_text(pdf_bytes, max_pages=30)
        except Exception as e:
            st.error(f"PDF extraction failed: {e}")
            st.stop()

    if not text.strip():
        st.error("Couldn’t extract text from this PDF. Try another file or a text-based PDF.")
        st.stop()

    if st.button("Generate Summary"):
        with st.spinner("Summarizing with AI…"):
            try:
                data = summarize_text(text, audience=audience, detail=detail)
            except TypeError:
                data = summarize_text(text, audience=audience)
            except Exception as e:
                st.error(f"Summarization failed: {e}")
                st.stop()

        st.subheader(data.get("title", "Summary"))
        st.markdown(f"**TL;DR**: {data.get('tl_dr', '')}")

        for sec in data.get("sections", []):
            st.markdown(f"### {sec.get('heading', 'Section')}")
            for b in sec.get("bullets", []):
                st.markdown(f"- {b}")

        if data.get("key_terms"):
            st.markdown("## Key Terms")
            for kt in data["key_terms"]:
                st.markdown(f"- **{kt.get('term','')}** — {kt.get('definition','')}")

        if data.get("formulas"):
            st.markdown("## Formulas")
            for f in data["formulas"]:
                st.markdown(f"- **{f.get('name','')}**: `{f.get('expression','')}` — {f.get('meaning','')}")

        if data.get("examples"):
            st.markdown("## Worked Examples")
            for e in data["examples"]:
                st.markdown(f"- {e}")

        if data.get("common_pitfalls"):
            st.markdown("## Common Pitfalls")
            for p in data["common_pitfalls"]:
                st.markdown(f"- {p}")

        if data.get("exam_questions"):
            st.markdown("## Exam-Style Questions")
            for q in data["exam_questions"]:
                st.markdown(f"**Q:** {q.get('question','')}")
                st.markdown(f"**Model answer:** {q.get('model_answer','')}")
                for pt in q.get("markscheme_points", []):
                    st.markdown(f"- {pt}")
                st.markdown("---")

        if data.get("flashcards"):
            st.markdown("## Flashcards")
            for c in data["flashcards"]:
                st.markdown(f"- **Front:** {c.get('front','')}\n\n  **Back:** {c.get('back','')}")

        # Save to account (if logged in)
        if "sb_user" in st.session_state:
            if st.checkbox("Save this summary to my account", value=True):
                try:
                    title = data.get("title") or "Untitled"
                    tl_dr = data.get("tl_dr") or ""
                    save_summary(title, tl_dr, data)
                    st.success("Saved to your account ✅")
                except Exception as e:
                    st.error(f"Save failed: {e}")
        else:
            st.info("Log in (left sidebar) to save this to your account.")
