"""Document preprocessing: normalise any upload into an image OpenAI vision accepts.

OpenAI's vision API only accepts image MIME types (PNG/JPEG/WebP/GIF). PDFs must be
rendered to images first. This module converts PDFs to a single stacked PNG.
"""
from __future__ import annotations

import io
import logging

import fitz  # PyMuPDF
from PIL import Image

logger = logging.getLogger(__name__)

_PDF_RENDER_DPI = 180
_MAX_PDF_PAGES = 4          # cap so a huge PDF can't produce an unusable image
_MAX_COMBINED_HEIGHT = 9000  # px; downscale if a multi-page stack exceeds this

_IMAGE_MIME_PREFIXES = ("image/",)


def to_vision_image(content: bytes, mime_type: str) -> tuple[bytes, str]:
    """Return (image_bytes, image_mime_type) suitable for OpenAI vision.

    - Image inputs pass through unchanged.
    - PDF inputs are rendered to a single PNG (multi-page PDFs stacked vertically).
    """
    mime = (mime_type or "").lower()

    if mime.startswith(_IMAGE_MIME_PREFIXES):
        return content, mime_type

    if mime == "application/pdf" or _looks_like_pdf(content):
        return _pdf_to_png(content), "image/png"

    # Unknown type — try as-is; OpenAI will reject it cleanly if unsupported.
    return content, mime_type or "application/octet-stream"


def _looks_like_pdf(content: bytes) -> bool:
    return content[:5] == b"%PDF-"


def _pdf_to_png(pdf_bytes: bytes) -> bytes:
    """Render PDF pages to PNGs and stack them vertically into one image."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages = []
        for i, page in enumerate(doc):
            if i >= _MAX_PDF_PAGES:
                logger.warning("PDF has >%d pages; rendering first %d only",
                               _MAX_PDF_PAGES, _MAX_PDF_PAGES)
                break
            pix = page.get_pixmap(dpi=_PDF_RENDER_DPI)
            pages.append(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))
    finally:
        doc.close()

    if not pages:
        raise ValueError("PDF contained no renderable pages")

    if len(pages) == 1:
        combined = pages[0]
    else:
        width = max(p.width for p in pages)
        total_height = sum(p.height for p in pages)
        combined = Image.new("RGB", (width, total_height), "white")
        y = 0
        for p in pages:
            combined.paste(p, (0, y))
            y += p.height

    if combined.height > _MAX_COMBINED_HEIGHT:
        scale = _MAX_COMBINED_HEIGHT / combined.height
        combined = combined.resize(
            (max(1, int(combined.width * scale)), _MAX_COMBINED_HEIGHT),
            Image.LANCZOS,
        )

    out = io.BytesIO()
    combined.save(out, format="PNG")
    return out.getvalue()
