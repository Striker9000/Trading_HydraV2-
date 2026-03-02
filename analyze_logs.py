import json

lines = []
with open('logs/app.jsonl', 'r', encoding='utf-8', errors='replace') as f:
    for line in f:
        line = line.strip()
        if not line: continue
        try: lines.append(json.loads(line))
        except: pass

print(f'Total events: {len(lines)}')

print('\n=== EQUITY TIMELINE ===')
prev_pnl = None
for d in lines:
    if d.get('event') == 'exitbot_ok':
        pnl = d.get('pnl', 0)
        ts = d.get('timestamp','')[:19]
        if prev_pnl is None or abs(pnl - prev_pnl) > 50:
            print(f'  {ts}  equity=${d.get("equity",0):,.0f}  day_pnl=${pnl:,.0f}')
            prev_pnl = pnl

print('\n=== ALL FILLS / EXITS / ORDERS ===')
keywords = ['fill','order_placed','order_submitted','trade_entered','trade_exit',
            'exit_triggered','exit_placed','stop_triggered','catastrophic',
            'v2_exit_decision','entry_placed','hailmary_exit','session_protection_exit',
            'alpaca_order','order_filled','position_closed']
for d in lines:
    ev = d.get('event','')
    if any(k in ev for k in keywords):
        ts = d.get('timestamp','')[:19]
        data = {k:v for k,v in d.items() if k not in ['event','timestamp','ts','level','bot_id','run_id']}
        print(f'  {ts} | {ev}')
        print(f'         {json.dumps(data)[:300]}')

print('\n=== ERRORS / WARNINGS ===')
for d in lines:
    ev = d.get('event','')
    lvl = d.get('level','')
    if lvl in ('error','warning','critical') or 'error' in ev or 'failed' in ev or 'reject' in ev:
        ts = d.get('timestamp','')[:19]
        data = {k:v for k,v in d.items() if k not in ['event','timestamp','ts','level','bot_id','run_id']}
        print(f'  {ts} | [{lvl}] {ev}')
        print(f'         {json.dumps(data)[:250]}')
