#ifndef BUZZER_H
#define BUZZER_H
/* One segment of tone (<= 20 ms), from one thread: 0 played, -EPERM a silent build (cap 0), -EBUSY one is playing,
 * -ETIMEDOUT the PWM did not stop by itself (stopped by the CPU then) */
int buzzer_segment(void);
/* Segments back to back for about ms, each started by the CPU after the last one stopped; the first error ends it */
int buzzer_beep(int ms);
#endif
