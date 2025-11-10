import io
from pypdf import PdfReader  # use pypdf; requirements pin below

def extract_pdf_text(file_bytes: bytes, max_pages: int = 30) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = min(len(reader.pages), max_pages)
    parts = []
    for i in range(pages):
        try:
            parts.append(reader.pages[i].extract_text() or "")
        except Exception:
            parts.append("")
    return "\n\n".join(parts)
