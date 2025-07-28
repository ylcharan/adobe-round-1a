#!/usr/bin/env python3

import os, json, time, logging, re
from pathlib import Path
from typing import List, Dict
import fitz                    # PyMuPDF
from PIL import Image
import pytesseract

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

class PDFProcessor:
    def __init__(self,
                 size_threshold: int = 15,
                 max_pages_scan: int = 50,
                 ocr_fallback: bool = True,
                 ocr_every_page: bool = False,
                 ocr_dpi: int = 200):
        self.size_threshold = size_threshold
        self.max_pages_scan = max_pages_scan
        self.ocr_fallback = ocr_fallback
        self.ocr_every_page = ocr_every_page
        self.ocr_dpi = ocr_dpi

        raw_patterns = [
            (r'^(Chapter\s+\d+)', 'H1'),
            (r'^(\d+\.\s+[A-Z][^.]*)', 'H1'),
            (r'^([A-Z][A-Z\s]{5,})\s*$', 'H1'),
            (r'^(\d+\.\d+\s+[A-Z][^.]*)', 'H2'),
            (r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*$', 'H2'),
            (r'^(\d+\.\d+\.\d+\s+[A-Z][^.]*)', 'H3'),
            (r'^([a-z]\)\s+[A-Z][^.]*)', 'H3'),
            (r'^([ivxlcdm]+\.\s+[A-Z][^.]*)', 'H3'),
            (r'^([A-Z][A-Za-z0-9 ]+)\n[-=]{3,}$', 'H1'),
            (r'^(Section|Appendix)\s+[A-Z0-9]+', 'H1'),
        ]
        self.heading_patterns = [(re.compile(p, re.IGNORECASE), lvl)
                                 for p, lvl in raw_patterns]

    def process_pdf(self, pdf_path: Path) -> Dict:
        try:
            with fitz.open(pdf_path) as doc:
                title = self._extract_title(doc)
                outline = self._extract_outline(doc)
            log.info(f"{pdf_path.name}: {len(outline)} headings")
            return {"title": title, "outline": outline}
        except Exception as e:
            log.error(f"{pdf_path.name}: {e}")
            return {"title": pdf_path.stem, "outline": []}

    def _ocr_page(self, page) -> str:
        mat = fitz.Matrix(self.ocr_dpi / 72, self.ocr_dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        return pytesseract.image_to_string(img)

    def _extract_title(self, doc) -> str:
        meta = (doc.metadata or {}).get("title", "").strip()
        if meta:
            return meta

        if len(doc) == 0:
            return "Untitled Document"

        first_page = doc[0]
        text = first_page.get_text().strip()
        if not text and self.ocr_fallback:
            text = self._ocr_page(first_page)

        for line in [l.strip() for l in text.splitlines() if l.strip()][:5]:
            if len(line) > 10 and not re.match(r'^(page\s*\d+|\d+\s*)$', line.lower()):
                return line
        return doc.name or "Untitled Document"

    def _extract_outline(self, doc) -> List[Dict]:
        toc = doc.get_toc()
        if toc:
            return [{"level": f"H{min(l,6)}", "text": t.strip(), "page": p}
                    for l, t, p in toc]
        return self._extract_outline_from_text(doc)

    def _extract_outline_from_text(self, doc) -> List[Dict]:
        total_pages = min(len(doc), self.max_pages_scan)
        outline, seen = [], set()

        def scan_page(pn: int) -> List[Dict]:
            page = doc[pn]
            raw_text = page.get_text()
            if (not raw_text or len(raw_text) < 5) and self.ocr_fallback and self.ocr_every_page:
                raw_text = self._ocr_page(page)
            blocks = page.get_text("dict")["blocks"] if raw_text else []
            hits: List[Dict] = []

            for b in blocks:
                for ln in b.get("lines", []):
                    text = "".join(s["text"] for s in ln["spans"]).strip()
                    if len(text) < 4:
                        continue
                    sizes = [s["size"] for s in ln["spans"]]
                    fonts = [s["font"] for s in ln["spans"]]
                    if max(sizes) < self.size_threshold and not any("Bold" in f for f in fonts):
                        continue
                    for regex, lvl in self.heading_patterns:
                        if regex.match(text):
                            hits.append({"level": lvl, "text": text, "page": pn + 1})
                            break
            return hits

        t0 = time.perf_counter()
        for pn in range(total_pages):
            for itm in scan_page(pn):
                key = (itm["text"].lower(), itm["level"])
                if key not in seen:
                    outline.append(itm)
                    seen.add(key)
        log.debug(f"scanned {total_pages} pages in {time.perf_counter()-t0:.2f}s")
        return outline

def process_pdfs():
    # Always use 'output/' in the current directory
    input_dir = Path.cwd() / "input"
    output_dir = Path.cwd() / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    proc = PDFProcessor()
    pdfs = [p for p in input_dir.iterdir() if p.suffix.lower() == ".pdf"]
    log.info(f"{len(pdfs)} PDF(s) found in {input_dir}")

    for pdf in pdfs:
        data = proc.process_pdf(pdf)
        with open(output_dir / f"{pdf.stem}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log.info(f"→ {pdf.stem}.json")

if __name__ == "__main__":
    log.info("Starting batch…")
    process_pdfs()
    log.info("Done.")
