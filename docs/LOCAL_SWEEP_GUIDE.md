# Running the Sweep Optimizer on Your Local Machine

## What You Need

- **Python 3.10+** installed
- **Your Alpaca API keys** (paper trading keys work fine)
- About **2-4 GB of RAM** (depends on combo count and symbol count)
- Patience — 800 combos × 20 symbols × 60 days takes ~30-60 minutes on a modern machine

---

## Step 1: Download the Files

You only need **one file**: `scripts/dynamic_sweep.py`

It's fully self-contained — no other project files are needed.

### Option A: Download from Replit
1. In Replit, click on `scripts/dynamic_sweep.py` in the file tree
2. Click the three dots (⋯) → **Download**
3. Save it somewhere on your machine (e.g., `~/trading-sweep/dynamic_sweep.py`)

### Option B: Copy-paste
1. Open `scripts/dynamic_sweep.py` in Replit
2. Select All → Copy
3. Create a new file on your machine and paste it in

---

## Step 2: Install Python (if you don't have it)

### On Windows:
1. Go to **https://www.python.org/downloads/**
2. Download Python 3.10 or newer
3. **IMPORTANT:** During install, check the box that says **"Add Python to PATH"**
4. Click "Install Now"
5. To verify, open Command Prompt and type: `python --version`

### On Mac:
```bash
brew install python
```
Or download from https://www.python.org/downloads/

### On Linux:
```bash
sudo apt install python3 python3-pip
```

---

## Step 3: Install Dependencies

Open a terminal (or Command Prompt / PowerShell on Windows) and run:

```bash
pip install alpaca-py
```

That's the only dependency. Everything else uses Python standard library.

> **Windows note:** If `pip` isn't recognized, try `python -m pip install alpaca-py` instead.

---

## Step 4: Set Your Alpaca API Keys

### On Mac/Linux:
```bash
export APCA_API_KEY_ID="your-key-here"
export APCA_API_SECRET_KEY="your-secret-here"
```

### On Windows (Command Prompt):
```cmd
set APCA_API_KEY_ID=your-key-here
set APCA_API_SECRET_KEY=your-secret-here
```

### On Windows (PowerShell):
```powershell
$env:APCA_API_KEY_ID="your-key-here"
$env:APCA_API_SECRET_KEY="your-secret-here"
```

> **Tip:** These are the same keys you use for the trading bot. Paper trading keys are fine — the sweep only reads historical market data, it never places trades.

---

## Step 5: Run the Sweep

### Create the results folder first:

**Mac/Linux:**
```bash
mkdir -p results
```

**Windows (Command Prompt):**
```cmd
mkdir results
```

**Windows (PowerShell):**
```powershell
New-Item -ItemType Directory -Force -Path results
```

### The Big Run (recommended):
```bash
python dynamic_sweep.py --mode ultra --max-combos 800 --days 60
```

### Other modes you can try:

| Mode | Parameters | What It Tests |
|------|-----------|---------------|
| `ultra` | 131 params | Everything including S/R detection |
| `mega` | 28 params | Core entry/exit/risk params |
| `freeroll` | 48 params | Loosened params for house-money sessions |
| `quick` | 10 params | Fast sanity check (~2 min) |
| `focused` | 10 params | Narrow ranges around current config |

### Examples:
```bash
# The big one — 800 combos, 60 days (30-60 min)
python dynamic_sweep.py --mode ultra --max-combos 800 --days 60

# Mega sweep — 500 combos, 60 days (~15-20 min)
python dynamic_sweep.py --mode mega --max-combos 500 --days 60

# Freeroll sweep — 300 combos, 60 days (~10 min)
python dynamic_sweep.py --mode freeroll --max-combos 300 --days 60

# Quick test run to make sure it works (~2 min)
python dynamic_sweep.py --mode quick --max-combos 10 --days 30
```

### What to expect (Horse Race Mode!):
The sweep runs as an interactive horse race in your terminal:
- Named horses (Thunderbolt, Midnight Run, Gold Rush...) race across ASCII progress bars
- Live leaderboard updates showing profit, position, and ETA countdown
- Quarter/Half/Three-quarter pole announcements with the current leader
- Commentary with reasoning like: *"Rocket takes the lead! Why? gap floor 0.5% vs 3.0% — tighter gap filter"*
- Struggling configs get called out: *"Iron Will is bleeding money — confirmation 1 bar vs 5 — too trigger-happy"*
- Finish line trophy with champion stats and runner-up comparison

> **Windows note on colors:** The horse race uses terminal colors. Windows 10/11 Command Prompt and PowerShell support them natively. If you see weird characters like `[92m` instead of colors, run this first in PowerShell: `[Console]::OutputEncoding = [Text.Encoding]::UTF8` or use **Windows Terminal** (free from the Microsoft Store) which has full color support.

---

## Step 6: Find Your Results

When it finishes, results are saved to:

```
results/dynamic_sweep_results.json
```

This file contains:
- **Top 20 configs** ranked by total profit
- **Kill factor analysis** showing which parameters matter most
- **Metadata** (date, mode, symbols tested, time elapsed)

---

## Step 7: Bring Results Back to Replit

### Option A: Upload the JSON file (easiest)
1. In Replit, navigate to the `results/` folder
2. Click the three dots (⋯) → **Upload file**
3. Upload your `dynamic_sweep_results.json` (it will overwrite the old one)
4. Tell me: **"I uploaded new sweep results, please apply the winner"**

### Option B: Paste the winning config
1. Open `results/dynamic_sweep_results.json` on your machine
2. Find the **#1 ranked config** — look for `"rank": 1`
3. Copy the entire `"params"` block for that config
4. Paste it to me in chat and say **"Apply this config"**

Here's what the winning config looks like in the JSON:

```json
{
  "rank": 1,
  "params": {
    "min_gap_pct": 2.0,
    "max_gap_pct": 10.0,
    "confirmation_bars": 7,
    "require_vwap_position": true,
    "time_stop_minutes": 30,
    ...
  },
  "metrics": {
    "total_profit": 3396.50,
    "win_rate": 0.54,
    "total_trades": 198,
    ...
  }
}
```

### Option C: Paste just the key numbers
If the JSON is too big, just tell me these numbers from the #1 config:

- **Total profit**: $___
- **Win rate**: ___%
- **Total trades**: ___
- **min_gap_pct**: ___
- **max_gap_pct**: ___
- **confirmation_bars**: ___
- **time_stop_minutes**: ___
- **max_trades_per_day**: ___
- **hard_stop_pct**: ___
- **time_based_exit_pct**: ___
- **rsi_overbought**: ___
- **rsi_oversold**: ___

And I'll update the configs for you.

---

## What I'll Do With the Results

Once you give me the results, I will:

1. **Review the winning config** — make sure it's sensible (not overfitted)
2. **Apply it to `config/bots.yaml` and `config/settings.yaml`** — update all relevant parameters
3. **Save it as the new "proven" profile** in the config switcher
4. **Keep the old proven config as a backup** in `config/profiles/`

---

## Troubleshooting

### "alpaca-py not installed"
```bash
pip install alpaca-py
```
On Windows, if `pip` doesn't work: `python -m pip install alpaca-py`

### "python is not recognized" (Windows)
Python isn't in your PATH. Either:
- Reinstall Python and check **"Add Python to PATH"** during install
- Or use the full path: `C:\Users\YourName\AppData\Local\Programs\Python\Python312\python.exe dynamic_sweep.py ...`

### "APCA_API_KEY_ID not set"
Make sure you set the environment variables in the **same terminal window** you're running the script from. They don't persist across terminal windows.

**Windows gotcha:** If you close and reopen Command Prompt, you need to `set` the keys again. To make them permanent on Windows:
1. Search "Environment Variables" in the Start menu
2. Click "Edit the system environment variables"
3. Click "Environment Variables" button
4. Under "User variables", click "New"
5. Add `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` with your values

### "No data returned for symbols"
Your API keys might be for paper trading only. Paper keys can still fetch historical data, but make sure they're valid. Try:
```bash
python -c "from alpaca.data.historical import StockHistoricalDataClient; import os; client = StockHistoricalDataClient(os.environ['APCA_API_KEY_ID'], os.environ['APCA_API_SECRET_KEY']); print('Connection OK')"
```

### Horse race shows weird characters instead of colors (Windows)
Use **Windows Terminal** (free from Microsoft Store) instead of the old Command Prompt. Or in PowerShell, run:
```powershell
[Console]::OutputEncoding = [Text.Encoding]::UTF8
```

### Emojis not showing (Windows)
The horse race uses emojis (horses, trophies, etc). If they show as boxes or question marks:
- Use **Windows Terminal** (supports emojis natively)
- Or use **VS Code's integrated terminal**
- The race still works without emojis — the data is all there

### It's running slow
- Reduce `--max-combos` (try 200-400 instead of 800)
- Reduce `--days` (try 30 instead of 60)
- The `mega` mode (28 params) is much faster than `ultra` (131 params)

### Memory error
- Reduce `--days` to 30
- The script processes one symbol at a time, so memory shouldn't be an issue unless your machine has < 2GB RAM

---

## Quick Reference

### Mac/Linux:
```bash
# Setup (one time)
pip install alpaca-py

# Set keys (every terminal session)
export APCA_API_KEY_ID="your-key"
export APCA_API_SECRET_KEY="your-secret"

# Create results folder
mkdir -p results

# Run the big sweep
python dynamic_sweep.py --mode ultra --max-combos 800 --days 60

# Results will be in: results/dynamic_sweep_results.json
# Upload that file back to Replit → results/ folder
```

### Windows (Command Prompt):
```cmd
REM Setup (one time)
pip install alpaca-py

REM Set keys (every terminal session)
set APCA_API_KEY_ID=your-key
set APCA_API_SECRET_KEY=your-secret

REM Create results folder
mkdir results

REM Run the big sweep
python dynamic_sweep.py --mode ultra --max-combos 800 --days 60

REM Results will be in: results\dynamic_sweep_results.json
REM Upload that file back to Replit → results/ folder
```

### Windows (PowerShell):
```powershell
# Setup (one time)
pip install alpaca-py

# Set keys (every terminal session)
$env:APCA_API_KEY_ID="your-key"
$env:APCA_API_SECRET_KEY="your-secret"

# Create results folder
New-Item -ItemType Directory -Force -Path results

# Run the big sweep
python dynamic_sweep.py --mode ultra --max-combos 800 --days 60

# Results will be in: results\dynamic_sweep_results.json
# Upload that file back to Replit → results/ folder
```
