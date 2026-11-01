#ifndef __ADR_H__
#define __ADR_H__

#include <stdbool.h>
#include <stdint.h>

/* AllDayRec recorder: mic -> WAV pieces -> POST /api/chunks; /ws pushes -> the AllDayRec page.
 * Config in kv: adr.url (https://host[:port]), adr.token, adr.device, adr.enabled (int), adr.chunk_ms (int). */

#define ADR_NAV_SCR_ID 0x4144 /* outside apps-ui's enum */
#define ADR_ROWS 8
#define ADR_ROW_BYTES 160
#define ADR_LIVE_BYTES 768
#define ADR_Q_BYTES 256
#define ADR_ANSWER_BYTES 1024
#ifndef ADR_PSRAM
#define ADR_PSRAM __attribute__((section(".psram.bss"))) /* SRAM is full */
#endif

typedef struct {
    char start[24]; /* the row's ISO start, the sort and replace key */
    char text[ADR_ROW_BYTES];
    bool draft;
} adr_row_t;

enum { ADR_ASK_NONE, ADR_ASK_LISTEN, ADR_ASK_SENT, ADR_ASK_SEARCH, ADR_ASK_ANSWER, ADR_ASK_DONE, ADR_ASK_ERROR };

typedef struct {
    uint32_t seq; /* bumped on every change */
    uint8_t tab;  /* 0 实时 1 总结 2 状态 3 问答 */
    uint8_t ask_back; /* the tab a click on 问答 goes back to */
    uint8_t ask;      /* ADR_ASK_* */
    char q[ADR_Q_BYTES];
    char answer[ADR_ANSWER_BYTES]; /* sources taken out; the reason on ADR_ASK_ERROR */
    adr_row_t rows[ADR_ROWS];
    uint8_t nrows;
    char partial[ADR_ROW_BYTES];
    char live[ADR_LIVE_BYTES];
    bool enabled, ws_up;
    uint32_t uploaded, failed, dropped_ms;
    int last_status;
    uint32_t last_ms;
    uint32_t last_ok_tick;
} adr_model_t;

int adr_init(void);
bool adr_button(int action);                 /* true: the AllDayRec page used the action */
bool adr_key(int action);                    /* the power key's raw events, from the button task; true: keep it from the app */
void adr_model_get(adr_model_t *out);       /* a copy, for the UI thread */

/* adr_view.c */
int adr_view_register(void);
void adr_view_toggle(void);                  /* any thread: open the page, or back to home */
bool adr_view_visible(void);
void adr_view_page(void);                    /* any thread: 总结/状态 one screen down, at the end back to the top */

#endif
