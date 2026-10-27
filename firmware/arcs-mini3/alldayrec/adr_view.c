#include <stdio.h>
#include <string.h>

#include "FreeRTOS.h"
#include "task.h"

#include "lisa_ui.h"
#include "lisa_ui_fonts.h"
#include "lisa_ui_invoke.h"
#include "lisa_ui_nav_scr.h"
#include "lvgl.h"

#include "adr.h"

#define W 240
#define H 240
#define TOP 30
#define NLINES (ADR_ROWS + 1) /* the rows + the sentence being spoken */
#define WHITE 0xffffff
#define GREY 0x8a8a8a
#define SPEAKING 0xffc83d /* the sentence being said */

static lv_obj_t *s_root, *s_tabs, *s_box, *s_dot, *s_lines[NLINES];
static lv_timer_t *s_timer;
static volatile bool s_visible;
static adr_model_t s_snap ADR_PSRAM; /* ~2.5 KB, off the UI task's stack */
static uint32_t s_shown = UINT32_MAX;
static int s_shown_tab = -1;
static char s_status[256] ADR_PSRAM;

/* One label per line, coloured by style: LVGL 8 resets "#rrggbb text#" recolouring at every wrapped line,
 * so a long sentence lost its colour and its closing '#' ate or showed the next colour code. */
static int line(int i, const char *text, uint32_t colour)
{
    lv_label_set_text(s_lines[i], text);
    lv_obj_set_style_text_color(s_lines[i], lv_color_hex(colour), 0);
    lv_obj_clear_flag(s_lines[i], LV_OBJ_FLAG_HIDDEN);
    return i + 1;
}

static void render(void)
{
    static const char *names[] = {"实时", "总结", "状态"};
    char tabs[96] = "";
    int n = 0;

    for (int i = 0; i < 3; i++) { /* one short line: recolouring is safe here */
        size_t len = strlen(tabs);
        snprintf(tabs + len, sizeof(tabs) - len, "%s%s %s#", i ? "  " : "", i == s_snap.tab ? "#ffffff" : "#8a8a8a",
                 names[i]);
    }
    lv_label_set_text(s_tabs, tabs);

    if (s_snap.tab == 0) {
        for (int i = 0; i < s_snap.nrows; i++) {
            n = line(n, s_snap.rows[i].text, s_snap.rows[i].draft ? GREY : WHITE);
        }
        if (s_snap.partial[0]) {
            n = line(n, s_snap.partial, SPEAKING);
        } else if (!s_snap.nrows) {
            n = line(n, "等待转写…", GREY);
        }
    } else if (s_snap.tab == 1) {
        n = line(n, s_snap.live[0] ? s_snap.live : "说满 1 分钟后出实时总结", s_snap.live[0] ? WHITE : GREY);
    } else {
        snprintf(s_status, sizeof(s_status),
                 "录音：%s\n推送：%s\n已上传：%lu 块\n失败：%lu 次\n最近：HTTP %d，%lu ms\n丢弃：%lu 秒",
                 s_snap.enabled ? "开" : "关", s_snap.ws_up ? "已连接" : "未连接", (unsigned long)s_snap.uploaded,
                 (unsigned long)s_snap.failed, s_snap.last_status, (unsigned long)s_snap.last_ms,
                 (unsigned long)(s_snap.dropped_ms / 1000));
        n = line(n, s_status, WHITE);
    }
    for (; n < NLINES; n++) {
        lv_obj_add_flag(s_lines[n], LV_OBJ_FLAG_HIDDEN);
    }
    if (s_snap.tab != s_shown_tab) { /* 实时 keeps the newest line at the bottom; older ones go off the top */
        lv_obj_set_flex_align(s_box, s_snap.tab == 0 ? LV_FLEX_ALIGN_END : LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_START,
                              LV_FLEX_ALIGN_START);
        lv_obj_scroll_to_y(s_box, 0, LV_ANIM_OFF);
        s_shown_tab = s_snap.tab;
    }
}

static void tick(lv_timer_t *t)
{
    (void)t;
    adr_model_get(&s_snap);
    bool rec = s_snap.last_ok_tick && xTaskGetTickCount() - s_snap.last_ok_tick < pdMS_TO_TICKS(10000);
    lv_obj_set_style_bg_color(s_dot, rec ? lv_color_hex(0xff3b30) : lv_color_hex(0x444444), 0);
    if (s_snap.seq != s_shown) {
        render();
        s_shown = s_snap.seq;
    }
}

static void plain(lv_obj_t *o)
{
    lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_pad_all(o, 0, 0);
    lv_obj_set_style_border_width(o, 0, 0);
    lv_obj_set_style_radius(o, 0, 0);
    lv_obj_set_style_bg_color(o, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
}

static lv_obj_t *label(lv_obj_t *parent)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, &lv_font_chinese_16, 0);
    lv_obj_set_style_text_color(l, lv_color_white(), 0);
    return l;
}

static int page_open(const struct lisa_ui_nav_scr *scr, void **data)
{
    (void)scr;
    s_root = lv_obj_create(lv_scr_act());
    plain(s_root);
    lv_obj_set_pos(s_root, 0, 0);
    lv_obj_set_size(s_root, W, H);

    s_tabs = label(s_root);
    lv_label_set_recolor(s_tabs, true);
    lv_obj_set_pos(s_tabs, 10, 6);

    s_dot = lv_obj_create(s_root);
    lv_obj_set_size(s_dot, 10, 10);
    lv_obj_set_style_radius(s_dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(s_dot, 0, 0);
    lv_obj_align(s_dot, LV_ALIGN_TOP_RIGHT, -12, 10);

    s_box = lv_obj_create(s_root); /* clips the lines */
    plain(s_box);
    lv_obj_set_pos(s_box, 0, TOP);
    lv_obj_set_size(s_box, W, H - TOP - 4);
    lv_obj_set_flex_flow(s_box, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_left(s_box, 8, 0);
    lv_obj_set_style_pad_row(s_box, 6, 0);
    lv_obj_set_style_pad_bottom(s_box, 10, 0); /* the panel's bottom edge hid part of the last line */
    for (int i = 0; i < NLINES; i++) {
        s_lines[i] = label(s_box);
        lv_label_set_long_mode(s_lines[i], LV_LABEL_LONG_WRAP);
        lv_obj_set_width(s_lines[i], W - 16);
        lv_obj_add_flag(s_lines[i], LV_OBJ_FLAG_HIDDEN);
    }

    s_shown = UINT32_MAX;
    s_shown_tab = -1;
    s_timer = lv_timer_create(tick, 250, NULL);
    tick(NULL);
    s_visible = true;
    *data = NULL;
    return 0;
}

static int page_close(const struct lisa_ui_nav_scr *scr, void *data)
{
    (void)scr;
    (void)data;
    s_visible = false;
    if (s_timer) {
        lv_timer_del(s_timer);
        s_timer = NULL;
    }
    if (s_root) {
        lv_obj_del(s_root);
        s_root = NULL;
        s_box = NULL;
    }
    return 0;
}

static int page_pause(const struct lisa_ui_nav_scr *scr, void *data)
{
    (void)scr;
    (void)data;
    s_visible = false;
    lv_timer_pause(s_timer);
    lv_obj_add_flag(s_root, LV_OBJ_FLAG_HIDDEN);
    return 0;
}

static int page_resume(const struct lisa_ui_nav_scr *scr, void *data)
{
    (void)scr;
    (void)data;
    lv_obj_clear_flag(s_root, LV_OBJ_FLAG_HIDDEN);
    s_shown = UINT32_MAX;
    lv_timer_resume(s_timer);
    s_visible = true;
    return 0;
}

static const struct lisa_ui_nav_scr s_nav = {
    .unique_id = ADR_NAV_SCR_ID,
    .open = page_open,
    .close = page_close,
    .pause = page_pause,
    .resume = page_resume,
};

/* ponytail: the page keeps its place when the summary updates; jump back to the top then if readers get lost */
static void page_worker(void *arg, uint32_t len)
{
    (void)arg;
    (void)len;
    if (!s_box || s_shown_tab == 0) { /* 实时 follows the newest line by itself */
        return;
    }
    lv_coord_t more = lv_obj_get_scroll_bottom(s_box);
    if (more > 0) { /* one line of the last screen stays in view */
        lv_obj_scroll_to_y(s_box, lv_obj_get_scroll_y(s_box) + LV_MIN(more, lv_obj_get_content_height(s_box) - 20),
                           LV_ANIM_ON);
    } else {
        lv_obj_scroll_to_y(s_box, 0, LV_ANIM_ON);
    }
}

void adr_view_page(void)
{
    lisa_ui_invoke_ui_delayed(page_worker, NULL, 0, 0);
}

static void toggle_worker(void *arg, uint32_t len)
{
    (void)arg;
    (void)len;
    if (lisa_ui_nav_scr_get_top_id() == ADR_NAV_SCR_ID) {
        lisa_ui_nav_scr_nav_default();
    } else {
        lisa_ui_nav_scr_nav_to(ADR_NAV_SCR_ID);
    }
}

int adr_view_register(void)
{
    return lisa_ui_nav_scr_add(&s_nav);
}

void adr_view_toggle(void)
{
    lisa_ui_invoke_ui_delayed(toggle_worker, NULL, 0, 0);
}

bool adr_view_visible(void)
{
    return s_visible;
}
