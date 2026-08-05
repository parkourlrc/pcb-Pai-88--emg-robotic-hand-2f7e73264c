/* Proportional grip control: EMG envelope -> gripper aperture.
 *
 * Single-channel EMG can tell you reliably how HARD a muscle is working,
 * not which of five postures a hand is in. So there is no classifier here
 * and no model: contraction strength maps continuously onto how far the
 * gripper closes. That uses the information the one channel actually
 * carries, and it removes training data, a model file and a whole runtime
 * dependency from the system.
 *
 * Pure C99 so the native tests run the same code as the Pico.
 *
 * Aperture convention: 0.0 = fully open, 1.0 = fully closed.
 */
#ifndef EMG_CONTROL_H
#define EMG_CONTROL_H

#include <stdint.h>

#include "config.h"
#include "safety.h"

typedef struct {
    /* calibration, in envelope counts, captured during bring-up */
    float rest_level;      /* envelope with the muscle relaxed          */
    float max_level;       /* envelope at a comfortable hard squeeze    */
    float deadzone;        /* fraction of span ignored just above rest  */
    float slew_per_tick;   /* max aperture change per control tick      */
} grip_config_t;

static inline void grip_config_defaults(grip_config_t *c)
{
    /* Placeholders only. Real values come from calibration - see
     * grip_calibrate(). Shipping a guess here would make the gripper
     * respond to noise on one arm and not at all on another. */
    c->rest_level    = 2.0f;
    c->max_level     = 60.0f;
    /* A deadzone matters more than it looks: without it the gripper
     * twitches continuously on resting noise, which reads as a broken
     * device even though the signal is fine. */
    c->deadzone      = 0.08f;
    c->slew_per_tick = APERTURE_SLEW_PER_S / (float)CONTROL_RATE_HZ;
}

/* Build a calibration from two measured envelope levels. Returns 0 and
 * leaves the config untouched if the span is not usable, so a failed
 * calibration cannot install a divide-by-zero or an inverted map. */
static inline int grip_calibrate(grip_config_t *c, float rest, float max_)
{
    if (!(max_ > rest)) return 0;
    if ((max_ - rest) < 1.0f) return 0;   /* span too small to be real */
    c->rest_level = rest;
    c->max_level  = max_;
    return 1;
}

typedef struct {
    float aperture;        /* current commanded aperture, 0..1 */
} grip_t;

static inline void grip_init(grip_t *g) { g->aperture = 0.0f; }

static inline float grip__clamp01(float v)
{
    if (v < 0.0f) return 0.0f;
    if (v > 1.0f) return 1.0f;
    return v;
}

/* Map an envelope level to a target aperture, before slew limiting. */
static inline float grip_target(const grip_config_t *c, float envelope)
{
    float span = c->max_level - c->rest_level;
    if (span <= 0.0f) return 0.0f;
    float t = (envelope - c->rest_level) / span;
    t = grip__clamp01(t);
    if (t < c->deadzone) return 0.0f;
    /* rescale so the gripper still reaches fully closed despite the
     * deadzone eating the bottom of the range */
    return grip__clamp01((t - c->deadzone) / (1.0f - c->deadzone));
}

/* Advance one control tick and return the aperture to command.
 *
 * The safety state decides what motion is permitted:
 *   ACTIVE          track the envelope, slew limited
 *   HOLD            freeze exactly - the commanded value does not change
 *   INIT/IDLE/FAULT open, slew limited, so a lost signal releases whatever
 *                   is held rather than gripping it indefinitely
 *   ESTOP           open immediately
 *
 * Opening on fault rather than freezing is the one genuinely contestable
 * choice in this file. For a bench gripper, releasing is the safer failure:
 * a frozen grip with no signal can keep crushing, and there is no force
 * sensor on rev E to notice. If this ever drives something that must not be
 * dropped, revisit exactly here.
 */
static inline float grip_update(grip_t *g, const grip_config_t *c,
                                safe_state_t st, float envelope)
{
    float target;
    switch (st) {
    case SAFE_ACTIVE:
        target = grip_target(c, envelope);
        break;
    case SAFE_HOLD:
        return g->aperture;          /* frozen: no movement at all */
    case SAFE_ESTOP:
        g->aperture = 0.0f;          /* release now, not gradually */
        return g->aperture;
    default:                          /* INIT, IDLE, FAULT */
        target = 0.0f;
        break;
    }

    float d = target - g->aperture;
    if (d >  c->slew_per_tick) d =  c->slew_per_tick;
    if (d < -c->slew_per_tick) d = -c->slew_per_tick;
    g->aperture = grip__clamp01(g->aperture + d);
    return g->aperture;
}

/* Aperture -> servo pulse width. Kept separate from the control law so the
 * mechanical trim during bring-up cannot disturb the control maths. */
static inline uint16_t grip_pulse_us(float aperture)
{
    float a = grip__clamp01(aperture);
    return (uint16_t)(SERVO_MIN_US +
                      a * (float)(SERVO_MAX_US - SERVO_MIN_US));
}

#endif /* EMG_CONTROL_H */
