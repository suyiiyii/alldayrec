#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>

#include "FreeRTOS.h"
#include "queue.h"
#include "semphr.h"
#include "task.h"

#include "HTTPCUsr_api.h"
#include "cJSON.h"
#include "lisa_audio.h"
#include "lisa_http.h"
#include "lisa_kv.h"
#include "lisa_mem.h"
#include "lisa_thread.h"
#include "lisa_websocket.h"
#include "shell.h"
#include "sys_wifi.h"
#include "voice_msg.h"
#ifdef CONFIG_LOG_UPLOAD
#include "log_upload.h"
#endif

#include "app_wakeup.h"
#include "service_power_policy.h"
#include "adr.h"

#define TAG "adr"
#include "lisa_log.h"

#define SR 16000
#define POOL_MS 12000            /* audio kept while the server is out of reach (12 s = 384 KB of the ~1.8 MB free heap); then frames drop */
#define WAV_HDR 44
#define TAIL_PIECES 3            /* after a question: 1.5 s of 0.5 s pieces, so the live ASR gets the audio after the last word */
#define TAIL_SAMPLES (SR / 2)
#define SEND_SLICE 4096          /* <= CONFIG_MBEDTLS_SSL_OUT_CONTENT_LEN: HTTPIntrnSend resends from the start on a short TLS write */
#define WATCH_EVERY_MS 20000     /* the server keeps drafts and live summaries going 60 s after a watch */
#define WS_MSG_MAX (32 * 1024)   /* a 5-minute final can be tens of KB; the next push brings the lines back */
#define TIME_VALID_MS 1767225600000LL /* 2026-01-01: before SNTP the clock says 1970 */

typedef struct {
    int64_t start_ms;
    uint32_t n;
    uint8_t body[]; /* WAV header, then n samples */
} chunk_t;

static struct {
    char url[160], token[96], device[48];
    uint32_t chunk_samples;
} s_cfg;

static bool s_on;
static QueueHandle_t s_free, s_full; /* both hold the whole pool, so a send never fails */
static chunk_t *s_cur;               /* the piece the audio callback is filling */
static int64_t s_next_ms;            /* where the next piece starts when no frame was dropped; 0: take the clock */
static volatile uint32_t s_dropped;  /* samples */
static SemaphoreHandle_t s_lock;
static adr_model_t s_m ADR_PSRAM;
static char s_http_headers[160], s_ws_header[128];
static volatile int s_ws_state;      /* 0 down, 1 connecting, 2 up */
static uint32_t s_ws_big;
static uint32_t s_tap_calls, s_tap_frames, s_tap_n, s_tap_echo; /* what the audio callback really delivers */
static int64_t s_tap_t0;
static volatile int32_t s_skew_ms;  /* the pieces' timeline minus the clock: ask times go on the pieces' timeline */
static volatile bool s_flush;        /* hand the piece being filled to the uploader now, however short */
static uint32_t s_tail;              /* short pieces still to go after a flush (audio callback only) */

/* a question asked by holding the key on the AllDayRec page */
typedef struct {
    int64_t start_ms, end_ms; /* on the pieces' timeline */
    uint32_t gen;
} ask_req_t;
static ask_req_t s_req;
static QueueHandle_t s_askq;
static uint32_t s_ask_gen;
static int64_t s_down_ms, s_end_ms;  /* clock time of the press and of the release */
static char s_ask_id[48] ADR_PSRAM, s_ask_prev[48] ADR_PSRAM;
static char s_ask_raw[2 * ADR_ANSWER_BYTES] ADR_PSRAM; /* the answer as streamed, sources and all */
static char s_ask_headers[160] ADR_PSRAM;
static int32_t s_lat_post = -1, s_lat_heard = -1, s_lat_delta = -1; /* ms from the release, -1: not yet */

static int64_t now_ms(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

/* ==================== capture: called from the audio callback ==================== */

static void adr_tap(const void *event)
{
    const lisa_audio_event_t *ev = event;

    if (!ev || !ev->record_buffer || !ev->record_samples) {
        return;
    }
    const int16_t *in = ev->record_buffer; /* interleaved: mic1 (voice), mic2 (echo reference) */
    uint32_t n = ev->record_samples / 2;   /* it counts both channels: 512 for a 256-frame buffer (measured) */
    int64_t now = now_ms(), t = now;
    if (t < TIME_VALID_MS) {
        return;
    }
    if (!s_tap_t0) {
        s_tap_t0 = t;
    }
    s_tap_calls++, s_tap_frames += n, s_tap_n = ev->record_samples, s_tap_echo = ev->echo_samples;
    t -= (int64_t)n * 1000 / SR; /* the frame's first sample */

    for (uint32_t i = 0; i < n;) {
        if (!s_cur) {
            if (xQueueReceive(s_free, &s_cur, 0) != pdTRUE) {
                s_cur = NULL;
                s_dropped += n - i;
                s_next_ms = 0;
                return;
            }
            int64_t at = t + (int64_t)i * 1000 / SR;
            /* ponytail: pieces stay back to back by sample count; re-anchor to the clock after a drop or
             * once the audio clock is 0.5 s off it (a jump in the server's timeline, not a gap) */
            s_cur->start_ms = (s_next_ms && llabs(s_next_ms - at) < 500) ? s_next_ms : at;
            s_cur->n = 0;
        }
        int16_t *pcm = (int16_t *)(s_cur->body + WAV_HDR);
        uint32_t size = s_tail ? TAIL_SAMPLES : s_cfg.chunk_samples;
        while (i < n && s_cur->n < size) {
            pcm[s_cur->n++] = in[2 * i++];
        }
        if (s_cur->n == size) {
            s_next_ms = s_cur->start_ms + (int64_t)s_cur->n * 1000 / SR;
            xQueueSend(s_full, &s_cur, 0);
            s_cur = NULL;
            if (s_tail) {
                s_tail--;
            }
        }
    }
    if (s_cur) {
        s_skew_ms = (int32_t)(s_cur->start_ms + (int64_t)s_cur->n * 1000 / SR - now);
    }
    if (s_flush) { /* a question just ended: its last words go out now, ahead of any backlog */
        s_flush = false;
        s_tail = TAIL_PIECES;
        if (s_cur && s_cur->n) {
            s_next_ms = s_cur->start_ms + (int64_t)s_cur->n * 1000 / SR;
            xQueueSendToFront(s_full, &s_cur, 0);
            s_cur = NULL;
        }
    }
}

/* ==================== upload: POST /api/chunks ==================== */

static struct {
    const uint8_t *p;
    uint32_t off, len;
} s_post; /* HTTPC_request_r's body callback has no user pointer; one upload at a time */

static void *http_headers(void)
{
    return s_http_headers;
}

static PostData http_body(void)
{
    PostData d = {.pData = NULL, .pLength = 0};
    uint32_t left = s_post.len - s_post.off;

    if (left) {
        d.pData = (void *)(s_post.p + s_post.off);
        d.pLength = left > SEND_SLICE ? SEND_SLICE : left;
        s_post.off += d.pLength;
    }
    return d;
}

static void put_le(uint8_t *p, uint32_t v, int bytes)
{
    while (bytes--) {
        *p++ = v & 0xff;
        v >>= 8;
    }
}

static void wav_header(uint8_t *h, uint32_t n)
{
    memcpy(h, "RIFF", 4);
    put_le(h + 4, 36 + n * 2, 4);
    memcpy(h + 8, "WAVEfmt ", 8);
    put_le(h + 16, 16, 4);
    put_le(h + 20, 1, 2);      /* PCM */
    put_le(h + 22, 1, 2);      /* mono */
    put_le(h + 24, SR, 4);
    put_le(h + 28, SR * 2, 4);
    put_le(h + 32, 2, 2);
    put_le(h + 34, 16, 2);
    memcpy(h + 36, "data", 4);
    put_le(h + 40, n * 2, 4);
}

static void drain(HTTPParameters *hp, uint32_t len)
{
    char buf[128];

    while (len) {
        UINT32 got = 0;
        int r = HTTPC_read(hp, buf, len < sizeof(buf) ? len : sizeof(buf), &got);
        len -= got < len ? got : len;
        if (r || !got) {
            break;
        }
    }
}

/* One POST: the HTTP status, or -1 without one. */
static int post_chunk(chunk_t *c, uint32_t *elapsed_ms)
{
    HTTPParameters *hp = lisa_mem_calloc(1, sizeof(*hp));
    HTTP_CLIENT info;
    int status = -1;
    TickType_t t0 = xTaskGetTickCount();

    if (!hp) {
        return -1;
    }
    wav_header(c->body, c->n);
    snprintf(hp->Uri, sizeof(hp->Uri), "%s/api/chunks?device=%s&start=%lu%03lu&n=%lu", s_cfg.url, s_cfg.device,
             (unsigned long)(c->start_ms / 1000), (unsigned long)(c->start_ms % 1000), (unsigned long)c->n);
    hp->HttpVerb = VerbPost;
    hp->nTimeout = 15;
    /* the client sends this first piece by strlen(), so binary (the WAV size field has zero bytes) goes through
     * http_body() from offset 4 on; pLength is the Content-Length */
    hp->pData = (void *)"RIFF";
    hp->pLength = WAV_HDR + c->n * 2;
    s_post.p = c->body;
    s_post.off = 4;
    s_post.len = hp->pLength;

    if (HTTPC_open(hp) == 0) {
        int r = HTTPC_request_r(hp, http_headers, http_body);
        if (r == 0 && s_post.off == s_post.len && HTTPC_get_request_info(hp, &info) == 0) {
            status = info.HTTPStatusCode;
            drain(hp, info.TotalResponseBodyLength);
        } else {
            LISA_LOGW(TAG, "upload failed: ret=%d sent=%lu/%lu", r, (unsigned long)s_post.off, (unsigned long)s_post.len);
        }
        HTTPC_close(hp);
    }
    *elapsed_ms = pdTICKS_TO_MS(xTaskGetTickCount() - t0);
    lisa_mem_free(hp);
    return status;
}

static void note_upload(int status, uint32_t ms)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_m.last_status = status;
    s_m.last_ms = ms;
    if (status >= 200 && status < 300) {
        s_m.uploaded++;
        s_m.last_ok_tick = xTaskGetTickCount();
    } else {
        s_m.failed++;
    }
    s_m.seq++;
    xSemaphoreGive(s_lock);
}

static void upload_task(void *arg)
{
    chunk_t *c;

    (void)arg;
    for (;;) {
        xQueueReceive(s_full, &c, portMAX_DELAY);
        for (uint32_t wait = 1000;; wait = wait < 8000 ? wait * 2 : 8000) {
#ifdef CONFIG_LOG_UPLOAD
            while (log_upload_is_running()) { /* it shares the HTTP client's global body callback state */
                vTaskDelay(pdMS_TO_TICKS(500));
            }
#endif
            while (!sys_wifi_is_connected()) {
                vTaskDelay(pdMS_TO_TICKS(500));
            }
            uint32_t ms;
            int st = post_chunk(c, &ms);
            note_upload(st, ms);
            if (st >= 200 && st < 300) {
                break;
            }
            LISA_LOGW(TAG, "piece at %lu%03lu: status %d after %lu ms", (unsigned long)(c->start_ms / 1000),
                      (unsigned long)(c->start_ms % 1000), st, (unsigned long)ms);
            if (st == 400) { /* the server cannot decode it: sending it again will not help */
                break;
            }
            vTaskDelay(pdMS_TO_TICKS(wait));
        }
        xQueueSend(s_free, &c, 0);
    }
}

/* ==================== model: what /ws pushes, kept for the page ==================== */

/* UTF-8-safe copy */
static void put_text(char *dst, size_t cap, const char *src)
{
    size_t n = strlen(src);

    if (n >= cap) {
        n = cap - 1;
        while (n && ((uint8_t)src[n] & 0xC0) == 0x80) {
            n--;
        }
    }
    memcpy(dst, src, n);
    dst[n] = 0;
}

/* the last cap-1 bytes, UTF-8-safe: the sentence being said grows at its end */
static void put_tail(char *dst, size_t cap, const char *src)
{
    size_t n = strlen(src), from = n < cap ? 0 : n - (cap - 1);

    while (from < n && ((uint8_t)src[from] & 0xC0) == 0x80) {
        from++;
    }
    put_text(dst, cap, src + from);
}

static void append(char *buf, size_t cap, const char *s)
{
    size_t len = strlen(buf);
    if (len + 1 < cap) {
        put_text(buf + len, cap - len, s);
    }
}

static const char *jstr(const cJSON *o, const char *key)
{
    const cJSON *v = cJSON_GetObjectItem(o, key);
    return cJSON_IsString(v) ? v->valuestring : NULL;
}

static void row_text(char *dst, const cJSON *r, bool tail)
{
    const char *sp = jstr(r, "speaker"), *tx = jstr(r, "text");
    char tmp[512];

    if (tail) { /* the sentence being said: no speaker yet, its newest words last */
        put_tail(dst, ADR_ROW_BYTES, tx ? tx : "");
        return;
    }
    snprintf(tmp, sizeof(tmp), "%s%s%s", sp && *sp ? sp : "", sp && *sp ? "：" : "", tx ? tx : "");
    put_text(dst, ADR_ROW_BYTES, tmp);
}

static void add_row(const cJSON *r, bool draft)
{
    const char *start = jstr(r, "start");
    adr_row_t *rows = s_m.rows;
    int i = 0;

    if (!start || !jstr(r, "text")) {
        return;
    }
    while (i < s_m.nrows && strcmp(rows[i].start, start) < 0) {
        i++;
    }
    if (i == s_m.nrows || strcmp(rows[i].start, start) != 0) {
        if (s_m.nrows == ADR_ROWS) { /* drop the oldest */
            if (i == 0) {
                return;
            }
            memmove(&rows[0], &rows[1], (i - 1) * sizeof(*rows));
            i--;
        } else {
            memmove(&rows[i + 1], &rows[i], (s_m.nrows - i) * sizeof(*rows));
            s_m.nrows++;
        }
        put_text(rows[i].start, sizeof(rows[i].start), start);
    }
    row_text(rows[i].text, r, false);
    rows[i].draft = draft;
}

/* a final replaces whatever the device showed for [from, to) */
static void drop_range(const char *from, const char *to)
{
    int k = 0;

    for (int i = 0; i < s_m.nrows; i++) {
        const char *st = s_m.rows[i].start;
        if (!(strcmp(st, from) >= 0 && strcmp(st, to) < 0)) {
            s_m.rows[k++] = s_m.rows[i];
        }
    }
    s_m.nrows = k;
}

static void add_items(const cJSON *topic, const char *key, const char *mark, int max)
{
    const cJSON *a = cJSON_GetObjectItem(topic, key), *it;
    int k = 0;

    cJSON_ArrayForEach(it, a) {
        const char *s = cJSON_IsString(it) ? it->valuestring : jstr(it, "what");
        if (s && k++ < max) {
            append(s_m.live, sizeof(s_m.live), mark);
            append(s_m.live, sizeof(s_m.live), s);
            append(s_m.live, sizeof(s_m.live), "\n");
        }
    }
}

static void set_live(const cJSON *live)
{
    const cJSON *topics = cJSON_GetObjectItem(live, "topics");
    int n = cJSON_GetArraySize(topics);

    s_m.live[0] = 0;
    for (int i = n - 1; i >= 0; i--) { /* newest topic first */
        const cJSON *t = cJSON_GetArrayItem(topics, i);
        const char *title = jstr(t, "title");
        append(s_m.live, sizeof(s_m.live), "【");
        append(s_m.live, sizeof(s_m.live), title ? title : "");
        append(s_m.live, sizeof(s_m.live), "】\n");
        add_items(t, "points", "- ", 3);
        add_items(t, "decisions", "定：", 2);
        add_items(t, "todos", "待办：", 2);
    }
}

/* ==================== ask: hold the key, ask, the answer streams in over /ws ==================== */

/* the answer's sources ([c20261101103000] and the like) out, in place; an unfinished one at the end too,
 * since the stream may cut it in two */
static void strip_cites(char *s)
{
    char *w = s;

    for (const char *r = s; *r;) {
        if (r[0] == '[' && (r[1] == 'c' || !r[1])) {
            const char *e = strchr(r, ']');
            if (!e) {
                break;
            }
            r = e + 1;
        } else {
            *w++ = *r++;
        }
    }
    *w = 0;
}

static int32_t since_end(void)
{
    return (int32_t)(now_ms() - s_end_ms);
}

static void ask_fail(const char *why)
{
    s_m.ask = ADR_ASK_ERROR;
    put_text(s_m.answer, sizeof(s_m.answer), why);
}

/* {"type":"ask","device":D,"id":ID,"stage":"heard"|"searching"|"done"|"error",...} or {...,"delta":"..."}; under s_lock */
static void ask_msg(const cJSON *m)
{
    const char *id = jstr(m, "id"), *stage = jstr(m, "stage"), *delta = jstr(m, "delta");

    if (!id || s_m.ask < ADR_ASK_SENT) {
        return;
    }
    if (s_ask_id[0] ? strcmp(id, s_ask_id) != 0 : strcmp(id, s_ask_prev) == 0) {
        return; /* an earlier question's */
    }
    if (!s_ask_id[0]) { /* /ws beat the POST's answer */
        put_text(s_ask_id, sizeof(s_ask_id), id);
    }
    if (delta) {
        if (s_lat_delta < 0) {
            s_lat_delta = since_end();
        }
        append(s_ask_raw, sizeof(s_ask_raw), delta);
        put_text(s_m.answer, sizeof(s_m.answer), s_ask_raw);
        strip_cites(s_m.answer);
        s_m.ask = ADR_ASK_ANSWER;
    } else if (!stage) {
        return;
    } else if (strcmp(stage, "heard") == 0) {
        if (s_lat_heard < 0) {
            s_lat_heard = since_end();
        }
        const char *q = jstr(m, "q");
        put_text(s_m.q, sizeof(s_m.q), q ? q : "");
    } else if (strcmp(stage, "searching") == 0) {
        s_m.ask = ADR_ASK_SEARCH;
    } else if (strcmp(stage, "done") == 0) {
        const char *a = jstr(m, "answer");
        if (a) {
            put_text(s_ask_raw, sizeof(s_ask_raw), a);
            put_text(s_m.answer, sizeof(s_m.answer), a);
            strip_cites(s_m.answer);
        }
        s_m.ask = ADR_ASK_DONE;
    } else if (strcmp(stage, "error") == 0) {
        const char *e = jstr(m, "error");
        ask_fail(e && *e ? e : "出错了");
    }
}

static void ws_data(lisa_ws_data_t *d)
{
    if (d->type != LISA_WS_TEXT || !d->len) {
        return;
    }
    if (d->len > WS_MSG_MAX) {
        s_ws_big++;
        return;
    }
    char *s = lisa_mem_alloc(d->len + 1);
    if (!s) {
        return;
    }
    memcpy(s, d->buf, d->len);
    s[d->len] = 0;
    cJSON *m = cJSON_Parse(s);
    lisa_mem_free(s);
    if (!m) {
        return;
    }

    const char *type = jstr(m, "type"), *dev = jstr(m, "device");
    if (type && dev && strcmp(dev, s_cfg.device) == 0) {
        const cJSON *it;
        xSemaphoreTake(s_lock, portMAX_DELAY);
        if (strcmp(type, "partial") == 0) {
            const cJSON *row = cJSON_GetObjectItem(m, "row");
            s_m.partial[0] = 0;
            if (cJSON_IsObject(row)) {
                row_text(s_m.partial, row, true);
            }
        } else if (strcmp(type, "draft") == 0) {
            cJSON_ArrayForEach(it, cJSON_GetObjectItem(m, "rows")) {
                add_row(it, true);
            }
        } else if (strcmp(type, "final") == 0) {
            const char *from = jstr(m, "from"), *to = jstr(m, "to");
            if (to) {
                drop_range(from ? from : "", to);
            }
            cJSON_ArrayForEach(it, cJSON_GetObjectItem(m, "rows")) {
                add_row(it, false);
            }
        } else if (strcmp(type, "ask") == 0) {
            ask_msg(m);
        } else if (strcmp(type, "live") == 0) {
            const cJSON *live = cJSON_GetObjectItem(m, "live");
            if (cJSON_IsObject(live)) {
                set_live(live);
            } else {
                s_m.live[0] = 0;
            }
        }
        s_m.seq++;
        xSemaphoreGive(s_lock);
    }
    cJSON_Delete(m);
}

void adr_model_get(adr_model_t *out)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    *out = s_m;
    xSemaphoreGive(s_lock);
    out->ws_up = s_ws_state == 2;
    out->dropped_ms = (uint32_t)((uint64_t)s_dropped * 1000 / SR);
}

/* ==================== /ws: watch keepalive ==================== */

static void ws_event(lisa_ws_event_t *e)
{
    if (e->what == LISA_WS_ON_CONNECTED) {
        s_ws_state = 2;
    } else if (e->what == LISA_WS_ON_DISCONNECTED || e->what == LISA_WS_ON_ERROR) {
        s_ws_state = 0;
    }
}

static void ws_task(void *arg)
{
    static char host[96], port[8];
    bool tls = strncmp(s_cfg.url, "https://", 8) == 0;
    const char *h = s_cfg.url + (tls ? 8 : 7);
    size_t hl = strcspn(h, ":/");

    (void)arg;
    snprintf(host, sizeof(host), "%.*s", (int)hl, h);
    if (h[hl] == ':') {
        snprintf(port, sizeof(port), "%.*s", (int)strcspn(h + hl + 1, "/"), h + hl + 1);
    } else {
        strcpy(port, tls ? "443" : "80");
    }
    lisa_ws_request_t req = {
        .scheme = (uint8_t *)(tls ? "wss" : "ws"),
        .host = (uint8_t *)host,
        .path = (uint8_t *)"/ws",
        .port = (uint8_t *)port,
        .timeout = 10000,
        .extra_header = s_ws_header,
        .on_event = ws_event,
        .on_data = ws_data,
    };
    lisa_ws_t *ws = lisa_ws_init(&req);
    if (!ws) {
        LISA_LOGE(TAG, "ws init failed");
        vTaskDelete(NULL);
        return;
    }

    for (uint32_t wait = 1000;; wait = wait < 8000 ? wait * 2 : 8000) {
        while (!sys_wifi_is_connected()) {
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
        s_ws_state = 1;
        if (lisa_ws_connect(ws) != LISA_WS_OK) { /* still marked connected from the last session */
            LISA_LOGW(TAG, "ws connect refused");
            s_ws_state = 0;
        }
        for (int i = 0; s_ws_state == 1 && i < 300; i++) {
            vTaskDelay(pdMS_TO_TICKS(100));
        }
        if (s_ws_state == 2) {
            LISA_LOGI(TAG, "ws up: %s:%s", host, port);
            wait = 1000;
            lisa_ws_send_text(ws, (const uint8_t *)"{\"type\":\"watch\",\"live\":true,\"wake\":true}");
            TickType_t last = xTaskGetTickCount();
            while (s_ws_state == 2) {
                vTaskDelay(pdMS_TO_TICKS(500));
                if (xTaskGetTickCount() - last >= pdMS_TO_TICKS(WATCH_EVERY_MS)) {
                    lisa_ws_send_text(ws, (const uint8_t *)"{\"type\":\"watch\",\"live\":true}");
                    last = xTaskGetTickCount();
                }
            }
            LISA_LOGW(TAG, "ws down");
        }
        lisa_ws_disconnect(ws);
        xSemaphoreTake(s_lock, portMAX_DELAY);
        s_m.seq++;
        xSemaphoreGive(s_lock);
        vTaskDelay(pdMS_TO_TICKS(wait));
    }
}

/* ==================== ask: the key and the POST ==================== */

static void ask_begin(int64_t down_ms)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_ask_id[0]) {
        put_text(s_ask_prev, sizeof(s_ask_prev), s_ask_id);
    }
    s_ask_id[0] = s_ask_raw[0] = s_m.q[0] = s_m.answer[0] = 0;
    s_m.ask = ADR_ASK_LISTEN;
    if (s_m.tab != 3) {
        s_m.ask_back = s_m.tab;
        s_m.tab = 3;
    }
    s_req.start_ms = down_ms + s_skew_ms;
    s_m.seq++;
    xSemaphoreGive(s_lock);
}

static void ask_end(void)
{
    static ask_req_t *const req = &s_req;

    s_flush = true;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_end_ms = now_ms();
    s_lat_post = s_lat_heard = s_lat_delta = -1;
    s_req.end_ms = s_end_ms + s_skew_ms;
    s_req.gen = ++s_ask_gen;
    s_m.ask = ADR_ASK_SENT;
    s_m.seq++;
    xSemaphoreGive(s_lock);
    xQueueSend(s_askq, &req, 0); /* a wake-up: the task reads s_req */
}

/* POST /api/ask/live/{device} answered (body NULL: no answer at all) */
static void ask_posted(uint32_t gen, const char *body)
{
    cJSON *j = body ? cJSON_Parse(body) : NULL;
    const char *id = jstr(j, "id");

    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (gen == s_ask_gen) {
        s_lat_post = since_end();
        if (id) {
            put_text(s_ask_id, sizeof(s_ask_id), id);
        } else if (s_m.ask == ADR_ASK_SENT && !s_ask_id[0]) {
            ask_fail(body ? "服务器没接这个问题" : "连不上服务器");
        }
        s_m.seq++;
    }
    xSemaphoreGive(s_lock);
    cJSON_Delete(j);
}

static void *ask_headers(void)
{
    return s_ask_headers;
}

static void ask_on_data(lisa_http_data_t *d)
{
    if (d && d->buf && d->user) {
        put_text(d->user, 256, (const char *)d->buf);
    }
}

static void ask_task(void *arg)
{
    static char url[256] ADR_PSRAM, body[64] ADR_PSRAM, resp[256] ADR_PSRAM;
    ask_req_t *p, r;
    uint32_t sent = 0;

    (void)arg;
    for (;;) {
        xQueueReceive(s_askq, &p, portMAX_DELAY);
        xSemaphoreTake(s_lock, portMAX_DELAY);
        r = *p;
        xSemaphoreGive(s_lock);
        if (r.gen == sent) { /* two wake-ups, one question */
            continue;
        }
        sent = r.gen;
        snprintf(url, sizeof(url), "%s/api/ask/live/%s", s_cfg.url, s_cfg.device);
        snprintf(body, sizeof(body), "{\"start\":%lld,\"end\":%lld}", (long long)r.start_ms, (long long)r.end_ms);
        resp[0] = 0;
        lisa_http_request_t req = {
            .method = LISA_HTTP_POST,
            .url = (uint8_t *)url,
            .headers = (uint8_t *)ask_headers, /* lisa_http takes the header callback here */
            .body = body,
            .body_len = strlen(body),
            .timeout = 10,
            .user = resp,
            .on_data = ask_on_data,
        };
        lisa_http_t *h = lisa_http_init(&req);
        lisa_http_err_e e = h ? lisa_http_perform(h) : LISA_HTTP_COMMON_ERR;
        lisa_http_cleanup(h);
        ask_posted(r.gen, e == LISA_HTTP_OK ? resp : NULL);
        LISA_LOGI(TAG, "ask %lld..%lld posted: %s", (long long)r.start_ms, (long long)r.end_ms, resp);
    }
}

/* Hold the key on the AllDayRec page to ask: it counts from the press once held 0.8 s (SHORT_START; shorter
 * presses stay clicks), the release ends it. While asking the long hold is ours: no power-off. */
bool adr_key(int action)
{
    if (!s_on) {
        return false;
    }
    switch (action) {
    case VOICE_MSG_BUTTON_ACTION_PRESS_DOWN:
        s_down_ms = now_ms();
        return false;
    case VOICE_MSG_BUTTON_ACTION_SHORT_START:
        if (adr_view_visible() && s_m.ask != ADR_ASK_LISTEN) {
            ask_begin(s_down_ms);
        }
        return false;
    case VOICE_MSG_BUTTON_ACTION_LONG_HOLD:
        return s_m.ask == ADR_ASK_LISTEN;
    case VOICE_MSG_BUTTON_ACTION_SHORT_UP:
    case VOICE_MSG_BUTTON_ACTION_LONG_UP:
    case VOICE_MSG_BUTTON_ACTION_LONG_HOLD_UP:
        if (s_m.ask == ADR_ASK_LISTEN) {
            ask_end();
        }
        return false;
    default:
        return false;
    }
}

/* ==================== button, shell, init ==================== */

bool adr_button(int action)
{
    if (!s_on) {
        return false;
    }
    if (action == VOICE_MSG_BUTTON_ACTION_QUADRUPLE_CLICK) {
        adr_view_toggle();
        return true;
    }
    if (action == VOICE_MSG_BUTTON_ACTION_CLICK && adr_view_visible()) {
        xSemaphoreTake(s_lock, portMAX_DELAY);
        s_m.tab = s_m.tab == 3 ? s_m.ask_back : (s_m.tab + 1) % 3;
        s_m.seq++;
        xSemaphoreGive(s_lock);
        return true;
    }
    if (action == VOICE_MSG_BUTTON_ACTION_DOUBLE_CLICK && adr_view_visible()) { /* not the camera preview here */
        adr_view_page();
        return true;
    }
    return false;
}

static int adr_cmd(int argc, char **argv)
{
    adr_model_t *m = lisa_mem_alloc(sizeof(*m));

    (void)argc;
    (void)argv;
    if (!s_on || !m) {
        shellPrint(shellGetCurrent(), "adr: off (kv adr.enabled=1, adr.url, adr.token, then reboot)\r\n");
        lisa_mem_free(m);
        return 0;
    }
    adr_model_get(m);
    shellPrint(shellGetCurrent(), "adr: device=%s url=%s piece=%lums token=set\r\n", s_cfg.device, s_cfg.url,
               (unsigned long)(s_cfg.chunk_samples / 16));
    shellPrint(shellGetCurrent(), "upload: ok=%lu failed=%lu last=%d in %lums queued=%lu dropped=%lums\r\n",
               (unsigned long)m->uploaded, (unsigned long)m->failed, m->last_status, (unsigned long)m->last_ms,
               (unsigned long)uxQueueMessagesWaiting(s_full), (unsigned long)m->dropped_ms);
    shellPrint(shellGetCurrent(), "ws: %s rows=%u partial=%u live=%u oversize=%lu\r\n", m->ws_up ? "up" : "down",
               m->nrows, (unsigned)strlen(m->partial), (unsigned)strlen(m->live), (unsigned long)s_ws_big);
    int64_t span = s_tap_t0 ? now_ms() - s_tap_t0 : 0;
    shellPrint(shellGetCurrent(), "tap: calls=%lu frames=%lu (record_samples=%lu echo=%lu) in %lums = %lu Hz\r\n",
               (unsigned long)s_tap_calls, (unsigned long)s_tap_frames, (unsigned long)s_tap_n,
               (unsigned long)s_tap_echo, (unsigned long)span,
               (unsigned long)(span > 0 ? (uint64_t)s_tap_frames * 1000 / (uint64_t)span : 0));
    shellPrint(shellGetCurrent(), "ask: stage=%u release->post=%ldms heard=%ldms first-delta=%ldms q=%u answer=%u\r\n",
               m->ask, (long)s_lat_post, (long)s_lat_heard, (long)s_lat_delta, (unsigned)strlen(m->q),
               (unsigned)strlen(m->answer));
    lisa_mem_free(m);
    return 0;
}
SHELL_EXPORT_CMD(SHELL_CMD_PERMISSION(0) | SHELL_CMD_TYPE(SHELL_TYPE_CMD_MAIN) | SHELL_CMD_DISABLE_RETURN, adr,
                 adr_cmd, alldayrec recorder status);

static bool kv_str(const char *key, char *dst, size_t cap)
{
    char *v = NULL;

    if (lisa_kv_get_string(key, &v) != 0 || !v) {
        return false;
    }
    snprintf(dst, cap, "%s", v);
    lisa_kv_free(v);
    return dst[0] != 0;
}

static int kv_int(const char *key, int def)
{
    int v;
    return lisa_kv_get_int(key, &v) == 0 ? v : def;
}

int adr_init(void)
{
    if (kv_int("adr.enabled", 0) != 1) {
        LISA_LOGI(TAG, "off");
        return 0;
    }
    if (!kv_str("adr.url", s_cfg.url, sizeof(s_cfg.url)) || !kv_str("adr.token", s_cfg.token, sizeof(s_cfg.token)) ||
        (strncmp(s_cfg.url, "http://", 7) != 0 && strncmp(s_cfg.url, "https://", 8) != 0)) {
        LISA_LOGW(TAG, "adr.url (http[s]://host[:port]) or adr.token missing");
        return -1;
    }
    size_t ul = strlen(s_cfg.url);
    while (ul && s_cfg.url[ul - 1] == '/') {
        s_cfg.url[--ul] = 0;
    }
    if (!kv_str("adr.device", s_cfg.device, sizeof(s_cfg.device))) {
        strcpy(s_cfg.device, "arcs-mini3");
    }
    int ms = kv_int("adr.chunk_ms", 2000);
    ms = ms < 1000 ? 1000 : ms > 10000 ? 10000 : ms;
    s_cfg.chunk_samples = ms * SR / 1000;
    snprintf(s_http_headers, sizeof(s_http_headers), "Content-Type:audio/wav&Authorization:Bearer %s", s_cfg.token);
    snprintf(s_ask_headers, sizeof(s_ask_headers), "Content-Type:application/json&Authorization:Bearer %s", s_cfg.token);
    snprintf(s_ws_header, sizeof(s_ws_header), "\r\nAuthorization: Bearer %s", s_cfg.token); /* nopoll: leading CRLF */

    uint32_t count = POOL_MS / ms;
    s_lock = xSemaphoreCreateMutex();
    s_free = xQueueCreate(count, sizeof(chunk_t *));
    s_full = xQueueCreate(count, sizeof(chunk_t *));
    s_askq = xQueueCreate(2, sizeof(ask_req_t *));
    if (!s_lock || !s_free || !s_full || !s_askq) {
        return -1;
    }
    for (uint32_t i = 0; i < count; i++) {
        chunk_t *c = lisa_mem_alloc(sizeof(chunk_t) + WAV_HDR + s_cfg.chunk_samples * 2);
        if (!c) {
            LISA_LOGE(TAG, "pool alloc failed at %lu", (unsigned long)i);
            return -1;
        }
        xQueueSend(s_free, &c, 0);
    }

    lisa_thread_attr_t up = {.name = (uint8_t *)"adr_up", .stack_size = 8192, .priority = LISA_OS_PRIORITY_LOW};
    lisa_thread_attr_t wsa = {.name = (uint8_t *)"adr_ws", .stack_size = 4096, .priority = LISA_OS_PRIORITY_LOW};
    lisa_thread_attr_t aska = {.name = (uint8_t *)"adr_ask", .stack_size = 8192, .priority = LISA_OS_PRIORITY_LOW};
    if (!lisa_thread_create(&up, upload_task, NULL) || !lisa_thread_create(&wsa, ws_task, NULL) ||
        !lisa_thread_create(&aska, ask_task, NULL)) {
        LISA_LOGE(TAG, "thread create failed");
        return -1;
    }
    s_m.enabled = true;
    s_on = true;
    adr_view_register();
    service_power_policy_set_recording_active(true);
    app_wakeup_record_tap = adr_tap;
    LISA_LOGI(TAG, "on: device=%s url=%s piece=%dms pool=%lu", s_cfg.device, s_cfg.url, ms, (unsigned long)count);
    return 0;
}
