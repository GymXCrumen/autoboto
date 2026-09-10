"""
Thin wrapper around the official `MetaTrader5` Python package.

IMPORTANT: MetaTrader5 is Windows-only and talks to a running MT5
terminal on the same machine over local IPC, not a network API —
this is why the bot has to run on a Windows VPS with the terminal
open, rather than on Railway.

Before this will place trades, "Allow Algo Trading" must be enabled
both in Tools -> Options -> Expert Advisors, and via the "Algo
Trading" toggle button in the terminal's own toolbar. Both need to
be on — one without the other silently rejects every order.

Assumes a NETTING account (one net position per symbol; MT5 computes
the blended average price itself as you add volume) rather than a
HEDGING account (separate tickets per order on the same symbol).
Most retail accounts are netting — check `mt5.account_info().margin_mode`
if you're unsure. On a hedging account, scale-ins create additional
tickets instead of growing one position, and get_position()'s
"return the first position on this symbol" assumption breaks.
"""

from typing import Optional

import MetaTrader5 as mt5

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}

MAGIC_NUMBER = 20260910  # tags every order this bot places
DEVIATION_POINTS = 20


class MT5Error(RuntimeError):
    pass


class MT5Client:
    def __init__(self, login: int, password: str, server: str, terminal_path: Optional[str] = None):
        kwargs = {"login": login, "password": password, "server": server}
        if terminal_path:
            kwargs["path"] = terminal_path
        if not mt5.initialize(**kwargs):
            raise MT5Error(f"mt5.initialize() failed: {mt5.last_error()}")
        if not mt5.login(login, password=password, server=server):
            mt5.shutdown()
            raise MT5Error(f"mt5.login() failed: {mt5.last_error()}")

    def shutdown(self) -> None:
        mt5.shutdown()

    def ensure_symbol(self, symbol: str) -> None:
        info = mt5.symbol_info(symbol)
        if info is None:
            raise MT5Error(
                f"Symbol '{symbol}' not found — check the exact name in Market "
                f"Watch (some brokers add suffixes like .a or m, e.g. EURUSDm)."
            )
        if not info.visible and not mt5.symbol_select(symbol, True):
            raise MT5Error(f"Could not add {symbol} to Market Watch.")

    # --- market data ---------------------------------------------------------

    def pip_size(self, symbol: str) -> float:
        # 5- and 3-digit quoting: 1 pip = 10 points. 4- and 2-digit: 1 pip = 1 point.
        info = mt5.symbol_info(symbol)
        return info.point * 10 if info.digits in (3, 5) else info.point

    def get_price(self, symbol: str) -> dict:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise MT5Error(f"No tick data for {symbol}: {mt5.last_error()}")
        return {"bid": tick.bid, "ask": tick.ask}

    def get_candles(self, symbol: str, timeframe: str, count: int) -> list:
        """Last `count` completed closes, oldest first (skips the forming bar)."""
        tf = TIMEFRAMES[timeframe]
        rates = mt5.copy_rates_from_pos(symbol, tf, 1, count)
        if rates is None:
            raise MT5Error(f"copy_rates_from_pos failed for {symbol}: {mt5.last_error()}")
        return [float(r["close"]) for r in rates]

    def get_account_info(self) -> dict:
        info = mt5.account_info()
        if info is None:
            raise MT5Error(f"account_info() failed: {mt5.last_error()}")
        return {"balance": info.balance, "equity": info.equity}

    # --- positions -------------------------------------------------------------

    def get_position(self, symbol: str) -> Optional[dict]:
        """Net open position on `symbol` tagged with our magic number, or None."""
        positions = mt5.positions_get(symbol=symbol)
        ours = [p for p in positions or [] if p.magic == MAGIC_NUMBER]
        if not ours:
            return None
        p = ours[0]
        return {
            "ticket": p.ticket,
            "direction": "long" if p.type == mt5.ORDER_TYPE_BUY else "short",
            "volume": p.volume,
            "price_open": p.price_open,
            "sl": p.sl,
            "tp": p.tp,
        }

    # --- trading -----------------------------------------------------------------

    def market_order(self, symbol: str, direction: str, lot: float,
                      sl: Optional[float] = None, tp: Optional[float] = None) -> dict:
        tick = mt5.symbol_info_tick(symbol)
        order_type = mt5.ORDER_TYPE_BUY if direction == "long" else mt5.ORDER_TYPE_SELL
        price = tick.ask if direction == "long" else tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": round(lot, 2),
            "type": order_type,
            "price": price,
            "deviation": DEVIATION_POINTS,
            "magic": MAGIC_NUMBER,
            "comment": "martingale-bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if sl is not None:
            request["sl"] = sl
        if tp is not None:
            request["tp"] = tp
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            raise MT5Error(f"order_send failed: {result}")
        return {"price": result.price, "volume": result.volume}

    def modify_sl_tp(self, symbol: str, ticket: int,
                      sl: Optional[float] = None, tp: Optional[float] = None) -> None:
        position = mt5.positions_get(ticket=ticket)
        if not position:
            raise MT5Error(f"No open position with ticket {ticket}")
        p = position[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": symbol,
            "position": ticket,
            "sl": sl if sl is not None else p.sl,
            "tp": tp if tp is not None else p.tp,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            raise MT5Error(f"SL/TP modify failed: {result}")

    def close_position(self, symbol: str, ticket: int) -> dict:
        position = mt5.positions_get(ticket=ticket)
        if not position:
            raise MT5Error(f"No open position with ticket {ticket}")
        p = position[0]
        tick = mt5.symbol_info_tick(symbol)
        close_type = mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": p.volume,
            "type": close_type,
            "position": ticket,
            "price": price,
            "deviation": DEVIATION_POINTS,
            "magic": MAGIC_NUMBER,
            "comment": "martingale-bot-close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            raise MT5Error(f"close order failed: {result}")
        return {"price": result.price}
