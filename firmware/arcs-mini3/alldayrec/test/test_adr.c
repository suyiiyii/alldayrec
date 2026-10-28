/* Host check of adr.c's logic, from the SDK root:
 *   cc -std=gnu11 -DADR_PSRAM= -Iapps/arcs-mini/alldayrec -Iapps/arcs-mini/alldayrec/test/stub -Iarcs-sdk/components/lisa_websocket -Iarcs-sdk/modules/cjson \
 *      apps/arcs-mini/alldayrec/test/test_adr.c arcs-sdk/modules/cjson/cJSON.c -o /tmp/test_adr && /tmp/test_adr */
#include <assert.h>
#include "../adr.c"

TickType_t fake_ticks;
int64_t fake_now_ms;
void (*app_wakeup_record_tap)(const void *event);
void adr_view_toggle(void) {}
void adr_view_page(void) {}
bool adr_view_visible(void) { return true; }
int adr_view_register(void) { return 0; }
lisa_ws_t *lisa_ws_init(lisa_ws_request_t *r) { (void)r; return NULL; }
lisa_ws_err_e lisa_ws_connect(lisa_ws_t *w) { (void)w; return LISA_WS_OK; }
lisa_ws_err_e lisa_ws_disconnect(lisa_ws_t *w) { (void)w; return LISA_WS_OK; }
lisa_ws_err_e lisa_ws_send_text(lisa_ws_t *w, const uint8_t *t) { (void)w; (void)t; return LISA_WS_OK; }

QueueHandle_t xQueueCreate(int cap, int size)
{
    (void)size;
    fake_queue_t *q = calloc(1, sizeof(*q));
    q->cap = cap;
    return q;
}
BaseType_t xQueueSend(QueueHandle_t q, const void *item, TickType_t wait)
{
    (void)wait;
    if (q->count == q->cap) return 0;
    q->items[(q->head + q->count++) % q->cap] = *(void *const *)item;
    return 1;
}
BaseType_t xQueueReceive(QueueHandle_t q, void *item, TickType_t wait)
{
    (void)wait;
    if (!q->count) return 0;
    *(void **)item = q->items[q->head];
    q->head = (q->head + 1) % q->cap;
    q->count--;
    return 1;
}

static void ws(const char *json)
{
    lisa_ws_data_t d = {.type = LISA_WS_TEXT, .buf = json, .len = strlen(json)};
    ws_data(&d);
}

static void test_text(void)
{
    char b[8];
    put_text(b, sizeof(b), "一二三");            /* 9 bytes into 7: whole characters only */
    assert(strcmp(b, "一二") == 0);
    put_tail(b, sizeof(b), "一二三");
    assert(strcmp(b, "二三") == 0);
    put_tail(b, sizeof(b), "ab");
    assert(strcmp(b, "ab") == 0);
}

static void test_wav_and_body(void)
{
    uint8_t h[WAV_HDR];
    wav_header(h, 32000);
    assert(memcmp(h, "RIFF", 4) == 0 && memcmp(h + 8, "WAVEfmt ", 8) == 0 && memcmp(h + 36, "data", 4) == 0);
    assert(h[4] == ((36 + 64000) & 0xff) && h[5] == ((36 + 64000) >> 8 & 0xff) && h[6] == 0);  /* zero bytes: why "RIFF" goes first */
    assert(h[24] == (16000 & 0xff) && h[25] == 16000 >> 8 && h[40] == (64000 & 0xff) && h[41] == 64000 >> 8);

    static uint8_t body[10000];
    s_post.p = body, s_post.off = 4, s_post.len = sizeof(body);
    uint32_t sent = 4;
    for (PostData d; (d = http_body()).pData; sent += d.pLength) {
        assert(d.pData == body + sent && d.pLength <= SEND_SLICE);
    }
    assert(sent == sizeof(body) && s_post.off == s_post.len);
}

static void test_ws(void)
{
    strcpy(s_cfg.device, "arcs-mini3");
    ws("{\"type\":\"draft\",\"device\":\"other\",\"rows\":[{\"start\":\"2026-10-27T10:00:00.000\",\"text\":\"x\"}]}");
    assert(s_m.nrows == 0);                         /* another device's rows */
    ws("{\"type\":\"partial\",\"device\":\"arcs-mini3\",\"row\":{\"start\":\"2026-10-27T10:00:04.000\",\"speaker\":\"\",\"text\":\"正在说\"}}");
    assert(strcmp(s_m.partial, "正在说") == 0);
    ws("{\"type\":\"draft\",\"device\":\"arcs-mini3\",\"rows\":["
       "{\"start\":\"2026-10-27T10:00:02.000\",\"speaker\":\"S?\",\"text\":\"第二句\"},"
       "{\"start\":\"2026-10-27T10:00:00.000\",\"speaker\":\"\",\"text\":\"第一句\"}]}");
    assert(s_m.nrows == 2 && strcmp(s_m.rows[0].text, "第一句") == 0 && strcmp(s_m.rows[1].text, "S?：第二句") == 0);
    assert(s_m.rows[0].draft && s_m.rows[1].draft);
    ws("{\"type\":\"partial\",\"device\":\"arcs-mini3\",\"row\":null}");
    assert(s_m.partial[0] == 0);
    /* the final covers [10:00:00, 10:00:03): both drafts go, its rows come in */
    ws("{\"type\":\"final\",\"device\":\"arcs-mini3\",\"from\":\"2026-10-27T10:00:00\",\"to\":\"2026-10-27T10:00:03.000\",\"rows\":["
       "{\"start\":\"2026-10-27T10:00:00.100\",\"speaker\":\"我\",\"text\":\"第一句话\"}]}");
    assert(s_m.nrows == 1 && !s_m.rows[0].draft && strcmp(s_m.rows[0].text, "我：第一句话") == 0);

    char m[256];
    for (int i = 0; i < ADR_ROWS + 3; i++) {          /* more than fit: the newest stay, in order */
        snprintf(m, sizeof(m), "{\"type\":\"draft\",\"device\":\"arcs-mini3\",\"rows\":[{\"start\":\"2026-10-27T11:00:%02d.000\",\"text\":\"r%d\"}]}", i, i);
        ws(m);
    }
    assert(s_m.nrows == ADR_ROWS && strcmp(s_m.rows[0].text, "r3") == 0 && strcmp(s_m.rows[ADR_ROWS - 1].text, "r10") == 0);
    ws("{\"type\":\"draft\",\"device\":\"arcs-mini3\",\"rows\":[{\"start\":\"2026-10-27T09:00:00.000\",\"text\":\"old\"}]}");
    assert(strcmp(s_m.rows[0].text, "r3") == 0);      /* older than everything kept: not shown */

    ws("{\"type\":\"live\",\"device\":\"arcs-mini3\",\"live\":{\"topics\":["
       "{\"id\":1,\"title\":\"演示视频\",\"points\":[\"周五前发给客户\"],\"todos\":[{\"who\":\"我\",\"what\":\"测续航\"}]},"
       "{\"id\":2,\"title\":\"开会\",\"points\":[\"明天下午三点\"],\"decisions\":[\"在线上开\"]}]}}");
    assert(strcmp(s_m.live, "【开会】\n- 明天下午三点\n定：在线上开\n【演示视频】\n- 周五前发给客户\n待办：测续航\n") == 0);
    ws("{\"type\":\"live\",\"device\":\"arcs-mini3\",\"live\":null}");
    assert(s_m.live[0] == 0);

    uint32_t seq = s_m.seq;
    ws("{\"type\":\"event\",\"text\":\"收到块\"}");      /* no device: ignored */
    char *big = malloc(WS_MSG_MAX + 2);
    memset(big, ' ', WS_MSG_MAX + 1), big[WS_MSG_MAX + 1] = 0;
    ws(big);
    free(big);
    assert(s_m.seq == seq && s_ws_big == 1);
}

static void test_tap(void)
{
    static int16_t frame[2 * 256];
    lisa_audio_event_t ev = {.record_buffer = frame, .record_samples = 2 * 256}; /* the driver counts both channels */
    chunk_t *c;

    for (int i = 0; i < 256; i++) frame[2 * i] = (int16_t)i, frame[2 * i + 1] = -1;  /* mic2 must not leak in */
    s_cfg.chunk_samples = 400;                       /* frames straddle pieces */
    s_free = xQueueCreate(2, sizeof(chunk_t *));
    s_full = xQueueCreate(2, sizeof(chunk_t *));
    for (int i = 0; i < 2; i++) {
        c = malloc(sizeof(chunk_t) + WAV_HDR + 400 * 2);
        xQueueSend(s_free, &c, 0);
    }

    fake_now_ms = 1000;                               /* before SNTP: nothing */
    adr_tap(&ev);
    assert(!s_cur && !uxQueueMessagesWaiting(s_full));

    int64_t t0 = 1767225600000LL + 5000;
    fake_now_ms = t0;
    adr_tap(&ev);                                     /* samples 0..255 */
    fake_now_ms = t0 + 16;
    adr_tap(&ev);                                     /* 256..511: piece 1 done at 400 */
    assert(xQueueReceive(s_full, &c, 0));
    assert(c->n == 400 && c->start_ms == t0 - 16);
    int16_t *pcm = (int16_t *)(c->body + WAV_HDR);
    assert(pcm[0] == 0 && pcm[255] == 255 && pcm[256] == 0 && pcm[399] == 143);
    xQueueSend(s_free, &c, 0);

    fake_now_ms = t0 + 32 + 7;                        /* 7 ms of callback jitter: still back to back */
    adr_tap(&ev);
    fake_now_ms = t0 + 48;
    adr_tap(&ev);                                     /* piece 2 done */
    assert(xQueueReceive(s_full, &c, 0));
    assert(c->start_ms == t0 - 16 + 25);              /* 400 samples after piece 1 */
    chunk_t *keep = c;                                /* the uploader holds it: one piece left in the pool */

    fake_now_ms = t0 + 64;
    adr_tap(&ev);
    fake_now_ms = t0 + 80;
    adr_tap(&ev);                                     /* piece 3 done; the rest of the frame finds no free piece */
    fake_now_ms = t0 + 96;
    adr_tap(&ev);                                     /* still none: dropped */
    assert(s_dropped > 0 && s_next_ms == 0);
    uint32_t dropped = s_dropped;
    xQueueReceive(s_full, &c, 0);
    xQueueSend(s_free, &c, 0);
    xQueueSend(s_free, &keep, 0);
    fake_now_ms = t0 + 5000;
    adr_tap(&ev);                                     /* after the drop: back on the clock */
    assert(s_cur && s_cur->start_ms == t0 + 5000 - 16 && s_dropped == dropped);
}

int main(void)
{
    test_text();
    test_wav_and_body();
    test_ws();
    test_tap();
    puts("adr: all checks passed");
    return 0;
}
