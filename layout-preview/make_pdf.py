from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


HERE = Path(__file__).resolve().parent
DEST = HERE / "lism-layouts.pdf"
PAGE_W, PAGE_H = landscape(A4)

pages = [
    ("Main status", HERE / "main-status.png"),
    ("Display settings", HERE / "display-settings.png"),
    ("Active devices", HERE / "active-devices.png"),
]

pdf = canvas.Canvas(str(DEST), pagesize=(PAGE_W, PAGE_H))
pdf.setTitle("LisM dongle screen layout previews")
for index, (title, image_path) in enumerate(pages, start=1):
    with Image.open(image_path) as image:
        image_w, image_h = image.size
        max_w, max_h = PAGE_W - 48, PAGE_H - 84
        scale = min(max_w / image_w, max_h / image_h)
        draw_w, draw_h = image_w * scale, image_h * scale
        pdf.setFillColorRGB(0.965, 0.973, 0.98)
        pdf.rect(0, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
        pdf.setFillColorRGB(0.15, 0.19, 0.26)
        pdf.setFont("Helvetica-Bold", 17)
        pdf.drawString(24, PAGE_H - 30, f"LisM / {title}")
        pdf.drawImage(ImageReader(image), (PAGE_W - draw_w) / 2, (PAGE_H - draw_h) / 2 - 3, width=draw_w, height=draw_h)
        pdf.setFont("Helvetica", 9)
        pdf.setFillColorRGB(0.38, 0.43, 0.5)
        pdf.drawString(24, 18, "Layout preview only - not a photograph of the hardware display")
        pdf.drawRightString(PAGE_W - 24, 18, f"{index} / {len(pages)}")
        pdf.showPage()
pdf.save()
print(DEST)
