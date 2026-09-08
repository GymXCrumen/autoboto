"""
Core martingale scaling/risk logic: open a base position, and if price
moves against it, add up to MAX_SCALES more legs at an increasing
size, until either the (moving) take-profit is hit or the hard stop
caps the loss.

Position state is persisted to SQLite (state_store.py) after every
change, and resumed on startup, so a Railway restart mid-position
doesn't lose track of open legs. Telegram notifications (notify.py)
fire on open/scale/close/error, matching the alert style used
elsewhere in this repo.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional, Tuple

import config
import notify
import state_store
from oanda_client import OandaClient


def pip_size(instrument: str) -> float:
    # JPY pairs use 2 decimal places for a pip; everything else uses 4.
    return 0.01 if instrument.endswith("JPY") else 0.0001


@dataclass
class MartingalePosition:
    direction: str  # "long" or "short"
    entries: List[Tuple[float, int]] = field(default_factory=list)  # (price, units)

    @property
    def scales_done(self) -> int:
        return len(self.entries) - 1  # entries[0] is the base entry

    @property
    def total_units(self) -> int:
        return sum(units for _, units in self.entries)

    @property
    def average_price(self) -> float:
        total = self.total_units
        return sum(price * units for price, units in self.entries) / total


class MartingaleEngine:
    def __init__(self, client: OandaClient):
        self.client = client
        self.instrument = config.INSTRUMENT
        self.pip = pip_size(self.instrument)
        self.position: Optional[MartingalePosition] = None
        self._resume_if_any()

    def _resume_if_any(self):
        saved = state_store.load_open_position()
        if saved:
            direction, entries = saved
            self.position = MartingalePosition(direction=direction, entries=entries)
            print(
                f"[resume] Restored open {direction} position with "
                f"{len(entries)} leg(s) from state_store."
            )

    def run_forever(self, get_entry_signal: Callable):
        print(
            f"Starting martingale engine on {self.instrument} "
            f"({config.ENVIRONMENT} environment). Ctrl+C to stop."
        )
        while True:
            try:
                self._tick(get_entry_signal)
            except Exception as exc:
                # Never let a transient error (network blip, API hiccup)
                # kill the process silently — log and notify, keep going.
                print(f"[error] {exc!r}")
                notify.error(repr(exc))
            time.sleep(config.POLL_INTERVAL_SECONDS)

    def _tick(self, get_entry_signal: Callable):
        price = self.client.get_price(self.instrument)

        if self.position is None:
            direction = get_entry_signal(self.client, self.instrument)
            if direction:
                self._open_base(direction, price)
            return

        self._manage_open_position(price)

    def _open_base(self, direction: str, price: dict):
        entry_price = price["ask"] if direction == "long" else price["bid"]
        units = config.BASE_UNITS if direction == "long" else -config.BASE_UNITS
        self.client.market_order(self.instrument, units)
        self.position = MartingalePosition(direction=direction, entries=[(entry_price, units)])
        state_store.save_open_position(self.position.direction, self.position.entries)
        print(f"[open] {direction} base entry {units} units @ {entry_price}")
        notify.position_opened(direction, self.instrument, units, entry_price)

    def _manage_open_position(self, price: dict):
        pos = self.position
        current_price = price["bid"] if pos.direction == "long" else price["ask"]
        avg = pos.average_price

        move_pips = (current_price - avg) / self.pip
        if pos.direction == "short":
            move_pips = -move_pips

        # 1. Take profit
        if move_pips >= config.TAKE_PROFIT_PIPS:
            self._close(reason=f"take profit ({move_pips:.1f} pips)")
            return

        adverse_pips = -move_pips

        # 2. Hard stop — beyond the last scale, cut losses instead of adding more
        if (
            pos.scales_done >= config.MAX_SCALES
            and adverse_pips >= config.HARD_STOP_PIPS_BEYOND_LAST_SCALE
        ):
            self._close(
                reason=f"hard stop ({adverse_pips:.1f} pips against, "
                f"after {pos.scales_done} scales)"
            )
            return

        # 3. Scale in
        if pos.scales_done < config.MAX_SCALES and adverse_pips >= config.SCALE_TRIGGER_PIPS:
            self._scale_in(price)

    def _scale_in(self, price: dict):
        pos = self.position
        last_units = abs(pos.entries[-1][1])
        new_units = int(last_units * config.SCALE_MULTIPLIER)
        entry_price = price["ask"] if pos.direction == "long" else price["bid"]
        signed_units = new_units if pos.direction == "long" else -new_units
        self.client.market_order(self.instrument, signed_units)
        pos.entries.append((entry_price, signed_units))
        state_store.save_open_position(pos.direction, pos.entries)
        print(
            f"[scale {pos.scales_done}] added {signed_units} units @ {entry_price} "
            f"(new average {pos.average_price:.5f})"
        )
        notify.scaled_in(pos.direction, self.instrument, pos.scales_done, signed_units,
                          entry_price, pos.average_price)

    def _close(self, reason: str):
        pos = self.position
        self.client.close_position(self.instrument, pos.direction)
        print(f"[close] {self.instrument} {pos.direction} — {reason}")
        notify.position_closed(pos.direction, self.instrument, reason, pos.average_price)
        state_store.log_closed_trade(
            direction=pos.direction,
            scales_used=pos.scales_done,
            average_price=pos.average_price,
            reason=reason,
            closed_at=datetime.now(config.WAT).strftime("%Y-%m-%d %H:%M WAT"),
        )
        state_store.clear_open_position()
        self.position = None
