#!/usr/bin/env python3
"""Build the Key Maker: index.html (artifact/web, QR lib from cdnjs) and
bleshtastic-keymaker-offline.html (one file, works with no internet: save it to your phone)."""
from pathlib import Path
import re
H = Path(__file__).resolve().parent
env = (H.parent / "provisioning/channels.env").read_text()
block = re.search(r"BLESH_BLOCK_PSK=(\S+)", env).group(1)
src = (H / "index.src.html").read_text()
core = (H / "core.js").read_text()
page = src.replace("{{CORE}}", core).replace("{{BLOCK_PSK}}", block)
cdn = '<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcode-generator/1.4.4/qrcode.min.js"></script>'
(H / "index.html").write_text(page.replace("{{QRLIB}}", cdn))
lib = (H / "qrcode.js").read_text()
offline = page.replace("{{QRLIB}}", f"<script>\n{lib}\n</script>")
(H / "bleshtastic-keymaker-offline.html").write_text(
    '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n' + offline + "\n</html>\n")
print("built index.html + bleshtastic-keymaker-offline.html")
