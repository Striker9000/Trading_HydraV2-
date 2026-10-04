# Key Maker (rolling phrase keys)

A phrase + the month → that month's Cookout / Kitchen keys → one setup QR for the radio.
Everyone who knows the phrase gets the new key automatically on the 1st. Nobody has to send anything.

| File | What it is |
|---|---|
| `index.html` | Web version (published as an Artifact) |
| `bleshtastic-keymaker-offline.html` | Single file. Save it to your phone, works with no internet |
| `derive_keys.py` | Same math in Python, for setting up radios over USB: `meshtastic --seturl "$(python3 derive_keys.py --cookout '...' | tail -1)"` |
| `core.js` | The key math, shared by both pages |

## The math (v1)
- `phrase` = lowercase, letters/digits/spaces only, single spaces
- `key` = PBKDF2-SHA256(phrase, salt = `bleshtastic/v1/<cookout|kitchen>/<YYYY-MM>`, 600,000 rounds, 32 bytes)
- `check code` = first 3 bytes of SHA-256(key). Safe to say out loud to confirm two people match

The browser and Python versions were checked against each other: same phrase and month give byte-identical setup links.

## Rules for phrases
- A whole sentence, 6+ words, with something personal or odd in it. Not a famous lyric or quote.
- Say it in person. Never text it or write it on anything that gets photographed.
- Cookout and Kitchen phrases must be different.
- To cut someone off, pick a new phrase and tell only the people who are still in. The old one keeps working until the end of the month, so for an urgent cut, switch everyone to the new phrase *now* (pick "this month" in the page).

## The trade-off
Anyone who learns the phrase can make **every future month's key** until the phrase changes.
The monthly rollover protects against old leaked keys, not against a leaked phrase.
