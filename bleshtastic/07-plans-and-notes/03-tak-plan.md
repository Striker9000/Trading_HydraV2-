# 03 · TAK (Team Awareness Kit) plan

TAK is the live shared map that militaries, firefighters and search-and-rescue teams use: teammates' positions plus markers like "road closed," "medic," "shelter."
For Bleshtastic: a **community map that works over the radios when the internet is down.**

## What's needed
1. **Apps (free):** ATAK-CIV on Android plus the **Meshtastic ATAK plugin**. iTAK exists for iPhone, but check current Meshtastic support before promising it.
2. **Radios:** each TAK user's phone pairs to a radio. Meshtastic has a **TAK** device role and a **TAK Tracker** role. A T1000-E as a tracker is a pocket beacon with no phone needed.
3. **A separate channel** (for example `Ops`, made in the Key Maker). TAK needs location on, so it **never** goes on the Block.
4. **Offline maps:** download the city's map tiles onto every phone ahead of time.
5. **TAK server (optional):** OpenTAK Server on the same Pi as the gateway, for people on the internet and for history. Bridging it to the radios takes extra setup.
6. **Data package:** one file that sets up the app, plus a Bleshtastic icon set (shelter, medical, water, charging, avoid, police).

## Limits
- **Capacity:** about 10–30 people updating every few minutes. A Cookout or Kitchen tool, not a Block tool.
- **Safety:** a live map of your people is the most sensitive thing in the project. Opt-in only, change keys regularly, and a "go dark" rule if a radio is lost or taken.
- **Training:** one hands-on session per group.

## First test
3 Android phones + ATAK + the plugin, 3 T1000s on an `Ops` channel, an offline map of one neighborhood.
Walk around with cell data off and confirm everyone's dots and markers show up.
