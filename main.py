import os, re, shutil, subprocess, tempfile
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pdf2docx import Converter

app = FastAPI(title='Super Converter Pro PDF to Word API', version='1.0.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=False,
    allow_methods=['POST', 'GET', 'OPTIONS'],
    allow_headers=['*'],
)

MAX_MB = int(os.getenv('MAX_PDF_MB', '25'))


def parse_range(value: str, total: int):
    if not value or not value.strip():
        return 0, total
    m = re.fullmatch(r'\s*(\d+)\s*(?:-\s*(\d+)\s*)?', value)
    if not m:
        raise ValueError('Page range must look like 1-5 or 3.')
    start = int(m.group(1))
    end = int(m.group(2) or start)
    if start < 1 or end < start or end > total:
        raise ValueError(f'Page range must be between 1 and {total}.')
    return start - 1, end


def has_text(pdf_path: Path, start: int, end: int) -> bool:
    doc = fitz.open(pdf_path)
    try:
        for i in range(start, end):
            if doc[i].get_text('text').strip():
                return True
        return False
    finally:
        doc.close()


def run_ocr(input_pdf: Path, output_pdf: Path, lang: str):
    # OCRmyPDF adds a searchable text layer while retaining the original page image.
    cmd = [
        'ocrmypdf', '--skip-text', '--deskew', '--rotate-pages',
        '--output-type', 'pdf', '-l', lang, str(input_pdf), str(output_pdf)
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or 'OCR failed').strip()
        raise RuntimeError(detail[-1500:])


@app.get('/health')
def health():
    return {'ok': True, 'service': 'pdf-to-word'}


@app.post('/convert/pdf-to-word')
async def convert_pdf_to_word(
    file: UploadFile = File(...),
    mode: str = Form('high-fidelity'),
    ocr_lang: str = Form('eng'),
    page_range: str = Form(''),
):
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise HTTPException(400, 'Please upload a PDF file.')

    if mode not in {'high-fidelity', 'ocr'}:
        mode = 'high-fidelity'
    if ocr_lang not in {'eng', 'hin', 'eng+hin'}:
        ocr_lang = 'eng'

    work = Path(tempfile.mkdtemp(prefix='scp-pdfword-'))
    src = work / 'input.pdf'
    ocr_pdf = work / 'ocr.pdf'
    out = work / 'output.docx'

    try:
        total = 0
        with src.open('wb') as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_MB * 1024 * 1024:
                    raise HTTPException(413, f'PDF is too large. Maximum size is {MAX_MB} MB.')
                f.write(chunk)

        doc = fitz.open(src)
        pages = len(doc)
        doc.close()
        if pages == 0:
            raise ValueError('The PDF has no pages.')

        start, end = parse_range(page_range, pages)
        working_pdf = src

        # Automatically OCR scanned PDFs, or force OCR when user selects OCR.
        if mode == 'ocr' or not has_text(src, start, end):
            run_ocr(src, ocr_pdf, ocr_lang)
            working_pdf = ocr_pdf

        converter = Converter(str(working_pdf))
        try:
            # pdf2docx end is exclusive.
            converter.convert(str(out), start=start, end=end, multi_processing=False)
        finally:
            converter.close()

        if not out.exists() or out.stat().st_size < 1000:
            raise RuntimeError('The DOCX engine produced an empty document.')

        stem = Path(file.filename).stem
        suffix = '-OCR-editable.docx' if (mode == 'ocr' or working_pdf == ocr_pdf) else '-editable.docx'
        return FileResponse(
            path=str(out),
            media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            filename=stem + suffix,
            background=None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f'PDF to Word conversion failed: {exc}')
    finally:
        # FileResponse needs the file while sending; cleanup is handled by the OS/container
        # lifecycle in this simple free deployment. A periodic cleanup can remove old temp dirs.
        pass
