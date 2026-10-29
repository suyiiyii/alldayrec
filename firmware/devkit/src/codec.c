#include "codec.h"

#include <zephyr/logging/log.h>

#include "config.h"
#include "hpf.h"
#include "mic_health.h"
#include "mic_ring.h"
#include "utils.h"
#ifdef CODEC_OPUS
#include "lib/opus-1.2.1/opus.h"
#endif

LOG_MODULE_REGISTER(codec, CONFIG_LOG_DEFAULT_LEVEL);

//
// Output
//

static volatile codec_callback _callback = NULL;

void set_codec_callback(codec_callback callback)
{
    _callback = callback;
}

//
// Input
//

BUILD_ASSERT(MIC_BUFFER_SAMPLES == MIC_RING_SAMPLES && CODEC_PACKAGE_SAMPLES == MIC_RING_SAMPLES, "a mic buffer is a codec frame");
static struct mic_ring mic_ring;  // 1 s
static K_SEM_DEFINE(codec_sem, 0, 1);  // given with each mic buffer
extern uint32_t transport_frame_no;  // transport.c: the codec's frames, numbered as they are sent on
int codec_receive_pcm(int16_t *data, size_t len) // this gets called after mic data is finished
{
    bool kept = mic_ring_put(&mic_ring, data);
    k_sem_give(&codec_sem);
    if (!kept) {
        LOG_ERR("mic ring full: a buffer left out");
        return -1;
    }
    return 0;
}

//
// Thread
//

int16_t codec_input_samples[CODEC_PACKAGE_SAMPLES];
static struct hpf hpf[2];
struct mic_health mic_health;  // lr_log.c logs it every 10 minutes (HEALTH)
uint8_t codec_output_bytes[CODEC_OUTPUT_MAX_BYTES];
// phone mode's RAM ring (lr_store.c) takes 8 KB of it: STACKS on LR-P3 never saw more than 10.6 KB of it used
K_THREAD_STACK_DEFINE(codec_stack, IS_ENABLED(CONFIG_LR_STORE_PHONE) ? 24000 : 32000);
struct k_thread codec_thread;  // lr_log.c watches its stack
uint16_t execute_codec();

#if CODEC_OPUS
#if (CONFIG_OPUS_MODE == CONFIG_OPUS_MODE_CELT)
#define OPUS_ENCODER_SIZE 7180
#endif
#if (CONFIG_OPUS_MODE == CONFIG_OPUS_MODE_HYBRID)
#define OPUS_ENCODER_SIZE 10916
#endif
__ALIGN(4)
static uint8_t m_opus_encoder[OPUS_ENCODER_SIZE];
static OpusEncoder *const m_opus_state = (OpusEncoder *) m_opus_encoder;
#endif

void codec_entry()
{

    uint16_t output_size;
    uint32_t left_out;
    while (1) {

        // Read package
        if (!mic_ring_get(&mic_ring, codec_input_samples, &left_out)) {
            // woken by the next mic buffer, not polled: a frame is encoded as it comes, not up to 10 ms later
            k_sem_take(&codec_sem, K_MSEC(100));
            continue;
        }
        transport_frame_no += left_out;  // the buffers the ring had no room for: a gap in the frame numbers (mic_ring.h)

        // High-pass here, not in the PDM interrupt: the BLE stream and the SD store both take what this encodes
        mic_health_raw(&mic_health, codec_input_samples, CODEC_PACKAGE_SAMPLES);
        hpf_run(hpf, codec_input_samples, CODEC_PACKAGE_SAMPLES);
        mic_health_out(&mic_health, codec_input_samples, CODEC_PACKAGE_SAMPLES);

        // Run Codec
        output_size = execute_codec();

        // Notify
        if (_callback) {
            _callback(codec_output_bytes, output_size);
        }

        // Yield
        k_yield();
    }
}

int codec_start()
{

// OPUS
#if CODEC_OPUS
    ASSERT_TRUE(opus_encoder_get_size(1) == sizeof(m_opus_encoder));
    ASSERT_TRUE(opus_encoder_init(m_opus_state, 16000, 1, CODEC_OPUS_APPLICATION) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(m_opus_state, OPUS_SET_BITRATE(CODEC_OPUS_BITRATE)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(m_opus_state, OPUS_SET_VBR(CODEC_OPUS_VBR)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(m_opus_state, OPUS_SET_VBR_CONSTRAINT(0)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(m_opus_state, OPUS_SET_COMPLEXITY(CODEC_OPUS_COMPLEXITY)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(m_opus_state, OPUS_SET_SIGNAL(OPUS_SIGNAL_VOICE)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(m_opus_state, OPUS_SET_LSB_DEPTH(16)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(m_opus_state, OPUS_SET_DTX(0)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(m_opus_state, OPUS_SET_INBAND_FEC(0)) == OPUS_OK);
    ASSERT_TRUE(opus_encoder_ctl(m_opus_state, OPUS_SET_PACKET_LOSS_PERC(0)) == OPUS_OK);
#endif

    hpf_init(hpf, MIC_HPF_HZ, 16000);

    // Thread
    k_thread_create(&codec_thread,
                    codec_stack,
                    K_THREAD_STACK_SIZEOF(codec_stack),
                    (k_thread_entry_t) codec_entry,
                    NULL,
                    NULL,
                    NULL,
                    K_PRIO_PREEMPT(4),
                    0,
                    K_NO_WAIT);

    // Success
    return 0;
}

//
// Opus codec
//

#if CODEC_OPUS

uint16_t execute_codec()
{
    opus_int32 size = opus_encode(
        m_opus_state, codec_input_samples, CODEC_PACKAGE_SAMPLES, codec_output_bytes, sizeof(codec_output_bytes));
    if (size < 0) {
        LOG_WRN("Opus encoding failed: %d", size);
        return 0;
    }
    LOG_DBG("Opus encoding success: %i", size);
    return size;
}

#endif
