"""Your own material: notes, a syllabus, a profile, code conventions.

Answers are generic because the model knows nothing about you or your course.
Feed it a few documents and every answer is grounded in them instead -- your
terminology, your examples, your stack.

Everything lives as plain text under `context/`. Small collections are sent in
full; large ones are narrowed to the passages that match the question, so the
prompt stays cheap and focused.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT

log = logging.getLogger("context")

DIR = ROOT / "context"
MAX_FILE_CHARS = 200_000
SEND_WHOLE_UNDER = 4_000      # chars: below this, the model just gets everything
                              # (kept small -- every request resends it, and free
                              #  tiers meter tokens per minute)
CHUNK_CHARS = 1_200
TOP_CHUNKS = 6

STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "was", "for",
    "on", "with", "as", "at", "by", "it", "this", "that", "be", "from", "we",
    "you", "i", "my", "your", "what", "how", "why", "do", "does", "can", "sir",
}
WORD = re.compile(r"[A-Za-z0-9_+#.]{2,}")


@dataclass
class Doc:
    name: str
    text: str

    @property
    def chars(self) -> int:
        return len(self.text)


def _safe(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9 _.-]", "", name).strip() or "note"
    return stem[:60]


def ensure_dir():
    DIR.mkdir(parents=True, exist_ok=True)


def list_docs() -> list[Doc]:
    ensure_dir()
    out = []
    for p in sorted(DIR.glob("*.txt")):
        try:
            out.append(Doc(p.stem, p.read_text(encoding="utf-8")))
        except Exception:
            log.exception("could not read %s", p)
    return out


def save(name: str, text: str) -> Doc:
    ensure_dir()
    text = text.strip()[:MAX_FILE_CHARS]
    doc = Doc(_safe(name), text)
    (DIR / f"{doc.name}.txt").write_text(doc.text, encoding="utf-8")
    log.info("stored context %r (%d chars)", doc.name, doc.chars)
    return doc


def delete(name: str) -> bool:
    path = DIR / f"{_safe(name)}.txt"
    if path.exists():
        path.unlink()
        return True
    return False


def extract(filename: str, data: bytes) -> str:
    """Pull text out of whatever the user uploaded."""
    import io

    lower = filename.lower()
    if lower.endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError:
            raise RuntimeError("PDF support needs: pip install pypdf")
        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as exc:
            log.warning("pdf parse failed for %s: %s", filename, exc)
            raise RuntimeError(
                "That PDF could not be read. If it is a scan or a photo the text "
                "is an image, and if it is password protected it cannot be "
                "opened. Copy the text and use Paste text instead."
            ) from exc
        if not text.strip():
            raise RuntimeError(
                "That PDF has no selectable text -- it is probably a scan. Copy "
                "the text and use Paste text instead."
            )
        return text
    if lower.endswith(".docx"):
        try:
            import docx
        except ImportError:
            raise RuntimeError("Word support needs: pip install python-docx")
        try:
            document = docx.Document(io.BytesIO(data))
        except Exception as exc:
            raise RuntimeError("That Word file could not be opened.") from exc
        return "\n".join(p.text for p in document.paragraphs)
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise RuntimeError("could not read that file as text")


def _chunks(doc: Doc) -> list[tuple[str, str]]:
    """Split on blank lines, then pack into roughly CHUNK_CHARS pieces."""
    out, buf = [], ""
    for para in re.split(r"\n\s*\n", doc.text):
        para = para.strip()
        if not para:
            continue
        if len(buf) + len(para) + 2 > CHUNK_CHARS and buf:
            out.append((doc.name, buf))
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        out.append((doc.name, buf))
    return out


def _score(chunk: str, terms: set[str]) -> int:
    words = {w.lower() for w in WORD.findall(chunk)}
    return len(words & terms)


def build(question: str = "") -> str:
    """The context block to paste into the system prompt."""
    docs = list_docs()
    if not docs:
        return ""

    total = sum(d.chars for d in docs)
    if total <= SEND_WHOLE_UNDER or not question:
        parts = [f"--- {d.name} ---\n{d.text}" for d in docs if d.text]
        return "\n\n".join(parts)

    terms = {w.lower() for w in WORD.findall(question)} - STOP
    scored = []
    for doc in docs:
        for name, chunk in _chunks(doc):
            scored.append((_score(chunk, terms), name, chunk))
    scored.sort(key=lambda x: -x[0])
    picked = [(n, c) for s, n, c in scored[:TOP_CHUNKS] if s > 0]
    if not picked:
        picked = [(n, c) for _, n, c in scored[:2]]
    return "\n\n".join(f"--- {n} ---\n{c}" for n, c in picked)


def summary() -> list[dict]:
    return [{"name": d.name, "chars": d.chars} for d in list_docs()]
