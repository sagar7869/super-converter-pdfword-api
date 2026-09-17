# Super Converter Pro — High-Fidelity PDF → Word backend

This replaces the browser-only `pdf.js → docx.js` PDF-to-Word implementation with a server-side conversion engine based on `pdf2docx` + PyMuPDF, with OCRmyPDF/Tesseract fallback for scanned PDFs.

## Deploy on Render

1. Create a new GitHub repository/folder containing `main.py`, `requirements.txt`, `Dockerfile`, and `render.yaml`.
2. In Render, create a **Web Service** from that repository.
3. Runtime: Docker (picked up by `render.yaml`).
4. Plan: Free.
5. After deploy, test:
   `https://YOUR-SERVICE.onrender.com/health`
6. It should return JSON containing `"ok": true`.
7. Put that URL into the frontend constant `PDF_TO_WORD_API`.

## Endpoint

POST `/convert/pdf-to-word`
- multipart field: `file`
- form field: `mode=high-fidelity` or `ocr`
- form field: `ocr_lang=eng|hin|eng+hin`
- form field: `page_range=` or `1-5`

The service does not intentionally persist uploaded PDFs after conversion. Temporary files are used during processing. Configure platform-level logs/storage according to your deployment needs.
