#!/usr/bin/env python3
"""Build the Bleshtastic channel tiers as Meshtastic share links + QR codes.

Tiers (like IG: everyone -> close friends -> inner circle):
  0  LongFast     default public Meshtastic channel. Kept as primary so our radios
                  share a frequency with every other Meshtastic node in the city
                  and get relayed by them.
  1  Bleshtastic  THE BLOCK    public. Key is printed on flyers on purpose.
  2  Cookout      THE COOKOUT  trusted people. You got invited.
  3  Kitchen      THE KITCHEN  close friends. Only the ones you let cook.

Usage:
  python3 gen_channels.py            # uses keys in channels.env, creates them if missing
  python3 gen_channels.py --rotate cookout   # new key for one tier

Outputs in ./out/:
  links.txt                         every link, labeled
  qr/setup-<tier>.svg               full device setup up to that tier (replaces channels)
  qr/add-<channel>.svg              adds just that channel to a radio that's already set up
Requires: pip install meshtastic segno
"""
import base64, os, secrets, sys
from pathlib import Path

import segno
from meshtastic.protobuf import apponly_pb2, channel_pb2, config_pb2

HERE = Path(__file__).resolve().parent
ENV = HERE / "channels.env"
OUT = HERE / "out"

CHANNELS = [  # (env key, channel name <=11 chars, tier label)
    ("BLESH_BLOCK_PSK", "Bleshtastic", "block"),
    ("BLESH_COOKOUT_PSK", "Cookout", "cookout"),
    ("BLESH_KITCHEN_PSK", "Kitchen", "kitchen"),
]


def load_keys(rotate=None):
    keys = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                keys[k.strip()] = v.strip()
    changed = False
    for env_key, _, tier in CHANNELS:
        if env_key not in keys or rotate == tier:
            keys[env_key] = base64.b64encode(secrets.token_bytes(32)).decode()
            changed = True
    if changed:
        ENV.write_text(
            "# Bleshtastic channel keys. Block is public (it's on the flyer).\n"
            "# Cookout and Kitchen are private: never commit, post, or screenshot this file.\n"
            + "".join(f"{k}={keys[k]}\n" for k, _, _ in CHANNELS)
        )
        os.chmod(ENV, 0o600)
    return keys


def lora():
    # Every share link also writes LoRa config to the radio, so it must be complete:
    # an empty config would unset the region and disable transmit.
    c = config_pb2.Config.LoRaConfig()
    c.use_preset = True
    c.modem_preset = config_pb2.Config.LoRaConfig.ModemPreset.LONG_FAST
    c.region = config_pb2.Config.LoRaConfig.RegionCode.US
    c.hop_limit = 3
    c.tx_enabled = True
    return c


def settings(name, psk):
    s = channel_pb2.ChannelSettings()
    s.name = name
    s.psk = psk
    s.module_settings.position_precision = 0  # never share location on this channel
    return s


def default_longfast():
    s = channel_pb2.ChannelSettings()
    s.psk = bytes([1])  # Meshtastic's well-known default key ("AQ==")
    return s


def url(chs, add=False):
    cs = apponly_pb2.ChannelSet()
    cs.settings.extend(chs)
    cs.lora_config.CopyFrom(lora())
    b64 = base64.urlsafe_b64encode(cs.SerializeToString()).decode().rstrip("=")
    return f"https://meshtastic.org/e/{'?add=true' if add else ''}#{b64}"


def main():
    rotate = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--rotate" else None
    keys = load_keys(rotate)
    chans = [settings(name, base64.b64decode(keys[k])) for k, name, _ in CHANNELS]

    (OUT / "qr").mkdir(parents=True, exist_ok=True)
    lines = []

    def emit(fname, label, link):
        segno.make(link, error="m").save(OUT / "qr" / fname, scale=8, border=2, dark="#18142B")
        lines.append(f"{label}\n{link}\n")

    # Full setups (replace whatever is on the radio). Cumulative tiers.
    tiers = [("block", "PUBLIC"), ("cookout", "PRIVATE: trusted"), ("kitchen", "PRIVATE: close friends")]
    for i, (tier, note) in enumerate(tiers):
        emit(f"setup-{tier}.svg",
             f"[{note}] Full setup through {tier.upper()} (LongFast + {', '.join(c.name for c in chans[:i+1])})",
             url([default_longfast()] + chans[: i + 1]))

    # Add-one-channel links (keep everything else on the radio).
    for (k, name, tier), ch in zip(CHANNELS, chans):
        note = "PUBLIC: put this on flyers" if tier == "block" else "PRIVATE: share face to face only"
        emit(f"add-{tier}.svg", f"[{note}] Add channel {name}", url([ch], add=True))

    (OUT / "links.txt").write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"QR codes in {OUT/'qr'}")


if __name__ == "__main__":
    main()
