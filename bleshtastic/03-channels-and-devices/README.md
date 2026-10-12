# Channels & device setup

## The three tiers
Like Instagram: everybody → close friends → inner circle.

| Slot | Channel | Tier | Who | Key |
|---|---|---|---|---|
| 0 | LongFast | Meshtastic default | Every Meshtastic user in the city | Public default |
| 1 | **Bleshtastic** | **The Block** | Everybody we want on. The one we promote | Public: it's on the flyer QR |
| 2 | **Cookout** | **The Cookout** | Trusted people. You got invited | Private: face to face only |
| 3 | **Kitchen** | **The Kitchen** | Close friends. Only the ones you let cook | Private: you hand it out personally |

**Why LongFast stays in slot 0:** a radio's primary channel name picks its frequency.
If Bleshtastic were primary, our radios would sit on a different frequency from every other Meshtastic node in the city and wouldn't get relayed by them.
Keeping LongFast primary means every existing node and tower helps carry our encrypted messages, and our people also see the wider Meshtastic community.

Location sharing is off on every channel.

## Generate keys, links and QR codes
```bash
pip install meshtastic segno
python3 gen_channels.py                 # makes channels.env + out/links.txt + out/qr/*.svg
python3 gen_channels.py --rotate kitchen   # new Kitchen key (someone got cut off / lost a device)
```
| File | Use |
|---|---|
| `out/qr/setup-block.svg` | Full setup for a public device. **Replaces** all channels |
| `out/qr/setup-cookout.svg` | Full setup, trusted device |
| `out/qr/setup-kitchen.svg` | Full setup, your own / close-friends devices |
| `out/qr/add-block.svg` | **Adds** Bleshtastic to any Meshtastic radio. **This goes on the flyer** |
| `out/qr/add-cookout.svg` | Adds Cookout. Show it on your phone and let them scan, in person |
| `out/qr/add-kitchen.svg` | Adds Kitchen. Same rule, tighter circle |

## Add the channels to a radio that already has channels (keeps everything else)
Radio plugged into a laptop by USB. Close the Meshtastic app first or disconnect it from the radio.
```bash
pip install meshtastic
python3 add_channels.py --dry-run     # shows exactly what it would do
python3 add_channels.py               # adds Bleshtastic + Cookout + Kitchen to empty slots
```
- Only fills **empty** slots. Never edits or removes your other channels, and never changes region or preset.
- Saves a restore backup in `backups/` first (private, since it holds keys).
- Already there with the right key? Skipped. Same name with a different key? Left alone and reported.
- Windows: add `--port COM5` (check Device Manager). Over Bluetooth: `--ble "<radio name>"`.

## Put the channels on a T1000-E (no laptop needed)
1. Charge the T1000-E. Press and hold the button until it beeps on.
2. Phone: open the **Meshtastic** app → connect the T1000 over Bluetooth (default PIN `123456`).
3. Open the QR image (`setup-kitchen.svg` for your own) on another screen. In the app: **Channels → scan QR** (on iPhone, scan with the camera, which opens the app).
4. Accept. The radio reboots with LongFast + Bleshtastic + Cookout + Kitchen.
5. In the app: **Settings → Position → GPS mode: Disabled**, and set the device name to something like `Blesh SLA-0001`.

## Or over USB, in batches
```bash
./provision.sh kitchen SLA 0001     # yours
./provision.sh block   SLA 0042     # public devices
```

## Rotating keys
When a private key leaks (a lost phone, someone you had to cut off):
1. `python3 gen_channels.py --rotate cookout` (or `kitchen`)
2. Re-share the new **add** QR in person with everyone who should still be in.
3. Anyone not re-added just stops seeing new messages.

Rotate Kitchen and Cookout every few months anyway. Bleshtastic is public, so it doesn't need rotating unless people abuse it.

## What privacy each tier actually gets
- **Bleshtastic** is encrypted, but its key is public, so treat it like a public group chat.
- **Cookout/Kitchen** can only be read by radios with the key. The weak point is people: one screenshot of the QR and the key is out.
- Anyone with a channel's key can also post in it. Meshtastic can't make a channel read-only.
- For one-on-one messages, Meshtastic direct messages are encrypted to that one person (firmware 2.5+).
