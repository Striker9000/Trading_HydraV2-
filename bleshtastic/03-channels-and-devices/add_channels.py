#!/usr/bin/env python3
"""Add the Bleshtastic channels to a radio WITHOUT touching anything already on it.

- Only fills empty channel slots. Existing channels (yours, LongFast, anything) are left alone.
- Does not change region, modem preset or any other radio setting.
- Saves a backup of the radio's current channels before writing.
- Skips a channel that's already there with the same key. Refuses to overwrite one with the
  same name but a different key, unless you pass --replace-same-name.

Usage (radio plugged in by USB, Meshtastic app disconnected from it):
  pip install meshtastic
  python3 add_channels.py                    # auto-find the radio, add Block + Cookout + Kitchen
  python3 add_channels.py --dry-run          # show what would change, write nothing
  python3 add_channels.py --tiers block      # only the public Block
  python3 add_channels.py --port COM5        # Windows / pick a port
  python3 add_channels.py --ble "T1000-E_abcd"   # over Bluetooth instead of USB

Keys come from channels.env next to this file (private, not in git).
"""
import argparse, base64, datetime, json, re, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TIERS = {  # tier -> (channel name, env key)
    "block": ("Bleshtastic", "BLESH_BLOCK_PSK"),
    "cookout": ("Cookout", "BLESH_COOKOUT_PSK"),
    "kitchen": ("Kitchen", "BLESH_KITCHEN_PSK"),
}


def load_keys():
    env = HERE / "channels.env"
    if not env.exists():
        sys.exit(f"Missing {env}. Copy channels.env from bleshtastic-COMPLETE.zip into this folder.")
    keys = dict(re.findall(r"^(\w+)=(\S+)$", env.read_text(), re.M))
    return {t: base64.b64decode(keys[k]) for t, (_, k) in TIERS.items() if k in keys}


def plan(channels, wanted, replace_same_name=False):
    """Decide what to do for each wanted channel. Pure function (unit-tested).

    channels: list of objects with .index, .role (0=DISABLED,1=PRIMARY,2=SECONDARY), .settings.name, .settings.psk
    wanted:   list of (name, psk bytes)
    returns:  list of (action, name, slot_index_or_None, note)
    """
    free = [c.index for c in channels if c.role == 0]
    actions = []
    for name, psk in wanted:
        existing = next((c for c in channels if c.role != 0 and c.settings.name == name), None)
        if existing is not None:
            if bytes(existing.settings.psk) == psk:
                actions.append(("skip", name, existing.index, "already on the radio with the right key"))
            elif replace_same_name:
                actions.append(("replace", name, existing.index, "same name, different key: replacing key"))
            else:
                actions.append(("conflict", name, existing.index,
                                "a channel with this name exists with a DIFFERENT key; left alone "
                                "(rerun with --replace-same-name to overwrite it)"))
        elif not free:
            actions.append(("nospace", name, None, "no empty slots left (radios hold 8 channels)"))
        else:
            actions.append(("add", name, free.pop(0), "adding to empty slot"))
    return actions


def channel_table(node):
    rows = []
    for c in node.channels:
        if c.role == 0:
            continue
        name = c.settings.name or "(default) LongFast"
        role = {1: "PRIMARY", 2: "SECONDARY"}.get(c.role, str(c.role))
        rows.append(f"  slot {c.index}: {name:<14} {role}")
    return "\n".join(rows)


def connect(a):
    if a.ble:
        from meshtastic.ble_interface import BLEInterface
        return BLEInterface(a.ble)
    if a.host:
        from meshtastic.tcp_interface import TCPInterface
        return TCPInterface(a.host)
    from meshtastic.serial_interface import SerialInterface
    return SerialInterface(a.port)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port (default: auto-detect)")
    ap.add_argument("--ble", help="Bluetooth name or address instead of USB")
    ap.add_argument("--host", help="IP address for Wi-Fi/ethernet radios")
    ap.add_argument("--tiers", default="block,cookout,kitchen", help="comma list: block,cookout,kitchen")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    ap.add_argument("--replace-same-name", action="store_true")
    a = ap.parse_args()

    tiers = [t.strip() for t in a.tiers.split(",") if t.strip()]
    bad = [t for t in tiers if t not in TIERS]
    if bad:
        sys.exit(f"Unknown tier(s): {bad}. Use block, cookout, kitchen.")
    keys = load_keys()
    missing = [t for t in tiers if t not in keys]
    if missing:
        sys.exit(f"channels.env has no key for: {missing}")
    wanted = [(TIERS[t][0], keys[t]) for t in tiers]

    from meshtastic.protobuf import channel_pb2
    print("Connecting to the radio...")
    iface = connect(a)
    try:
        node = iface.localNode
        owner = (iface.getLongName() or "radio").replace(" ", "_")
        print(f"Connected: {iface.getLongName()}\n\nChannels on it now:\n{channel_table(node)}")

        lora = node.localConfig.lora
        if lora.region != 1 or lora.modem_preset != 0:
            print("\nNote: this radio is not on US / LongFast. Its other settings are being left alone,"
                  "\nbut it will only talk to Bleshtastic radios on the same region and preset.")

        actions = plan(node.channels, wanted, a.replace_same_name)
        print("\nPlan:")
        for act, name, slot, note in actions:
            where = f"slot {slot}" if slot is not None else "-"
            print(f"  {act.upper():<9} {name:<12} {where:<8} {note}")
        todo = [x for x in actions if x[0] in ("add", "replace")]
        if not todo:
            print("\nNothing to write.")
            return
        if a.dry_run:
            print("\nDry run: nothing written.")
            return

        # Backup first: a URL that restores the exact current channels + LoRa settings.
        bdir = HERE / "backups"; bdir.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bfile = bdir / f"{owner}-{stamp}.json"
        bfile.write_text(json.dumps({
            "radio": iface.getLongName(),
            "taken": stamp,
            "restore_with": "meshtastic --seturl \"<url>\"   (replaces channels with exactly these)",
            "url": node.getURL(includeAll=True),
        }, indent=2))
        print(f"\nBackup saved: {bfile}  (private: it contains your channel keys)")

        psk_by_name = dict(wanted)
        for act, name, slot, _ in todo:
            ch = node.channels[slot]
            if act == "add":
                ch.Clear()
                ch.index = slot
                ch.role = channel_pb2.Channel.Role.SECONDARY
                ch.settings.name = name
            ch.settings.psk = psk_by_name[name]
            ch.settings.module_settings.position_precision = 0   # never share location here
            print(f"Writing {name} to slot {slot}...")
            node.writeChannel(slot)
            time.sleep(2)
    finally:
        iface.close()

    print("\nChecking the radio...")
    time.sleep(6)
    iface = connect(a)
    try:
        node = iface.localNode
        print(f"Channels on it now:\n{channel_table(node)}")
        ok = all(any(c.role != 0 and c.settings.name == n and bytes(c.settings.psk) == k for c in node.channels)
                 for n, k in wanted)
        print("\nDONE: all Bleshtastic channels are on the radio." if ok
              else "\nSomething didn't stick. Run it again, or check the channel list in the app.")
    finally:
        iface.close()


if __name__ == "__main__":
    main()
