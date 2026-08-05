/* Native tests for the EMG gripper firmware logic.
 *
 * Builds and runs on the Mac with cc. There is no RP2040 toolchain or
 * hardware here, so this harness is the only thing standing between
 * "written" and "verified" for the protocol, DSP, control law and safety
 * state machine. main.cpp is the only file it cannot reach.
 *
 *   cd firmware/pico_emg && ./test/run_tests.sh
 */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "../include/config.h"
#include "../include/protocol.h"
#include "../include/dsp.h"
#include "../include/safety.h"
#include "../include/control.h"

static int g_pass = 0, g_fail = 0;

static void ok(int cond, const char *name, const char *detail)
{
    if (cond) { g_pass++; printf("  [PASS] %s\n", name); }
    else      { g_fail++; printf("  [FAIL] %s   %s\n", name, detail ? detail : ""); }
}

static void okf(int cond, const char *name, const char *fmt, double a, double b)
{
    char buf[256];
    snprintf(buf, sizeof buf, fmt, a, b);
    ok(cond, name, buf);
}

/* ------------------------------------------------------------------ */
static void test_protocol(void)
{
    printf("protocol\n");

    ok(p1_crc16((const uint8_t *)"123456789", 9) == 0x29B1,
       "CRC-16/CCITT-FALSE known vector 123456789 -> 0x29B1", NULL);

    ok(p1_frame_size(20) == 54, "frame is 54 bytes for n=20", NULL);

    uint16_t s[20];
    for (int i = 0; i < 20; i++) s[i] = (uint16_t)(2048 + i * 7);
    uint8_t buf[64];
    size_t n = p1_encode(buf, 0xBEEF, 0x12345678u, P1_F_SAT_HIGH, s, 20);
    ok(n == 54, "encode returns 54", NULL);

    p1_frame_t f; uint16_t out[20];
    ok(p1_decode(buf, n, &f, out, 20) == P1_OK, "decode accepts a good frame", NULL);
    ok(f.seq == 0xBEEF, "seq round-trips", NULL);
    ok(f.t_us == 0x12345678u, "t_us round-trips", NULL);
    ok(f.flags == P1_F_SAT_HIGH, "flags round-trip", NULL);
    ok(f.n == 20, "n round-trips", NULL);
    ok(memcmp(out, s, sizeof s) == 0, "every sample round-trips", NULL);

    /* Every single-bit corruption must be REJECTED. Header fields are
     * caught by a more specific check than the CRC - a flipped bit in the
     * magic, the version or the sample count is a malformed frame, not
     * merely a corrupt one - so assert rejection here and pin the CRC
     * specifically over the sample payload below. */
    int caught = 1, first_miss = -1;
    for (size_t i = 0; i < n && caught; i++) {
        for (int b = 0; b < 8 && caught; b++) {
            uint8_t save = buf[i];
            buf[i] ^= (uint8_t)(1u << b);
            if (p1_decode(buf, n, &f, out, 20) == P1_OK) {
                caught = 0; first_miss = (int)i;
            }
            buf[i] = save;
        }
    }
    {
        char d[64]; snprintf(d, sizeof d, "first missed byte %d", first_miss);
        ok(caught, "every single-bit corruption anywhere is rejected", d);
    }

    int payload_crc = 1;
    for (size_t i = P1_HEADER_SIZE; i < n - 2 && payload_crc; i++) {
        uint8_t save = buf[i];
        buf[i] ^= 0x01;
        if (p1_decode(buf, n, &f, out, 20) != P1_ERR_CRC) payload_crc = 0;
        buf[i] = save;
    }
    ok(payload_crc, "corruption in the sample payload is caught by CRC", NULL);

    uint8_t bad[64]; memcpy(bad, buf, n);
    bad[0] = 0x00;
    ok(p1_decode(bad, n, &f, out, 20) == P1_ERR_MAGIC, "bad magic detected", NULL);
    memcpy(bad, buf, n); bad[2] = 99;
    ok(p1_decode(bad, n, &f, out, 20) == P1_ERR_VERSION, "bad version detected", NULL);
    ok(p1_decode(buf, 20, &f, out, 20) == P1_ERR_LENGTH, "truncated frame detected", NULL);
    ok(p1_decode(buf, n, &f, out, 4) == P1_ERR_LENGTH, "undersized sample buffer refused", NULL);

    ok(p1_seq_gap(100, 101) == 0, "consecutive seq = no gap", NULL);
    ok(p1_seq_gap(100, 104) == 3, "seq gap counted", NULL);
    ok(p1_seq_gap(65535, 0) == 0, "seq wrap 65535->0 is NOT a gap of 65535", NULL);
}

/* ------------------------------------------------------------------ */
/* drive a sine through the chain and return steady-state peak amplitude */
static float chain_peak(float freq, float amp)
{
    emg_dsp_t d; emg_dsp_init(&d);
    const int settle = 4000, measure = 2000;
    float peak = 0.0f;
    for (int i = 0; i < settle + measure; i++) {
        float t = (float)i / (float)EMG_SAMPLE_RATE_HZ;
        float v = amp * sinf(2.0f * (float)M_PI * freq * t);
        float y = emg_dsp_push(&d, (uint16_t)(EMG_ADC_MID + (int)lrintf(v)));
        if (i >= settle && fabsf(y) > peak) peak = fabsf(y);
    }
    return peak;
}

static void test_dsp(void)
{
    printf("dsp\n");

    const float amp = 200.0f;
    float p50  = chain_peak(50.0f,  amp);
    float p100 = chain_peak(100.0f, amp);
    float p150 = chain_peak(150.0f, amp);
    float p75  = chain_peak(75.0f,  amp);
    float p200 = chain_peak(200.0f, amp);
    float p5   = chain_peak(5.0f,   amp);

    okf(20.0f * log10f(p50 / amp) < -18.0f,
        "50 Hz mains notched by at least 18 dB", "measured %.1f dB (peak %.1f)",
        20.0 * log10(p50 / amp), p50);
    okf(20.0f * log10f(p100 / amp) < -18.0f,
        "100 Hz harmonic notched", "measured %.1f dB", 20.0 * log10(p100 / amp), 0);
    okf(20.0f * log10f(p150 / amp) < -18.0f,
        "150 Hz harmonic notched", "measured %.1f dB", 20.0 * log10(p150 / amp), 0);
    okf(fabsf(20.0f * log10f(p75 / amp)) < 2.0f,
        "75 Hz EMG content passes within 2 dB", "measured %.1f dB",
        20.0 * log10(p75 / amp), 0);
    okf(fabsf(20.0f * log10f(p200 / amp)) < 2.0f,
        "200 Hz EMG content passes within 2 dB", "measured %.1f dB",
        20.0 * log10(p200 / amp), 0);
    okf(20.0f * log10f(p5 / amp) < -15.0f,
        "5 Hz motion artefact rejected", "measured %.1f dB",
        20.0 * log10(p5 / amp), 0);

    /* envelope of a pure sine of amplitude A is A/sqrt(2) */
    emg_dsp_t d; emg_dsp_init(&d);
    ok(!emg_dsp_ready(&d), "envelope not ready before the window fills", NULL);
    ok(emg_dsp_envelope(&d) == 0.0f, "envelope reads zero during warm-up", NULL);
    for (int i = 0; i < 8000; i++) {
        float t = (float)i / (float)EMG_SAMPLE_RATE_HZ;
        float v = 200.0f * sinf(2.0f * (float)M_PI * 75.0f * t);
        emg_dsp_push(&d, (uint16_t)(EMG_ADC_MID + (int)lrintf(v)));
    }
    ok(emg_dsp_ready(&d), "envelope ready after the window fills", NULL);
    float expect = 200.0f / sqrtf(2.0f);
    float got = emg_dsp_envelope(&d);
    okf(fabsf(got - expect) / expect < 0.05f,
        "envelope of a 200-count sine is A/sqrt(2)", "expected %.1f, got %.1f",
        expect, got);

    /* a DC input must produce an envelope of ~0: the 20 Hz high-pass
     * removes it entirely. This is what stops electrode offset from being
     * read as a permanent contraction. */
    emg_dsp_t dc; emg_dsp_init(&dc);
    for (int i = 0; i < 8000; i++) emg_dsp_push(&dc, EMG_ADC_MID + 500);
    okf(emg_dsp_envelope(&dc) < 1.0f,
        "a large DC offset does not register as contraction",
        "envelope %.3f counts", emg_dsp_envelope(&dc), 0);
}

/* ------------------------------------------------------------------ */
static safe_quality_t good_q(void)
{
    safe_quality_t q = {0, 0, 12.0f, (float)EMG_ADC_MID, 1};
    return q;
}

static void test_safety(void)
{
    printf("safety\n");
    safe_config_t c; safe_config_defaults(&c);
    safe_t s; safe_init(&s, 0);
    safe_quality_t q = good_q();

    ok(s.state == SAFE_INIT, "starts in INIT", NULL);

    /* warm-up gate: not ready keeps us in INIT no matter how long */
    safe_quality_t warm = good_q(); warm.envelope_ready = 0;
    for (int i = 0; i < 50; i++) { safe_note_samples(&s, (uint32_t)i); safe_update(&s, &c, (uint32_t)i, &warm, 0); }
    ok(s.state == SAFE_INIT, "stays in INIT until the envelope is ready", NULL);

    uint32_t t = 100;
    safe_note_samples(&s, t); safe_update(&s, &c, t, &q, 0);
    ok(s.state == SAFE_IDLE, "INIT -> IDLE once ready", NULL);

    for (int i = 0; i < c.idle_to_active; i++) {
        t += 20; safe_note_samples(&s, t); safe_update(&s, &c, t, &q, 0);
    }
    ok(s.state == SAFE_ACTIVE, "IDLE -> ACTIVE after enough good ticks", NULL);
    ok(safe_may_move(s.state), "motion permitted only in ACTIVE", NULL);

    /* one bad window drops to HOLD immediately */
    safe_quality_t bad = good_q(); bad.rms = 0.0f;   /* flatline */
    t += 20; safe_note_samples(&s, t); safe_update(&s, &c, t, &bad, 0);
    ok(s.state == SAFE_HOLD, "a bad window drops ACTIVE -> HOLD", NULL);
    ok(s.reason == SAFE_R_FLATLINE, "reason reports flatline", NULL);
    ok(!safe_may_move(s.state), "no motion permitted in HOLD", NULL);

    /* recovery needs several consecutive good windows, not one */
    t += 20; safe_note_samples(&s, t); safe_update(&s, &c, t, &q, 0);
    ok(s.state == SAFE_HOLD, "one good window does NOT leave HOLD", NULL);
    for (int i = 0; i < c.hold_recover; i++) {
        t += 20; safe_note_samples(&s, t); safe_update(&s, &c, t, &q, 0);
    }
    ok(s.state == SAFE_ACTIVE, "HOLD -> ACTIVE after hold_recover good ticks", NULL);

    /* a single bad window resets the recovery counter */
    t += 20; safe_note_samples(&s, t); safe_update(&s, &c, t, &bad, 0);
    for (int i = 0; i < c.hold_recover - 1; i++) {
        t += 20; safe_note_samples(&s, t); safe_update(&s, &c, t, &q, 0);
    }
    t += 20; safe_note_samples(&s, t); safe_update(&s, &c, t, &bad, 0);
    ok(s.good_run == 0, "a bad window resets the good run to zero", NULL);

    /* staleness is a hard fault even with a perfect last window */
    safe_t s2; safe_init(&s2, 0); safe_note_samples(&s2, 0);
    safe_update(&s2, &c, 0, &q, 0);
    safe_update(&s2, &c, c.stale_after_ms + 1, &q, 0);
    ok(s2.state == SAFE_FAULT, "a stale stream forces FAULT", NULL);
    ok(s2.reason == SAFE_R_STALE, "reason reports staleness", NULL);

    /* leaving FAULT is harder than leaving HOLD */
    uint32_t t2 = c.stale_after_ms + 1;
    for (int i = 0; i < c.hold_recover + 1; i++) {
        t2 += 10; safe_note_samples(&s2, t2); safe_update(&s2, &c, t2, &q, 0);
    }
    ok(s2.state == SAFE_FAULT, "FAULT does not clear on hold_recover ticks", NULL);
    /* Step exactly to the transition rather than counting blind: the
     * moment FAULT clears is what we want to inspect. */
    int ticks_in_fault = 0;
    while (s2.state == SAFE_FAULT && ticks_in_fault < 500) {
        t2 += 10; safe_note_samples(&s2, t2);
        safe_update(&s2, &c, t2, &q, 0);
        ticks_in_fault++;
    }
    ok(s2.state == SAFE_IDLE, "FAULT clears into IDLE, never straight to ACTIVE", NULL);
    ok(!safe_may_move(s2.state), "still no motion the moment FAULT clears", NULL);
    ok(s2.good_run == 0, "leaving FAULT resets the good run so IDLE re-qualifies", NULL);

    /* the IDLE qualification is real: idle_to_active-1 more ticks must not
     * be enough, the next one must be */
    for (int i = 0; i < c.idle_to_active - 1; i++) {
        t2 += 10; safe_note_samples(&s2, t2); safe_update(&s2, &c, t2, &q, 0);
    }
    ok(s2.state == SAFE_IDLE, "IDLE is not skipped on the way out of FAULT", NULL);
    t2 += 10; safe_note_samples(&s2, t2); safe_update(&s2, &c, t2, &q, 0);
    ok(s2.state == SAFE_ACTIVE, "ACTIVE only after the full re-qualification", NULL);

    /* ESTOP dominates and latches */
    safe_t s3; safe_init(&s3, 0); safe_note_samples(&s3, 0);
    safe_update(&s3, &c, 0, &q, 1);
    ok(s3.state == SAFE_ESTOP, "estop enters ESTOP", NULL);
    int escaped = 0;
    for (int i = 0; i < 500; i++) {
        safe_note_samples(&s3, (uint32_t)i);
        if (safe_update(&s3, &c, (uint32_t)i, &q, 0) != SAFE_ESTOP) escaped = 1;
    }
    ok(!escaped, "ESTOP latches: 500 perfect windows cannot clear it", NULL);
    safe_reset(&s3, 0);
    ok(s3.state == SAFE_INIT, "only reset() clears ESTOP", NULL);

    /* the important negative case: a genuinely resting muscle is quiet but
     * must NOT be called a flatline, or the gripper faults whenever the
     * user relaxes */
    safe_quality_t rest = good_q();
    rest.rms = 1.5f;                    /* quiet, but real electrode noise */
    safe_reason_t why = SAFE_R_NONE;
    ok(safe_quality_ok(&c, &rest, &why), "a quiet resting muscle is OK, not a fault", NULL);
    ok(why == SAFE_R_OK, "resting reason is OK", NULL);

    /* saturation and DC drift are detected */
    safe_quality_t sat = good_q(); sat.sat_high_count = 50;
    ok(!safe_quality_ok(&c, &sat, &why) && why == SAFE_R_SATURATED, "saturation detected", NULL);
    safe_quality_t drift = good_q(); drift.mean = (float)EMG_ADC_MID + 900.0f;
    ok(!safe_quality_ok(&c, &drift, &why) && why == SAFE_R_DC_DRIFT, "DC drift detected", NULL);

    /* determinism: the same input sequence gives the same state sequence */
    safe_t a, b2; safe_init(&a, 0); safe_init(&b2, 0);
    int same = 1;
    for (uint32_t i = 0; i < 200; i++) {
        const safe_quality_t *qq = (i % 7 == 0) ? &bad : &q;
        safe_note_samples(&a, i); safe_note_samples(&b2, i);
        if (safe_update(&a, &c, i, qq, 0) != safe_update(&b2, &c, i, qq, 0)) same = 0;
    }
    ok(same, "state machine is deterministic", NULL);
}

/* ------------------------------------------------------------------ */
static void test_control(void)
{
    printf("control\n");
    grip_config_t c; grip_config_defaults(&c);
    ok(grip_calibrate(&c, 3.0f, 63.0f), "calibration accepts a sane span", NULL);
    ok(!grip_calibrate(&c, 50.0f, 10.0f), "calibration rejects an inverted span", NULL);
    ok(!grip_calibrate(&c, 10.0f, 10.2f), "calibration rejects a span too small to be real", NULL);
    ok(c.rest_level == 3.0f && c.max_level == 63.0f,
       "a rejected calibration leaves the old one intact", NULL);

    ok(grip_target(&c, 3.0f) == 0.0f, "at rest the target is fully open", NULL);
    ok(grip_target(&c, 1.0f) == 0.0f, "below rest clamps to open", NULL);
    okf(fabsf(grip_target(&c, 63.0f) - 1.0f) < 1e-5,
        "at max the target is fully closed", "got %.4f (want %.4f)",
        grip_target(&c, 63.0f), 1.0);
    ok(grip_target(&c, 1000.0f) == 1.0f, "above max clamps to closed", NULL);

    /* deadzone: just above rest must still read as open */
    float just_above = 3.0f + 0.04f * 60.0f;   /* 4% of span, deadzone is 8% */
    ok(grip_target(&c, just_above) == 0.0f, "deadzone suppresses resting jitter", NULL);
    float past = 3.0f + 0.20f * 60.0f;
    ok(grip_target(&c, past) > 0.0f, "past the deadzone the gripper responds", NULL);

    /* slew limiting: a full-scale command cannot arrive in one tick */
    grip_t g; grip_init(&g);
    float first = grip_update(&g, &c, SAFE_ACTIVE, 63.0f);
    okf(first <= c.slew_per_tick + 1e-6f,
        "a full-scale step is slew limited", "moved %.4f, limit %.4f",
        first, c.slew_per_tick);
    int ticks = 1;
    while (g.aperture < 0.999f && ticks < 1000) {
        grip_update(&g, &c, SAFE_ACTIVE, 63.0f); ticks++;
    }
    okf(ticks >= (int)(1.0f / c.slew_per_tick) - 1,
        "full travel takes the expected number of ticks", "%d ticks (>= %d)",
        (double)ticks, (double)((int)(1.0f / c.slew_per_tick) - 1));

    /* HOLD freezes bit-exactly */
    float held = g.aperture;
    for (int i = 0; i < 20; i++) grip_update(&g, &c, SAFE_HOLD, 3.0f);
    ok(g.aperture == held, "HOLD freezes the aperture bit-exactly", NULL);

    /* ESTOP releases immediately, not gradually */
    grip_update(&g, &c, SAFE_ESTOP, 63.0f);
    ok(g.aperture == 0.0f, "ESTOP opens immediately", NULL);

    /* FAULT releases, but slew limited */
    grip_t g2; grip_init(&g2);
    for (int i = 0; i < 200; i++) grip_update(&g2, &c, SAFE_ACTIVE, 63.0f);
    float before = g2.aperture;
    float after = grip_update(&g2, &c, SAFE_FAULT, 63.0f);
    ok(after < before, "FAULT releases the grip", NULL);
    okf((before - after) <= c.slew_per_tick + 1e-6f,
        "FAULT release is slew limited, not a snap", "moved %.4f, limit %.4f",
        before - after, c.slew_per_tick);

    /* aperture never escapes [0,1] under any state or input */
    grip_t g3; grip_init(&g3);
    int in_range = 1;
    safe_state_t states[] = {SAFE_INIT, SAFE_IDLE, SAFE_ACTIVE, SAFE_HOLD,
                             SAFE_FAULT, SAFE_ESTOP};
    for (int i = 0; i < 2000; i++) {
        safe_state_t st = states[i % 6];
        float env = (i % 13) * 20.0f - 40.0f;      /* includes negatives */
        float a = grip_update(&g3, &c, st, env);
        if (a < 0.0f || a > 1.0f) in_range = 0;
    }
    ok(in_range, "aperture stays within [0,1] across every state and input", NULL);

    /* pulse width mapping */
    ok(grip_pulse_us(0.0f) == SERVO_MIN_US, "aperture 0 -> min pulse", NULL);
    ok(grip_pulse_us(1.0f) == SERVO_MAX_US, "aperture 1 -> max pulse", NULL);
    ok(grip_pulse_us(-5.0f) == SERVO_MIN_US, "out-of-range aperture clamps low", NULL);
    ok(grip_pulse_us(9.0f) == SERVO_MAX_US, "out-of-range aperture clamps high", NULL);
}

int main(void)
{
    printf("EMG gripper firmware - native logic tests\n\n");
    test_protocol();
    test_dsp();
    test_safety();
    test_control();
    printf("\n%d passed, %d failed\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
