/* Safety state machine for the EMG gripper.
 *
 * This runs ON THE PICO, not on the host. That is the whole point: the
 * thing that decides whether a motor may move is on the device that drives
 * the motor, so pulling the USB cable cannot strand the gripper in a
 * closed position.
 *
 * Pure and deterministic. No I/O, no delays, no clock reads - the caller
 * passes time in. That makes every transition reachable from a native test.
 *
 * On rev E there are no force sensors, so signal quality IS the primary
 * safety input. A saturated, flatlined or mains-swamped channel means we do
 * not know what the muscle is doing, and the correct response to not
 * knowing is to stop moving.
 */
#ifndef EMG_SAFETY_H
#define EMG_SAFETY_H

#include <stdint.h>

#include "config.h"

typedef enum {
    SAFE_INIT = 0,   /* powered, envelope window not yet filled            */
    SAFE_IDLE,       /* healthy but not yet trusted for enough windows     */
    SAFE_ACTIVE,     /* normal operation, the gripper tracks the envelope  */
    SAFE_HOLD,       /* recoverable doubt: freeze exactly where we are     */
    SAFE_FAULT,      /* stream or signal lost: release slowly              */
    SAFE_ESTOP       /* latched stop, only reset() clears it               */
} safe_state_t;

typedef struct {
    /* quality thresholds, in raw ADC counts unless noted */
    uint16_t sat_high;          /* >= this counts as railed high          */
    uint16_t sat_low;           /* <= this counts as railed low           */
    uint16_t sat_max_in_window; /* how many railed samples we tolerate    */
    float    flatline_rms;      /* below this the channel looks dead      */
    uint16_t dc_max_offset;     /* |mean - 2048| beyond this is drift     */
    /* hysteresis, in control ticks */
    uint16_t idle_to_active;
    uint16_t hold_recover;
    uint16_t fault_recover;
    /* timing */
    uint32_t stale_after_ms;    /* no fresh samples for this long -> FAULT */
} safe_config_t;

static inline void safe_config_defaults(safe_config_t *c)
{
    c->sat_high          = 4090;
    c->sat_low           = 5;
    c->sat_max_in_window = 5;
    /* A resting muscle is quiet but never silent: real electrodes always
     * show a few counts of noise. Genuinely zero variance means a dead
     * link or a disconnected buffer, not relaxation. Set well below any
     * plausible resting level so rest is NOT mistaken for a fault. */
    c->flatline_rms      = 0.30f;
    c->dc_max_offset     = 600;
    c->idle_to_active    = 10;
    c->hold_recover      = 5;
    c->fault_recover     = 20;
    c->stale_after_ms    = 250;
}

/* Why the machine is in the state it is - surfaced in telemetry so a human
 * can tell "resting" from "electrode fell off". */
typedef enum {
    SAFE_R_NONE = 0,
    SAFE_R_WARMUP,
    SAFE_R_OK,
    SAFE_R_SATURATED,
    SAFE_R_FLATLINE,
    SAFE_R_DC_DRIFT,
    SAFE_R_STALE,
    SAFE_R_ESTOP,
    SAFE_R_RECOVERING
} safe_reason_t;

typedef struct {
    safe_state_t  state;
    safe_reason_t reason;
    uint16_t      good_run;      /* consecutive good ticks                */
    uint16_t      bad_run;
    uint32_t      last_sample_ms;
    uint32_t      transitions;   /* counted, for telemetry                */
} safe_t;

static inline void safe_init(safe_t *s, uint32_t now_ms)
{
    s->state = SAFE_INIT;
    s->reason = SAFE_R_WARMUP;
    s->good_run = 0;
    s->bad_run = 0;
    s->last_sample_ms = now_ms;
    s->transitions = 0;
}

/* Only way out of a latched ESTOP. */
static inline void safe_reset(safe_t *s, uint32_t now_ms)
{
    safe_init(s, now_ms);
}

/* One window's worth of quality evidence. */
typedef struct {
    uint16_t sat_high_count;
    uint16_t sat_low_count;
    float    rms;
    float    mean;
    int      envelope_ready;
} safe_quality_t;

static inline int safe_quality_ok(const safe_config_t *c,
                                  const safe_quality_t *q,
                                  safe_reason_t *why)
{
    if (q->sat_high_count > c->sat_max_in_window ||
        q->sat_low_count  > c->sat_max_in_window) {
        *why = SAFE_R_SATURATED; return 0;
    }
    if (q->rms < c->flatline_rms) { *why = SAFE_R_FLATLINE; return 0; }
    float d = q->mean - (float)EMG_ADC_MID;
    if (d < 0) d = -d;
    if (d > (float)c->dc_max_offset) { *why = SAFE_R_DC_DRIFT; return 0; }
    *why = SAFE_R_OK;
    return 1;
}

static inline void safe__go(safe_t *s, safe_state_t to, safe_reason_t why)
{
    if (s->state != to) { s->state = to; s->transitions++; }
    s->reason = why;
}

/* Advance one control tick.
 *   now_ms         monotonic milliseconds, supplied by the caller
 *   q              quality evidence for the most recent window
 *   estop          external stop request (button, host command)
 * Returns the new state. */
static inline safe_state_t safe_update(safe_t *s, const safe_config_t *c,
                                       uint32_t now_ms,
                                       const safe_quality_t *q,
                                       int estop)
{
    /* ESTOP dominates everything and latches. */
    if (estop || s->state == SAFE_ESTOP) {
        safe__go(s, SAFE_ESTOP, SAFE_R_ESTOP);
        return s->state;
    }

    /* A stale stream is a hard fault regardless of how good the last
     * window looked - old data is not evidence about now. */
    if ((uint32_t)(now_ms - s->last_sample_ms) > c->stale_after_ms) {
        s->good_run = 0;
        s->bad_run++;
        safe__go(s, SAFE_FAULT, SAFE_R_STALE);
        return s->state;
    }

    if (!q->envelope_ready) {
        safe__go(s, SAFE_INIT, SAFE_R_WARMUP);
        return s->state;
    }

    safe_reason_t why;
    int ok = safe_quality_ok(c, q, &why);

    if (ok) { s->good_run++; s->bad_run = 0; }
    else    { s->bad_run++;  s->good_run = 0; }

    switch (s->state) {
    case SAFE_INIT:
        safe__go(s, SAFE_IDLE, ok ? SAFE_R_OK : why);
        break;
    case SAFE_IDLE:
        if (!ok) safe__go(s, SAFE_HOLD, why);
        else if (s->good_run >= c->idle_to_active)
            safe__go(s, SAFE_ACTIVE, SAFE_R_OK);
        break;
    case SAFE_ACTIVE:
        if (!ok) safe__go(s, SAFE_HOLD, why);
        break;
    case SAFE_HOLD:
        /* One good window is not enough - that is what makes a marginal
         * signal chatter between moving and frozen. */
        if (ok && s->good_run >= c->hold_recover)
            safe__go(s, SAFE_ACTIVE, SAFE_R_OK);
        else if (!ok)
            safe__go(s, SAFE_HOLD, why);
        else
            s->reason = SAFE_R_RECOVERING;
        break;
    case SAFE_FAULT:
        /* Leaving FAULT is deliberately harder than leaving HOLD, and it
         * lands in IDLE rather than ACTIVE.
         *
         * The counter is reset on the way out. Without that reset the
         * qualification is vacuous: good_run has already reached
         * fault_recover (20), which trivially exceeds idle_to_active (10),
         * so IDLE would be passed through in a single tick and the gripper
         * would resume motion the instant the fault cleared. Resetting
         * makes recovery genuinely cost fault_recover + idle_to_active
         * consecutive good windows before anything moves. */
        if (ok && s->good_run >= c->fault_recover) {
            s->good_run = 0;
            safe__go(s, SAFE_IDLE, SAFE_R_OK);
        } else {
            s->reason = ok ? SAFE_R_RECOVERING : why;
        }
        break;
    case SAFE_ESTOP:
        break;                       /* unreachable, handled above */
    }
    return s->state;
}

/* Call whenever fresh samples arrive, so staleness is measured from data
 * rather than from the control tick. */
static inline void safe_note_samples(safe_t *s, uint32_t now_ms)
{
    s->last_sample_ms = now_ms;
}

static inline int safe_may_move(safe_state_t st) { return st == SAFE_ACTIVE; }

#endif /* EMG_SAFETY_H */
