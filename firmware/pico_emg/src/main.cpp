/* EMG gripper firmware for the rev E board.
 *
 * ============================ SAFETY ============================
 *  BATTERY POWER ONLY. Never wear the electrodes while anything
 *  connected to this board touches mains. The driven-right-leg circuit
 *  actively drives your reference electrode; R14 (390k) holds body
 *  current under 5 uA, but that argument depends on the board being
 *  galvanically isolated from mains. The Pico's USB is the one path
 *  that can reach it, so the host must run on its own battery.
 * ================================================================
 *
 * The entire control loop runs here, on the Pico. The host is an
 * observer: it plots, calibrates and logs. Unplug it and the gripper
 * keeps working, because the thing deciding whether a motor may move
 * should not depend on a USB cable.
 *
 * Division of labour:
 *   ISR at 1 kHz   read ADC0, push one sample into a ring buffer. That
 *                  is all. Never touches USB, never blocks.
 *   loop()         drains the ring, runs the DSP, evaluates quality,
 *                  advances the safety state machine, drives the servo
 *                  at 50 Hz, and streams telemetry frames.
 *
 * All the logic worth testing lives in the headers, which compile on a
 * host too - see test/run_tests.sh. This file is the wiring.
 */
#include <Arduino.h>
#include <Servo.h>
#include <hardware/adc.h>
#include <pico/time.h>

/* Header-only, everything static inline, so there is no linkage to
 * declare. Wrapping these in extern "C" would only drag <math.h> and
 * <string.h> in with C linkage and break the C++ build. */
#include "config.h"
#include "protocol.h"
#include "dsp.h"
#include "safety.h"
#include "control.h"

/* ---- sample ring, written by the ISR, drained by loop() --------------- */
#define RING_LEN 512                       /* 512 ms of slack at 1 kHz */
static volatile uint16_t g_ring[RING_LEN];
static volatile uint32_t g_head = 0;       /* ISR writes here */
static volatile uint32_t g_tail = 0;       /* loop() reads here */
static volatile bool     g_overrun = false;
static volatile uint32_t g_first_us = 0;

static repeating_timer_t g_timer;

static bool sample_isr(repeating_timer_t *)
{
    uint16_t v = adc_read();               /* 12-bit, ADC0 */
    uint32_t head = g_head;
    uint32_t next = (head + 1u) % RING_LEN;
    if (next == g_tail) {
        /* USB backed up and we are about to lap the reader. Drop the
         * sample rather than corrupt the stream, and flag it so the host
         * knows the gap is real and not a CRC problem. */
        g_overrun = true;
    } else {
        if (head == g_tail) g_first_us = time_us_32();
        g_ring[head] = v;
        g_head = next;
    }
    return true;                           /* keep the timer running */
}

/* ---- state ------------------------------------------------------------ */
static emg_dsp_t      g_dsp;
static safe_t         g_safe;
static safe_config_t  g_safe_cfg;
static grip_t         g_grip;
static grip_config_t  g_grip_cfg;
static Servo          g_servo;

static uint16_t g_seq = 0;
static uint16_t g_frame_samples[SAMPLES_PER_FRAME];
static uint8_t  g_frame_n = 0;
static uint8_t  g_frame_flags = 0;
static uint32_t g_frame_t_us = 0;

/* per-window quality accumulators */
static uint16_t g_sat_hi = 0, g_sat_lo = 0;
static double   g_sum = 0.0;
static uint32_t g_count = 0;

static uint32_t g_last_control_ms = 0;

void setup()
{
    Serial.begin(USB_BAUD);

    pinMode(STATUS_LED_GPIO, OUTPUT);
    digitalWrite(STATUS_LED_GPIO, LOW);

    adc_init();
    adc_gpio_init(EMG_ADC_GPIO);
    adc_select_input(EMG_ADC_CHANNEL);

    emg_dsp_init(&g_dsp);
    safe_config_defaults(&g_safe_cfg);
    safe_init(&g_safe, millis());
    grip_config_defaults(&g_grip_cfg);
    grip_init(&g_grip);

    g_servo.attach(SERVO_GPIO, SERVO_MIN_US, SERVO_MAX_US);
    g_servo.writeMicroseconds(grip_pulse_us(0.0f));   /* start open */

    /* Hardware timer, not a delay() loop: the sample interval has to be
     * exactly 1 ms regardless of what loop() is doing, or the filter
     * coefficients (designed for fs = 1000) are wrong. */
    add_repeating_timer_us(-(1000000 / EMG_SAMPLE_RATE_HZ),
                           sample_isr, nullptr, &g_timer);
}

static void emit_frame_if_full()
{
    if (g_frame_n < SAMPLES_PER_FRAME) return;
    uint8_t buf[P1_HEADER_SIZE + SAMPLES_PER_FRAME * 2 + P1_CRC_SIZE];
    size_t n = p1_encode(buf, g_seq++, g_frame_t_us, g_frame_flags,
                         g_frame_samples, g_frame_n);
    /* Non-blocking by policy: if the host is not draining, drop telemetry
     * rather than stall the control loop behind it. */
    if (Serial.availableForWrite() >= (int)n) Serial.write(buf, n);
    g_frame_n = 0;
    g_frame_flags = 0;
}

void loop()
{
    /* ---- drain everything the ISR has queued ---- */
    bool got_any = false;
    while (g_tail != g_head) {
        uint16_t raw = g_ring[g_tail];
        g_tail = (g_tail + 1u) % RING_LEN;
        got_any = true;

        emg_dsp_push(&g_dsp, raw);

        if (raw >= g_safe_cfg.sat_high) g_sat_hi++;
        if (raw <= g_safe_cfg.sat_low)  g_sat_lo++;
        g_sum += (double)raw;
        g_count++;

        if (g_frame_n == 0) g_frame_t_us = g_first_us;
        if (g_frame_n < SAMPLES_PER_FRAME)
            g_frame_samples[g_frame_n++] = raw;
        if (raw >= g_safe_cfg.sat_high) g_frame_flags |= P1_F_SAT_HIGH;
        if (raw <= g_safe_cfg.sat_low)  g_frame_flags |= P1_F_SAT_LOW;
        emit_frame_if_full();
    }

    uint32_t now = millis();
    if (got_any) safe_note_samples(&g_safe, now);
    if (g_overrun) { g_frame_flags |= P1_F_OVERRUN; g_overrun = false; }

    /* ---- control tick ---- */
    if ((uint32_t)(now - g_last_control_ms) < (1000u / CONTROL_RATE_HZ)) return;
    g_last_control_ms = now;

    safe_quality_t q;
    q.sat_high_count = g_sat_hi;
    q.sat_low_count  = g_sat_lo;
    q.rms            = emg_dsp_envelope(&g_dsp);
    q.mean           = g_count ? (float)(g_sum / (double)g_count)
                               : (float)EMG_ADC_MID;
    q.envelope_ready = emg_dsp_ready(&g_dsp);
    g_sat_hi = g_sat_lo = 0; g_sum = 0.0; g_count = 0;

    if (!q.envelope_ready) g_frame_flags |= P1_F_CONTACT_SUSPECT;

    safe_state_t st = safe_update(&g_safe, &g_safe_cfg, now, &q, 0);
    float aperture = grip_update(&g_grip, &g_grip_cfg, st,
                                 emg_dsp_envelope(&g_dsp));
    g_servo.writeMicroseconds(grip_pulse_us(aperture));

    /* Solid while tracking, blinking otherwise - readable across a bench
     * without a host attached. */
    digitalWrite(STATUS_LED_GPIO,
                 st == SAFE_ACTIVE ? HIGH : ((now / 250u) & 1u));
}
