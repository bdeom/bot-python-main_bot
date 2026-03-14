"""
IBKR Equity + Options Compounder Bot
Long/Short · London + NY sessions · Intraday only
Target: €1,000/month on €8,000 base
"""

from ib_insync import *
from options_engine import OptionsEngine
from risk_manager import RiskManager
from state import BotState
import asyncio, datetime, pytz, json, logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

CET          = pytz.timezone('Europe/Madrid')
PAPER_PORT   = 7497
LIVE_PORT    = 7496
CLIENT_ID    = 1
BASE_EQUITY  = 8000.0
RISK_PCT     = 0.015    # 1.5% per trade
MAX_DD_PCT   = 0.03     # 3% daily drawdown halt
MAX_EQ_POS   = 4
INSTRUMENTS  = ['SPY', 'QQQ', 'GLD', 'TLT']

SESSIONS = [
    (datetime.time(8,  0), datetime.time(10, 30), 'London'),
    (datetime.time(14,30), datetime.time(18,  0), 'NY'),
]


class IBKRCompounder:

    def __init__(self, paper=True):
        self.ib       = IB()
        self.port     = PAPER_PORT if paper else LIVE_PORT
        self.opts     = OptionsEngine(self.ib)
        self.risk     = RiskManager(BASE_EQUITY, RISK_PCT, MAX_DD_PCT)
        self.state    = BotState()
        self.eq_pos   = {}
        self.halted   = False

    # ------------------------------------------------------------------ #
    #  MAIN RUN LOOP
    # ------------------------------------------------------------------ #
    async def run(self):
        await self.ib.connectAsync('127.0.0.1', self.port, clientId=CLIENT_ID)
        log.info(f"Connected to IBKR | port={self.port} paper={self.port==PAPER_PORT}")

        equity = await self.get_equity()
        self.risk.reset_day(equity)
        self.state.update_equity(equity)
        log.info(f"Starting equity: €{equity:.2f}")

        self.ib.pendingTickersEvent += self.on_tick

        # Subscribe live data
        for sym in INSTRUMENTS:
            c = Stock(sym, 'SMART', 'USD')
            await self.ib.qualifyContractsAsync(c)
            self.ib.reqMktData(c, '233', False, False)

        while True:
            now_cet = datetime.datetime.now(CET).time()
            in_session, session_name = self._check_session(now_cet)

            equity = await self.get_equity()
            self.state.update_equity(equity)

            if self.halted:
                log.warning("Bot halted — daily drawdown limit reached")
                await asyncio.sleep(60)
                continue

            if in_session:
                self.state.set_session(session_name)
                await self._run_session(equity, session_name)
            else:
                self.state.set_session('Closed')
                await self._close_all_intraday()

            await asyncio.sleep(60)

    # ------------------------------------------------------------------ #
    #  SESSION LOGIC
    # ------------------------------------------------------------------ #
    async def _run_session(self, equity, session_name):
        halted = await self.risk.check_drawdown(self.ib, equity)
        if halted:
            self.halted = True
            return

        open_count = sum(1 for p in self.eq_pos.values() if p.get('open'))
        if open_count < MAX_EQ_POS:
            await self._evaluate_signals()

        await self.opts.run_options_cycle(equity, session_name, self.state)

    async def _evaluate_signals(self):
        for sym in INSTRUMENTS:
            if sym in self.eq_pos and self.eq_pos[sym].get('open'):
                continue
            bars = await self.ib.reqHistoricalDataAsync(
                Stock(sym, 'SMART', 'USD'),
                endDateTime='', durationStr='1 D',
                barSizeSetting='5 mins',
                whatToShow='TRADES', useRTH=True
            )
            if len(bars) < 22:
                continue

            closes  = [b.close for b in bars]
            ef      = self._ema(closes, 9)
            es      = self._ema(closes, 21)
            rsi_v   = self._rsi(closes, 14)
            vwap_v  = self._vwap(bars)
            price   = closes[-1]

            signal = 0
            if ef > es and rsi_v < 65 and price > vwap_v * 1.003:
                signal = 1
            elif ef < es and rsi_v > 35 and price < vwap_v * 0.997:
                signal = -1

            if signal != 0:
                await self._enter_equity(sym, signal, price)

    async def _enter_equity(self, sym, direction, price):
        equity = await self.get_equity()
        qty    = self.risk.position_size(equity, price)
        if qty < 1:
            return

        contract = Stock(sym, 'SMART', 'USD')
        action   = 'BUY' if direction == 1 else 'SELL'
        order    = MarketOrder(action, qty)
        self.ib.placeOrder(contract, order)

        self.eq_pos[sym] = {
            'open': True, 'direction': direction,
            'qty': qty, 'entry': price, 'symbol': sym
        }
        self.state.add_trade(sym, action, qty, price)
        log.info(f"  EQUITY {action} {qty}x {sym} @ {price:.2f}")

    async def _close_all_intraday(self):
        for sym, pos in list(self.eq_pos.items()):
            if not pos.get('open'):
                continue
            contract = Stock(sym, 'SMART', 'USD')
            action   = 'SELL' if pos['direction'] == 1 else 'BUY'
            self.ib.placeOrder(contract, MarketOrder(action, pos['qty']))
            pos['open'] = False
            log.info(f"  CLOSE {sym} EOD")
        self.eq_pos = {}
        log.info("Session closed — all intraday flat")

    # ------------------------------------------------------------------ #
    #  TICK HANDLER
    # ------------------------------------------------------------------ #
    def on_tick(self, tickers):
        for ticker in tickers:
            sym = ticker.contract.symbol
            if sym in self.eq_pos and self.eq_pos[sym].get('open'):
                price = ticker.last or ticker.close
                if price:
                    self.risk.check_stop(
                        self.ib, sym, price, self.eq_pos, self._on_stop
                    )

    def _on_stop(self, sym):
        if sym in self.eq_pos:
            self.eq_pos[sym]['open'] = False

    # ------------------------------------------------------------------ #
    #  HELPERS
    # ------------------------------------------------------------------ #
    async def get_equity(self):
        try:
            summary = await self.ib.accountSummaryAsync()
            for item in summary:
                if item.tag == 'NetLiquidation':
                    return float(item.value)
        except Exception as e:
            log.error(f"get_equity error: {e}")
        return BASE_EQUITY

    def _check_session(self, t):
        for start, end, name in SESSIONS:
            if start <= t <= end:
                return True, name
        return False, None

    @staticmethod
    def _ema(closes, period):
        k = 2 / (period + 1)
        ema = closes[0]
        for c in closes[1:]:
            ema = c * k + ema * (1 - k)
        return ema

    @staticmethod
    def _rsi(closes, period=14):
        gains = losses = 0.0
        for i in range(1, period + 1):
            d = closes[-i] - closes[-i - 1]
            (gains if d > 0 else losses).__class__
            if d > 0: gains += d
            else: losses -= d
        rs = (gains / period) / ((losses / period) + 1e-9)
        return 100 - 100 / (1 + rs)

    @staticmethod
    def _vwap(bars):
        tv = pv = 0.0
        for b in bars:
            tv += b.volume
            pv += ((b.high + b.low + b.close) / 3) * b.volume
        return pv / tv if tv else 0.0


if __name__ == '__main__':
    import sys
    paper = '--live' not in sys.argv
    if not paper:
        log.warning("LIVE MODE — real money will be traded!")
    bot = IBKRCompounder(paper=paper)
    asyncio.run(bot.run())
