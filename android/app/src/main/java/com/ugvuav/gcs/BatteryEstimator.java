package com.ugvuav.gcs;

/**
 * Drone battery percentage that stays steady in flight.
 *
 * The drone's own percentage (message 9C) is a straight line over the live voltage (measured:
 * 0 % = 10.4 V, 100 % = 12.4 V, 0.5 % per 0.01 V). In flight the battery voltage sags under the
 * motor current, so that number drops at take-off and moves with every throttle change, although
 * the battery is not that much emptier.
 *
 * What this shows instead:
 *  - on the ground (motors off for 8 s): the drone's resting value, smoothed;
 *  - at take-off: the value from just before take-off, held until the drone hovers;
 *  - 5 to 15 s after take-off it measures the sag (resting value minus the hover value), and from
 *    then on shows the drone's value plus that sag, smoothed over 20 s;
 *  - in flight the number only goes down, at most 6 % per minute (a real battery drains slower),
 *    faster when the drone itself reports low voltage;
 *  - after landing it follows the resting value down, or jumps to it when it is 5 % or more higher
 *    (battery swapped or charged).
 * The drone's own low-battery levels still drive the warnings and its automatic return / landing.
 *
 * Plain Java (no Android classes) so it can be tested on a PC; times are milliseconds.
 */
final class BatteryEstimator {
    static final long REST_MS = 8000;
    static final long SAG_FROM_MS = 5000, SAG_TO_MS = 15000;
    static final double REST_TAU_S = 3, FLIGHT_TAU_S = 20;
    static final double FLIGHT_DROP_PER_S = 0.1;   // 6 % per minute
    static final double LOW_DROP_PER_S = 2.0;      // when the drone reports low voltage
    static final double GROUND_DROP_PER_S = 1.0;
    static final double RESYNC = 5;
    static final double MAX_SAG = 40;

    private double display = -1, rest = -1, takeoff = -1, sag = Double.NaN, flight = -1;
    private double sagSum;
    private int sagCount, lastArmed, raw = -1;
    private long last, armedChanged, flyingSince;

    synchronized void reset() {
        display = rest = takeoff = flight = -1;
        sag = Double.NaN;
        sagSum = 0;
        sagCount = lastArmed = 0;
        raw = -1;
        last = armedChanged = flyingSince = 0;
    }

    /** True when the last sample is older than ms (a new connection may be a new battery). */
    synchronized boolean staleFor(long now, long ms) {
        return last == 0 || now - last > ms;
    }

    /**
     * One sample from message 9C.
     * @param pct    the drone's percentage (9C BatPer)
     * @param armed  8B Armed: 0 motors off, 1 motors on the ground, 2 flying
     * @param lowBat 8B LowBat: 0 normal, 1 low, 2 return home, 3 forced landing
     */
    synchronized void update(int pct, int armed, int lowBat, long now) {
        if (pct < 0 || pct > 100) return;
        raw = pct;
        double dt = last == 0 ? 0 : Math.max(0, Math.min(1.0, (now - last) / 1000.0));
        last = now;
        if (display < 0) {  // first sample
            display = rest = pct;
            lastArmed = armed;
            armedChanged = now;
            if (armed == 2) flyingSince = now;
            if (armed != 0) startFlight();
        }
        if (armed != lastArmed) {
            if (lastArmed == 0) startFlight();  // motors started: a new flight from the current value
            if (armed == 2 && lastArmed != 2) flyingSince = now;
            lastArmed = armed;
            armedChanged = now;
        }
        if (armed == 0) {
            if (now - armedChanged < REST_MS && dt > 0) return;  // just landed: the voltage recovers
            rest = rest < 0 ? pct : rest + (pct - rest) * Math.min(1, dt / REST_TAU_S);
            if (rest >= display + RESYNC) display = rest;
            else if (rest < display) display = Math.max(rest, display - GROUND_DROP_PER_S * dt);
            return;
        }
        if (armed != 2) return;  // motors running on the ground: hold
        long t = now - flyingSince;
        if (Double.isNaN(sag)) {
            if (t >= SAG_FROM_MS && t <= SAG_TO_MS) {
                sagSum += pct;
                sagCount++;
            }
            if (t > SAG_TO_MS) {
                double hover = sagCount > 0 ? sagSum / sagCount : pct;
                sag = Math.max(0, Math.min(MAX_SAG, takeoff - hover));
                flight = display;
            }
            return;
        }
        double est = Math.min(100, pct + sag);
        flight += (est - flight) * Math.min(1, dt / FLIGHT_TAU_S);
        if (flight < display) {
            double rate = lowBat >= 1 ? LOW_DROP_PER_S : FLIGHT_DROP_PER_S;
            display = Math.max(flight, display - rate * dt);
        }
    }

    private void startFlight() {
        takeoff = display;
        rest = -1;  // measure the resting value again after landing
        sag = Double.NaN;
        sagSum = 0;
        sagCount = 0;
        flight = -1;
    }

    /** Percentage to show, or -1 before the first sample. */
    synchronized int percent() {
        return display < 0 ? -1 : (int) Math.round(Math.max(0, Math.min(100, display)));
    }

    /** The drone's own (voltage) percentage of the last sample, or -1. */
    synchronized int raw() {
        return raw;
    }

    /** Sag compensation in use, in percent, or -1 while not measured. */
    synchronized int sag() {
        return Double.isNaN(sag) ? -1 : (int) Math.round(sag);
    }
}
