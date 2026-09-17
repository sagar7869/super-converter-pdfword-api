# Super Converter Pro — Complete Conversion Backend v3

Endpoints:
- GET /health
- POST /convert/pdf-to-word
- POST /convert/office-to-pdf

PDF to Word uses PyMuPDF + pdf2docx and OCRmyPDF/Tesseract for scanned or mixed PDFs.
Office to PDF uses LibreOffice headless for DOC/DOCX, XLS/XLSX and PPT/PPTX.

Reliability improvements:
- one conversion job at a time on Render Free
- lock is released only after the response has finished sending
- temporary folders are deleted after each response
- explicit size/page/time limits
- clear 429/413/500 errors

No open-source PDF-to-DOCX engine guarantees pixel-perfect iLovePDF-equivalent output for every PDF.
The DOCX is editable and the converter aims to preserve text, images, tables and layout.
