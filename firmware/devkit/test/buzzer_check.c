/* buzzer_seq.h: a segment is at most 20 ms of tone plus one low period, the duty never above 25 %, cap 0 silent */
#include <assert.h>
#include <stdio.h>

#include "../src/buzzer_seq.h"

int main(void)
{
    uint16_t seq[BUZ_PERIODS + 1];

    assert(BUZ_PERIODS * BUZ_TOP <= BUZ_SEG_MS * 16000 && BUZ_SEG_MS <= 20);
    assert(buz_fill(seq, 0) == 0 && buz_fill(seq, -5) == 0);
    for (int cap = 1; cap <= 1000; cap++) {
        int n = buz_fill(seq, cap);

        assert(n == BUZ_PERIODS + 1 && (long)n * BUZ_TOP <= 20250L * 16);  // <= 20.25 ms at 16 MHz
        for (int i = 0; i < n - 1; i++) {
            assert(seq[i] & BUZ_POL && (seq[i] & 0x7FFF) <= BUZ_TOP / 4 && (seq[i] & 0x7FFF) > 0);
        }
        assert(seq[n - 1] == BUZ_POL);  // the last period low
    }
    buz_fill(seq, 100);
    assert((seq[0] & 0x7FFF) == 400);  // 10 %
    buz_fill(seq, 1000);
    assert((seq[0] & 0x7FFF) == 1000);  // capped at 25 %
    puts("buzzer_check: ok");
    return 0;
}
