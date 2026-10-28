/* Host stand-ins for the SDK headers adr.c includes: just enough to run its logic. */
#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/time.h>

typedef uint32_t TickType_t;
typedef int BaseType_t;
#define pdTRUE 1
#define pdPASS 1
#define portMAX_DELAY 0xffffffffu
#define pdMS_TO_TICKS(x) (x)
#define pdTICKS_TO_MS(x) (x)

typedef struct { void *items[32]; int head, count, cap; } fake_queue_t;
typedef fake_queue_t *QueueHandle_t;
typedef void *SemaphoreHandle_t;
QueueHandle_t xQueueCreate(int cap, int size);
BaseType_t xQueueSend(QueueHandle_t q, const void *item, TickType_t wait);
BaseType_t xQueueReceive(QueueHandle_t q, void *item, TickType_t wait);
static inline int uxQueueMessagesWaiting(QueueHandle_t q) { return q->count; }
static inline SemaphoreHandle_t xSemaphoreCreateMutex(void) { return (void *)1; }
#define xSemaphoreTake(s, t) ((void)(s), 1)
#define xSemaphoreGive(s) ((void)(s), 1)
extern TickType_t fake_ticks;
static inline TickType_t xTaskGetTickCount(void) { return fake_ticks; }
static inline void vTaskDelay(TickType_t t) { fake_ticks += t; }
static inline void vTaskDelete(void *t) { (void)t; }

extern int64_t fake_now_ms;
static inline int fake_gtod(struct timeval *tv) { tv->tv_sec = fake_now_ms / 1000; tv->tv_usec = (fake_now_ms % 1000) * 1000; return 0; }
#define gettimeofday(tv, tz) fake_gtod(tv)

typedef uint32_t UINT32;
typedef enum { VerbPost = 1 } HTTP_VERB;
typedef struct { char Uri[512]; HTTP_VERB HttpVerb; void *pData; UINT32 pLength; UINT32 nTimeout; } HTTPParameters;
typedef struct { UINT32 HTTPStatusCode, TotalResponseBodyLength; } HTTP_CLIENT;
typedef struct { void *pData; int32_t pLength; } PostData;
static inline int HTTPC_open(HTTPParameters *p) { (void)p; return -1; }
static inline int HTTPC_request_r(HTTPParameters *p, void *(*h)(void), PostData (*d)(void)) { (void)p; (void)h; (void)d; return -1; }
static inline int HTTPC_get_request_info(HTTPParameters *p, void *c) { (void)p; (void)c; return -1; }
static inline int HTTPC_read(HTTPParameters *p, void *b, UINT32 n, UINT32 *got) { (void)p; (void)b; (void)n; *got = 0; return -1; }
static inline int HTTPC_close(HTTPParameters *p) { (void)p; return 0; }

typedef struct { const void *record_buffer, *echo_buffer; uint32_t record_samples, echo_samples; } lisa_audio_event_t;

#include <stdlib.h>
static inline void *lisa_mem_alloc(uint32_t n) { return malloc(n); }
static inline void *lisa_mem_calloc(uint32_t c, uint32_t n) { return calloc(c, n); }
static inline void lisa_mem_free(void *p) { free(p); }
static inline int lisa_kv_get_string(const char *k, char **v) { (void)k; (void)v; return -1; }
static inline int lisa_kv_get_int(const char *k, int *v) { (void)k; (void)v; return -1; }
static inline void lisa_kv_free(void *v) { free(v); }
typedef struct { uint8_t *name; uint32_t stack_size, priority; } lisa_thread_attr_t;
#define LISA_OS_PRIORITY_LOW 1
static inline void *lisa_thread_create(const lisa_thread_attr_t *a, void (*f)(void *), void *arg) { (void)a; (void)f; (void)arg; return NULL; }

#define shellPrint(sh, ...) printf(__VA_ARGS__)
#define shellGetCurrent() NULL
#define SHELL_EXPORT_CMD(...)
static inline bool sys_wifi_is_connected(void) { return true; }
enum { VOICE_MSG_BUTTON_ACTION_CLICK = 1, VOICE_MSG_BUTTON_ACTION_DOUBLE_CLICK = 2, VOICE_MSG_BUTTON_ACTION_QUADRUPLE_CLICK = 4 };
extern void (*app_wakeup_record_tap)(const void *event);
static inline void service_power_policy_set_recording_active(bool a) { (void)a; }
#define LISA_LOGI(tag, ...) ((void)0)
#define LISA_LOGW(tag, ...) ((void)0)
#define LISA_LOGE(tag, ...) ((void)0)
