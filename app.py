# app.py
import streamlit as st

st.set_page_config(page_title="StudyBloom • Summarize", page_icon="📚")
st.title("📚 StudyBloom — PDF Summarizer")

# --- health check: show if the key is present ---
has_key = False
try:
    has_key = bool(st.secrets.get("OPENAI_API_KEY"))
except Exception:
    has_key = False

st.caption("Health check:")
st.write("- Streamlit secrets has OPENAI_API_KEY:", "✅" if has_key else "❌")
if not has_key:
    st.warning("Add OPENAI_API_KEY in Manage app → Edit secrets, then click Rerun.")
    st.stop()

# --- rest of the app ---
from pdf_utils import extract_pdf_text
from llm import summarize_text

st.caption("Upload a lecture PDF → extract text → AI summary → download Markdown.")

audience = st.selectbox("Audience style", ["university", "A-Level / IB", "GCSE", "HKDSE"], index=0)
uploaded = st.file_uploader("Upload PDF", type=["pdf"])

if uploaded:
    with st.spinner("Extracting text…"):
        pdf_bytes = uploaded.read()
        try:
            text = extract_pdf_text(pdf_bytes, max_pages=30)
        except Exception as e:
            st.error(f"PDF extraction failed: {e}")
            st.stop()

    st.write(f"**Extracted characters:** {len(text):,}")
    st.code(text[:1500] + ("..." if len(text) > 1500 else ""))

    if st.button("Generate Summary"):
        with st.spinner("Summarizing with AI…"):
            a = "university" if audience == "university" else "high school"
            try:
                data = summarize_text(text, audience=a)
            except Exception as e:
                st.error(f"Summarization failed: {e}")
                st.stop()

        st.subheader(data.get("title","Summary"))
        st.markdown(f"**TL;DR**: {data.get('tl_dr','')}")

        for sec in data.get("sections", []):
            st.markdown(f"### {sec.get('heading','Section')}")
            for b in sec.get("bullets", []):
                st.markdown(f"- {b}")

        md_lines = [f"# {data.get('title','Summary')}", f"**TL;DR**: {data.get('tl_dr','')}", ""]
        for sec in data.get("sections", []):
            md_lines.append(f"## {sec.get('heading','Section')}")
            for b in sec.get("bullets", []):
                md_lines.append(f"- {b}")
            md_lines.append("")
        md_content = "\n".join(md_lines)
        st.download_button("⬇️ Download Markdown", md_content, file_name="summary.md")

