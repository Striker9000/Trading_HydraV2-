# Sound Effects for Streaming Dashboard

## How to Add Custom Sounds

Place your custom sound files in this directory. Supported formats: MP3, WAV, OGG

### Sound Types and Files

1. **Signal Sounds** (New trade opportunity detected)
   - `chime.mp3` - Default chime sound
   - `bell.mp3` - Bell notification
   - `ding.mp3` - Quick ding

2. **Entry Sounds** (Position entered)
   - `click.mp3` - Click sound
   - `pop.mp3` - Pop sound
   - `whoosh.mp3` - Whoosh transition

3. **Profit Sounds** (Target hit, winning trade)
   - `kaching.mp3` - Cash register sound
   - `success.mp3` - Success fanfare
   - `coins.mp3` - Coin drop sound

4. **Stop Loss Sounds** (Trade stopped out)
   - `buzz.mp3` - Error buzz
   - `error.mp3` - Error tone
   - `thud.mp3` - Thud sound

## Where to Get Free Sounds

### Recommended Sites (Royalty-Free):
- **Freesound.org** - https://freesound.org/
- **Zapsplat** - https://www.zapsplat.com/
- **Mixkit** - https://mixkit.co/free-sound-effects/
- **BBC Sound Effects** - https://sound-effects.bbcrewind.co.uk/

### Search Terms:
- "notification chime"
- "button click"
- "cash register"
- "success jingle"
- "error buzz"
- "coins drop"

## Creating Your Own Sounds

You can also record or create custom sounds using:
- **Audacity** (Free audio editor)
- **GarageBand** (Mac)
- **Online tone generators**

Keep sounds:
- **Short** (0.5-2 seconds)
- **Clear** (no background noise)
- **Appropriate volume** (not too loud)

## Default Sounds

If you don't add custom sounds, the dashboard will use browser default sounds or silence.

To enable sounds:
1. Add `.mp3` files to this directory
2. Name them according to the list above
3. Reload the streaming dashboard
4. Test sounds in Settings panel

## Custom Sound Names

To use different filenames, edit the `SOUND_FILES` mapping in:
`streaming.js` (around line 20)

Example:
```javascript
const SOUND_FILES = {
    signal: {
        chime: '/static/sounds/my_custom_chime.mp3',
        // ... etc
    }
};
```
