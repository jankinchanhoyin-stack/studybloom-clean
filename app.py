import streamlit as st
from pdf_utils import extract_pdf_text
from llm import summarize_text

st.set_page_config(page_title="StudyBloom • Summarizer", page_icon="📚")
st.title("📚 StudyBloom — PDF Summarizer")

st.caption("Upload a lecture PDF → get focused study notes, flashcards, and exam-style questions.")

audience_label = st.selectbox(
    "Audience style",
    ["University", "A-Level / IB", "GCSE", "HKDSE"],
    index=0,
)
audience = "university" if audience_label == "University" else "high school"
detail = st.slider("Detail level (more = longer output)", 1, 5, 3)

uploaded = st.file_uploader("Upload PDF", type=["pdf"])

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
                # Call new signature first
                data = summarize_text(text, audience=audience, detail=detail)
            except TypeError:
                # Fallback if an older llm.py without 'detail' is live
                data = summarize_text(text, audience=audience)
            except Exception as e:
                st.error(f"Summarization failed: {e}")
                st.stop()

        st.subheader(data.get("title", "Summary"))
        st.markdown(f"**TL;DR**: {data.get('tl_dr', '')}")

        # Sections
        for sec in data.get("sections", []):
            st.markdown(f"### {sec.get('heading', 'Section')}")
            for b in sec.get("bullets", []):
                st.markdown(f"- {b}")

        # Key Terms
        kts = data.get("key_terms", [])
        if kts:
            st.markdown("## Key Terms")
            for kt in kts:
                st.markdown(f"- **{kt.get('term','')}** — {kt.get('definition','')}")

        # Formulas
        forms = data.get("formulas", [])
        if forms:
            st.markdown("## Formulas")
            for f in forms:
                st.markdown(f"- **{f.get('name','')}**: `{f.get('expression','')}` — {f.get('meaning','')}")
        
        # Worked Examples
        exs = data.get("examples", [])
        if exs:
            st.markdown("## Worked Examples")
            for e in exs:
                st.markdown(f"- {e}")

        # Common Pitfalls
        pits = data.get("common_pitfalls", [])
        if pits:
            st.markdown("## Common Pitfalls")
            for p in pits:
                st.markdown(f"- {p}")

        # Exam Questions
        qs = data.get("exam_questions", [])
        if qs:
            st.markdown("## Exam-Style Questions")
            for q in qs:
                st.markdown(f"**Q:** {q.get('question','')}")
                st.markdown(f"**Model answer:** {q.get('model_answer','')}")
                for pt in q.get("markscheme_points", []):
                    st.markdown(f"- {pt}")
                st.markdown("---")

        # Flashcards
        fcs = data.get("flashcards", [])
        if fcs:
            st.markdown("## Flashcards")
            for c in fcs:
                st.markdown(f"- **Front:** {c.get('front','')}\n\n  **Back:** {c.get('back','')}")

        # Markdown Export
        md_lines = [f"# {data.get('title','Summary')}", f"**TL;DR**: {data.get('tl_dr','')}", ""]
        for sec in data.get("sections", []):
            md_lines.append(f"## {sec.get('heading','Section')}")
            for b in sec.get("bullets", []):
                md_lines.append(f"- {b}")
            md_lines.append("")
        if kts:
            md_lines.append("## Key Terms")
            for kt in kts:
                md_lines.append(f"- **{kt.get('term','')}** — {kt.get('definition','')}")
            md_lines.append("")
        if forms:
            md_lines.append("## Formulas")
            for f in forms:
                md_lines.append(f"- **{f.get('name','')}**: {f.get('expression','')} — {f.get('meaning','')}")
            md_lines.append("")
        if exs:
            md_lines.append("## Worked Examples")
            for e in exs:
                md_lines.append(f"- {e}")
            md_lines.append("")
        if pits:
            md_lines.append("## Common Pitfalls")
            for p in pits:
                md_lines.append(f"- {p}")
            md_lines.append("")
        if qs:
            md_lines.append("## Exam-Style Questions")
            for q in qs:
                md_lines.append(f"**Q:** {q.get('question','')}")
                md_lines.append(f"**Model answer:** {q.get('model_answer','')}")
                for pt in q.get("markscheme_points", []):
                    md_lines.append(f"- {pt}")
                md_lines.append("")
        if fcs:
            md_lines.append("## Flashcards")
            for c in fcs:
                md_lines.append(f"- Front: {c.get('front','')}")
                md_lines.append(f"  Back: {c.get('back','')}")
            md_lines.append("")
        md_content = "\n".join(md_lines)
        st.download_button("⬇️ Download Markdown", md_content, file_name="summary.md")
