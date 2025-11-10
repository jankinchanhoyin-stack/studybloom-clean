import io
from pypdf import PdfReader

def extract_pdf_text(file_bytes: bytes, max_pages: int = 30) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = min(len(reader.pages), max_pages)
    chunks = []
    for i in range(pages):
        try:
            chunks.append(reader.pages[i].extract_text() or "")
        except Exception:
            chunks.append("")
    return "\n\n".join(chunks)