#include "nand_log.h"

#include <errno.h>
#include <string.h>

/* docs/nand-store.md. Pages go around the chip block by block in physical order, bad blocks skipped, so the blocks
 * with data, read from the one after the block being written, have rising seq0 (their first page's seq); the pages of
 * a block are seq0, seq0 + 1, ... up to the next such block's seq0 (a block left early: a boot, a failed program).
 * A file's pages are one run of seqs. */
#define INV 0xFFFFFFFFu
#define MIN(a, b) ((a) < (b) ? (a) : (b))

uint32_t nl_err[NL_ERR_N];

static const struct nl_ops *ops;
static uint32_t nblocks;
static uint32_t seq0[NL_MAX_BLOCKS];                             // INV: no data (erased, bad, unreadable)
static uint8_t bad[NL_MAX_BLOCKS / 8], retire[NL_MAX_BLOCKS / 8];  // retire: failed once, marked bad when next erased
static uint32_t wblock, wpage;  // the block being written and its next page (NL_PPB: the next block first)
static uint32_t next_seq, up;   // up: the files before it are all deleted (uploaded)
static uint32_t top;            // every seq written is below it (kept in NVS, ops->save_top)

struct ent {
    uint32_t file, seq, last, size;  // first and last page's seq (last INV: none written yet)
};
static struct ent files[NL_MAX_FILES];
static int nfiles;
static uint32_t wfile;  // the file appended to: the last entry
static uint32_t gone;   // the highest file deleted since the mount (INV: none): never appended to again
static uint8_t pg[NL_PAGE];  // its open page: pg_len bytes from pg_off on
static uint32_t pg_len, pg_off;

static uint8_t rd[NL_PAGE + NL_SPARE];  // the last page read whole (rd_seq)
static uint32_t rd_seq;
static uint32_t cur_file, cur_off, cur_seq;  // where the last nl_read left off: the next one goes on from there

struct hdr {
    uint32_t seq, file, off;
    uint16_t len, crc;
};

static bool bit(const uint8_t *m, uint32_t b)
{
    return m[b >> 3] >> (b & 7) & 1;
}

static void set_bit(uint8_t *m, uint32_t b)
{
    m[b >> 3] |= 1 << (b & 7);
}

static void put32(uint8_t *p, uint32_t v)
{
    p[0] = v, p[1] = v >> 8, p[2] = v >> 16, p[3] = v >> 24;
}

static uint32_t get32(const uint8_t *p)
{
    return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t) p[3] << 24;
}

static uint16_t crc16(uint16_t c, const uint8_t *p, uint32_t n)  // CRC-16/CCITT
{
    while (n--) {
        c ^= (uint16_t) *p++ << 8;
        for (int i = 0; i < 8; i++) {
            c = c & 0x8000 ? c << 1 ^ 0x1021 : c << 1;
        }
    }
    return c;
}

static uint16_t page_crc(const struct hdr *h, const uint8_t *d)
{
    uint8_t b[14];
    put32(b, h->seq);
    put32(b + 4, h->file);
    put32(b + 8, h->off);
    b[12] = h->len, b[13] = h->len >> 8;
    return crc16(crc16(0xFFFF, b, 14), d, h->len);
}

/* The header in 4 B pieces where both chips' ECC covers the spare area; 0x800 (the bad-block mark) stays FFh */
static void pack(uint8_t *sp, const struct hdr *h)
{
    memset(sp, 0xFF, NL_SPARE);
    put32(sp + 4, h->seq);
    put32(sp + 20, h->file);
    put32(sp + 36, h->off);
    put32(sp + 52, h->len | (uint32_t) h->crc << 16);
}

enum { H_OK, H_ERASED, H_BAD, H_FAIL };  // H_FAIL: the read itself failed

/* A read tried again on an error or an ECC failure: an SPI glitch passes, the chip's own state does not */
static int read_op(uint32_t page, uint16_t col, void *buf, uint16_t n, int *ecc)
{
    int err = 0;
    for (int i = 0; i < 3; i++) {
        *ecc = 0;
        err = ops->read(page, col, buf, n, ecc);
        if (!err && *ecc != 2) {
            return 0;
        }
    }
    return err;  // 0 with *ecc 2: not correctable
}

/* A read taken when two in a row agree: one bit wrong on the SPI lines (no ECC there) would go into the list and the
 * uploaded pointer for good. n: NL_SPARE at most */
static int read2(uint32_t page, uint16_t col, uint8_t *buf, uint16_t n, int *ecc)
{
    uint8_t b[NL_SPARE];
    for (int i = 0; i < 3; i++) {
        int e2, err = read_op(page, col, buf, n, ecc) ?: read_op(page, col, b, n, &e2);
        if (err) {
            return err;
        }
        if (*ecc == e2 && !memcmp(buf, b, n)) {
            return 0;
        }
    }
    return -EIO;
}

static int unpack(const uint8_t *sp, int ecc, struct hdr *h)
{
    bool ff = true;
    for (int i = 0; i < NL_SPARE; i++) {
        ff = ff && sp[i] == 0xFF;
    }
    if (ecc == 1) {
        nl_err[NL_ECC_FIXED]++;
    } else if (ecc == 2) {
        nl_err[NL_ECC_BAD]++;
    }
    if (ff) {
        return H_ERASED;
    }
    h->seq = get32(sp + 4);
    h->file = get32(sp + 20);
    h->off = get32(sp + 36);
    h->len = get32(sp + 52);
    h->crc = get32(sp + 52) >> 16;
    return ecc == 2 || h->seq == INV || !h->len || h->len > NL_PAGE ? H_BAD : H_OK;
}

/* A page's header alone, read twice (its spare bytes into sp if given; H_FAIL: none) */
static int read_hdr(uint32_t page, struct hdr *h, uint8_t *sp)
{
    uint8_t s[NL_SPARE];
    int ecc;
    if (read2(page, NL_PAGE, s, NL_SPARE, &ecc)) {
        return H_FAIL;
    }
    if (sp) {
        memcpy(sp, s, NL_SPARE);
    }
    return unpack(s, ecc, h);
}

/* Block a's data newer than block b's: a higher seq0, or the same one later round the ring (a first page whose program
 * failed, its seq written again in the next block, and the failed block's bad mark did not take) */
static bool newer(uint32_t a, uint32_t b)
{
    return b == INV || seq0[a] > seq0[b] ||
           (seq0[a] == seq0[b] && (a + nblocks - b) % nblocks < (b + nblocks - a) % nblocks);
}

/* The page that has seq: INV if none does */
static uint32_t page_of(uint32_t seq)
{
    uint32_t best = INV;
    if (seq >= next_seq) {
        return INV;
    }
    for (uint32_t b = 0; b < nblocks; b++) {
        if (seq0[b] != INV && seq0[b] <= seq && newer(b, best)) {
            best = b;
        }
    }
    return best == INV || seq - seq0[best] >= NL_PPB ? INV : best * NL_PPB + seq - seq0[best];
}

/* The header of the page with seq. One ECC could not correct is taken as read when its seq is the one asked for */
static int hdr_at(uint32_t seq, struct hdr *h)
{
    uint8_t sp[NL_SPARE];
    uint32_t p = page_of(seq);
    int r = p == INV ? H_BAD : read_hdr(p, h, sp);
    if (r == H_BAD && p != INV) {
        r = unpack(sp, 0, h);
    }
    return r == H_FAIL || (r == H_OK && h->seq != seq) ? H_BAD : r;
}

/* A page whole into rd: 0, -EIO the read failed (*ecc 0), -ENODATA no header. *crc_ok false: its data does not match
 * its CRC (cut mid-program, or ECC could not correct it, *ecc 2: the header taken as read, the caller checks its seq) */
static int read_whole(uint32_t page, struct hdr *h, bool *crc_ok, int *ecc)
{
    if (read_op(page, 0, rd, NL_PAGE + NL_SPARE, ecc)) {
        *ecc = 0;
        return -EIO;
    }
    if (unpack(rd + NL_PAGE, *ecc, h) != H_OK && (*ecc != 2 || unpack(rd + NL_PAGE, 0, h) != H_OK)) {
        return -ENODATA;
    }
    *crc_ok = page_crc(h, rd) == h->crc;
    return 0;
}

enum { V_OK, V_EMPTY, V_UNKNOWN };

/* Block b's seq0 from its pages read whole (mount): the first whose header and CRC hold gives it, its seq less its
 * page. V_EMPTY: no page that holds and none can (the first blank, or the only one written cut mid-program); V_UNKNOWN:
 * a read that failed, or written pages that do not check: may hold data */
static int verify(uint32_t b, uint32_t *s0)
{
    for (uint32_t p = 0; p < 3; p++) {
        struct hdr h;
        bool crc_ok;
        int ecc, err = read_whole(b * NL_PPB + p, &h, &crc_ok, &ecc);
        if (err == -EIO) {
            return V_UNKNOWN;
        }
        if (!err && crc_ok && h.seq >= p) {
            *s0 = h.seq - p;
            return V_OK;
        }
        bool blank = true;
        for (int i = 0; i < NL_SPARE; i++) {
            blank = blank && rd[NL_PAGE + i] == 0xFF;
        }
        if (blank) {
            return p <= 1 ? V_EMPTY : V_UNKNOWN;  // two written pages that do not check: not one cut short
        }
    }
    return V_UNKNOWN;
}

static uint32_t next_good(uint32_t b)
{
    for (uint32_t i = 0; i < nblocks; i++) {
        b = b == INV ? 0 : (b + 1) % nblocks;
        if (!bit(bad, b)) {
            return b;
        }
    }
    return INV;
}

/* The seq after block b's last page: the next block round the ring that starts at or past it (older ones in between:
 * a block left alone at a mount, its data kept), or next_seq for the newest; NL_PPB pages at most */
static uint32_t block_end(uint32_t b)
{
    uint32_t end = seq0[b] + NL_PPB < next_seq ? seq0[b] + NL_PPB : next_seq;
    for (uint32_t c = (b + 1) % nblocks; c != b; c = (c + 1) % nblocks) {
        if (seq0[c] != INV && newer(c, b)) {
            return MIN(seq0[c], end);
        }
    }
    return end;
}

/* Block b about to be erased: the files that start in it go (their rest in later blocks is not listed: a file starts
 * at offset 0); a file not deleted yet is a loss (NL_LOST) */
static void drop(uint32_t b)
{
    if (seq0[b] == INV) {
        return;
    }
    uint32_t lo = seq0[b], hi = block_end(b);
    int n = 0;
    for (int i = 0; i < nfiles; i++) {
        if (files[i].seq >= lo && files[i].seq < hi && files[i].file != wfile) {
            nl_err[NL_LOST]++;  // shortcut: a file the ring laps while it is written (bigger than the chip) is kept
        } else {
            files[n++] = files[i];
        }
    }
    nfiles = n;
    seq0[b] = INV;
    rd_seq = cur_file = INV;
}

static void retire_block(uint32_t b)
{
    ops->mark_bad(b);
    set_bit(bad, b);
    drop(b);
}

/* The next good block erased for writing, the oldest: what it held dropped first */
static int advance(void)
{
    uint32_t b = wblock;
    for (uint32_t i = 0; i < nblocks; i++) {
        b = next_good(b);
        if (b == INV) {
            return -EIO;
        }
        if (bit(retire, b)) {
            retire_block(b);
            continue;
        }
        drop(b);
        int err = ops->erase(b);
        if (err && err != -EIO) {
            return err;  // the chip does not answer: no block to blame
        }
        if (err) {
            nl_err[NL_FAILS]++;
            retire_block(b);
            continue;
        }
        wblock = b;
        wpage = 0;
        return 0;
    }
    return -EIO;
}

/* The open page onto the chip. A program that fails: its block retires at the next erase, the page goes to the next
 * block with the same seq (the failed one is past its block's end) */
static int put_page(void)
{
    struct hdr h = {next_seq, wfile, pg_off, pg_len, 0};
    uint8_t sp[NL_SPARE];
    memset(pg + pg_len, 0xFF, NL_PAGE - pg_len);
    h.crc = page_crc(&h, pg);
    pack(sp, &h);
    for (int tries = 0; tries < 8; tries++) {
        int err = wpage == NL_PPB ? advance() : 0;
        if (err) {
            return err;
        }
        if (next_seq >= top) {  // into NVS before a seq past it is on the chip: 8 MB on, about every 35 min recording
            top = next_seq + 64 * NL_PPB;
            ops->save_top(top);
        }
        if (!wpage) {
            seq0[wblock] = next_seq;
        }
        err = ops->program(wblock * NL_PPB + wpage, pg, sp);
        if (err && err != -EIO) {
            if (!wpage) {
                seq0[wblock] = INV;
            }
            wpage = NL_PPB;  // the chip does not answer: the page may be half done, so not this block again
            return err;
        }
        if (!err) {
            struct ent *e = &files[nfiles - 1];
            e->last = next_seq++;
            wpage++;
            pg_off += pg_len;
            pg_len = 0;
            return 0;
        }
        nl_err[NL_FAILS]++;
        if (!wpage) {  // nothing else in it: marked bad now, or its first page (which may read back right) would
            seq0[wblock] = INV;  // start a block with the same seq as the next one after a remount
            retire_block(wblock);
        } else {
            set_bit(retire, wblock);  // its pages before this one are data: marked when the ring comes to it
        }
        wpage = NL_PPB;
    }
    return -EIO;
}

static int find(uint32_t file)
{
    for (int i = nfiles - 1; i >= 0; i--) {
        if (files[i].file == file) {
            return i;
        }
    }
    return -1;
}

static void new_file(uint32_t file, uint32_t seq, uint32_t size)
{
    if (nfiles == NL_MAX_FILES) {  // shortcut: past 256 files the oldest is forgotten (a file per reconnect, 4 MB each)
        memmove(files, files + 1, sizeof(files[0]) * --nfiles);
        nl_err[NL_LOST]++;
    }
    files[nfiles++] = (struct ent){file, seq, seq, size};
}

/* A page found at mount into the list: on to the file before it, or the start of a new one */
static void add(const struct hdr *h)
{
    struct ent *e = nfiles ? &files[nfiles - 1] : NULL;
    if (e && e->file == h->file) {
        e->last = h->seq;
        e->size = h->off + h->len > e->size ? h->off + h->len : e->size;
    } else if (!h->off) {  // else the rest of a file whose start is gone
        new_file(h->file, h->seq, h->len);
    }
}

/* The pages from s to e (headers hs, he, read as rs, re) into the list, in order. A file's pages are one run: one file
 * at both ends is that file all through; else split in two */
static void scan(uint32_t s, int rs, struct hdr *hs, uint32_t e, int re, struct hdr *he)
{
    if (rs == H_OK && re == H_OK && hs->file == he->file) {
        add(hs);
        add(he);
        return;
    }
    if (e - s <= 1) {
        if (rs == H_OK) {
            add(hs);
        }
        if (e != s && re == H_OK) {
            add(he);
        }
        return;
    }
    struct hdr hm;
    uint32_t m = s + (e - s) / 2;
    int rm = hdr_at(m, &hm);
    scan(s, rs, hs, m, rm, &hm);
    scan(m, rm, &hm, e, re, he);
}

/* The file list from the uploaded pointer on: block by block in ring order, from the one after the block written */
static void build(void)
{
    for (uint32_t i = 1, b; i <= nblocks; i++) {
        b = (wblock + i) % nblocks;
        if (seq0[b] == INV) {
            continue;
        }
        uint32_t s = seq0[b] > up ? seq0[b] : up, e = block_end(b) - 1;
        if (e < up || e < s) {
            continue;
        }
        struct hdr hs, he;
        int rs = hdr_at(s, &hs), re = hdr_at(e, &he);
        scan(s, rs, &hs, e, re, &he);
    }
}

/* A block that may hold data but cannot be made out at this mount (reads that fail, pages that do not check): left as
 * it is, not used, not erased; the next mount looks again. Its files are not listed, so this mount writes past every
 * seq ever written (top) and the uploaded pointer stays where it was (nl_delete).
 * shortcut: one readable again later sits out of seq order round the ring, and build() walks the ring in place order,
 * so its files may list out of order (none erased; up goes by the lowest seq listed); sort the blocks by seq0 if it
 * ever shows in the field. One that never reads again (pages ECC cannot correct) holds up at every boot: the files
 * after it listed again, uploaded twice; remember it in NVS if that shows */
static uint32_t unknown;
static void keep(uint32_t b)
{
    set_bit(bad, b);
    seq0[b] = INV;
    unknown++;
}

/* Recovery never erases a block that may hold data: the newest block's first page and the one to be erased next (the
 * oldest) read whole and checked, blocks out of order round the ring checked again; what cannot be made out is kept */
int nl_mount(const struct nl_ops *o, uint32_t n, uint32_t u, uint32_t t0)
{
    static uint8_t odd[NL_MAX_BLOCKS / 8];      // first page blank but its first main byte not: factory bad or torn
    static uint8_t checked[NL_MAX_BLOCKS / 8];  // seq0 read whole
    struct hdr h;
    uint8_t sp[NL_SPARE];
    ops = o;
    nblocks = MIN(n, NL_MAX_BLOCKS);
    memset(seq0, 0xFF, sizeof(seq0));
    memset(bad, 0, sizeof(bad));
    memset(retire, 0, sizeof(retire));
    memset(odd, 0, sizeof(odd));
    memset(checked, 0, sizeof(checked));
    nfiles = 0;
    unknown = 0;
    wfile = gone = rd_seq = cur_file = INV;
    pg_len = pg_off = 0;
    up = u;
    top = t0;
    next_seq = INV;  // page_of: no bound yet

    // every block's first page: bad marks (0x800, ours and the factory's), seq0, blank
    bool data = false;
    uint32_t bad_end = 0;  // past the seqs that blocks marked bad say they hold (below top: a factory one's garbage not)
    for (uint32_t b = 0; b < nblocks; b++) {
        int r = read_hdr(b * NL_PPB, &h, sp), ecc;
        uint8_t m0 = 0xFF;
        uint32_t s0;
        if (r == H_FAIL) {
            keep(b);
        } else if (sp[0] != 0xFF) {
            set_bit(bad, b);
            bad_end = r == H_OK && h.seq < top && h.seq >= bad_end ? h.seq + 1 : bad_end;
        } else if (r == H_OK) {
            seq0[b] = h.seq;
        } else if (r == H_BAD) {  // not ours, ECC failed, or cut mid-program: its pages say
            set_bit(checked, b);
            int v = verify(b, &s0);
            if (v == V_UNKNOWN) {
                keep(b);
            } else if (v == V_OK) {
                seq0[b] = s0;
            }
        } else if (read2(b * NL_PPB, 0, &m0, 1, &ecc)) {
            keep(b);
        } else if (m0 != 0xFF) {
            set_bit(odd, b);  // W25N02KV's datasheet marks factory bad blocks in both; a first page cut short too
        }
        data = data || seq0[b] != INV;
    }
    // a chip with nothing of ours on it: its odd blocks are the factory's, marked so for good
    for (uint32_t b = 0; b < nblocks && !data && !unknown; b++) {
        if (bit(odd, b)) {
            ops->mark_bad(b);
            set_bit(bad, b);
        }
    }

    uint32_t last, head;
    for (;;) {
        last = head = INV;
        for (uint32_t b = 0; b < nblocks; b++) {
            if (seq0[b] != INV && newer(b, head)) {
                head = b;
            }
        }
        if (head == INV) {
            break;
        }
        uint32_t s0;
        if (!bit(checked, head)) {
            set_bit(checked, head);
            int v = verify(head, &s0);
            if (v != V_OK || s0 != seq0[head]) {  // V_OK: its first page read wrong in the scan
                if (v == V_UNKNOWN) {
                    keep(head);
                } else {
                    seq0[head] = v == V_OK ? s0 : INV;
                }
                continue;
            }
        }
        // its last page: halves on "blank"; that page whole, else the one before it (a page cut mid-program is the
        // newest, and only that one); a read that fails: kept
        uint32_t lo = 0, hi = NL_PPB;
        int r = H_OK;
        while (hi - lo > 1 && r != H_FAIL) {
            uint32_t mid = (lo + hi) / 2;
            r = read_hdr(head * NL_PPB + mid, &h, NULL);
            if (r == H_ERASED) {
                hi = mid;
            } else {
                lo = mid;
            }
        }
        bool crc_ok = false;
        int ecc, err = r == H_FAIL ? -EIO : read_whole(head * NL_PPB + lo, &h, &crc_ok, &ecc);
        if (err == -EIO) {
            keep(head);
            continue;
        }
        if (!err && crc_ok && h.seq == seq0[head] + lo) {
            last = h.seq;
        } else if (lo) {
            last = seq0[head] + lo - 1;
        } else {
            seq0[head] = INV;
            continue;
        }
        // the block to be erased next (after it round the ring, the oldest), and blocks out of order checked: one
        // newer than the newest means a seq0 read wrong
        uint32_t t = next_good(head), prev = INV;
        bool again = false;
        for (uint32_t c = t; c != INV && c != head && !again; c = (c + 1) % nblocks) {
            if (bit(bad, c) || (seq0[c] == INV && c != t)) {
                continue;
            }
            if ((c == t || seq0[c] > seq0[head] || (prev != INV && seq0[c] <= prev)) && !bit(checked, c)) {
                set_bit(checked, c);
                int v = verify(c, &s0);
                if (v == V_UNKNOWN) {
                    keep(c);
                    again = c == t;  // the next one is erased instead: checked first
                    continue;
                }
                seq0[c] = v == V_OK ? s0 : INV;
                again = seq0[c] != INV && newer(c, head);
            }
            prev = seq0[c] != INV ? seq0[c] : prev;
        }
        if (!again) {
            break;
        }
    }
    // odd blocks left are factory bad, but the next one to be written after the newest: that one is ours, cut
    // mid-program (no newest: none is)
    uint32_t t = head == INV ? INV : next_good(head);
    for (uint32_t b = 0; b < nblocks; b++) {
        if (bit(odd, b) && b != t) {
            set_bit(bad, b);
        }
    }
    // a block kept, or one marked bad that says it holds seqs from here on (the newest, its mark read wrong, or the
    // bit gone in the chip: 0x800 has no ECC on W25N01GV): past every seq written, none written twice
    next_seq = last == INV ? 0 : last + 1;
    next_seq = next_seq > up ? next_seq : up;
    unknown += bad_end > next_seq;
    next_seq = unknown && top > next_seq ? top : next_seq;

    // the list first (the files the next erase takes are counted lost), then writing goes on in the next block, erased
    // now: nothing is programmed twice, and an erase cut short is done again
    wblock = head;
    wpage = NL_PPB;
    build();
    rd_seq = INV;
    return advance();
}

int nl_flush(void)
{
    return pg_len ? put_page() : 0;
}

int nl_append(uint32_t file, const void *d, uint32_t n)
{
    if (file != wfile || !nfiles) {
        int err = nl_flush();
        int i = find(file);
        if (err) {
            return err;
        }
        if ((i >= 0 && i != nfiles - 1) || (i < 0 && gone != INV && file <= gone)) {
            return -EROFS;  // its pages would not be one run; or a file deleted, not to come back with other bytes
        }
        if (i < 0) {
            new_file(file, next_seq, 0);
            files[nfiles - 1].last = INV;
        }
        wfile = file;
        pg_off = files[nfiles - 1].size;
    }
    for (uint32_t i = 0; i < n;) {
        uint32_t k = MIN(n - i, NL_PAGE - pg_len);
        memcpy(pg + pg_len, (const uint8_t *) d + i, k);
        pg_len += k;
        files[nfiles - 1].size += k;
        i += k;
        if (pg_len == NL_PAGE) {
            int err = put_page();
            if (err) {
                return err;
            }
        }
    }
    return n;
}

int nl_read(uint32_t file, uint32_t off, void *d, uint32_t n)
{
    int i = find(file);
    if (i < 0) {
        return -ENOENT;
    }
    struct ent *e = &files[i];
    if (off >= e->size || !n) {
        return 0;
    }
    if (file == wfile && off >= pg_off) {  // the open page, not on the chip yet
        uint32_t k = MIN(n, pg_off + pg_len - off);
        memcpy(d, pg + (off - pg_off), k);
        return k;
    }
    struct hdr h;
    uint32_t s;
    if (file == cur_file && off == cur_off) {
        s = cur_seq;
    } else {  // the last page that starts at or before off
        uint32_t lo = e->seq, hi = e->last;
        while (hi != INV && lo < hi) {
            uint32_t mid = lo + (hi - lo + 1) / 2;
            if (hdr_at(mid, &h) != H_OK || h.file != file) {
                return -EIO;
            }
            if (h.off <= off) {
                lo = mid;
            } else {
                hi = mid - 1;
            }
        }
        s = lo;
    }
    bool crc_ok = true;
    int ecc = 0;
    uint32_t p = page_of(s);
    if (s != rd_seq) {
        rd_seq = INV;
        if (p == INV || read_whole(p, &h, &crc_ok, &ecc) || h.seq != s) {
            return -EIO;
        }
        if (!crc_ok && ecc != 2) {
            nl_err[NL_ECC_BAD]++;  // sent as read: most of it is right (ECC's own failure counted already)
        }
        rd_seq = s;
    }
    unpack(rd + NL_PAGE, 0, &h);
    if (h.file != file || off < h.off || off >= h.off + h.len) {
        return -EIO;
    }
    uint32_t k = MIN(n, h.off + h.len - off);
    memcpy(d, rd + (off - h.off), k);
    cur_file = file;
    cur_off = off + k;
    cur_seq = cur_off == h.off + h.len ? s + 1 : s;
    return k;
}

int nl_delete(uint32_t file, bool *moved)
{
    int i = find(file);
    *moved = false;
    if (i < 0) {
        return -ENOENT;
    }
    if (file == wfile && pg_len) {
        return -EBUSY;  // its open page is not on the chip yet
    }
    if (file == wfile) {
        wfile = INV;  // closed and flushed (a segment just rolled over): the next append starts a new file
    }
    gone = gone == INV || file > gone ? file : gone;
    memmove(files + i, files + i + 1, sizeof(files[0]) * (nfiles - i - 1));
    nfiles--;
    uint32_t u = next_seq;  // the lowest seq listed: the list may be out of seq order (keep())
    for (int k = 0; k < nfiles; k++) {
        u = MIN(u, files[k].seq);
    }
    if (u > up && !unknown) {  // a block kept: what it holds is not listed, so not uploaded either
        up = u;
        *moved = true;
    }
    return 0;
}

uint32_t nl_up(void)
{
    return up;
}

int nl_files(void)
{
    return nfiles;
}

void nl_file(int i, uint32_t *file, uint32_t *size)
{
    *file = files[i].file;
    *size = files[i].size;
}

uint64_t nl_total(void)
{
    uint64_t n = 0;
    for (uint32_t b = 0; b < nblocks; b++) {
        n += !bit(bad, b);
    }
    return n * NL_PPB * NL_PAGE;
}

uint64_t nl_used(void)
{
    uint64_t n = 0;
    for (int i = 0; i < nfiles; i++) {
        n += files[i].size;
    }
    return n;
}
