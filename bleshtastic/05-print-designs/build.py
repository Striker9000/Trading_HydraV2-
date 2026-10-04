#!/usr/bin/env python3
"""Fill the public QR into the print templates, then render PDFs + PNG previews (node render.js)."""
import re, subprocess
from pathlib import Path
H = Path(__file__).resolve().parent
import io, segno
pub = (H.parent / "03-channels-and-devices/out/block-public.txt").read_text()
link = re.search(r"BLOCK_ADD_URL=(\S+)", pub).group(1)
buf = io.BytesIO()
segno.make(link, error="m").save(buf, kind="svg", xmldecl=False, svgns=True, omitsize=True, border=0, dark="#18142B")
qr = buf.getvalue().decode()
for name in ["flyer", "setup-card", "sticker"]:
    (H / f"{name}.built.html").write_text((H / f"{name}.html").read_text().replace("{{QR_BLOCK}}", qr))
subprocess.run(["node", str(H / "render.js")], check=True, cwd=H)
