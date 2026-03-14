"""
Shared state — bot writes here, dashboard reads via JSON file.
Thread-safe via file locks. Dashboard polls /api/state every 2s.
"""

import json, datetime, os, threading
from pathlib import Path

STATE_FILE = Path(__file__).parent.parent / 'dashboard' / 'static' / 'state.json'
_lock = threading.Lock()


class BotState:

    def __init__(self):
        self.data = {
            'equity':          8000.0,
            'base_equity':     8000.0,
            'daily_pnl':       0.0,
            'monthly_pnl':     0.0,
            'session':         'Initializing',
            'halted':          False,
            'trades':          [],
            'opt_positions':   [],
            'eq_positions':    [],
            'equity_curve':    [],
            'monthly_target':  1000.0,
            'last_update':     '',
        }
        self._write()

    def update_equity(self, equity: float):
        with _lock:
            self.data['equity']     = round(equity, 2)
            self.data['daily_pnl']  = round(equity - self.data['base_equity'], 2)
            self.data['last_update'] = datetime.datetime.now().isoformat()
            # Append to equity curve (last 200 points)
            self.data['equity_curve'].append({
                'time': datetime.datetime.now().strftime('%H:%M'),
                'value': round(equity, 2)
            })
            if len(self.data['equity_curve']) > 200:
                self.data['equity_curve'].pop(0)
            self._write()

    def set_session(self, name: str):
        with _lock:
            self.data['session'] = name
            self._write()

    def add_trade(self, sym: str, action: str, qty: int, price: float):
        with _lock:
            trade = {
                'time':   datetime.datetime.now().strftime('%H:%M:%S'),
                'symbol': sym,
                'action': action,
                'qty':    qty,
                'price':  round(price, 2),
                'type':   'equity'
            }
            self.data['trades'].insert(0, trade)
            self.data['trades'] = self.data['trades'][:50]   # keep last 50
            self._write()

    def add_option_trade(self, desc: str, action: str, premium: float, strategy: str):
        with _lock:
            trade = {
                'time':     datetime.datetime.now().strftime('%H:%M:%S'),
                'symbol':   desc,
                'action':   action,
                'qty':      1,
                'price':    round(premium, 2),
                'type':     'option',
                'strategy': strategy
            }
            self.data['trades'].insert(0, trade)
            self.data['trades'] = self.data['trades'][:50]
            self._write()

    def set_halted(self, halted: bool):
        with _lock:
            self.data['halted'] = halted
            self._write()

    def _write(self):
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(STATE_FILE) + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self.data, f)
        os.replace(tmp, STATE_FILE)
