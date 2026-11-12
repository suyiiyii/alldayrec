// Host check of the NAND ring log (src/nand_log.c) on a simulated W25N: pages programmed once each, in order within a
// block, only after an erase (any other program fails the check), bits only from 1 to 0. Covers write and read back,
// remounts, the list rebuilt by halving against a scan of every page, DELETE and the uploaded pointer, the ring going
// round with and without uploads, a power cut at every program and erase of a run (the page or block left half done),
// factory and runtime bad blocks (program and erase failures), ECC corrected and not correctable. A cut page may keep
// its header whole (only its data half done); a failed program may still read back right; reads at mount may fail or
// come back with a bit wrong: recovery never erases what may hold data.
//   cc -O1 -o /tmp/nand_check test/nand_check.c && /tmp/nand_check [test]

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
static bool pfail_good;  // the failing program reports P-FAIL, yet its page reads back right (ECC takes the few bits)
static bool tear_spare;  // a cut program leaves the spare area (the header) whole, the data half done
static bool mark_fails;  // a bad-block mark does not take
static uint32_t rfail_page = INV;  // reads of this page fail (-EIO), rfail_n more times
static int rfail_n;
static uint32_t rflip_page = INV;  // the next rflip_n reads of this page's byte rflip_at come back with rflip_mask
static uint32_t rflip_at = NL_PAGE + 7, rflip_mask = 0x01;  // flipped (SPI); bit 24 of the header's seq
static int rflip_n = 1;
static uint32_t marg_page = INV;   // reads of this page report ECC 0 and 1 by turns (a marginal bit), the data the same
static uint32_t noisy_page = INV;  // reads of this page: ECC 2, and its wrong bits differ read to read
static int marg_n;
static uint32_t sim_top, sim_erasing = INV;  // kept in NVS: the seq ceiling, the block an erase was begun on
static int save_fails;  // the next NVS writes that fail
static int erase_half;  // an erase cut short leaves every page: 1 late (bits all over gone to 1), 2 early (a few in the
                        // data, the header whole); 0: some erased, some whole, some late, some early
static int nprog[NP];   // programs of the page since its block's erase: NoP 4
static int erases[NB];
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
    if (page == rfail_page && rfail_n > 0) {
        rfail_n--;
        return -EIO;
    }
    memcpy(buf, mem[page] + col, n);
    if (page == rflip_page && col <= rflip_at && rflip_at < col + n) {
        ((uint8_t *) buf)[rflip_at - col] ^= rflip_mask;
        rflip_page = --rflip_n > 0 ? rflip_page : INV;
    }
    *ecc = ecc_of[page];
    if (*ecc == 2 && n > 8) {
        ((uint8_t *) buf)[n / 2] ^= 0x5A;  // not correctable: some bits wrong
    }
    if (page == marg_page && *ecc < 2) {
        *ecc = marg_n++ & 1;
    }
    if (page == noisy_page && n > 8) {
        *ecc = 2;
        ((uint8_t *) buf)[n / 2] ^= 1 + rnd() % 255;  // spare: 0x81C, between the header's pieces
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
    assert(++nprog[page] <= 4);
    if (page % NL_PPB) {
        assert(programmed[page - 1]);  // in order: the one before it in the block was
        for (uint32_t p = page + 1; p % NL_PPB; p++) {
            assert(!programmed[p]);
        }
    }
    programmed[page] = true;
    ecc_of[page] = torn ? 2 : 0;  // cut mid-program: on the chip, ECC cannot correct it either
    for (int i = 0; i < PS; i++) {
        uint8_t v = i < NL_PAGE ? data[i] : spare[i - NL_PAGE];
        bool part = (torn && !(tear_spare && i >= NL_PAGE)) || (page == pfail && !pfail_good);
        mem[page][i] &= part ? v | rnd() : v;  // cut or failing: some bits only
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
    erases[block]++;
    for (uint32_t p = block * NL_PPB; p < (block + 1) * NL_PPB; p++) {
        unsigned r = erase_half ? erase_half + 1 : rnd() % 4;
        if (!torn || r == 0) {
            memset(mem[p], 0xFF, PS);
            programmed[p] = false;
            nprog[p] = 0;
            ecc_of[p] = 0;
        } else if (r == 2 && programmed[p]) {  // cut short: half erased, bits all over gone to 1, ECC cannot correct it
            for (int i = 0; i < PS; i++) {     // (1: left whole)
                mem[p][i] |= rnd() & rnd();
            }
            ecc_of[p] = 2;
        } else if (r == 3 && programmed[p]) {  // cut early: a few 0 bits gone to 1, ECC cannot correct them, the
            for (int j = 0, n = 0; n < 4 && j < 4096; j++) {  // header's bytes whole (they read as the run they were)
                int i = rnd() % NL_PAGE, b = 1 << rnd() % 8;
                n += !(mem[p][i] & b);
                mem[p][i] |= b;
            }
            ecc_of[p] = 2;
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
    if (!dead && !mark_fails) {
        assert(++nprog[block * NL_PPB] <= 4);
        mem[block * NL_PPB][NL_PAGE] = 0;
    }
    return 0;
}

static int sim_save(uint32_t *to, uint32_t v)
{
    if (save_fails > 0 && save_fails--) {
        return -EIO;
    }
    *to = dead ? *to : v;  // the power gone: nothing reaches NVS either (the run goes on as if it did)
    return 0;
}

static int sim_save_top(uint32_t top)
{
    assert(dead || top > sim_top);
    return sim_save(&sim_top, top);
}

static int sim_save_erase(uint32_t block)
{
    return sim_save(&sim_erasing, block);
}

static const struct nl_ops sim = {sim_read, sim_program, sim_erase, sim_mark, sim_save_top, sim_save_erase};

static int mount(uint32_t up)
{
    return nl_mount(&sim, NB, up, sim_top, sim_erasing);
}

static void fresh(void)
{
    memset(mem, 0xFF, sizeof(mem));
    memset(programmed, 0, sizeof(programmed));
    memset(ecc_of, 0, sizeof(ecc_of));
    memset(committed, 0, sizeof(committed));
    memset(nl_err, 0, sizeof(nl_err));
    memset(nprog, 0, sizeof(nprog));
    memset(erases, 0, sizeof(erases));
    pfail = efail = rfail_page = rflip_page = INV;
    rflip_at = NL_PAGE + 7, rflip_mask = 0x01, rflip_n = 1;
    rfail_n = 0;
    sim_top = 0, sim_erasing = marg_page = noisy_page = INV;
    save_fails = 0;
    erase_half = 0;
    hung = pfail_good = tear_spare = mark_fails = false;
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
    assert(!mount(0) && nl_files() == 0 && nl_total() == (uint64_t) NB * NL_PPB * NL_PAGE);
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

    assert(!mount(0));  // remount: the same list, the halving's against a full scan
    assert(nl_files() == 3 && size_of(1) == 8000 && size_of(2) == 100 && size_of(3) == 4100);
    check_all(false);
    check_list_by_scan();

    // DELETE: in order, the uploaded pointer goes on and the files stay gone; out of order, back after a remount
    assert(!nl_delete(1, &moved) && moved);
    uint32_t u = nl_up();
    assert(!nl_delete(3, &moved) && !moved && nl_files() == 1);
    assert(!mount(u));
    assert(nl_files() == 2 && size_of(1) == INV && size_of(2) == 100 && size_of(3) == 4100);
    put(4, 0, 10);  // a new file after a remount
    assert(!nl_flush());
    assert(!mount(u) && size_of(4) == 10);
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
    assert(!mount(0));
    writer(1, 200, true, &u);  // ~6 MB through a 2 MB chip, the phone keeping up
    assert(nl_err[NL_LOST] == 0 && nl_files() == 1);
    check_all(false);
    assert(!mount(u) && nl_files() == 1);
    check_all(false);

    fresh();
    u = 0;
    assert(!mount(0));
    writer(1, 200, false, &u);  // no phone: the oldest go
    assert(nl_err[NL_LOST] > 0 && nl_files() > 10);
    check_all(false);
    // the remount erases the block after the one written, the oldest: the files that start in it go
    uint32_t t = next_good(wblock), lo = seq0[t], hi = block_end(t);
    int n = nl_files(), d = 0;
    for (int i = 0; i < n; i++) {
        d += lo != INV && files[i].seq >= lo && files[i].seq < hi;
    }
    assert(!mount(0));
    check_list_by_scan();
    check_all(false);
    assert(nl_files() == n - d);
    printf("wrap ok (%u lost)\n", nl_err[NL_LOST]);
}

/* A run of files, the power cut at op k: after a remount every page whose program finished reads back, the list
 * matches a scan, writing goes on and survives another remount. wrapped: on a chip the ring has gone round already
 * (uploaded), so the cuts fall in erases of old data too */
static void test_cuts(bool wrapped)
{
    static uint8_t mem0[NP][PS];
    static bool prog0[NP];
    static int ecc0[NP];
    uint32_t u = 0, top0, erasing0;
    fresh();
    assert(!mount(0));
    if (wrapped) {
        rnd_state = 3;
        writer(500, 90, true, &u);
        assert(nl_files() == 1);  // the open one stays; the rest are uploaded
    }
    memcpy(mem0, mem, sizeof(mem));
    memcpy(prog0, programmed, sizeof(programmed));
    memcpy(ecc0, ecc_of, sizeof(ecc_of));
    top0 = sim_top, erasing0 = sim_erasing;
    uint32_t u0 = u;
    long total = -1;
    for (long k = 0; k <= total + 1; k++) {  // k 0: no cut, counts the ops
        memset(committed, 0, sizeof(committed));
        memcpy(mem, mem0, sizeof(mem));
        memcpy(programmed, prog0, sizeof(programmed));
        memcpy(ecc_of, ecc0, sizeof(ecc_of));
        sim_top = top0, sim_erasing = erasing0;
        dead = false;
        assert(!mount(u0));
        ops_n = 0;
        cut_at = k ? k : -1;
        tear_spare = k & 1;  // every other cut: the header whole, the data half done
        rnd_state = 7;
        writer(1, 12, false, &u);
        if (!k) {
            total = ops_n;
            continue;
        }
        dead = false;
        cut_at = -1;
        tear_spare = false;
        assert(!mount(u0));
        assert(!unknown && !held);  // a page cut mid-program or a block's erase cut short (ECC cannot correct either)
                                    // is not kept: no block, no uploaded pointer held at every cut
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
        assert(!mount(u0));
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
    assert(!mount(0) && nl_total() == (uint64_t) (NB - 2) * NL_PPB * NL_PAGE);
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
    assert(!mount(u) && nl_total() == (uint64_t) (NB - 4) * NL_PPB * NL_PAGE);
    check_all(false);
    printf("bad blocks ok\n");
}

static void test_ecc(void)
{
    fresh();
    assert(!mount(0));
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
    // a block's first page not correctable at mount: the block's place found from its next page, the file whole
    put(2, 0, 70 * NL_PAGE);
    assert(!nl_flush());
    uint32_t b2 = page_of(files[1].seq + 65) / NL_PPB;
    ecc_of[b2 * NL_PPB] = 2;
    assert(!mount(0));
    assert(size_of(1) == 20000 && size_of(2) == 70 * NL_PAGE);
    check_file(1, 20000, false);
    check_file(2, 70 * NL_PAGE, true);
    printf("ecc ok\n");
}

/* A chip that stops answering (busy past its timeout) is not a bad block: none retired, the data before it kept */
static void test_hang(void)
{
    fresh();
    assert(!mount(0));
    put(1, 0, 10000);
    assert(!nl_flush());
    hung = true;
    uint8_t b[NL_PAGE] = {0};
    for (int i = 0; i < 3; i++) {  // a program that times out, then the next block's erase
        assert(nl_append(2, b, sizeof(b)) < 0 || nl_flush());
    }
    assert(nl_err[NL_FAILS] == 0);
    hung = false;
    assert(!mount(0) && nl_total() == (uint64_t) NB * NL_PPB * NL_PAGE);
    check_file(1, 10000, false);
    put(3, 0, 5000);
    assert(!nl_flush() && size_of(3) == 5000);
    printf("hang ok\n");
}

/* The phone deletes a segment just closed (flushed) before the next one has its first bytes */
static void test_delete_closed(void)
{
    fresh();
    assert(!mount(0));
    put(1, 0, 5000);
    bool moved;
    assert(nl_delete(1, &moved) == -EBUSY);  // its open page still in RAM
    assert(!nl_flush() && !nl_delete(1, &moved) && moved && nl_files() == 0);
    put(2, 0, 7000);
    assert(!nl_flush() && size_of(2) == 7000);
    assert(!mount(nl_up()) && nl_files() == 1 && size_of(1) == INV);
    check_file(2, 7000, false);
    printf("delete closed ok\n");
}

/* A program of a block's first page fails (P-FAIL) though the page reads back right: the seq goes again to the next
 * block. After a remount the list and the data are whole, before the ring has moved on (a) and after (a2); also when
 * the failed block's bad mark did not take (two blocks then start with the same seq) */
static void test_pfail_good(void)
{
    for (int m = 0; m < 4; m++) {
        fresh();
        mark_fails = m & 1;
        assert(!mount(0));
        pfail = 2 * NL_PPB;  // block 0 and 1 fill up, block 2's first page fails
        pfail_good = true;
        put(1, 0, (m < 2 ? 64 + 64 + 10 : 64 * 4 + 5) * NL_PAGE);
        assert(!nl_flush() && nl_err[NL_FAILS] == 1);
        uint32_t want = size_of(1);
        assert(!mount(0));
        assert(size_of(1) == want);
        check_file(1, want, false);
        check_list_by_scan();
        put(2, 0, 9000);  // and on
        assert(!nl_flush() && !mount(0) && size_of(1) == want && size_of(2) == 9000);
        check_all(false);
    }
    printf("p-fail, page good ok\n");
}

/* A bit read wrong all through a mount (past the two reads that agree: an old block's seq looks the newest), and reads
 * that fail at mount, once, twice or all through it: nothing erased, the file whole after the next mount */
static void test_mount_reads(void)
{
    fresh();
    assert(!mount(0));
    put(1, 0, 64 * 5 * NL_PAGE);
    assert(!nl_flush());
    uint32_t want = size_of(1);
    rflip_page = 2 * NL_PPB, rflip_n = 1000;  // block 2's first page, an old block, wrong all through the mount
    assert(!mount(0));
    rflip_page = INV;
    assert(size_of(1) == want);
    check_file(1, want, false);
    assert(!mount(0) && size_of(1) == want);
    // the newest block's seq read low (192 as 64): the one before it looks the newest, the newest the next to erase
    fresh();
    assert(!mount(0));
    put(1, 0, (64 * 3 + 30) * NL_PAGE);
    assert(!nl_flush() && wblock == 3 && seq0[3] == 192);
    want = size_of(1);
    rflip_page = 3 * NL_PPB, rflip_n = 1000;
    rflip_at = NL_PAGE + 4, rflip_mask = 0x80;
    assert(!mount(0));
    rflip_page = INV;
    assert(size_of(1) == want && erases[3] == 1);
    check_file(1, want, false);

    static const int fails[] = {1, 2, 100};  // once, twice (the retries take both), then for the whole mount
    for (int i = 0; i < 3; i++) {
        int n = fails[i];
        fresh();
        assert(!mount(0));
        put(1, 0, (64 * 3 + 30) * NL_PAGE);
        assert(!nl_flush());
        want = size_of(1);
        uint32_t hb = wblock;
        rfail_page = hb * NL_PPB;  // the newest block's first page
        rfail_n = n;
        int e0 = erases[hb];
        assert(!mount(0));
        assert(erases[hb] == e0 && (n == 100) == (unknown > 0));
        put(2, 0, 5000);
        assert(!nl_flush());
        rfail_n = 0;
        assert(!mount(0));
        assert(size_of(1) == want && size_of(2) == 5000);
        check_all(false);
        check_list_by_scan();
    }
    printf("mount read errors ok\n");
}

/* A W25N02KV-style factory bad block (the main area's first byte only) right after the block written at a boot: never
 * erased, never used */
static void test_odd_after_head(void)
{
    fresh();
    mem[5 * NL_PPB][0] = 0;
    assert(!mount(0));
    put(1, 0, (64 * 4 + 3) * NL_PAGE);  // blocks 0-3 full, 3 pages into block 4
    assert(!nl_flush() && wblock == 4);
    assert(!mount(0));
    put(2, 0, 70 * NL_PAGE);
    assert(!nl_flush());
    assert(erases[5] == 0 && bit(bad, 5));
    check_all(false);
    printf("factory bad after the newest ok\n");
}

/* DELETE of a file just closed on a page boundary, then a late batch of it: refused, not a new file of that name */
static void test_late_append(void)
{
    fresh();
    assert(!mount(0));
    put(1, 0, 3 * NL_PAGE);
    bool moved;
    assert(!nl_delete(1, &moved));
    assert(nl_append(1, "late", 4) == -EROFS && size_of(1) == INV);
    put(2, 0, 100);
    assert(!nl_flush() && size_of(2) == 100);
    printf("late append ok\n");
}

/* Header reads at mount that come back wrong: once (an SPI glitch: read twice, taken when two agree), or all through
 * the mount (blocks out of seq order, even level with the one before, read whole). The list and the data whole after
 * that mount (nothing for the uploaded pointer to pass), and after the next. The newest block's bad-block byte (no ECC
 * on W25N01GV) all through: it looks bad, its seqs not written again, the uploaded pointer held */
static void test_mount_flips(void)
{
    static const struct {
        uint32_t blk, at, mask;
        int n;
    } f[] = {
        {3, NL_PAGE + 4, 0x10, 1},     // 192 as 208: in order, file 1 ends in it
        {3, NL_PAGE + 4, 0x40, 1},     // 192 as 128, the one before it
        {3, NL_PAGE + 4, 0x40, 1000},  //   all through: level with the one before
        {4, NL_PAGE + 5, 0x01, 1000},  // 256 as 0: below it
        {5, NL_PAGE, 0x01, 1},         // the newest block (320..331) looks bad
        {5, NL_PAGE, 0x01, 1000},
    };
    for (int i = 0; i < 6; i++) {
        fresh();
        assert(!mount(0));
        put(1, 0, (64 * 3 + 40) * NL_PAGE);
        assert(!nl_flush());
        put(2, 0, 100 * NL_PAGE);
        assert(!nl_flush() && wblock == 5);
        rflip_page = f[i].blk * NL_PPB, rflip_at = f[i].at, rflip_mask = f[i].mask, rflip_n = f[i].n;
        assert(!mount(0));
        rflip_page = INV;
        bool hold = i == 2 || i == 3 || i == 5;  // a header wrong all through the mount: the uploaded pointer held
        assert(unknown == (i == 5) && held == hold && size_of(1) == (64 * 3 + 40) * NL_PAGE);
        assert(i == 5 || size_of(2) == 100 * NL_PAGE);
        check_all(false);
        bool moved;
        assert(!nl_delete(1, &moved) && moved != hold);
        uint32_t u = nl_up();
        put(3, 0, 40 * NL_PAGE);
        assert(!nl_flush());
        assert(!mount(u));
        assert(size_of(1) == (hold ? (64 * 3 + 40) * NL_PAGE : INV));
        assert(size_of(2) == 100 * NL_PAGE && size_of(3) == 40 * NL_PAGE);
        check_all(false);
        check_list_by_scan();
    }
    printf("mount bit flips ok\n");
}

/* A page whole into the chip as written: file f's bytes from off, len of them, seq; bad: its data not its CRC's */
static void place(uint32_t page, uint32_t seq, uint32_t f, uint32_t off, uint32_t len, bool bad)
{
    struct hdr h = {seq, f, off, len, 0};
    for (uint32_t i = 0; i < NL_PAGE; i++) {
        mem[page][i] = i < len ? pat(f, off + i) : 0xFF;
    }
    h.crc = page_crc(&h, mem[page]);
    mem[page][0] ^= bad;
    pack(mem[page] + NL_PAGE, &h);
    programmed[page] = true;
}

/* The block to be erased (after the newest) has two pages written that do not check: kept, not erased; the one after
 * it is then the one to erase, checked first: it holds the newest data (its first page's seq read wrong) */
static void test_erase_target(void)
{
    fresh();
    assert(!mount(0));
    put(1, 0, (64 * 3 + 30) * NL_PAGE);  // head block 3
    assert(!nl_flush() && wblock == 3 && !erases[4] && !erases[5]);
    place(4 * NL_PPB, 5, 1, 0, NL_PAGE, true);  // old, rotted
    place(4 * NL_PPB + 1, 6, 1, NL_PAGE, NL_PAGE, true);
    place(5 * NL_PPB, 7, 8, 0, 100, true);  // the newest: its first page says 7, its second 301
    place(5 * NL_PPB + 1, 301, 9, 0, 100, false);
    assert(!mount(0));
    assert(!erases[4] && !erases[5] && bit(bad, 4) && seq0[5] == 300 && wblock == 6);
    assert(size_of(1) == (64 * 3 + 30) * NL_PAGE && size_of(9) == 100);
    printf("erase target checked ok\n");
}

/* Blocks kept (unreadable all through a mount) at two boots in a row: no seq written twice (the ceiling in NVS); the
 * newest block kept while its file is not uploaded: the file listed short, the phone takes it and deletes it, the
 * uploaded pointer stays (kept in NVS as it was): the next boot lists it whole */
static void test_kept_boots(void)
{
    fresh();
    assert(!mount(0));
    put(1, 0, (64 * 3 + 10) * NL_PAGE);  // head block 3: 10 pages
    assert(!nl_flush() && wblock == 3);
    rfail_page = 3 * NL_PPB, rfail_n = 1000;  // boot 2: block 3 unreadable
    assert(!mount(0) && unknown == 1);
    rfail_n = 0;
    put(2, 0, 40 * NL_PAGE);
    assert(!nl_flush());
    uint32_t h2 = wblock;
    rfail_page = h2 * NL_PPB, rfail_n = 1000;  // boot 3: block 3 reads again, the one boot 2 wrote does not
    assert(!mount(0) && unknown == 1);
    rfail_n = 0;
    put(3, 0, 20 * NL_PAGE);
    assert(!nl_flush());
    assert(!mount(0) && !unknown);  // boot 4: all read
    assert(size_of(1) == (64 * 3 + 10) * NL_PAGE && size_of(2) == 40 * NL_PAGE && size_of(3) == 20 * NL_PAGE);
    check_all(false);
    check_list_by_scan();

    fresh();
    assert(!mount(0));
    put(1, 0, (64 * 2 + 30) * NL_PAGE);  // file 1's tail in the head block
    assert(!nl_flush());
    rfail_page = wblock * NL_PPB, rfail_n = 1000;
    assert(!mount(0));
    rfail_n = 0;
    assert(size_of(1) == 64 * 2 * NL_PAGE);
    put(2, 0, 3 * NL_PAGE);
    assert(!nl_flush());
    bool moved;
    assert(!nl_delete(1, &moved) && !moved);
    assert(!mount(nl_up()) && size_of(1) == (64 * 2 + 30) * NL_PAGE && size_of(2) == 3 * NL_PAGE);
    check_all(false);

    // a block kept while the ring goes round it, readable again: its file (old) lists among new ones, out of seq order;
    // a DELETE of the first listed does not take the uploaded pointer past it
    fresh();
    assert(!mount(0));
    put(1, 0, 64 * NL_PAGE);  // block 0
    put(2, 0, 30 * NL_PAGE);  // block 1
    assert(!nl_flush());
    rfail_page = NL_PPB, rfail_n = 1000;
    assert(!mount(0) && unknown == 1);
    for (uint32_t f = 10; f < 60; f++) {
        put(f, 0, 20 * NL_PAGE);
    }
    assert(!nl_flush() && wblock == 2);
    rfail_n = 0;
    assert(!mount(0) && !unknown && size_of(2) == 30 * NL_PAGE);
    uint32_t f0, s0;
    nl_file(0, &f0, &s0);
    assert(f0 != 2 && !nl_delete(f0, &moved));
    assert(!mount(nl_up()) && size_of(2) == 30 * NL_PAGE);
    check_all(false);
    printf("kept at boots ok\n");
}

/* The first page of file f (its offset 0) on the chip */
static uint32_t first_page_of(uint32_t f)
{
    for (uint32_t p = 0; p < NP; p++) {
        struct hdr h;
        if (programmed[p] && unpack(mem[p] + NL_PAGE, 0, &h) == H_OK && h.file == f && h.off == 0) {
            return p;
        }
    }
    return INV;
}

/* Pages that read unlike twice in a row at mount: ECC 0 and 1 by turns (a marginal bit, the data the same) on file 2's
 * first page or file 1's last, ECC 2 with bits wrong differently each read, reads failing all through the mount. The
 * list whole, or the uploaded pointer held: DELETE of file 1 never takes it past file 2 */
static void test_unlike_reads(void)
{
    for (int k = 0; k < 7; k++) {
        fresh();
        assert(!mount(0));
        put(1, 0, 100 * NL_PAGE + 300);
        assert(!nl_flush());
        put(2, 0, 50 * NL_PAGE);
        assert(!nl_flush());
        put(3, 0, 20 * NL_PAGE);
        assert(!nl_flush());
        uint32_t p2 = first_page_of(2);
        if (k == 0) {
            marg_page = p2;
        } else if (k == 1) {
            marg_page = p2 - 1;
        } else if (k == 2) {
            noisy_page = p2;
        } else if (k == 3) {
            rfail_page = p2, rfail_n = 1000;
        } else {  // ECC cannot correct it, a wrong bit in its header's off, seq or file, at every read
            ecc_of[p2] = 2;
            mem[p2][NL_PAGE + (k == 4 ? 36 : k == 5 ? 5 : 20)] ^= 0x10;
        }
        bool hold = k >= 2;  // a header not read for sure: its file may not be listed
        assert(!mount(0));
        assert(size_of(1) == 100 * NL_PAGE + 300 && !unknown && held == hold);
        assert(k >= 3 || size_of(2) == 50 * NL_PAGE);
        marg_page = noisy_page = INV, rfail_n = 0;
        if (k >= 4) {  // read right again later
            ecc_of[p2] = 0;
            mem[p2][NL_PAGE + (k == 4 ? 36 : k == 5 ? 5 : 20)] ^= 0x10;
        }
        bool moved;
        assert(!nl_delete(1, &moved) && moved == !hold);
        assert(!mount(nl_up()) && size_of(1) == (hold ? 100 * NL_PAGE + 300 : INV));
        assert(size_of(2) == 50 * NL_PAGE && size_of(3) == 20 * NL_PAGE);
        check_all(false);
    }
    printf("unlike reads ok\n");
}

/* The ring gone round, every file but the open one uploaded, the newest block full: the block after it holds old data
 * and is the next erased. *u: the uploaded pointer, *f: the open file */
static uint32_t lapped(uint32_t *u, uint32_t *f)
{
    fresh();
    assert(!mount(0));
    for (*f = 1; *f < 40; (*f)++) {
        put(*f, 0, 40 * NL_PAGE);
        assert(!nl_flush());
        bool moved;
        assert(!nl_delete(*f, &moved));
        *u = nl_up();
    }
    put(*f, 0, (NL_PPB - wpage) * NL_PAGE);
    assert(!nl_flush() && sim_erasing == wblock);  // NVS names the block erased last: the one written
    uint32_t t = next_good(wblock);
    assert(seq0[t] != INV);
    return t;
}

/* An erase cut short on the block after the newest, late (every page's bits all over gone to 1) or early (a few, the
 * headers whole: they read as the run they were), as the ring goes on and the one a mount does: erased again at the
 * next mount (NVS has it as begun, the run older than the newest), not kept: the uploaded pointer goes on. The NVS
 * write before an erase failing: tried once more, then erased all the same (counted): recording goes on. Kept, not
 * erased: the same block NVS does not name, or whose reads fail; the newest block, named, its first three pages not
 * checking (they are one file's run, newer than every block); a block NVS names that is not the one to erase. A run
 * past top (bits the erase turned to 1, seqs never written): erased again */
static void test_erase_cut(void)
{
    for (int k = 0; k < 6; k++) {
        uint32_t u = 0, f;
        uint32_t t = lapped(&u, &f);
        int e0 = erases[t];
        uint32_t fails = nl_err[NL_NVS];
        if (k == 2) {  // NVS takes no writes: each erase goes on (two here), counted
            save_fails = 1 << 30;
            put(f, size_of(f), 70 * NL_PAGE);
            assert(!nl_flush() && erases[t] == e0 + 1 && nl_err[NL_NVS] == fails + 2 && !nl_err[NL_FAILS]);
            save_fails = 0;
        } else if (k == 3) {  // one write fails at the mount's erase: tried again, the erase goes on, not counted
            save_fails = 1;
            assert(!mount(u) && erases[t] == e0 + 1 && nl_err[NL_NVS] == fails && wblock == t);
        } else {
            erase_half = k < 4 ? 1 : 2, ops_n = 0, cut_at = 1;
            if (k & 1) {
                assert(!mount(u));
            } else {
                nl_append(f, "x", 1), nl_flush();
            }
            assert(dead && erases[t] == e0 + 1 && ecc_of[t * NL_PPB] == 2 && sim_erasing == t);
            dead = erase_half = 0, cut_at = -1;
            assert(!mount(u) && !unknown && !held && erases[t] == e0 + 2 && wblock == t && sim_erasing == t);
        }
        put(f + 1, 0, 10 * NL_PAGE);
        assert(!nl_flush());
        bool moved;
        assert(!nl_delete(f, &moved) && moved);
        assert(!mount(nl_up()) && size_of(f) == INV && size_of(f + 1) == 10 * NL_PAGE);
        check_all(false);
        check_list_by_scan();
    }
    for (int k = 0; k < 3; k++) {
        uint32_t u = 0, f;
        uint32_t t = lapped(&u, &f);
        int e0 = erases[t];
        for (uint32_t p = t * NL_PPB; p < (t + 1) * NL_PPB; p++) {
            ecc_of[p] = 2;  // pages ECC cannot correct, their headers no run (as an erase cut short leaves them)
            mem[p][NL_PAGE + 7] |= k == 2 ? 0x10 : 0;  // k 2: named, a run from bit 28 of every seq gone to 1
        }
        mem[t * NL_PPB + 1][NL_PAGE + 4] ^= k < 2 ? 0x10 : 0;
        if (k == 1) {  // named, its second page's reads failing all through the mount
            rfail_page = t * NL_PPB + 1, rfail_n = 1000;
        }
        sim_erasing = k ? t : sim_erasing;
        assert(!mount(u) && bit(bad, t) == (k < 2) && unknown == (k < 2) && erases[t] == e0 + (k == 2));
        rfail_n = 0;
    }

    // the newest block (NVS names it), its pages not checking: the first three only, one header hit on the second (no
    // run there: k 0 its file, k 1 its seq): read on to one that checks, data, listed whole; all it has, a run: kept
    for (int k = 0; k < 3; k++) {
        fresh();
        assert(!mount(0));
        put(1, 0, (64 * 2 + 40) * NL_PAGE);  // blocks 0, 1 full, block 2: 40 pages
        assert(!nl_flush() && wblock == 2 && sim_erasing == 2);
        for (uint32_t p = 2 * NL_PPB; p < 2 * NL_PPB + (k < 2 ? 3 : 40); p++) {
            ecc_of[p] = 2;
        }
        mem[2 * NL_PPB + 1][NL_PAGE + (k ? 7 : 23)] |= k < 2 ? 0x01 : 0;  // bit 24
        assert(!mount(0) && erases[2] == 1 && bit(bad, 2) == (k == 2) && unknown == (k == 2));
        assert(k == 2 || size_of(1) == (64 * 2 + 40) * NL_PAGE);
    }

    // the ring gone round: the newest kept so, the next to erase is then the one after it, checked first (its first
    // page's data not checking, its second page's reads failing: kept too)
    uint32_t u = 0, f;
    uint32_t t = lapped(&u, &f), h = wblock;
    int e0 = erases[t];
    for (uint32_t p = h * NL_PPB; p < (h + 1) * NL_PPB; p++) {
        ecc_of[p] = 2;
    }
    mem[t * NL_PPB][0] ^= 1;
    rfail_page = t * NL_PPB + 1, rfail_n = 1000;
    assert(!mount(u) && bit(bad, h) && bit(bad, t) && unknown == 2 && erases[t] == e0);
    rfail_n = 0;

    fresh();
    assert(!mount(0));
    put(1, 0, (64 * 3 + 30) * NL_PAGE);
    assert(!nl_flush());
    for (uint32_t p = NL_PPB; p < 2 * NL_PPB; p++) {
        mem[p][0] ^= 1;  // block 1: no page that checks (the first ECC 2, the rest their data wrong)
    }
    ecc_of[NL_PPB] = 2;
    sim_erasing = 1;
    assert(!mount(0) && bit(bad, 1) && unknown == 1 && erases[1] == 1);
    printf("erase cut short ok\n");
}

/* The seq ceiling in NVS: a write of it that fails is tried again at the next page; NVS cleared (the chip's data
 * still there): a kept block still skipped; a page at the ceiling itself (it went to NVS first) and one just below it
 * never written again; a factory bad block whose bytes read as a header with a seq past the ceiling: not kept */
static void test_ceiling(void)
{
    fresh();
    assert(!mount(0));
    save_fails = 1;
    put(1, 0, 2 * NL_PAGE);
    assert(!nl_flush() && sim_top == 1 + 64 * NL_PPB);

    fresh();
    assert(!mount(0));
    put(1, 0, (64 * 3 + 10) * NL_PAGE);
    assert(!nl_flush());
    sim_top = 0;  // NVS cleared (a chip erase over SWD)
    rfail_page = 3 * NL_PPB, rfail_n = 1000;
    assert(!mount(0) && unknown == 1);
    rfail_n = 0;
    put(2, 0, 40 * NL_PAGE);
    assert(!nl_flush() && !mount(0));
    assert(size_of(1) == (64 * 3 + 10) * NL_PAGE && size_of(2) == 40 * NL_PAGE);
    check_all(false);

    // at the ceiling: boot 2 jumps to it (block 0 kept) and writes one page there; boot 3 keeps that block
    fresh();
    assert(!mount(0));
    put(1, 0, 10 * NL_PAGE);
    assert(!nl_flush());
    sim_top = 200;
    rfail_page = 0, rfail_n = 1000;
    assert(!mount(0) && unknown == 1);
    rfail_n = 0;
    put(2, 0, NL_PAGE);
    assert(!nl_flush());
    uint32_t b2 = wblock;
    assert(seq0[b2] == 200);
    rfail_page = b2 * NL_PPB, rfail_n = 1000;
    assert(!mount(0) && unknown == 1);
    rfail_n = 0;
    put(3, 0, 10 * NL_PAGE);
    assert(!nl_flush() && !mount(0));
    assert(size_of(1) == 10 * NL_PAGE && size_of(2) == NL_PAGE && size_of(3) == 10 * NL_PAGE);
    check_all(false);

    // just below it: block 1 holds 100..109, the ceiling 110
    fresh();
    assert(!mount(0));
    put(1, 0, 10 * NL_PAGE);
    assert(!nl_flush());
    for (uint32_t i = 0; i < 10; i++) {
        place(NL_PPB + i, 100 + i, 2, i * NL_PAGE, NL_PAGE, false);
    }
    sim_top = 110;
    rfail_page = NL_PPB, rfail_n = 1000;
    assert(!mount(0) && unknown == 1);
    rfail_n = 0;
    put(3, 0, 10 * NL_PAGE);
    assert(!nl_flush() && !mount(0));
    assert(size_of(1) == 10 * NL_PAGE && size_of(2) == 10 * NL_PAGE && size_of(3) == 10 * NL_PAGE);
    check_all(false);

    fresh();
    struct hdr g = {0x7FFFFFF0, 77, 0, 100, 0};
    pack(mem[6 * NL_PPB] + NL_PAGE, &g);
    mem[6 * NL_PPB][0] = mem[6 * NL_PPB][NL_PAGE] = 0;  // factory bad, its spare reading as a header
    assert(!mount(0));
    put(1, 0, 10 * NL_PAGE);
    put(2, 0, 10 * NL_PAGE);
    assert(!nl_flush() && !mount(0) && !unknown && bit(bad, 6));
    bool moved;
    assert(!nl_delete(1, &moved) && moved);
    printf("seq ceiling ok\n");
}

int main(int argc, char **argv)
{
    static const struct {
        const char *name;
        void (*fn)(void);
    } tests[] = {{"basic", test_basic}, {"wrap", test_wrap}, {"bad", test_bad}, {"ecc", test_ecc},
                 {"hang", test_hang}, {"delete_closed", test_delete_closed}, {"pfail_good", test_pfail_good},
                 {"mount_reads", test_mount_reads}, {"odd_after_head", test_odd_after_head},
                 {"late_append", test_late_append}, {"mount_flips", test_mount_flips},
                 {"erase_target", test_erase_target}, {"kept_boots", test_kept_boots},
                 {"unlike_reads", test_unlike_reads}, {"erase_cut", test_erase_cut}, {"ceiling", test_ceiling}};
    for (size_t i = 0; i < sizeof(tests) / sizeof(tests[0]); i++) {
        if (argc < 2 || !strcmp(argv[1], tests[i].name)) {
            tests[i].fn();
        }
    }
    if (argc < 2 || !strcmp(argv[1], "cuts")) {
        test_cuts(false);
        test_cuts(true);
    }
    printf("nand_check: %s ok\n", argc < 2 ? "all" : argv[1]);
    return 0;
}
