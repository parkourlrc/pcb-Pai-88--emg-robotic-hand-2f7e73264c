/* Build-time configuration for the EMG gripper firmware.
 *
 * Everything the rest of the firmware needs to know about the rev E board
 * lives here, so there are no magic numbers buried in the logic.
 *
 * Pin numbers are Pico GPIO numbers, not physical pin numbers. The board
 * ties physical pin 31 to the analog front end, which is GP26 / ADC0, and
 * breaks out physical pins 1,2,4,5,6 (GP0..GP4) to the servo headers.
 */
#ifndef EMG_CONFIG_H
#define EMG_CONFIG_H

/* ---- acquisition ------------------------------------------------------ */
#define EMG_ADC_GPIO        26      /* GP26 = ADC0 = board net ADC_IN      */
#define EMG_ADC_CHANNEL     0
#define EMG_SAMPLE_RATE_HZ  1000
#define EMG_ADC_BITS        12
#define EMG_ADC_MAX         4095
#define EMG_ADC_MID         2048    /* VMID 1.65 V sits at mid-scale       */

/* ---- board constants, used only for reporting in physical units -------
 * Total analog gain is 20.61 (AD623 with R9 = 5.1k) x 16 (U2A with
 * R4 = 1k, R5 = 15k) = 329.7. One ADC count is therefore
 * (3.3 / 4096) / 329.7 = 2.443 uV referred to the skin.
 */
#define EMG_VREF            3.3f
#define EMG_TOTAL_GAIN      329.7f
#define EMG_UV_PER_COUNT    2.443f

/* ---- servo output ----------------------------------------------------- */
#define SERVO_GPIO          0       /* GP0 -> J5 pin 3 (SRV1)              */
#define SERVO_MIN_US        1000    /* fully open; trim during bring-up    */
#define SERVO_MAX_US        2000    /* fully closed                        */
#define SERVO_UPDATE_HZ     50      /* standard hobby servo frame rate     */

/* ---- control ---------------------------------------------------------- */
#define ENVELOPE_WINDOW_MS  200     /* moving RMS window                   */
#define CONTROL_RATE_HZ     50
#define APERTURE_SLEW_PER_S 2.0f    /* full travel in 0.5 s at most        */

/* ---- link ------------------------------------------------------------- */
#define USB_BAUD            921600  /* ignored by USB CDC, kept for tools  */
#define SAMPLES_PER_FRAME   20      /* -> 50 frames/s at 1 kHz             */

/* ---- LED -------------------------------------------------------------- */
#define STATUS_LED_GPIO     25      /* Pico on-board LED                   */

#endif /* EMG_CONFIG_H */
