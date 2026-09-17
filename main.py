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

app = FastAPI(title='Super Converter Pro Conversion API', version='3.0.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=False,
                   allow_methods=['POST','GET','OPTIONS'], allow_headers=['*'])

MAX_MB = int(os.getenv('MAX_PDF_MB','25'))
MAX_PAGES = int(os.getenv('MAX_PDF_PAGES','80'))
OCR_TIMEOUT = int(os.getenv('OCR_TIMEOUT_SECONDS','600'))
OFFICE_TIMEOUT = int(os.getenv('OFFICE_TIMEOUT_SECONDS','600'))
JOB_LOCK = threading.Lock()

def cleanup_and_release(work: Path):
    shutil.rmtree(work, ignore_errors=True)
    try: JOB_LOCK.release()
    except RuntimeError: pass

def parse_range(value: str, total: int):
    if not value or not value.strip(): return 0, total
    m = re.fullmatch(r'\s*(\d+)\s*(?:-\s*(\d+)\s*)?', value)
    if not m: raise ValueError('Page range must look like 1-5 or 3.')
    start, end = int(m.group(1)), int(m.group(2) or m.group(1))
    if start < 1 or end < start or end > total:
        raise ValueError(f'Page range must be between 1 and {total}.')
    return start-1, end

def pages_need_ocr(pdf_path: Path, start: int, end: int):
    doc = fitz.open(pdf_path)
    try:
        for i in range(start,end):
            text = re.sub(r'\s+','',doc[i].get_text('text') or '')
            if len(text) < 5: return True
        return False
    finally: doc.close()

def run_command(cmd, timeout, label):
    try: proc = subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'{label} timed out after {timeout} seconds. Try a smaller file or page range.')
    if proc.returncode != 0:
        detail=(proc.stderr or proc.stdout or f'{label} failed').strip()
        raise RuntimeError(detail[-2500:])

def run_ocr(src: Path, dst: Path, lang: str):
    run_command(['ocrmypdf','--skip-text','--deskew','--rotate-pages','--output-type','pdf','-l',lang,str(src),str(dst)],OCR_TIMEOUT,'OCR')

def save_upload(upload: UploadFile, path: Path):
    total=0
    with path.open('wb') as dst:
        while True:
            chunk=upload.file.read(1024*1024)
            if not chunk: break
            total += len(chunk)
            if total > MAX_MB*1024*1024:
                raise HTTPException(413,f'File is too large. Maximum size is {MAX_MB} MB.')
            dst.write(chunk)
    try: upload.file.close()
    except Exception: pass

def office_filter(filename: str):
    ext=Path(filename).suffix.lower()
    if ext in {'.doc','.docx'}: return 'pdf:writer_pdf_Export'
    if ext in {'.xls','.xlsx'}: return 'pdf:calc_pdf_Export'
    if ext in {'.ppt','.pptx'}: return 'pdf:impress_pdf_Export'
    return None

@app.get('/')
def root(): return {'ok':True,'service':'super-converter','version':'3.0.0'}

@app.get('/health')
def health(): return {'ok':True,'service':'super-converter','version':'3.0.0'}

@app.post('/convert/pdf-to-word')
def convert_pdf_to_word(file: UploadFile=File(...), mode: str=Form('high-fidelity'), ocr_lang: str=Form('eng'), page_range: str=Form('')):
    if not file.filename or not file.filename.lower().endswith('.pdf'): raise HTTPException(400,'Please upload a PDF file.')
    if mode not in {'high-fidelity','ocr'}: mode='high-fidelity'
    if ocr_lang not in {'eng','hin','eng+hin'}: ocr_lang='eng'
    if not JOB_LOCK.acquire(blocking=False):
        raise HTTPException(429,'The conversion server is busy processing another file. Please wait and try again.')
    work=Path(tempfile.mkdtemp(prefix='scp-pdfword-')); src=work/'input.pdf'; ocr_pdf=work/'ocr.pdf'; out=work/'output.docx'
    try:
        save_upload(file,src)
        doc=fitz.open(src)
        try: pages=len(doc)
        finally: doc.close()
        if pages==0: raise ValueError('The PDF has no pages.')
        if pages>MAX_PAGES: raise HTTPException(413,f'This service allows up to {MAX_PAGES} pages. Use Page Range for a smaller section.')
        start,end=parse_range(page_range,pages); working=src; used_ocr=False
        if mode=='ocr' or pages_need_ocr(src,start,end):
            run_ocr(src,ocr_pdf,ocr_lang); working=ocr_pdf; used_ocr=True
        converter=None
        try:
            converter=Converter(str(working)); converter.convert(str(out),start=start,end=end,multi_processing=False)
        finally:
            if converter is not None: converter.close()
        if not out.exists() or out.stat().st_size<1000: raise RuntimeError('The DOCX engine produced an empty document.')
        stem=Path(file.filename).stem; suffix='-OCR-editable.docx' if used_ocr else '-editable.docx'
        return FileResponse(str(out),media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',filename=stem+suffix,background=BackgroundTask(cleanup_and_release,work))
    except HTTPException:
        cleanup_and_release(work); raise
    except Exception as exc:
        cleanup_and_release(work); raise HTTPException(500,f'PDF to Word conversion failed: {exc}')

@app.post('/convert/office-to-pdf')
def convert_office_to_pdf(file: UploadFile=File(...)):
    if not file.filename: raise HTTPException(400,'Please upload an Office file.')
    filt=office_filter(file.filename)
    if not filt: raise HTTPException(400,'Supported files: DOC/DOCX, XLS/XLSX, PPT/PPTX.')
    if not JOB_LOCK.acquire(blocking=False):
        raise HTTPException(429,'The conversion server is busy processing another file. Please wait and try again.')
    work=Path(tempfile.mkdtemp(prefix='scp-office-')); src=work/Path(file.filename).name; outdir=work/'out'; outdir.mkdir()
    try:
        save_upload(file,src)
        run_command(['libreoffice','--headless','--convert-to',filt,'--outdir',str(outdir),str(src)],OFFICE_TIMEOUT,'Office conversion')
        pdf=outdir/(src.stem+'.pdf')
        if not pdf.exists() or pdf.stat().st_size<500: raise RuntimeError('LibreOffice did not produce a PDF.')
        return FileResponse(str(pdf),media_type='application/pdf',filename=src.stem+'.pdf',background=BackgroundTask(cleanup_and_release,work))
    except HTTPException:
        cleanup_and_release(work); raise
    except Exception as exc:
        cleanup_and_release(work); raise HTTPException(500,f'Office to PDF conversion failed: {exc}')
