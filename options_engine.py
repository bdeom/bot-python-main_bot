"""
Options Engine — Covered Calls, Cash-Secured Puts, Vertical Spreads
"""

from ib_insync import *
import asyncio, datetime, logging

log = logging.getLogger(__name__)


class OptionsEngine:

    def __init__(self, ib: IB):
        self.ib       = ib
        self.open_pos = {}

    async def run_options_cycle(self, equity, session, state):
        await self._manage_existing(state)
        await self._covered_calls(equity, state)
        await self._csp(equity, state)
        if session == 'NY':
            await self._vertical_spread(equity, state)

    async def _manage_existing(self, state):
        for key, pos in list(self.open_pos.items()):
            ticker = self.ib.reqMktData(pos['contract'], '', False, False)
            await asyncio.sleep(1)
            mid = ((ticker.bid or 0) + (ticker.ask or 0)) / 2
            if mid <= 0:
                continue
            profit_pct = (pos['premium'] - mid) / pos['premium']
            if profit_pct >= 0.50:
                order = MarketOrder('BUY', pos['qty'])
                self.ib.placeOrder(pos['contract'], order)
                log.info(f"  OPT CLOSE {key} at 50% profit")
                del self.open_pos[key]

    async def _covered_calls(self, equity, state):
        if len(self.open_pos) >= 3:
            return
        chain, spot = await self._chain_spot('SPY')
        if not spot:
            return
        expiry  = self._next_friday(min_days=7)
        strike  = round(spot * 1.015 / 5) * 5
        if strike not in chain.strikes:
            return
        opt, ticker = await self._get_option('SPY', expiry, strike, 'C')
        if not ticker.bid or ticker.bid < spot * 0.006:
            return
        order = LimitOrder('SELL', 1, round(ticker.bid - 0.01, 2))
        self.ib.placeOrder(opt, order)
        key = f'CC_SPY_{strike}'
        self.open_pos[key] = {
            'contract': opt, 'premium': ticker.bid,
            'target_close': ticker.bid * 0.50, 'qty': 1
        }
        state.add_option_trade(f'SPY {strike}C', 'SELL', ticker.bid, 'Covered Call')
        log.info(f"  OPT SELL CC SPY {expiry} {strike}C @ {ticker.bid:.2f}")

    async def _csp(self, equity, state):
        if len(self.open_pos) >= 3:
            return
        chain, spot = await self._chain_spot('QQQ')
        if not spot:
            return
        expiry  = self._next_friday(min_days=14)
        strike  = round(spot * 0.96 / 5) * 5
        if strike not in chain.strikes:
            return
        if strike * 100 > equity * 0.20:
            return
        opt, ticker = await self._get_option('QQQ', expiry, strike, 'P')
        if not ticker.bid or ticker.bid < spot * 0.004:
            return
        order = LimitOrder('SELL', 1, round(ticker.bid - 0.01, 2))
        self.ib.placeOrder(opt, order)
        key = f'CSP_QQQ_{strike}'
        self.open_pos[key] = {
            'contract': opt, 'premium': ticker.bid,
            'target_close': ticker.bid * 0.50, 'qty': 1
        }
        state.add_option_trade(f'QQQ {strike}P', 'SELL', ticker.bid, 'Cash-Secured Put')
        log.info(f"  OPT SELL CSP QQQ {expiry} {strike}P @ {ticker.bid:.2f}")

    async def _vertical_spread(self, equity, state):
        if len(self.open_pos) >= 3:
            return
        chain, spot = await self._chain_spot('SPY')
        if not spot:
            return
        expiry     = self._next_friday(min_days=5)
        buy_strike = round(spot / 5) * 5
        sell_strike = buy_strike + 5
        if buy_strike not in chain.strikes or sell_strike not in chain.strikes:
            return
        long_opt,  lt = await self._get_option('SPY', expiry, buy_strike,  'C')
        short_opt, st = await self._get_option('SPY', expiry, sell_strike, 'C')
        if not lt.ask or not st.bid:
            return
        debit    = round(lt.ask - st.bid, 2)
        max_gain = 5.0 - debit
        if debit <= 0 or debit > 3.0 or max_gain / debit < 1.5:
            return
        legs  = [
            ComboLeg(long_opt.conId,  1, 'BUY',  'SMART'),
            ComboLeg(short_opt.conId, 1, 'SELL', 'SMART'),
        ]
        combo = Contract(
            symbol='SPY', secType='BAG',
            currency='USD', exchange='SMART', comboLegs=legs
        )
        self.ib.placeOrder(combo, LimitOrder('BUY', 1, debit))
        desc = f'SPY {buy_strike}/{sell_strike}C'
        state.add_option_trade(desc, 'BUY', debit, 'Bull Spread')
        log.info(f"  OPT BUY Spread {desc} @ {debit:.2f}")

    async def _chain_spot(self, sym):
        contract = Stock(sym, 'SMART', 'USD')
        await self.ib.qualifyContractsAsync(contract)
        chains = await self.ib.reqSecDefOptParamsAsync(sym, '', 'STK', contract.conId)
        chain  = next((c for c in chains if c.exchange == 'SMART'), None)
        if not chain:
            return None, None
        ticker = self.ib.reqMktData(contract, '', False, False)
        await asyncio.sleep(1.5)
        return chain, ticker.last or ticker.close

    async def _get_option(self, sym, expiry, strike, right):
        opt = Option(sym, expiry, strike, right, 'SMART')
        await self.ib.qualifyContractsAsync(opt)
        ticker = self.ib.reqMktData(opt, '100,101,106', False, False)
        await asyncio.sleep(1.5)
        return opt, ticker

    @staticmethod
    def _next_friday(min_days=7):
        d = datetime.date.today() + datetime.timedelta(days=min_days)
        while d.weekday() != 4:
            d += datetime.timedelta(days=1)
        return d.strftime('%Y%m%d')
