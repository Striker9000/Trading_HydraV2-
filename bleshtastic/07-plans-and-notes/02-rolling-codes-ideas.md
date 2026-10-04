# 02 · Rolling codes: all the ideas

Meshtastic channels have one fixed key until someone changes it, and the radios can't run anything like Google Authenticator.
Every idea below is really a different way for people to **get the current key**, and to **stop getting it** once they're cut off.

| # | Idea | Works offline? | Status |
|---|---|---|---|
| 1 | **Scheduled rotation:** new key on the 1st, show the new QR in person | Yes | Ready: `gen_channels.py --rotate cookout` |
| 2 | **Google Authenticator door:** enter your 6-digit code on a private page or bot to get the current QR | No | Idea |
| 3 | **Remote admin push:** you push new keys to friends' radios over the mesh (firmware 2.5+ admin keys). They're trusting you with control of their device | Yes | Idea. Test on 2 T1000s first |
| 4 | **Rolling phrase:** phrase + month → key, rolls over automatically | Yes | **BUILT:** `02-key-maker/` |
| 5 | **Riddle website:** the right answer unlocks the QR, new riddle every month | No | Idea |
| 6 | **Hidden in a picture:** key hidden inside a normal photo, opened with your phrase. Social apps destroy hidden data, so share the original file | Depends | Idea |
| 7 | **Radio DM bot:** on the 1st, a bot sends each trusted radio the new key in an encrypted direct message. Take someone off the list to cut them off | Yes | Idea. Strong pairing with #4 |
| 8 | **NFC tap:** a 50¢ tag in a sticker, keychain or wristband opens the channel link | Yes | Idea. Great for Block stickers |
| 9 | **Two-person unlock:** the Kitchen key is split so any 2 of 3 trusted people must combine their pieces | Yes | Idea |
| 10 | **Scratch-offs at events:** next month's QR under a scratch-off, only at events | Yes | Idea |

## Recommended combo
- **Cookout:** rolling phrase (#4). The "only we would know" idea finally works as a real lock, as long as the phrase is long.
- **Kitchen:** radio DM bot (#7). No internet needed, instant cut-off, and nobody hands over control of their device.
- **Block:** NFC stickers (#8), just for fun and marketing.

## ⚠️ Decision needed
The Key Maker **replaces** the fixed Cookout and Kitchen keys from `03-channels-and-devices/`.
Pick one system per channel. Once someone scans a Key Maker code, the old private QR codes won't match them anymore.
