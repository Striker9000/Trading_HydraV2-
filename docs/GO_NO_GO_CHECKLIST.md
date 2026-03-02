# Go/No-Go Checklist ... Trading_Hydra (Printable)

Print this page. Treat it like a pre-flight checklist. If any No-Go item is true... you do not go live.

## Identity
- Date: ______________________
- Environment:  Paper / Live (circle one)
- Account equity: $______________________
- Build / commit / tag: ______________________________
- Operator: ______________________________

---

## No-Go ... immediate stop

### Safety and kill-switches
- [ ] Daily max loss kill-switch is enabled and value is correct
- [ ] Global halt flag works (can be triggered and stays latched)
- [ ] ExitBot runs every loop and can flatten positions
- [ ] API failure or auth failure triggers halt
- [ ] Stale data triggers halt

### Data integrity
- [ ] Quote freshness guard is consistent with cache settings
- [ ] Bar feed is complete (no missing candles during trade window)
- [ ] Timezone and session rules are correct (RTH for stocks/options)

### Execution controls
- [ ] Execution enforces spread gate at order time (all assets)
- [ ] Execution enforces min liquidity rules (volume, OI, etc)
- [ ] Slippage assumptions are defined and conservative
- [ ] Duplicate order guard is enabled (no loop-spam)

### Risk limits
- [ ] Per-trade risk caps are configured for day/swing/long
- [ ] Total open risk cap is configured and enforced
- [ ] Max positions per strategy is configured and enforced
- [ ] Concentration / correlation guard is enabled
- [ ] Options max trades per day is sane (<= 10 until proven)

### Testability
- [ ] Tests run clean on a fresh environment (pip install then pytest)
- [ ] Dependencies are pinned or locked (reproducible installs)
- [ ] Mock mode can run end-to-end without broker calls

### Observability
- [ ] Decision Record log is emitted per symbol per loop
- [ ] You can answer: why did it trade... why did it not trade
- [ ] Logs are stored and rotated safely (no disk fill)

If any checkbox above cannot be confirmed... NO-GO.

---

## Go ... required confirmations

### Pre-market / pre-session
- [ ] You reviewed open positions and intended exposure
- [ ] You reviewed scheduled events that could change volatility (earnings, Fed, CPI)
- [ ] You confirmed market is open for the asset class you are running

### Dry run
- [ ] System runs for 10 minutes in paper with no errors
- [ ] Quotes update at expected rate
- [ ] A forced halt test was performed successfully

### Live conditions
- [ ] Position sizing is appropriate for equity and volatility
- [ ] Max loss and max trades settings are correct for live
- [ ] Operator monitoring is active (dashboard or tail logs)

GO criteria are met only when every item is checked.

---

## Emergency actions
- Trigger global halt: ______________________________
- Flatten all positions: ____________________________
- Broker dashboard link: ____________________________
- Log folder location: ______________________________
