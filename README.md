# Super Converter Pro — PDF → Word Backend v2

This backend keeps the existing `POST /convert/pdf-to-word` API used by the
frontend, but improves reliability for repeated conversions and scanned/mixed
PDFs.

## Important fidelity note

The engine is `pdf2docx`, with PyMuPDF analysis and OCRmyPDF/Tesseract for
scanned or mixed pages. It aims to preserve the PDF's text positions, images,
tables and page layout while producing an editable DOCX.

No general PDF→DOCX engine can guarantee pixel-for-pixel visual identity and
full editability for every PDF. PDFs are fixed-layout documents while DOCX is
a reflowable document format. Complex fonts, forms, vector artwork, unusual
tables and layered graphics can therefore still differ from the source.

## Reliability improvements in v2

- Only one conversion runs at a time on the Render Free instance.
- Prevents concurrent large jobs from exhausting RAM and making a second job
  appear permanently stuck.
- Temporary work folders are deleted after the DOCX response finishes.
- OCR and conversion have explicit timeouts.
- Mixed/scanned pages trigger OCR when selected pages lack meaningful text.
- Page count is capped to keep the free instance stable.
- `/` and `/health` provide simple service checks.
- The frontend endpoint remains:
  `/convert/pdf-to-word`

## Render

Deploy `main.py`, `requirements.txt`, `Dockerfile`, and `render.yaml` as a
Docker Web Service.

After deployment, test:

`https://YOUR-SERVICE.onrender.com/health`

Expected JSON contains `"ok": true`.

Keep the frontend API constant pointed at the deployed service URL.

## Request fields

`file` — PDF upload

`mode` — `high-fidelity` or `ocr`

`ocr_lang` — `eng`, `hin`, or `eng+hin`

`page_range` — empty for all pages, or e.g. `1-5`

## Render Free recommendation

For very large or complicated PDFs, convert smaller page ranges. The free
instance has limited CPU/RAM and is not equivalent to a commercial conversion
service such as iLovePDF.
