# Bleshtastic Gateway (Phone Mode)

Connects the radio mesh to an invite-only Matrix chat room so people without a radio can join in.

```
Element app (phone) ⇄ Matrix homeserver ⇄ mmrelay on Raspberry Pi ⇄ USB radio ⇄ LoRa mesh
```

## Parts (~$80–120)
- Raspberry Pi 4 or 5 (2 GB is plenty), power supply, 32 GB microSD
- One Meshtastic radio provisioned with `provisioning/provision.sh` (a Heltec V3 or RAK4631 works well), USB cable
- Optional: put the gateway radio's antenna high. The gateway is also a Tower.

## 1. Matrix homeserver
Pick one:
- **Fastest:** create the rooms on an existing public homeserver (matrix.org). Free, zero ops. You don't control the server.
- **Better (week 2+):** your own server. Rent a $5–6/mo VPS and install Synapse or Conduwuit with Docker, at a domain like `chat.bleshtastic.org`. You control membership and data.

Create:
| Room | Who can post | Bridged to mesh channel |
|---|---|---|
| `#blesh-block` | Anyone invited | 1 (Bleshtastic) |
| `#blesh-cookout` | Trusted members | 2 (Cookout) |
| *(none)* | | 3 (Kitchen) stays radio-only on purpose |
| `#blesh-alerts` (optional, Matrix-only) | **Operators only** (set "send messages" power level to 50) | not bridged. Operators repost alerts to the Block |

All rooms: **Invite only**, encryption on, history visible to "members since they joined".

Create a bot account (e.g. `@bleshbot`) and invite it to each room.

## 2. Pi setup
```bash
# Raspberry Pi OS Lite, 64-bit, SSH enabled
sudo apt update && sudo apt install -y pipx
pipx ensurepath && exec $SHELL
pipx install mmrelay
mkdir -p ~/.mmrelay && cp config.sample.yaml ~/.mmrelay/config.yaml
nano ~/.mmrelay/config.yaml     # fill in homeserver, bot token, room IDs
mmrelay                         # test in the foreground first
```
Then run it as a service (mmrelay ships an installer for this. See `mmrelay --help`, or use `bleshtastic-gateway.service` in this folder).

mmrelay is under active development. If a config key below doesn't match, use the sample config that ships with the installed version (`mmrelay --generate-config`) as the source of truth.

## 3. Test
1. Post in `#blesh-block` from your phone. It should show on a radio within seconds.
2. Send from a radio. It should show in the room as `[Blesh SLA-0042]: message`.
3. Pull the Pi's internet. Radios should keep talking to each other. Only Phone Mode drops.

## Notes
- Mesh airtime is small. Long Matrix messages get truncated, and floods slow the mesh. Keep the bridge to text only, and use rate limits if they're offered.
- Messages that cross the bridge sit on the Matrix server. That's less private than radio-only, so tell members.
- Keep MQTT **off** on all community radios unless you run a private broker. The public Meshtastic MQTT server would leak encrypted packet metadata (and plaintext if keys leak) to the internet.
