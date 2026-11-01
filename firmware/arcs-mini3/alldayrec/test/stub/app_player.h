#pragma once
#include "stubs.h"
typedef struct app_player app_player_t;
typedef enum { APP_PLAYER_STATE_IDLE, APP_PLAYER_STATE_PREPARING, APP_PLAYER_STATE_PREPARED, APP_PLAYER_STATE_PLAYING,
               APP_PLAYER_STATE_PAUSED, APP_PLAYER_STATE_STOPPED, APP_PLAYER_STATE_ERROR } app_player_state_t;
enum { APP_PLAYER_OK = 0, APP_PLAYER_ERR_IO = -6 };
static inline app_player_state_t app_player_get_state(app_player_t *p) { (void)p; return APP_PLAYER_STATE_IDLE; }
static inline int app_player_set_decode_prepared_count(app_player_t *p, uint32_t n) { (void)p; (void)n; return 0; }
static inline int app_player_set_readstream_buf_size(app_player_t *p, uint32_t n) { (void)p; (void)n; return 0; }
static inline int app_player_play_stream(app_player_t *p, int sr, int ch, int bits) { (void)p; (void)sr; (void)ch; (void)bits; return 0; }
static inline int app_player_write_stream(app_player_t *p, const uint8_t *b, int n, int ms) { (void)p; (void)b; (void)ms; return n; }
static inline int app_player_finish_stream(app_player_t *p) { (void)p; return 0; }
static inline int app_player_stop(app_player_t *p) { (void)p; return 0; }
static inline int app_player_reset(app_player_t *p) { (void)p; return 0; }
