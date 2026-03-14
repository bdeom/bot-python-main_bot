"""
Risk Manager — position sizing, stop loss, daily drawdown guard
"""

from ib_insync import *
import logging

log = logging.getLogger(__name__)


class RiskManager:

    def __init__(self, base_equity, risk_pct, max_dd_pct):
        self.base        = base_equity
        self.risk_pct    = risk_pct
        self.max_dd_pct  = max_dd_pct
        self._day_start  = base_equity
        self._day_high   = base_equity

    def reset_day(self, equity):
        self._day_start = equity
        self._day_high  = equity

    def position_size(self, equity, price, atr=None):
        stop_dist = atr if atr else price * 0.01
        qty = int((equity * self.risk_pct) / stop_dist)
        return max(qty, 1)

    async def check_drawdown(self, ib: IB, equity: float) -> bool:
        self._day_high = max(self._day_high, equity)
        dd = (self._day_high - equity) / self._day_high
        if dd >= self.max_dd_pct:
            log.error(f"HALT — drawdown {dd*100:.1f}% >= {self.max_dd_pct*100:.0f}%")
            await self._close_all(ib)
            return True
        progress = (equity - self.base) / (self.base * 0.125) * 100
        log.info(f"Equity €{equity:.0f} | Monthly {progress:.0f}% | DD {dd*100:.1f}%")
        return False

    def check_stop(self, ib, sym, price, positions, on_hit):
        if sym not in positions or not positions[sym].get('open'):
            return
        p = positions[sym]
        d = p['direction']
        e = p['entry']
        stop_dist = e * 0.01
        stop  = e - stop_dist if d == 1 else e + stop_dist
        tp    = e + stop_dist * 2 if d == 1 else e - stop_dist * 2
        hit_sl = (d ==  1 and price <= stop) or (d == -1 and price >= stop)
        hit_tp = (d ==  1 and price >= tp)   or (d == -1 and price <= tp)
        if hit_sl or hit_tp:
            contract = Stock(sym, 'SMART', 'USD')
            action   = 'SELL' if d == 1 else 'BUY'
            ib.placeOrder(contract, MarketOrder(action, p['qty']))
            tag = 'TP' if hit_tp else 'SL'
            log.info(f"  {tag} {sym} @ {price:.2f}")
            on_hit(sym)

    @staticmethod
    async def _close_all(ib: IB):
        for pos in ib.positions():
            action = 'SELL' if pos.position > 0 else 'BUY'
            ib.placeOrder(pos.contract, MarketOrder(action, abs(pos.position)))
        log.info("All positions closed by risk manager")
