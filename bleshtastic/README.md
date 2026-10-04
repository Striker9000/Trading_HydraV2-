# BLESHTASTIC: start here

**Our network. Our info. Even when the towers go down.**
An invite-only community mesh network built on Meshtastic® LoRa radios, with a phone-only on-ramp.
Independent community project, not affiliated with Meshtastic LLC.

## What's in this folder
| Folder | What it is | Open first |
|---|---|---|
| `01-website/` | Landing page: device lineup, Phone/Radio modes, invite-request message, EN/ES/Kreyòl/FR | `index.html` |
| `02-key-maker/` | Rolling phrase keys: phrase + month → that month's Cookout/Kitchen keys + setup QR | `bleshtastic-keymaker-offline.html` (save to your phone) |
| `03-channels-and-devices/` | The channel tiers, QR codes, USB setup script, key rotation | `README.md` |
| `04-phone-mode-gateway/` | Raspberry Pi + mmrelay bridge to invite-only Matrix chat rooms | `README.md` |
| `05-print-designs/` | Flyer (8.5×11), two-sided setup card (6×4), 3×3 sticker. PDFs and PNGs ready to print | `out/` |
| `06-marketing/` | Taglines, flyer copy, video scripts, alert templates, outreach plan, **Higgsfield AI ad scripts** | `higgsfield-ads.md` |
| `07-plans-and-notes/` | Launch plan, rolling-code ideas, TAK plan, product line/reselling, **open decisions & to-do** | `05-open-decisions-and-todo.md` |

## The channels
| Slot | Channel | Tier | Who |
|---|---|---|---|
| 0 | LongFast | Meshtastic default | Everyone on Meshtastic (keeps us on the same frequency so every node relays us) |
| 1 | **Bleshtastic** | **The Block** | Public. The flyer/sticker QR |
| 2 | **Cookout** | **The Cookout** | Trusted. You got invited |
| 3 | **Kitchen** | **The Kitchen** | Inner circle. Not everybody at the cookout gets to cook |

Location sharing is off on every channel.

## Published pages (private until you share them)
- Landing page: https://claude.ai/artifact/RgjR5WanVo2UzCyKBjZHt5
- Key Maker: https://claude.ai/artifact/DxgZRQgqicsA4sRjVVA2qo

## 🔒 Private files: NOT in git
These hold the Cookout and Kitchen keys. They're in the **bleshtastic-COMPLETE.zip** download and are git-ignored:
- `03-channels-and-devices/channels.env`: the keys themselves
- `03-channels-and-devices/out/links.txt`: every setup link, including private ones
- `03-channels-and-devices/out/qr/*cookout*`, `*kitchen*`, `*Cookout*`, `*Kitchen*`: private QR codes

On a new computer, copy `channels.env` from the zip into `03-channels-and-devices/`, then run `python3 gen_channels.py` to rebuild the private QR codes.
**Never** commit, post, text or screenshot those files.

## Rebuild commands
```bash
pip install meshtastic segno            # once
cd 03-channels-and-devices && python3 gen_channels.py      # QR codes + links (+ block-public.txt)
cd 02-key-maker && python3 build.py                         # rebuild Key Maker pages
cd 05-print-designs && python3 build.py                     # rebuild flyer/card/sticker (needs Node + Playwright)
./03-channels-and-devices/provision.sh kitchen SLA 0001     # set up a radio over USB
```

## v0.1 shopping list (one neighborhood)
| Item | Qty | Approx. |
|---|---|---|
| SenseCAP T1000-E (the Card) | 8 | $320 |
| LilyGo T-Deck Plus / T-LoRa Pager (demo units) | 2 | $200 |
| Outdoor solar Tower | 3 | $300–450 |
| Raspberry Pi 4/5 + radio (gateway) | 1 | $100 |
| Printing | — | $50 |
| **Total** | | **~$1,000–1,150** |

Prices are rough. Check current listings.

## Status
Everything here was built and checked in software: the QR codes scan to the right links, and the Key Maker page and Python script give identical keys.
**Nothing has been tested on real radios yet.** Start with the test list in `07-plans-and-notes/05-open-decisions-and-todo.md`.
