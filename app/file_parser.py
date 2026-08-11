from io import BytesIO
from pathlib import Path
from docx import Document
from pypdf import PdfReader


def extract_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".csv"}:
        for enc in ("utf-8-sig", "utf-8", "big5", "gb18030"):
            try:
                return content.decode(enc)
            except UnicodeDecodeError:
                continue
        return content.decode("utf-8", errors="replace")

    if suffix == ".docx":
        doc = Document(BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    if suffix == ".pdf":
        reader = PdfReader(BytesIO(content))
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    raise ValueError("只支援 .txt、.md、.docx、.pdf（CSV只作純文字讀取）。")


def actual_page_count(filename: str, content: bytes) -> int | None:
    """Return a reliable source page count when the file format provides one."""
    if Path(filename).suffix.lower() == ".pdf":
        return len(PdfReader(BytesIO(content)).pages)
    return None
