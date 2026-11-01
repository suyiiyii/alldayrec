/* Host check of adr.c's logic, from the SDK root:
 *   cc -std=gnu11 -DADR_PSRAM= -Iapps/arcs-mini/alldayrec -Iapps/arcs-mini/alldayrec/test/stub -Iarcs-sdk/components/lisa_websocket -Iarcs-sdk/modules/cjson \
 *      apps/arcs-mini/alldayrec/test/test_adr.c arcs-sdk/modules/cjson/cJSON.c -o /tmp/test_adr && /tmp/test_adr */
#include <assert.h>
#include "../adr.c"

TickType_t fake_ticks;
int64_t fake_now_ms;
void (*app_wakeup_record_tap)(const void *event);
app_player_t *miniapp_player;
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
BaseType_t xQueueSendToFront(QueueHandle_t q, const void *item, TickType_t wait)
{
    (void)wait;
    if (q->count == q->cap) return 0;
    q->head = (q->head + q->cap - 1) % q->cap;
    q->items[q->head] = *(void *const *)item;
    q->count++;
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

static void test_cites(void)
{
    char s[128];
    strcpy(s, "明天三点开会[c20261101103000]，在线上[c20261101103000, c20261101103500]。");
    strip_cites(s);
    assert(strcmp(s, "明天三点开会，在线上。") == 0);
    strcpy(s, "明天三点[c2026110");                     /* the stream cut a source in two: hide the half */
    strip_cites(s);
    assert(strcmp(s, "明天三点") == 0);
    strcpy(s, "明天[");
    strip_cites(s);
    assert(strcmp(s, "明天") == 0);
    strcpy(s, "[1] 列表 a]b");                          /* not a source */
    strip_cites(s);
    assert(strcmp(s, "[1] 列表 a]b") == 0);
}

static void test_ask(void)
{
    static int16_t frame[2 * 256];
    lisa_audio_event_t ev = {.record_buffer = frame, .record_samples = 2 * 256};
    ask_req_t *r;
    chunk_t *c;

    strcpy(s_cfg.device, "arcs-mini3");
    s_on = true;
    s_m.tab = 1;
    s_askq = xQueueCreate(2, sizeof(ask_req_t *));
    s_cfg.chunk_samples = 32000;
    s_free = xQueueCreate(4, sizeof(chunk_t *));
    s_full = xQueueCreate(4, sizeof(chunk_t *));
    for (int i = 0; i < 2; i++) {
        c = malloc(sizeof(chunk_t) + WAV_HDR + 32000 * 2);
        xQueueSend(s_free, &c, 0);
    }
    s_cur = NULL, s_next_ms = 0;
    int64_t t0 = 1767225600000LL + 60000;
    fake_now_ms = t0;
    adr_tap(&ev);                                      /* the piece starts at t0-16 */
    fake_now_ms = t0 + 23;
    adr_tap(&ev);                                      /* 32 ms of audio by t0+23: the pieces run 7 ms behind */
    assert(s_skew_ms == -7);

    /* a click is not a question */
    fake_now_ms = t0 + 100;
    assert(!adr_key(VOICE_MSG_BUTTON_ACTION_PRESS_DOWN));
    fake_now_ms = t0 + 300;
    assert(!adr_key(VOICE_MSG_BUTTON_ACTION_SHORT_UP));
    assert(s_m.ask == ADR_ASK_NONE && s_m.tab == 1 && !uxQueueMessagesWaiting(s_askq));

    /* hold: counts from the press once held 0.8 s; the long hold (power-off) is kept from the app */
    fake_now_ms = t0 + 1000;
    adr_key(VOICE_MSG_BUTTON_ACTION_PRESS_DOWN);
    fake_now_ms = t0 + 1800;
    adr_key(VOICE_MSG_BUTTON_ACTION_SHORT_START);
    assert(s_m.ask == ADR_ASK_LISTEN && s_m.tab == 3 && s_m.ask_back == 1);
    assert(adr_key(VOICE_MSG_BUTTON_ACTION_LONG_HOLD));
    fake_now_ms = t0 + 4000;
    assert(!adr_key(VOICE_MSG_BUTTON_ACTION_LONG_HOLD_UP));
    assert(s_m.ask == ADR_ASK_SENT && s_flush);
    assert(xQueueReceive(s_askq, &r, 0) && r->start_ms == t0 + 1000 - 7 && r->end_ms == t0 + 4000 - 7 && r->gen == 1);
    assert(!adr_key(VOICE_MSG_BUTTON_ACTION_LONG_HOLD));   /* not asking: power-off as before */

    /* the next audio callback hands the short piece over, ahead of the queue */
    chunk_t *queued = s_cur;
    xQueueSend(s_full, &queued, 0);                    /* stands for a backlog piece */
    s_cur = NULL;
    xQueueReceive(s_free, &s_cur, 0);
    s_cur->start_ms = t0 + 32 - 16, s_cur->n = 100;
    adr_tap(&ev);
    assert(!s_flush && !s_cur);
    assert(xQueueReceive(s_full, &c, 0) && c->n == 356 && c->start_ms == t0 + 16);
    assert(s_next_ms == t0 + 16 + 356 * 1000 / 16000);
    chunk_t *flushed = c;
    assert(xQueueReceive(s_full, &c, 0) && c == queued);

    /* then 1.5 s of 0.5 s pieces, back to back, so the last words reach the live ASR; then full pieces again */
    xQueueSend(s_free, &flushed, 0);
    xQueueSend(s_free, &queued, 0);
    int64_t next = 0;
    for (int k = 0; k < TAIL_PIECES + 1; k++) {
        while (!uxQueueMessagesWaiting(s_full)) {
            fake_now_ms += 16;
            adr_tap(&ev);
        }
        assert(xQueueReceive(s_full, &c, 0) && c->n == (k < TAIL_PIECES ? TAIL_SAMPLES : 32000));
        assert(!next || c->start_ms == next);
        next = c->start_ms + c->n * 1000 / 16000;
        xQueueSend(s_free, &c, 0);
    }
    assert(!s_tail);

    /* /ws: another device's and an earlier question's messages are not ours */
    strcpy(s_ask_prev, "old");
    fake_now_ms = t0 + 4600;
    ws("{\"type\":\"ask\",\"device\":\"other\",\"id\":\"q2\",\"stage\":\"heard\",\"q\":\"别的\"}");
    ws("{\"type\":\"ask\",\"device\":\"arcs-mini3\",\"id\":\"old\",\"delta\":\"旧的\"}");
    assert(s_m.q[0] == 0 && s_m.answer[0] == 0 && s_lat_heard < 0);
    ws("{\"type\":\"ask\",\"device\":\"arcs-mini3\",\"id\":\"q2\",\"stage\":\"heard\",\"q\":\"明天几点开会\"}");
    assert(strcmp(s_m.q, "明天几点开会") == 0 && s_lat_heard == 600 && strcmp(s_ask_id, "q2") == 0);
    fake_now_ms = t0 + 4700;
    ask_posted(1, "{\"id\":\"q2\"}");
    assert(s_lat_post == 700 && s_m.ask == ADR_ASK_SENT);
    ws("{\"type\":\"ask\",\"device\":\"arcs-mini3\",\"id\":\"q2\",\"stage\":\"searching\"}");
    assert(s_m.ask == ADR_ASK_SEARCH);
    fake_now_ms = t0 + 6200;
    ws("{\"type\":\"ask\",\"device\":\"arcs-mini3\",\"id\":\"q2\",\"delta\":\"下午三点[c2026\"}");
    ws("{\"type\":\"ask\",\"device\":\"arcs-mini3\",\"id\":\"q2\",\"delta\":\"1101103000]，线上。\"}");
    assert(s_m.ask == ADR_ASK_ANSWER && strcmp(s_m.answer, "下午三点，线上。") == 0 && s_lat_delta == 2200);
    ws("{\"type\":\"ask\",\"device\":\"arcs-mini3\",\"id\":\"q2\",\"stage\":\"done\",\"answer\":\"下午三点开[c20261101103000]，线上。\"}");
    assert(s_m.ask == ADR_ASK_DONE && strcmp(s_m.answer, "下午三点开，线上。") == 0);

    /* a click on 问答 goes back */
    adr_button(VOICE_MSG_BUTTON_ACTION_CLICK);
    assert(s_m.tab == 1);

    /* the next question: q2 is now the earlier one; the server could not hear it */
    fake_now_ms = t0 + 9000;
    adr_key(VOICE_MSG_BUTTON_ACTION_PRESS_DOWN);
    adr_key(VOICE_MSG_BUTTON_ACTION_SHORT_START);
    assert(s_m.tab == 3 && s_m.answer[0] == 0 && strcmp(s_ask_prev, "q2") == 0);
    adr_key(VOICE_MSG_BUTTON_ACTION_SHORT_UP);
    ws("{\"type\":\"ask\",\"device\":\"arcs-mini3\",\"id\":\"q2\",\"stage\":\"done\",\"answer\":\"迟到的\"}");
    assert(s_m.ask == ADR_ASK_SENT);
    ask_posted(1, "{\"id\":\"q2\"}");             /* a stale POST answer */
    assert(s_ask_id[0] == 0);
    ask_posted(2, "{\"id\":\"q3\"}");
    ws("{\"type\":\"ask\",\"device\":\"arcs-mini3\",\"id\":\"q3\",\"stage\":\"error\",\"error\":\"没听清\"}");
    assert(s_m.ask == ADR_ASK_ERROR && strcmp(s_m.answer, "没听清") == 0);

    /* the POST itself failed */
    adr_key(VOICE_MSG_BUTTON_ACTION_PRESS_DOWN);
    adr_key(VOICE_MSG_BUTTON_ACTION_SHORT_START);
    adr_key(VOICE_MSG_BUTTON_ACTION_SHORT_UP);
    ask_posted(3, NULL);
    assert(s_m.ask == ADR_ASK_ERROR && strcmp(s_m.answer, "连不上服务器") == 0);
    s_on = false;
}

static void bin(const char *id, const int16_t *pcm, uint32_t n)
{
    static uint8_t b[64 + 4000];
    size_t l = strlen(id);
    memcpy(b, id, l), b[l] = '\n', memcpy(b + l + 1, pcm, n * 2);
    lisa_ws_data_t d = {.type = LISA_WS_BIN, .buf = b, .len = l + 1 + n * 2};
    ws_data(&d);
}

static void test_say(void)
{
    static int16_t pcm[1600], frame[2 * 256];
    static uint8_t got[SAY_BYTES];
    lisa_audio_event_t ev = {.record_buffer = frame, .record_samples = 2 * 256};
    chunk_t *c;

    for (int i = 0; i < 1600; i++) pcm[i] = (int16_t)(i * 7 + 1);
    strcpy(s_cfg.device, "arcs-mini3");
    s_sayq = xQueueCreate(1, sizeof(uint8_t *));
    s_say_buf = malloc(SAY_BYTES);
    s_m.ask = ADR_ASK_ANSWER, s_say = SAY_IDLE;
    strcpy(s_ask_id, "q9");
    fake_now_ms = 1767225600000LL + 100000, s_end_ms = fake_now_ms - 1500;

    /* audio frames: only after this question's "speak", only this question's id */
    bin("q9", pcm, 1600);
    assert(s_say_len == 0);
    ws("{\"type\":\"ask\",\"device\":\"arcs-mini3\",\"id\":\"q8\",\"stage\":\"speak\"}");
    assert(s_say == SAY_IDLE);
    ws("{\"type\":\"ask\",\"device\":\"arcs-mini3\",\"id\":\"q9\",\"stage\":\"speak\"}");
    assert(s_say == SAY_ON && uxQueueMessagesWaiting(s_sayq) == 1);
    bin("q8", pcm, 1600);
    bin("q99", pcm, 1600);
    bin("", pcm, 1600);
    assert(s_say_len == 0 && s_lat_say < 0);
    bin("q9", pcm, 1600);
    bin("q9", pcm + 800, 800);
    assert(s_say_len == 4800 && s_lat_say == 1500);
    assert(say_take(got, 3000) == 3000 && memcmp(got, pcm, 3000) == 0);
    assert(say_take(got, sizeof(got)) == 1800 && memcmp(got, (uint8_t *)pcm + 3000, 200) == 0 &&
           memcmp(got + 200, pcm + 800, 1600) == 0);

    /* full: whole frames dropped, never half; the ring wraps */
    for (int i = 0; i < SAY_BYTES / 3200 + 1; i++) bin("q9", pcm, 1600);
    assert(s_say_len == SAY_BYTES && s_say_drop == 3200);
    say_take(got, 1000);
    bin("q9", pcm, 1600);
    assert(s_say_len == SAY_BYTES - 1000 && s_say_drop == 6400);
    say_take(got, 3000);
    bin("q9", pcm, 1600);
    assert(say_take(got, sizeof(got)) == SAY_BYTES - 800 && memcmp(got + SAY_BYTES - 800 - 3200, pcm, 3200) == 0);

    ws("{\"type\":\"ask\",\"device\":\"arcs-mini3\",\"id\":\"q9\",\"stage\":\"spoken\"}");
    assert(s_say == SAY_END);

    /* speaking: mic1 goes up as zeros, the piece's timeline as ever */
    for (int i = 0; i < 256; i++) frame[2 * i] = (int16_t)(i + 1), frame[2 * i + 1] = 5;
    s_cfg.chunk_samples = 256;
    s_free = xQueueCreate(2, sizeof(chunk_t *));
    s_full = xQueueCreate(2, sizeof(chunk_t *));
    c = malloc(sizeof(chunk_t) + WAV_HDR + 256 * 2);
    xQueueSend(s_free, &c, 0);
    s_cur = NULL, s_next_ms = 0, s_tail = 0, s_flush = false;
    s_mute = true;
    adr_tap(&ev);
    assert(xQueueReceive(s_full, &c, 0) && c->n == 256);
    for (int i = 0; i < 256; i++) assert(((int16_t *)(c->body + WAV_HDR))[i] == 0);
    xQueueSend(s_free, &c, 0);

    /* the next question: the answer stops, the mic is back at once */
    s_on = true;
    adr_key(VOICE_MSG_BUTTON_ACTION_PRESS_DOWN);
    adr_key(VOICE_MSG_BUTTON_ACTION_SHORT_START);
    assert(s_say_stop && !s_mute);
    adr_tap(&ev);
    assert(xQueueReceive(s_full, &c, 0) && ((int16_t *)(c->body + WAV_HDR))[255] == 256);
    s_on = false;
}

int main(void)
{
    test_text();
    test_wav_and_body();
    test_ws();
    test_tap();
    test_cites();
    test_ask();
    test_say();
    puts("adr: all checks passed");
    return 0;
}
