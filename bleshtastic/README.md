# Bleshtastic

**Our network. Our info. Even when the towers go down.**
An invite-only community mesh network built on [Meshtastic](https://meshtastic.org) LoRa radios, with a phone-only on-ramp.

| Folder | What's in it |
|---|---|
| `site/` | Landing page (EN / ES / Kreyòl / FR), with an invite-request message generator |
| `provisioning/` | The three channel tiers (Block / Cookout / Kitchen), QR codes, the device setup script, key rotation |
| `gateway/` | Raspberry Pi + mmrelay setup that bridges the mesh to an invite-only Matrix room (Phone Mode) |
| `print/` | Print-ready flyer (8.5×11) and two-sided setup card (6×4) PDFs, with the working join QR. `python3 build.py` rebuilds them |
| `marketing/` | Taglines, flyers, setup card, video scripts, alert templates, outreach plan, ambassador kit |

## v0.1 shopping list (one neighborhood)
| Item | Qty | Approx. |
|---|---|---|
| SenseCAP T1000-E (the Card) | 8 | $320 |
| LilyGo T-Deck Plus or T-LoRa Pager (demo units) | 2 | $200 |
| Outdoor Tower: RAK WisMesh / Heltec board + enclosure + solar + antenna | 3 | $300–450 |
| Raspberry Pi 4/5 + radio (gateway) | 1 | $100 |
| VPS for Matrix (optional at first) | 1 | $6/mo |
| Printing (counter cards, setup cards) | — | $50 |
| **Total** | | **~$1,000–1,150** |

Prices are rough. Check current listings before ordering.

## 3-weekend build
1. **Weekend 1:** order hardware, then `provisioning/make-keys.sh`, flash firmware, `provision.sh` every device.
2. **Weekend 2:** Pi gateway + Matrix rooms (`gateway/README.md`). Test phone ⇄ radio.
3. **Weekend 3:** mount 3 Towers, publish the landing page, print cards, hand ambassador kits to 2–3 shops or churches.

Then: demos at every gathering, first 25–50 members, and short videos from real use.

## Ground rules we're building in
- **Membership is by vouching.** A member sponsors every new person. Bad invites cost the sponsor their invite privileges.
- **No race test.** The culture, outreach and invite network are Black-centered. Eligibility is "a member vouches for you." This also keeps paid device sales clear of civil-rights law problems. Talk to a lawyer before charging membership fees.
- **Privacy defaults:** GPS off, MQTT off, keys rotated, QR codes shared only in person.
- **Alerts:** operator-tagged, time-stamped, read-only on the Matrix side. Rumor checks are part of the job.
- **Trademark:** say "works with Meshtastic®" and "not affiliated with Meshtastic LLC". Read their trademark policy and have a backup name ready if they ask you to change it.

## Still to decide
- Domain (`bleshtastic.org`? `.com`?), plus social handles. Grab them now.
- First city and neighborhood.
- Selling devices: drop-ship plus provisioning at a meetup, or stock inventory?
- A nonprofit, LLC, or informal group? This affects donations, liability for rooftop Towers, and sales.
