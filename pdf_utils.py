# pdf_utils.py
from typing import List
from io import BytesIO
from pypdf import PdfReader
from pptx import Presentation   # requires python-pptx
from PIL import Image

def _read_bytes(file) -> bytes:
    # Streamlit's UploadedFile has getvalue(); local file-like too
    if hasattr(file, "getvalue"):
        return file.getvalue()
    return file.read()

def _extract_pdf(b: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(b))
        if reader.is_encrypted:
            # Try decrypt with blank; still fail? raise friendly msg
            try:
                reader.decrypt("")
            except Exception:
                raise RuntimeError("This PDF appears to be password-protected/encrypted.")
        out = []
        for page in reader.pages:
            try:
                out.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(out).strip()
    except Exception as e:
        msg = str(e).lower()
        if "password" in msg or "encrypt" in msg or "aes" in msg:
            raise RuntimeError("This PDF appears to be password-protected/encrypted.")
        raise

def _extract_pptx(b: bytes) -> str:
    try:
        prs = Presentation(BytesIO(b))
        out = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text"):
                    out.append(shape.text)
        return "\n".join(out).strip()
    except Exception as e:
        msg = str(e).lower()
        if "password" in msg or "encrypt" in msg or "aes" in msg:
            raise RuntimeError("This PPTX appears to be password-protected/encrypted.")
        raise

def _extract_image(_b: bytes) -> str:
    # OCR intentionally omitted to avoid system deps on Streamlit Cloud.
    # We just return a placeholder so pipeline doesn't crash.
    return ""  # could be enhanced later with a cloud OCR API

def _extract_txt(b: bytes) -> str:
    return b.decode("utf-8", errors="ignore")

def extract_any(files: List) -> str:
    texts = []
    for f in files:
        name = getattr(f, "name", "").lower()
        b = _read_bytes(f)
        try:
            if name.endswith(".pdf"):
                texts.append(_extract_pdf(b))
            elif name.endswith(".pptx"):
                texts.append(_extract_pptx(b))
            elif name.endswith(".txt"):
                texts.append(_extract_txt(b))
            elif name.endswith((".png", ".jpg", ".jpeg")):
                # Skip OCR for stability; add a line so the user knows.
                tmp = _extract_image(b)
                if tmp.strip():
                    texts.append(tmp)
                else:
                    texts.append(f"[Image: {name}]")
            else:
                texts.append(_extract_txt(b))
        except RuntimeError as re:
            # Friendly message for encrypted content
            raise RuntimeError(f"{name}: {re}")
        except Exception as e:
            raise RuntimeError(f"Failed to read {name}: {e}")
    combined = "\n\n".join(t for t in texts if t)
    return combined
