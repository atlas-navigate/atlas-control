#!/usr/bin/env python3
"""
Draw one Atlas's unit record (for the factory and support) from its identity.

    sudo /usr/local/lib/atlas/bin/atlas-identity label-json \\
        | /opt/atlas/current/venv/bin/python3 tools/manufacturing/make_label.py --out DIR

Writes DIR/<name>.svg, DIR/<name>.png and DIR/<name>.txt (mode 0600: they
hold the setup code). No label ships with Atlas (owner decision, 2026-11-12):
owners join the Wi-Fi named after its address, with the code in that name as
the password, and pair with the Atlas button. The record carries:

  - the Atlas app QR code (ATLAS1:…): with it support can pair the app with
    no typing, trusting this unit's TLS key from the first connection
  - the name, the Wi-Fi name (which is also the address to open,
    atlas-xxxx.local), how to connect (press the Atlas button, pick the
    colour it shows) and the setup code, the backup for that

The Wi-Fi password is never printed (it is inside the app QR code only).
"""
import argparse
import json
import os
import sys

import qrcode
from qrcode.constants import ERROR_CORRECT_M

W, H = 560, 340           # record canvas (px ≈ 0.26 mm at 96 dpi)
QR_PX = 230


def matrix(data):
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.get_matrix()


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def svg_qr(m, x, y, size):
    cell = size / len(m)
    rects = [f'<rect x="{x + c * cell:.2f}" y="{y + r * cell:.2f}" width="{cell:.2f}" height="{cell:.2f}"/>'
             for r, row in enumerate(m) for c, on in enumerate(row) if on]
    return f'<g fill="#000">{"".join(rects)}</g>'


def lines_for(label):
    # The Wi-Fi name is the .local address the owner opens next, so it is
    # printed once; the setup code is only the backup for the button.
    return [("Wi-Fi name = address", label["hotspot_ssid"]),
            ("Then", "press button, pick colour"), ("Setup code (backup)", label["setup_code"])]


def render_svg(label):
    app = matrix(label["app_qr"])
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
             f'<rect width="{W}" height="{H}" fill="#fff"/>',
             f'<text x="24" y="44" font-family="Helvetica,Arial,sans-serif" font-size="30" '
             f'font-weight="700">{_esc(label["name"])}</text>',
             svg_qr(app, 24, 64, QR_PX),
             f'<text x="{24 + QR_PX / 2}" y="{64 + QR_PX + 22}" text-anchor="middle" '
             f'font-family="Helvetica,Arial,sans-serif" font-size="15">Set up with the Atlas app</text>']
    y = 90
    for key, value in lines_for(label):
        parts.append(f'<text x="{24 + QR_PX + 24}" y="{y}" font-family="Helvetica,Arial,sans-serif" '
                     f'font-size="14" fill="#555">{_esc(key)}</text>')
        parts.append(f'<text x="{24 + QR_PX + 24}" y="{y + 22}" font-family="Menlo,Consolas,monospace" '
                     f'font-size="17" font-weight="700">{_esc(value)}</text>')
        y += 58
    parts.append("</svg>")
    return "\n".join(parts)


def render_png(label, path):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(img)
    try:
        big = ImageFont.truetype("DejaVuSans-Bold.ttf", 28)
        small = ImageFont.truetype("DejaVuSans.ttf", 14)
        mono = ImageFont.truetype("DejaVuSansMono-Bold.ttf", 16)
    except OSError:
        big = small = mono = ImageFont.load_default()
    draw.text((24, 14), label["name"], fill="black", font=big)
    m = matrix(label["app_qr"])
    cell = QR_PX / len(m)
    x0 = 24
    for r, row in enumerate(m):
        for c, on in enumerate(row):
            if on:
                draw.rectangle([x0 + c * cell, 64 + r * cell, x0 + (c + 1) * cell - 1, 64 + (r + 1) * cell - 1],
                               fill="black")
    draw.text((x0 + 10, 64 + QR_PX + 8), "Set up with the Atlas app", fill="black", font=small)
    y = 74
    for key, value in lines_for(label):
        draw.text((24 + QR_PX + 24, y), key, fill="#555", font=small)
        draw.text((24 + QR_PX + 24, y + 18), value, fill="black", font=mono)
        y += 58
    img.save(path)


def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-png", action="store_true")
    args = ap.parse_args()
    label = json.load(sys.stdin)
    for key in ("name", "hostname", "setup_code", "hotspot_ssid", "app_qr"):
        if not label.get(key):
            sys.exit(f"label data is missing {key}")
    os.makedirs(args.out, mode=0o700, exist_ok=True)
    base = os.path.join(args.out, label["name"])
    write_private(base + ".svg", render_svg(label).encode())
    facts = [("Name", label["name"])] + lines_for(label) + [("Open", f"https://{label['hostname']}.local")]
    write_private(base + ".txt", ("\n".join(f"{k}: {v}" for k, v in facts) + "\n").encode())
    written = [base + ".svg", base + ".txt"]
    if not args.no_png:
        write_private(base + ".png", b"")
        render_png(label, base + ".png")
        written.append(base + ".png")
    print("\n".join(written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
