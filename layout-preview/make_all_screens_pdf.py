from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


HERE = Path(__file__).resolve().parent
DEST = HERE / "lism-all-screens.pdf"

SCREEN_PAIRS = [
    ("Main status", "status-landscape.png", "status-portrait.png"),
    ("Display settings", "settings-landscape.png", "settings-portrait.png"),
    ("Active devices", "devices-landscape.png", "devices-portrait.png"),
]


def page_base(pdf, page_size, title, index):
    page_w, page_h = page_size
    pdf.setPageSize(page_size)
    pdf.setFillColorRGB(0.965, 0.973, 0.985)
    pdf.rect(0, 0, page_w, page_h, stroke=0, fill=1)

    pdf.setFillColorRGB(0.14, 0.18, 0.25)
    pdf.setFont("Helvetica-Bold", 19)
    pdf.drawString(38, page_h - 48, title)
    pdf.setStrokeColorRGB(0.84, 0.87, 0.91)
    pdf.line(38, 43, page_w - 38, 43)
    pdf.setFont("Helvetica", 9)
    pdf.setFillColorRGB(0.38, 0.43, 0.51)
    pdf.drawString(38, 27, "Layout previews; battery/RSSI values are examples, not hardware measurements")
    pdf.drawRightString(page_w - 38, 27, f"{index} / 4")
    return page_w, page_h


def columns(pdf, page_w, header_y):
    gap = 28
    available = page_w - 76 - gap
    left_w = available * 658 / (658 + 362)
    right_w = available - left_w
    right_x = 38 + left_w + gap
    pdf.setFont("Helvetica", 11)
    pdf.setFillColorRGB(0.35, 0.41, 0.50)
    pdf.drawCentredString(38 + left_w / 2, header_y, "Landscape | 320 x 172")
    pdf.drawCentredString(right_x + right_w / 2, header_y, "Portrait | 172 x 320")
    return left_w, right_w, right_x


def draw_pair(pdf, pair, top, left_w, right_w, right_x, max_h):
    with Image.open(HERE / pair[1]) as wide, Image.open(HERE / pair[2]) as tall:
        scale = min(left_w / wide.width, right_w / tall.width,
                    max_h / wide.height, max_h / tall.height)
        for image, x, width in [(wide, 38, left_w), (tall, right_x, right_w)]:
            draw_w, draw_h = image.width * scale, image.height * scale
            pdf.drawImage(ImageReader(image), x + (width - draw_w) / 2,
                          top - draw_h, width=draw_w, height=draw_h)


pdf = canvas.Canvas(str(DEST), pagesize=A4)
pdf.setTitle("LisM dongle - all screen layouts")
page_w, page_h = page_base(pdf, A3, "LisM XIAO 1.47 - all screen layouts", 1)
left_w, right_w, right_x = columns(pdf, page_w, page_h - 78)
row_h = (page_h - 146) / 3
for row, pair in enumerate(SCREEN_PAIRS):
    row_top = page_h - 102 - row * row_h
    pdf.setFont("Helvetica-Bold", 12)
    pdf.setFillColorRGB(0.14, 0.18, 0.25)
    pdf.drawString(38, row_top, pair[0])
    draw_pair(pdf, pair, row_top - 12, left_w, right_w, right_x, row_h - 30)
pdf.showPage()

for page_number, pair in enumerate(SCREEN_PAIRS, start=2):
    page_w, page_h = page_base(pdf, landscape(A4), pair[0], page_number)
    left_w, right_w, right_x = columns(pdf, page_w, page_h - 78)
    draw_pair(pdf, pair, page_h - 98, left_w, right_w, right_x, page_h - 158)
    pdf.showPage()
pdf.save()
print(DEST)
