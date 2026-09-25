#!/usr/bin/env python3
"""
Draw the label for one Atlas from its identity.

    sudo /usr/local/lib/atlas/bin/atlas-identity label-json \\
        | /opt/atlas/current/venv/bin/python3 tools/manufacturing/make_label.py --out DIR

Writes DIR/<name>.svg, DIR/<name>.png and DIR/<name>.txt (mode 0600: they
hold the setup code and hotspot password). The label carries:

  - a standard Wi-Fi QR code — any phone camera joins Atlas's own network
  - the Atlas app QR code (ATLAS1:…): the app pairs with no typing and
    trusts this unit's TLS key from the very first connection
  - the name, the hotspot name and password, the setup code, and the
    address to open in a browser
"""
import argparse
import json
import os
import sys

import qrcode
from qrcode.constants import ERROR_CORRECT_M

W, H = 800, 340           # label canvas (px ≈ 0.26 mm at 96 dpi)
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
    return [("Wi-Fi", label["hotspot_ssid"]), ("Password", label["hotspot_password"]),
            ("Setup code", label["setup_code"]), ("Open", f"https://{label['hostname']}.local")]


def render_svg(label):
    wifi, app = matrix(label["wifi_qr"]), matrix(label["app_qr"])
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
             f'<rect width="{W}" height="{H}" fill="#fff"/>',
             f'<text x="24" y="44" font-family="Helvetica,Arial,sans-serif" font-size="30" '
             f'font-weight="700">{_esc(label["name"])}</text>',
             svg_qr(wifi, 24, 64, QR_PX), svg_qr(app, 24 + QR_PX + 20, 64, QR_PX),
             f'<text x="{24 + QR_PX / 2}" y="{64 + QR_PX + 22}" text-anchor="middle" '
             f'font-family="Helvetica,Arial,sans-serif" font-size="15">Join Atlas Wi-Fi</text>',
             f'<text x="{24 + QR_PX * 1.5 + 20}" y="{64 + QR_PX + 22}" text-anchor="middle" '
             f'font-family="Helvetica,Arial,sans-serif" font-size="15">Set up with the Atlas app</text>']
    y = 90
    for key, value in lines_for(label):
        parts.append(f'<text x="{24 + 2 * QR_PX + 44}" y="{y}" font-family="Helvetica,Arial,sans-serif" '
                     f'font-size="14" fill="#555">{_esc(key)}</text>')
        parts.append(f'<text x="{24 + 2 * QR_PX + 44}" y="{y + 22}" font-family="Menlo,Consolas,monospace" '
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
    for i, data in enumerate((label["wifi_qr"], label["app_qr"])):
        m = matrix(data)
        cell = QR_PX / len(m)
        x0 = 24 + i * (QR_PX + 20)
        for r, row in enumerate(m):
            for c, on in enumerate(row):
                if on:
                    draw.rectangle([x0 + c * cell, 64 + r * cell, x0 + (c + 1) * cell - 1, 64 + (r + 1) * cell - 1],
                                   fill="black")
        draw.text((x0 + 10, 64 + QR_PX + 8), ("Join Atlas Wi-Fi", "Set up with the Atlas app")[i],
                  fill="black", font=small)
    y = 74
    for key, value in lines_for(label):
        draw.text((24 + 2 * QR_PX + 44, y), key, fill="#555", font=small)
        draw.text((24 + 2 * QR_PX + 44, y + 18), value, fill="black", font=mono)
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
    for key in ("name", "hostname", "setup_code", "hotspot_ssid", "hotspot_password", "app_qr", "wifi_qr"):
        if not label.get(key):
            sys.exit(f"label data is missing {key}")
    os.makedirs(args.out, mode=0o700, exist_ok=True)
    base = os.path.join(args.out, label["name"])
    write_private(base + ".svg", render_svg(label).encode())
    write_private(base + ".txt", ("\n".join(f"{k}: {v}" for k, v in [("Name", label["name"])] + lines_for(label))
                                  + "\n").encode())
    written = [base + ".svg", base + ".txt"]
    if not args.no_png:
        write_private(base + ".png", b"")
        render_png(label, base + ".png")
        written.append(base + ".png")
    print("\n".join(written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
