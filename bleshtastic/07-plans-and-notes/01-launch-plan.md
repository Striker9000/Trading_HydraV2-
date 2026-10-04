# 01 · Launch plan (the deep dive)

**Bleshtastic** is an invite-only community mesh network built on Meshtastic LoRa radios, with a phone-only on-ramp.
Tagline: **Our network. Our info. Even when the towers go down.**

## How it works
```
Radio Mode:  Phone → Bluetooth → Card / Pager / Communicator → LoRa → radios + rooftop Towers
Phone Mode:  Element (Matrix) app → internet → Pi gateway (mmrelay) → radio → the mesh
```
- **Radio Mode** works with no cell service, Wi-Fi or power grid.
- **Phone Mode** lets people with no radio join while the internet is up. Setup is in `04-phone-mode-gateway/`.

## Hardware
| Bleshtastic name | Device | ~Price | For |
|---|---|---|---|
| The Card | Seeed SenseCAP T1000-E | $40 | "I don't want another gadget." Lives in a wallet, phone is the screen |
| The Pager | LilyGo T-LoRa Pager | $80–100 | Elders and simple users. No phone needed |
| The Communicator | LilyGo T-Deck Plus | $80–110 | Power users. Full keyboard, BlackBerry feel |
| The Tower | Solar outdoor node (RAK / Heltec / Seeed) | $80–150 | **What makes the network work** |

**Range:** pocket radios reach about 0.5–2 miles in a city. Rooftop Towers carry it across neighborhoods.
Launch **one neighborhood at a time**: 50 radios + 3–5 Towers in one area beats 500 scattered.

## Getting it out there
- Barbershops and salons (counter card, demo device, Tower on the roof), churches, HBCUs, Greek orgs, block clubs, mutual aid groups.
- Cookouts, Juneteenth, festivals, car meets: anywhere cell service jams. Demo it live.
- Short videos: airplane-mode demos, the Card out of a wallet, the "grandma test." Scripts are in `06-marketing/`.
- Ambassadors (one "plug" per neighborhood) and group buys.

## Translation
1. **Plain language:** never say LoRa, PSK, MQTT or "node" to regular people. Say Card, Pager, Communicator, Tower, Channel, invite.
2. **Other languages:** Spanish, Haitian Creole and French first. The site and marketing already have drafts. **Have native speakers check them.**

## Membership
- You get in because a member vouches for you. A bad invite costs the sponsor their invite privileges.
- The culture, outreach and invite network are Black-centered. **There's no race test written into the rules**: eligibility is "a member vouches for you." That keeps paid device sales clear of civil-rights law problems (Section 1981). Talk to a lawyer before charging membership fees.

## Security reality
- Anyone with a channel's key can read **and post** on it. Treat the Block as public.
- Location sharing is off by default on every device.
- Change private keys when something leaks (see `03-channels-and-devices/` and `02-key-maker/`).
- Alerts only count with an operator tag: `[OP-SLA1 20:42]`.
- Keep MQTT off on community radios unless you run a private broker.

## Legal
- 915 MHz in the US is license-free. Encryption and selling are both fine.
- **Trademark:** "Meshtastic" is a registered trademark. Say "works with Meshtastic®" and "not affiliated with Meshtastic LLC." Read their trademark policy and have a backup name ready in case they ask you to change it.

## 3-weekend build
1. **Weekend 1:** buy hardware → `03-channels-and-devices/gen_channels.py` → flash firmware → `provision.sh` each device.
2. **Weekend 2:** Pi gateway + Matrix rooms → test phone ⇄ radio.
3. **Weekend 3:** mount 3 Towers, publish the site, print cards, give 2–3 shops or churches an ambassador kit.

**Budget for one neighborhood:** about $1,000–1,150 (8 Cards, 2 demo units, 3 Towers, Pi gateway, printing). See the main README.
