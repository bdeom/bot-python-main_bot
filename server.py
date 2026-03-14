"""
Dashboard Server — Flask + Socket.IO
Serves live P&L dashboard. Reads state.json written by the bot.
Run: python dashboard/server.py
Open: http://localhost:5000
"""

from flask import Flask, render_template, jsonify, send_from_directory
from flask_socketio import SocketIO
import json, threading, time, os
from pathlib import Path

app     = Flask(__name__, static_folder='static', template_folder='templates')
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

STATE_FILE = Path(__file__).parent / 'static' / 'state.json'
DEMO_MODE  = not STATE_FILE.exists()   # show demo data if bot not running


def read_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return _demo_state()


def _demo_state():
    """Simulated state for dashboard preview."""
    import math, datetime, random
    equity = 8000.0
    curve  = []
    base   = 8000.0
    for i in range(80):
        base += random.uniform(-12, 18)
        curve.append({
            'time':  f"{8 + i//12:02d}:{(i*5) % 60:02d}",
            'value': round(base, 2)
        })
    return {
        'equity':         round(base, 2),
        'base_equity':    8000.0,
        'daily_pnl':      round(base - 8000, 2),
        'monthly_pnl':    round(base - 8000 + 340, 2),
        'monthly_target': 1000.0,
        'session':        'NY',
        'halted':         False,
        'last_update':    datetime.datetime.now().isoformat(),
        'equity_curve':   curve,
        'trades': [
            {'time':'14:32:11','symbol':'SPY','action':'BUY', 'qty':12,'price':543.20,'type':'equity'},
            {'time':'14:28:44','symbol':'QQQ 445P','action':'SELL','qty':1,'price':1.85,'type':'option','strategy':'Cash-Secured Put'},
            {'time':'10:15:02','symbol':'SPY 540C','action':'SELL','qty':1,'price':2.30,'type':'option','strategy':'Covered Call'},
            {'time':'09:47:33','symbol':'QQQ','action':'SELL','qty':8,'price':447.60,'type':'equity'},
            {'time':'09:12:55','symbol':'GLD','action':'BUY', 'qty':15,'price':218.40,'type':'equity'},
            {'time':'08:31:10','symbol':'SPY 540/545C','action':'BUY','qty':1,'price':1.60,'type':'option','strategy':'Bull Spread'},
        ]
    }


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/state')
def api_state():
    return jsonify(read_state())


def push_state():
    """Background thread — push state to all connected clients every 2s."""
    while True:
        state = read_state()
        socketio.emit('state', state)
        time.sleep(2)


@socketio.on('connect')
def on_connect():
    socketio.emit('state', read_state())


if __name__ == '__main__':
    t = threading.Thread(target=push_state, daemon=True)
    t.start()
    print("Dashboard running → http://localhost:5000")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)

