"""Drone battery percentage that stays steady in flight (port of BatteryEstimator.java).

The drone's own percentage (message 9C) is a straight line over the live voltage (measured:
0 % = 10.4 V, 100 % = 12.4 V). In flight the voltage sags under the motor current, so that number
drops at take-off and moves with the throttle. This shows the resting value on the ground, holds the
take-off value until the drone hovers, measures the sag 5 to 15 s after take-off, then shows the
drone's value plus that sag, smoothed over 20 s and only ever going down (at most 6 % per minute,
faster when the drone reports low voltage). After landing it follows the resting value down, or jumps
to it when it is 5 % or more higher (battery swapped). Times are milliseconds.
"""
import math
import threading

REST_MS = 8000
SAG_FROM_MS, SAG_TO_MS = 5000, 15000
REST_TAU_S, FLIGHT_TAU_S = 3.0, 20.0
FLIGHT_DROP_PER_S = 0.1
LOW_DROP_PER_S = 2.0
GROUND_DROP_PER_S = 1.0
RESYNC = 5.0
MAX_SAG = 40.0


class BatteryEstimator:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.display = self.rest = self.takeoff = self.flight = -1.0
        self.sag = math.nan
        self.sag_sum, self.sag_count = 0.0, 0
        self.last_armed, self.raw_pct = 0, -1
        self.last = self.armed_changed = self.flying_since = 0

    def stale_for(self, now_ms, ms):
        return self.last == 0 or now_ms - self.last > ms

    def _start_flight(self):
        self.takeoff = self.display
        self.rest = -1.0  # measure the resting value again after landing
        self.sag = math.nan
        self.sag_sum, self.sag_count = 0.0, 0
        self.flight = -1.0

    def update(self, pct, armed, low_bat, now):
        """One 9C sample: the drone's percentage, 8B Armed (0 off, 1 on the ground, 2 flying), 8B LowBat."""
        if not 0 <= pct <= 100:
            return
        with self.lock:
            self.raw_pct = pct
            dt = 0.0 if self.last == 0 else max(0.0, min(1.0, (now - self.last) / 1000.0))
            self.last = now
            if self.display < 0:  # first sample
                self.display = self.rest = float(pct)
                self.last_armed, self.armed_changed = armed, now
                if armed == 2:
                    self.flying_since = now
                if armed != 0:
                    self._start_flight()
            if armed != self.last_armed:
                if self.last_armed == 0:
                    self._start_flight()
                if armed == 2 and self.last_armed != 2:
                    self.flying_since = now
                self.last_armed, self.armed_changed = armed, now
            if armed == 0:
                if now - self.armed_changed < REST_MS and dt > 0:
                    return
                self.rest = float(pct) if self.rest < 0 else self.rest + (pct - self.rest) * min(1.0, dt / REST_TAU_S)
                if self.rest >= self.display + RESYNC:
                    self.display = self.rest
                elif self.rest < self.display:
                    self.display = max(self.rest, self.display - GROUND_DROP_PER_S * dt)
                return
            if armed != 2:
                return
            t = now - self.flying_since
            if math.isnan(self.sag):
                if SAG_FROM_MS <= t <= SAG_TO_MS:
                    self.sag_sum += pct
                    self.sag_count += 1
                if t > SAG_TO_MS:
                    hover = self.sag_sum / self.sag_count if self.sag_count else pct
                    self.sag = max(0.0, min(MAX_SAG, self.takeoff - hover))
                    self.flight = self.display
                return
            est = min(100.0, pct + self.sag)
            self.flight += (est - self.flight) * min(1.0, dt / FLIGHT_TAU_S)
            if self.flight < self.display:
                rate = LOW_DROP_PER_S if low_bat >= 1 else FLIGHT_DROP_PER_S
                self.display = max(self.flight, self.display - rate * dt)

    def percent(self):
        # Java's Math.round: floor(x + 0.5)
        return -1 if self.display < 0 else int(math.floor(max(0.0, min(100.0, self.display)) + 0.5))

    def raw(self):
        return self.raw_pct

    def sag_pct(self):
        return -1 if math.isnan(self.sag) else int(math.floor(self.sag + 0.5))
