import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import fitz
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pdf2docx import Converter

app = FastAPI(
    title="Super Converter Pro PDF to Word API",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

MAX_MB = int(os.getenv("MAX_PDF_MB", "25"))
MAX_PAGES = int(os.getenv("MAX_PDF_PAGES", "80"))
OCR_TIMEOUT = int(os.getenv("OCR_TIMEOUT_SECONDS", "600"))
CONVERT_TIMEOUT = int(os.getenv("CONVERT_TIMEOUT_SECONDS", "900"))

# Render Free instances have limited RAM/CPU. Serialising conversions prevents
# two large PDFs from competing for memory and making the second request appear
# stuck indefinitely.
CONVERSION_LOCK = threading.Lock()


def parse_range(value: str, total: int):
    if not value or not value.strip():
        return 0, total

    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", value)
    if not m:
        raise ValueError("Page range must look like 1-5 or 3.")

    start = int(m.group(1))
    end = int(m.group(2) or start)

    if start < 1 or end < start or end > total:
        raise ValueError(f"Page range must be between 1 and {total}.")

    return start - 1, end


def page_has_meaningful_text(page) -> bool:
    text = page.get_text("text").strip()
    # Avoid treating a page containing only a tiny header/footer or whitespace
    # as a fully text-based page.
    return len(re.sub(r"\s+", "", text)) >= 5


def pages_need_ocr(pdf_path: Path, start: int, end: int) -> bool:
    doc = fitz.open(pdf_path)
    try:
        return any(not page_has_meaningful_text(doc[i]) for i in range(start, end))
    finally:
        doc.close()


def run_command(cmd, timeout_seconds: int, label: str):
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"{label} timed out after {timeout_seconds} seconds. "
            "Try a smaller PDF or a smaller page range."
        )

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"{label} failed").strip()
        raise RuntimeError(detail[-2500:])


def run_ocr(input_pdf: Path, output_pdf: Path, lang: str):
    cmd = [
        "ocrmypdf",
        "--skip-text",
        "--deskew",
        "--rotate-pages",
        "--output-type",
        "pdf",
        "-l",
        lang,
        str(input_pdf),
        str(output_pdf),
    ]
    run_command(cmd, OCR_TIMEOUT, "OCR")


def convert_with_pdf2docx(input_pdf: Path, output_docx: Path, start: int, end: int):
    converter = None
    try:
        converter = Converter(str(input_pdf))
        converter.convert(
            str(output_docx),
            start=start,
            end=end,
            multi_processing=False,
        )
    finally:
        if converter is not None:
            converter.close()


def cleanup_workdir(work: Path):
    shutil.rmtree(work, ignore_errors=True)


@app.get("/")
def root():
    return {
        "ok": True,
        "service": "super-converter-pdfword",
        "version": "2.0.0",
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "pdf-to-word",
        "version": "2.0.0",
    }


@app.post("/convert/pdf-to-word")
def convert_pdf_to_word(
    file: UploadFile = File(...),
    mode: str = Form("high-fidelity"),
    ocr_lang: str = Form("eng"),
    page_range: str = Form(""),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")

    if mode not in {"high-fidelity", "ocr"}:
        mode = "high-fidelity"

    if ocr_lang not in {"eng", "hin", "eng+hin"}:
        ocr_lang = "eng"

    # A single conversion at a time is intentional for Render Free.
    if not CONVERSION_LOCK.acquire(blocking=False):
        raise HTTPException(
            429,
            "The conversion engine is busy processing another PDF. "
            "Please wait a moment and try again.",
        )

    work = Path(tempfile.mkdtemp(prefix="scp-pdfword-"))
    src = work / "input.pdf"
    ocr_pdf = work / "ocr.pdf"
    out = work / "output.docx"

    try:
        total_bytes = 0
        with src.open("wb") as dst:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break

                total_bytes += len(chunk)
                if total_bytes > MAX_MB * 1024 * 1024:
                    raise HTTPException(
                        413,
                        f"PDF is too large. Maximum size is {MAX_MB} MB.",
                    )
                dst.write(chunk)

        try:
            file.file.close()
        except Exception:
            pass

        doc = fitz.open(src)
        try:
            pages = len(doc)
        finally:
            doc.close()

        if pages == 0:
            raise ValueError("The PDF has no pages.")

        if pages > MAX_PAGES:
            raise HTTPException(
                413,
                f"This service allows up to {MAX_PAGES} pages per conversion. "
                "Use Page Range to convert a smaller section.",
            )

        start, end = parse_range(page_range, pages)
        working_pdf = src
        used_ocr = False

        # OCR scanned/mixed PDFs when requested or when any selected page lacks
        # meaningful text. OCRmyPDF preserves the page appearance and adds text.
        if mode == "ocr" or pages_need_ocr(src, start, end):
            run_ocr(src, ocr_pdf, ocr_lang)
            working_pdf = ocr_pdf
            used_ocr = True

        convert_with_pdf2docx(working_pdf, out, start, end)

        if not out.exists() or out.stat().st_size < 1000:
            raise RuntimeError("The DOCX engine produced an empty document.")

        stem = Path(file.filename).stem
        suffix = "-OCR-editable.docx" if used_ocr else "-editable.docx"

        return FileResponse(
            path=str(out),
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
            filename=stem + suffix,
            background=BackgroundTask(cleanup_workdir, work),
        )

    except HTTPException:
        cleanup_workdir(work)
        raise
    except Exception as exc:
        cleanup_workdir(work)
        raise HTTPException(
            500,
            f"PDF to Word conversion failed: {str(exc)}",
        )
    finally:
        CONVERSION_LOCK.release()
