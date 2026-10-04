#!/usr/bin/env python3
"""Python twin of the Key Maker page (02-key-maker/core.js). Same phrase + month -> same key.

  python3 derive_keys.py --cookout "your long cookout phrase" [--kitchen "kitchen phrase"] [--month 2026-11]

Prints the check codes and the full-setup link (LongFast + Bleshtastic + Cookout [+ Kitchen]).
Use it to provision radios over USB:  meshtastic --seturl "<link>"
"""
import argparse, base64, datetime, hashlib, re, sys, unicodedata
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "03-channels-and-devices"))
from gen_channels import url, settings, default_longfast  # noqa: E402

ITER = 600_000

def normalize(p):
    p = unicodedata.normalize("NFKC", p).lower()
    p = "".join(c for c in p if unicodedata.category(c)[0] in "LN" or c.isspace())
    return re.sub(r"\s+", " ", p).strip()

def derive(phrase, tier, month):
    return hashlib.pbkdf2_hmac("sha256", normalize(phrase).encode(), f"bleshtastic/v1/{tier}/{month}".encode(), ITER, 32)

def check_code(key):
    h = hashlib.sha256(key).hexdigest()[:6].upper()
    return f"{h[:3]}-{h[3:]}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookout", required=True)
    ap.add_argument("--kitchen")
    ap.add_argument("--month", default=datetime.date.today().strftime("%Y-%m"))
    a = ap.parse_args()
    pub = (Path(__file__).resolve().parent.parent / "03-channels-and-devices/out/block-public.txt").read_text()
    block = base64.b64decode(re.search(r"BLESH_BLOCK_PSK=(\S+)", pub).group(1))
    chans = [default_longfast(), settings("Bleshtastic", block)]
    ck = derive(a.cookout, "cookout", a.month); chans.append(settings("Cookout", ck))
    print(f"Month {a.month}\nCookout check code: {check_code(ck)}")
    if a.kitchen:
        kk = derive(a.kitchen, "kitchen", a.month); chans.append(settings("Kitchen", kk))
        print(f"Kitchen check code: {check_code(kk)}")
    print(url(chans))

if __name__ == "__main__":
    main()
