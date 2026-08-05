/* Signal processing for a single sEMG channel at 1 kHz.
 *
 * Plain C99, no hardware dependencies, so the native tests exercise the
 * exact code that runs on the Pico.
 *
 * Chain, in order:
 *   1. remove the VMID DC offset (raw counts are centred on 2048)
 *   2. 20 Hz high-pass, 2nd order Butterworth  - kills motion artefact and
 *      electrode drift, which are large and slow
 *   3. 50 / 100 / 150 Hz notches, Q = 30       - mains and its harmonics
 *   4. rectify and take a moving RMS           - the envelope we control on
 *
 * There is deliberately NO digital low-pass. The analog front end already
 * rolls off at 442 Hz (R6 3.6k with C4 100n) and we sample at 1 kHz, so a
 * digital low-pass anywhere near 450 Hz would sit on top of Nyquist and buy
 * nothing. Adding one would only cost CPU and phase.
 *
 * Coefficients were generated with scipy for fs = 1000 Hz and pasted in
 * below; the native tests re-measure the response so a bad paste cannot
 * pass silently. Verified response of the chain as built:
 *     5 Hz -24.1 dB | 20 Hz -3.0 dB | 50 Hz -24.8 dB | 75 Hz -0.0 dB
 *   100 Hz -36.7 dB | 150 Hz -40.2 dB | 200..450 Hz flat
 */
#ifndef EMG_DSP_H
#define EMG_DSP_H

#include <math.h>
#include <stdint.h>
#include <string.h>

#include "config.h"

/* ---- biquad, transposed direct form II -------------------------------- */
typedef struct {
    float b0, b1, b2, a1, a2;   /* normalised so a0 == 1 */
    float z1, z2;
} biquad_t;

static inline float biquad_process(biquad_t *s, float x)
{
    float y = s->b0 * x + s->z1;
    s->z1 = s->b1 * x - s->a1 * y + s->z2;
    s->z2 = s->b2 * x - s->a2 * y;
    return y;
}

static inline void biquad_reset(biquad_t *s) { s->z1 = 0.0f; s->z2 = 0.0f; }

#define EMG_NUM_STAGES 4

/* scipy, fs = 1000 Hz:
 *   butter(2, 20/500, 'high')  and  iirnotch(f0, 30, 1000) for 50/100/150 */
static const float EMG_STAGE_COEFFS[EMG_NUM_STAGES][5] = {
    /* 20 Hz high-pass, Butterworth order 2 */
    {+0.9149691441f, -1.8299382882f, +0.9149691441f, -1.8226949252f, +0.8371816513f},
    /* 50 Hz notch, Q=30 */
    {+0.9947912377f, -1.8922053779f, +0.9947912377f, -1.8922053779f, +0.9895824753f},
    /* 100 Hz notch, Q=30 */
    {+0.9896361754f, -1.6012649682f, +0.9896361754f, -1.6012649682f, +0.9792723507f},
    /* 150 Hz notch, Q=30 */
    {+0.9845337086f, -1.1573887886f, +0.9845337086f, -1.1573887886f, +0.9690674172f},
};

/* ---- moving RMS ------------------------------------------------------- */
/* Materialised as a named integer constant rather than a macro that
 * expands to a division: the macro form both trips
 * bugprone-integer-division wherever it is used in a float context, and
 * silently truncates to zero for short windows (a 1 ms window at 500 Hz
 * would give a zero-length buffer and divide by zero in the envelope).
 * The static assert makes that a build failure instead. */
enum {
    EMG_ENV_LEN = (EMG_SAMPLE_RATE_HZ * ENVELOPE_WINDOW_MS) / 1000
};
/* _Static_assert is C11; the firmware translation unit is C++, which
 * spells it static_assert. Both compile the same headers, so pick per
 * language rather than assuming one. */
#if defined(__cplusplus)
#  define EMG_STATIC_ASSERT(c, m) static_assert(c, m)
#else
#  define EMG_STATIC_ASSERT(c, m) _Static_assert(c, m)
#endif
EMG_STATIC_ASSERT(EMG_ENV_LEN > 0,
                  "ENVELOPE_WINDOW_MS is too short for EMG_SAMPLE_RATE_HZ: "
                  "the moving-RMS window rounds down to zero samples");

typedef struct {
    biquad_t stages[EMG_NUM_STAGES];
    float    sq[EMG_ENV_LEN];   /* ring of squared samples */
    uint32_t idx;
    uint32_t filled;
    double   sum_sq;            /* double: a float accumulator drifts */
} emg_dsp_t;

static inline void emg_dsp_init(emg_dsp_t *d)
{
    for (int i = 0; i < EMG_NUM_STAGES; i++) {
        d->stages[i].b0 = EMG_STAGE_COEFFS[i][0];
        d->stages[i].b1 = EMG_STAGE_COEFFS[i][1];
        d->stages[i].b2 = EMG_STAGE_COEFFS[i][2];
        d->stages[i].a1 = EMG_STAGE_COEFFS[i][3];
        d->stages[i].a2 = EMG_STAGE_COEFFS[i][4];
        biquad_reset(&d->stages[i]);
    }
    memset(d->sq, 0, sizeof d->sq);
    d->idx = 0;
    d->filled = 0;
    d->sum_sq = 0.0;
}

/* Filter one raw ADC count and fold it into the envelope.
 * Returns the filtered sample, in counts, centred on zero. */
static inline float emg_dsp_push(emg_dsp_t *d, uint16_t raw)
{
    float x = (float)raw - (float)EMG_ADC_MID;
    for (int i = 0; i < EMG_NUM_STAGES; i++)
        x = biquad_process(&d->stages[i], x);

    d->sum_sq -= (double)d->sq[d->idx];
    d->sq[d->idx] = x * x;
    d->sum_sq += (double)d->sq[d->idx];
    d->idx = (d->idx + 1u) % EMG_ENV_LEN;
    if (d->filled < EMG_ENV_LEN) d->filled++;
    /* the running subtraction can leave a tiny negative residue */
    if (d->sum_sq < 0.0) d->sum_sq = 0.0;
    return x;
}

/* Current envelope in ADC counts RMS. Zero until the window has filled, so
 * a freshly started controller does not act on a partial window. */
static inline float emg_dsp_envelope(const emg_dsp_t *d)
{
    if (d->filled < EMG_ENV_LEN) return 0.0f;
    return (float)sqrt(d->sum_sq / (double)EMG_ENV_LEN);
}

static inline int emg_dsp_ready(const emg_dsp_t *d)
{
    return d->filled >= EMG_ENV_LEN;
}

/* Convenience: envelope expressed at the skin, in microvolts. */
static inline float emg_dsp_envelope_uv(const emg_dsp_t *d)
{
    return emg_dsp_envelope(d) * EMG_UV_PER_COUNT;
}

#endif /* EMG_DSP_H */
