/* PICO1 wire protocol - telemetry from the Pico to a host.
 *
 * Deliberately plain C99 with no hardware dependencies, so the exact same
 * source compiles into the firmware AND into the native test harness. A
 * protocol implementation that is only exercised on the target is a
 * protocol implementation nobody has tested.
 *
 * Frame layout, little-endian throughout (the RP2040 is little-endian):
 *
 *   offset  size   field
 *    0      2      magic   = 0xE2, 0xA2
 *    2      1      version = 1
 *    3      1      flags   (see below)
 *    4      2      seq     uint16, +1 per frame, wraps
 *    6      4      t_us    uint32, micros() of the FIRST sample
 *   10      1      n       uint8, sample count
 *   11      1      reserved = 0
 *   12      n*2    samples uint16 each, raw 12-bit ADC, right-justified
 *   12+n*2 2       crc16   CRC-16/CCITT-FALSE over bytes [0, 12+n*2)
 *
 * With n = 20 a frame is 54 bytes and the link carries 50 frames/s.
 *
 * Telemetry only. The control loop does not depend on this link - see
 * safety.h. If the host disappears, the gripper keeps working.
 */
#ifndef EMG_PROTOCOL_H
#define EMG_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define P1_MAGIC0        0xE2u
#define P1_MAGIC1        0xA2u
#define P1_VERSION       1u
#define P1_HEADER_SIZE   12u
#define P1_CRC_SIZE      2u

/* flags byte */
#define P1_F_SAT_HIGH        0x01u  /* a sample hit the top of the range   */
#define P1_F_SAT_LOW         0x02u  /* ...or the bottom                    */
#define P1_F_CONTACT_SUSPECT 0x04u  /* electrode contact looks bad         */
#define P1_F_OVERRUN         0x08u  /* sampler outran the USB writer       */

static inline size_t p1_frame_size(uint8_t n)
{
    return (size_t)P1_HEADER_SIZE + (size_t)n * 2u + P1_CRC_SIZE;
}

/* CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no final
 * xor. Known-answer: crc16("123456789") == 0x29B1. The native tests assert
 * exactly that, which is what stops the firmware and the host quietly
 * disagreeing about the polynomial. */
static inline uint16_t p1_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFFu;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int b = 0; b < 8; b++)
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u)
                                  : (uint16_t)(crc << 1);
    }
    return crc;
}

/* Encode a frame into out (must hold p1_frame_size(n)). Returns the number
 * of bytes written. Byte placement is explicit rather than a struct cast,
 * so padding and alignment can never corrupt the layout. */
static inline size_t p1_encode(uint8_t *out, uint16_t seq, uint32_t t_us,
                               uint8_t flags, const uint16_t *samples,
                               uint8_t n)
{
    size_t i = 0;
    out[i++] = P1_MAGIC0;
    out[i++] = P1_MAGIC1;
    out[i++] = P1_VERSION;
    out[i++] = flags;
    out[i++] = (uint8_t)(seq & 0xFFu);
    out[i++] = (uint8_t)(seq >> 8);
    out[i++] = (uint8_t)(t_us & 0xFFu);
    out[i++] = (uint8_t)((t_us >> 8) & 0xFFu);
    out[i++] = (uint8_t)((t_us >> 16) & 0xFFu);
    out[i++] = (uint8_t)((t_us >> 24) & 0xFFu);
    out[i++] = n;
    out[i++] = 0u;                       /* reserved */
    for (uint8_t s = 0; s < n; s++) {
        out[i++] = (uint8_t)(samples[s] & 0xFFu);
        out[i++] = (uint8_t)(samples[s] >> 8);
    }
    uint16_t crc = p1_crc16(out, i);
    out[i++] = (uint8_t)(crc & 0xFFu);
    out[i++] = (uint8_t)(crc >> 8);
    return i;
}

typedef struct {
    uint16_t seq;
    uint32_t t_us;
    uint8_t  flags;
    uint8_t  n;
    const uint16_t *samples;   /* points into the caller's buffer */
} p1_frame_t;

typedef enum {
    P1_OK = 0,
    P1_ERR_MAGIC,
    P1_ERR_VERSION,
    P1_ERR_LENGTH,
    P1_ERR_CRC
} p1_status_t;

/* Decode in place. samples_out must have room for n uint16 values. The
 * distinct error codes matter: a caller needs to tell a truncated frame
 * (resync) from a corrupt one (count it and carry on). */
static inline p1_status_t p1_decode(const uint8_t *buf, size_t len,
                                    p1_frame_t *out, uint16_t *samples_out,
                                    size_t samples_cap)
{
    if (len < P1_HEADER_SIZE + P1_CRC_SIZE) return P1_ERR_LENGTH;
    if (buf[0] != P1_MAGIC0 || buf[1] != P1_MAGIC1) return P1_ERR_MAGIC;
    if (buf[2] != P1_VERSION) return P1_ERR_VERSION;
    uint8_t n = buf[10];
    size_t need = p1_frame_size(n);
    if (len < need) return P1_ERR_LENGTH;
    if (n > samples_cap) return P1_ERR_LENGTH;
    uint16_t want = (uint16_t)(buf[need - 2] | ((uint16_t)buf[need - 1] << 8));
    if (p1_crc16(buf, need - P1_CRC_SIZE) != want) return P1_ERR_CRC;

    out->seq   = (uint16_t)(buf[4] | ((uint16_t)buf[5] << 8));
    out->t_us  = (uint32_t)buf[6] | ((uint32_t)buf[7] << 8) |
                 ((uint32_t)buf[8] << 16) | ((uint32_t)buf[9] << 24);
    out->flags = buf[3];
    out->n     = n;
    for (uint8_t s = 0; s < n; s++)
        samples_out[s] = (uint16_t)(buf[P1_HEADER_SIZE + s * 2] |
                        ((uint16_t)buf[P1_HEADER_SIZE + s * 2 + 1] << 8));
    out->samples = samples_out;
    return P1_OK;
}

/* Sequence gap accounting that survives the uint16 wrap. 65535 -> 0 is a
 * gap of zero, not 65535. */
static inline uint16_t p1_seq_gap(uint16_t prev, uint16_t now)
{
    return (uint16_t)((uint16_t)(now - prev) - 1u);
}

#endif /* EMG_PROTOCOL_H */
