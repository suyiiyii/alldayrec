// Host check of the NAND ring log (src/nand_log.c) on a simulated W25N: pages programmed once each, in order within a
// block, only after an erase (any other program fails the check), bits only from 1 to 0. Covers write and read back,
// remounts, the list rebuilt by halving against a scan of every page, DELETE and the uploaded pointer, the ring going
// round with and without uploads, a power cut at every program and erase of a run (the page or block left half done),
// factory and runtime bad blocks (program and erase failures), ECC corrected and not correctable.
//   cc -O1 -o /tmp/nand_check test/nand_check.c && /tmp/nand_check

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

#include "../src/nand_log.c"

#define NB 16  // blocks in the simulated chip
#define NP (NB * NL_PPB)
#define PS (NL_PAGE + NL_SPARE)
static uint8_t mem[NP][PS];
static bool programmed[NP];
static int ecc_of[NP];          // what a read of the page reports
static uint32_t pfail = INV;    // a program of this page fails
static uint32_t efail = INV;    // an erase of this block fails, once
static long ops_n, cut_at = -1;  // the op the power goes at (left half done); then nothing more reaches the chip
static bool dead;
static bool hung;  // the chip stops answering: program and erase time out
static uint32_t committed[1 << 12];  // per file (low 12 bits): bytes in pages whose program finished
static unsigned rnd_state = 1;

static unsigned rnd(void)
{
    rnd_state = rnd_state * 1103515245 + 12345;
    return rnd_state >> 16;
}

static int sim_read(uint32_t page, uint16_t col, void *buf, uint16_t n, int *ecc)
{
    assert(page < NP && col + n <= PS);
    if (dead) {
        return -EIO;
    }
    memcpy(buf, mem[page] + col, n);
    *ecc = ecc_of[page];
    if (*ecc == 2 && n > 8) {
        ((uint8_t *) buf)[n / 2] ^= 0x5A;  // not correctable: some bits wrong
    }
    return 0;
}

/* true for the op the power goes in (left half done); dead after it */
static bool cut(void)
{
    if (dead || ++ops_n != cut_at) {
        return false;
    }
    dead = true;
    return true;
}

static int sim_program(uint32_t page, const uint8_t *data, const uint8_t *spare)
{
    assert(page < NP);
    if (hung) {
        return -ETIMEDOUT;
    }
    bool torn = cut();
    if (dead && !torn) {
        return 0;
    }
    assert(!programmed[page]);  // never twice without an erase
    if (page % NL_PPB) {
        assert(programmed[page - 1] || !programmed[page]);  // in order: the ones before it in the block were
        for (uint32_t p = page + 1; p % NL_PPB; p++) {
            assert(!programmed[p]);
        }
    }
    programmed[page] = true;
    for (int i = 0; i < PS; i++) {
        uint8_t v = i < NL_PAGE ? data[i] : spare[i - NL_PAGE];
        mem[page][i] &= torn || page == pfail ? v | rnd() : v;  // cut or failing: some bits only
    }
    if (page == pfail) {
        return -EIO;
    }
    if (!torn) {
        uint32_t f = get32(spare + 20), off = get32(spare + 36), len = get32(spare + 52) & 0xFFFF;
        committed[f & 0xFFF] = committed[f & 0xFFF] > off + len ? committed[f & 0xFFF] : off + len;
    }
    return 0;
}

static int sim_erase(uint32_t block)
{
    if (hung) {
        return -ETIMEDOUT;
    }
    bool torn = cut();
    if (dead && !torn) {
        return 0;
    }
    for (uint32_t p = block * NL_PPB; p < (block + 1) * NL_PPB; p++) {
        if (!torn || rnd() & 1) {  // cut short: some pages erased
            memset(mem[p], 0xFF, PS);
            programmed[p] = false;
        }
    }
    if (block == efail) {
        efail = INV;
        return -EIO;
    }
    return 0;
}

static int sim_mark(uint32_t block)
{
    if (!dead) {
        mem[block * NL_PPB][NL_PAGE] = 0;
    }
    return 0;
}

static const struct nl_ops sim = {sim_read, sim_program, sim_erase, sim_mark};

static void fresh(void)
{
    memset(mem, 0xFF, sizeof(mem));
    memset(programmed, 0, sizeof(programmed));
    memset(ecc_of, 0, sizeof(ecc_of));
    memset(committed, 0, sizeof(committed));
    memset(nl_err, 0, sizeof(nl_err));
    pfail = efail = INV;
    hung = false;
    ops_n = 0;
    cut_at = -1;
    dead = false;
}

static uint8_t pat(uint32_t file, uint32_t off)
{
    return file * 131 + off * 7 + (off >> 9);
}

static void put(uint32_t file, uint32_t off, uint32_t n)
{
    static uint8_t b[8192];
    for (uint32_t k; n; off += k, n -= k) {
        k = n < sizeof(b) ? n : sizeof(b);
        for (uint32_t i = 0; i < k; i++) {
            b[i] = pat(file, off + i);
        }
        assert(nl_append(file, b, k) == (int) k);
    }
}

static uint32_t size_of(uint32_t file)
{
    for (int i = 0; i < nl_files(); i++) {
        uint32_t f, s;
        nl_file(i, &f, &s);
        if (f == file) {
            return s;
        }
    }
    return INV;
}

/* A listed file read whole, from off 0 and from a few offsets in, byte for byte; bad: bytes allowed to differ (ECC) */
static void check_file(uint32_t file, uint32_t size, bool bad_ok)
{
    static uint8_t b[4096];
    uint32_t off = 0;
    int wrong = 0;
    while (off < size) {
        int n = nl_read(file, off, b, sizeof(b));
        assert(n > 0);
        for (int i = 0; i < n; i++) {
            wrong += b[i] != pat(file, off + i);
        }
        off += n;
    }
    assert(off == size && nl_read(file, off, b, sizeof(b)) == 0);
    assert(bad_ok || !wrong);
    for (int k = 0; k < 5 && size; k++) {  // jumps: the halving
        uint32_t o = rnd() % size;
        int n = nl_read(file, o, b, 16);
        assert(n > 0);
        assert(bad_ok || b[0] == pat(file, o));
    }
}

static void check_all(bool bad_ok)
{
    for (int i = 0; i < nl_files(); i++) {
        uint32_t f, s;
        nl_file(i, &f, &s);
        check_file(f, s, bad_ok);
    }
}

/* The list as a scan of every page would have it (seq order, CRC good, from the uploaded pointer, a file starting at
 * offset 0), against what nl_mount built by halving */
static void check_list_by_scan(void)
{
    static struct { uint32_t seq, file, end; } pages[NP];
    int n = 0;
    for (uint32_t p = 0; p < NP; p++) {
        struct hdr h;
        if (mem[p - p % NL_PPB][NL_PAGE] != 0xFF || unpack(mem[p] + NL_PAGE, 0, &h) != H_OK ||
            page_crc(&h, mem[p]) != h.crc || page_of(h.seq) != p || h.seq < nl_up()) {
            continue;
        }
        pages[n].seq = h.seq, pages[n].file = h.file, pages[n++].end = h.off + h.len;
        if (h.off == 0) {
            pages[n - 1].end |= 0x80000000;  // a start
        }
    }
    for (int i = 0; i < n; i++) {  // seq order
        for (int j = i + 1; j < n; j++) {
            if (pages[j].seq < pages[i].seq) {
                __typeof__(pages[0]) t = pages[i];
                pages[i] = pages[j], pages[j] = t;
            }
        }
        assert(!i || pages[i].seq != pages[i - 1].seq);  // no seq twice
    }
    uint32_t lf[NL_MAX_FILES], ls[NL_MAX_FILES];
    int m = 0;
    for (int i = 0; i < n; i++) {
        if (m && lf[m - 1] == pages[i].file) {
            ls[m - 1] = pages[i].end & 0x7FFFFFFF;
        } else if (pages[i].end & 0x80000000) {
            lf[m] = pages[i].file, ls[m++] = pages[i].end & 0x7FFFFFFF;
        }
    }
    assert(m == nl_files());
    for (int i = 0; i < m; i++) {
        uint32_t f, s;
        nl_file(i, &f, &s);
        assert(f == lf[i] && s == ls[i]);
    }
}

static void test_basic(void)
{
    fresh();
    assert(!nl_mount(&sim, NB, 0) && nl_files() == 0 && nl_total() == (uint64_t) NB * NL_PPB * NL_PAGE);
    put(1, 0, 5000);
    put(1, 5000, 3000);
    assert(size_of(1) == 8000);
    check_file(1, 8000, false);  // the open page from RAM, the rest from the chip
    assert(!nl_flush());
    put(2, 0, 100);
    assert(!nl_flush());
    put(3, 0, 4100);
    bool moved;
    assert(nl_delete(3, &moved) == -EBUSY);  // being written
    assert(nl_append(1, "x", 1) == -EROFS);  // an older file: its pages would not be one run
    assert(!nl_flush());
    check_all(false);
    assert(nl_used() == 8000 + 100 + 4100);

    assert(!nl_mount(&sim, NB, 0));  // remount: the same list, the halving's against a full scan
    assert(nl_files() == 3 && size_of(1) == 8000 && size_of(2) == 100 && size_of(3) == 4100);
    check_all(false);
    check_list_by_scan();

    // DELETE: in order, the uploaded pointer goes on and the files stay gone; out of order, back after a remount
    assert(!nl_delete(1, &moved) && moved);
    uint32_t u = nl_up();
    assert(!nl_delete(3, &moved) && !moved && nl_files() == 1);
    assert(!nl_mount(&sim, NB, u));
    assert(nl_files() == 2 && size_of(1) == INV && size_of(2) == 100 && size_of(3) == 4100);
    put(4, 0, 10);  // a new file after a remount
    assert(!nl_flush());
    assert(!nl_mount(&sim, NB, u) && size_of(4) == 10);
    check_list_by_scan();
    printf("basic ok\n");
}

/* Files of random sizes in random pieces; every file but the open one deleted (uploaded) at times when upload */
static uint32_t writer(uint32_t first, int files, bool upload, uint32_t *u)
{
    uint32_t f = first;
    for (int i = 0; i < files; i++, f++) {
        uint32_t size = 0, want = 2000 + rnd() % 60000;
        while (size < want) {
            uint32_t n = 1 + rnd() % 6000;
            put(f, size, n);
            size += n;
        }
        assert(!nl_flush());
        if (upload && i) {
            bool moved;
            assert(!nl_delete(f - 1, &moved));
            *u = nl_up();
        }
    }
    return f;
}

static void test_wrap(void)
{
    uint32_t u = 0;
    fresh();
    assert(!nl_mount(&sim, NB, 0));
    writer(1, 200, true, &u);  // ~6 MB through a 2 MB chip, the phone keeping up
    assert(nl_err[NL_LOST] == 0 && nl_files() == 1);
    check_all(false);
    assert(!nl_mount(&sim, NB, u) && nl_files() == 1);
    check_all(false);

    fresh();
    u = 0;
    assert(!nl_mount(&sim, NB, 0));
    writer(1, 200, false, &u);  // no phone: the oldest go
    assert(nl_err[NL_LOST] > 0 && nl_files() > 10);
    check_all(false);
    uint32_t n = nl_files(), f0, s0;
    nl_file(0, &f0, &s0);
    assert(!nl_mount(&sim, NB, 0));
    check_list_by_scan();
    check_all(false);
    assert(nl_files() <= (int) n && nl_files() >= (int) n - 3);  // a block or two of the oldest go at the remount
    printf("wrap ok (%u lost)\n", nl_err[NL_LOST]);
}

/* A run of files, the power cut at op k: after a remount every page whose program finished reads back, the list
 * matches a scan, writing goes on and survives another remount. wrapped: on a chip the ring has gone round already
 * (uploaded), so the cuts fall in erases of old data too */
static void test_cuts(bool wrapped)
{
    static uint8_t mem0[NP][PS];
    static bool prog0[NP];
    uint32_t u = 0;
    fresh();
    assert(!nl_mount(&sim, NB, 0));
    if (wrapped) {
        rnd_state = 3;
        writer(500, 90, true, &u);
        assert(nl_files() == 1);  // the open one stays; the rest are uploaded
    }
    memcpy(mem0, mem, sizeof(mem));
    memcpy(prog0, programmed, sizeof(programmed));
    uint32_t u0 = u;
    long total = -1;
    for (long k = 0; k <= total + 1; k++) {  // k 0: no cut, counts the ops
        memset(committed, 0, sizeof(committed));
        memcpy(mem, mem0, sizeof(mem));
        memcpy(programmed, prog0, sizeof(programmed));
        dead = false;
        assert(!nl_mount(&sim, NB, u0));
        ops_n = 0;
        cut_at = k ? k : -1;
        rnd_state = 7;
        writer(1, 12, false, &u);
        if (!k) {
            total = ops_n;
            continue;
        }
        dead = false;
        cut_at = -1;
        assert(!nl_mount(&sim, NB, u0));
        check_list_by_scan();
        check_all(false);
        for (uint32_t f = 1; f <= 12; f++) {
            uint32_t s = size_of(f);
            assert(committed[f] == 0 || (s != INV && s >= committed[f]));  // nothing that was on the chip lost
        }
        for (uint32_t f = 100; f < 103; f++) {
            put(f, 0, 3000 + f);
            assert(!nl_flush());
        }
        assert(!nl_mount(&sim, NB, u0));
        check_list_by_scan();
        check_all(false);
        assert(size_of(102) == 3102);
    }
    printf("power cuts%s ok (%ld cut points)\n", wrapped ? " (ring gone round)" : "", total);
}

static void test_bad(void)
{
    uint32_t u = 0;
    fresh();
    mem[3 * NL_PPB][0] = mem[3 * NL_PPB][NL_PAGE] = 0;  // factory bad, W25N01GV style: main and spare
    mem[5 * NL_PPB][0] = 0;                              // W25N02KV style: the main area's first byte only
    assert(!nl_mount(&sim, NB, 0) && nl_total() == (uint64_t) (NB - 2) * NL_PPB * NL_PAGE);
    pfail = 7 * NL_PPB + 9;  // a program fails mid-block
    efail = 10;              // an erase fails
    writer(1, 150, true, &u);  // round the ring a few times
    assert(nl_err[NL_FAILS] == 2 && nl_err[NL_LOST] == 0);
    for (uint32_t p = 3 * NL_PPB; p < 4 * NL_PPB; p++) {
        assert(!programmed[p]);
    }
    for (uint32_t p = 5 * NL_PPB; p < 6 * NL_PPB; p++) {
        assert(!programmed[p]);
    }
    assert(mem[7 * NL_PPB][NL_PAGE] == 0 && mem[10 * NL_PPB][NL_PAGE] == 0);  // marked
    check_all(false);
    assert(!nl_mount(&sim, NB, u) && nl_total() == (uint64_t) (NB - 4) * NL_PPB * NL_PAGE);
    check_all(false);
    printf("bad blocks ok\n");
}

static void test_ecc(void)
{
    fresh();
    assert(!nl_mount(&sim, NB, 0));
    put(1, 0, 20000);
    assert(!nl_flush());
    uint32_t p = page_of(files[0].seq + 3);
    ecc_of[p] = 1;  // corrected: as good
    check_file(1, 20000, false);
    assert(nl_err[NL_ECC_FIXED] > 0);
    ecc_of[p] = 2;  // not correctable: sent as read, counted
    rd_seq = INV;
    uint8_t b[NL_PAGE];
    assert(nl_read(1, 3 * NL_PAGE, b, sizeof(b)) == NL_PAGE && nl_err[NL_ECC_BAD] > 0);
    check_file(1, 20000, true);
    ecc_of[p] = 0;
    // a block's first page unreadable at mount: the block's pages are not indexed, the file ends before it
    put(2, 0, 70 * NL_PAGE);
    assert(!nl_flush());
    uint32_t b2 = page_of(files[1].seq + 65) / NL_PPB;
    ecc_of[b2 * NL_PPB] = 2;
    assert(!nl_mount(&sim, NB, 0));
    assert(size_of(1) == 20000 && size_of(2) < 70 * NL_PAGE);
    check_file(1, 20000, false);
    printf("ecc ok\n");
}

/* A chip that stops answering (busy past its timeout) is not a bad block: none retired, the data before it kept */
static void test_hang(void)
{
    fresh();
    assert(!nl_mount(&sim, NB, 0));
    put(1, 0, 10000);
    assert(!nl_flush());
    hung = true;
    uint8_t b[NL_PAGE] = {0};
    for (int i = 0; i < 3; i++) {  // a program that times out, then the next block's erase
        assert(nl_append(2, b, sizeof(b)) < 0 || nl_flush());
    }
    assert(nl_err[NL_FAILS] == 0);
    hung = false;
    assert(!nl_mount(&sim, NB, 0) && nl_total() == (uint64_t) NB * NL_PPB * NL_PAGE);
    check_file(1, 10000, false);
    put(3, 0, 5000);
    assert(!nl_flush() && size_of(3) == 5000);
    printf("hang ok\n");
}

/* The phone deletes a segment just closed (flushed) before the next one has its first bytes */
static void test_delete_closed(void)
{
    fresh();
    assert(!nl_mount(&sim, NB, 0));
    put(1, 0, 5000);
    bool moved;
    assert(nl_delete(1, &moved) == -EBUSY);  // its open page still in RAM
    assert(!nl_flush() && !nl_delete(1, &moved) && moved && nl_files() == 0);
    put(2, 0, 7000);
    assert(!nl_flush() && size_of(2) == 7000);
    assert(!nl_mount(&sim, NB, nl_up()) && nl_files() == 1 && size_of(1) == INV);
    check_file(2, 7000, false);
    printf("delete closed ok\n");
}

int main(void)
{
    test_basic();
    test_wrap();
    test_bad();
    test_ecc();
    test_hang();
    test_delete_closed();
    test_cuts(false);
    test_cuts(true);
    printf("nand_check: all ok\n");
    return 0;
}
