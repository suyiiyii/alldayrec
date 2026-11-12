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

struct ent {
    uint32_t file, seq, last, size;  // first and last page's seq (last INV: none written yet)
};
static struct ent files[NL_MAX_FILES];
static int nfiles;
static uint32_t wfile;  // the file appended to: the last entry
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

enum { H_OK, H_ERASED, H_BAD };

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

/* A page's header alone (its spare bytes into sp if given) */
static int read_hdr(uint32_t page, struct hdr *h, uint8_t *sp)
{
    uint8_t s[NL_SPARE];
    int ecc = 0;
    if (ops->read(page, NL_PAGE, s, NL_SPARE, &ecc)) {
        memset(s, 0, sizeof(s));  // unreadable: neither erased nor ours (nor a bad-block mark: s[0] is looked at alone)
        s[0] = 0xFF;
        ecc = 2;
    }
    if (sp) {
        memcpy(sp, s, NL_SPARE);
    }
    return unpack(s, ecc, h);
}

/* The page that has seq: INV if none does */
static uint32_t page_of(uint32_t seq)
{
    uint32_t best = INV;
    if (seq >= next_seq) {
        return INV;
    }
    for (uint32_t b = 0; b < nblocks; b++) {
        if (seq0[b] != INV && seq0[b] <= seq && (best == INV || seq0[b] > seq0[best])) {
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
    return r == H_OK && h->seq != seq ? H_BAD : r;
}

/* A page whole into rd: 0, or -EIO for one unreadable or with no header. *crc_ok false: its data does not match its
 * CRC (cut mid-program, or ECC could not correct it: the header taken as read, the caller checks its seq) */
static int read_whole(uint32_t page, struct hdr *h, bool *crc_ok)
{
    int ecc = 0;
    if (ops->read(page, 0, rd, NL_PAGE + NL_SPARE, &ecc) ||
        (unpack(rd + NL_PAGE, ecc, h) != H_OK && (ecc != 2 || unpack(rd + NL_PAGE, 0, h) != H_OK))) {
        return -EIO;
    }
    *crc_ok = page_crc(h, rd) == h->crc;
    return 0;
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

/* The seq after block b's last page: the next block with data in ring order, or next_seq for the newest */
static uint32_t block_end(uint32_t b)
{
    for (uint32_t c = (b + 1) % nblocks; c != b; c = (c + 1) % nblocks) {
        if (seq0[c] != INV) {
            return seq0[c] > seq0[b] ? seq0[c] : next_seq;
        }
    }
    return next_seq;
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
        set_bit(retire, wblock);
        if (!wpage) {
            seq0[wblock] = INV;
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

int nl_mount(const struct nl_ops *o, uint32_t n, uint32_t u)
{
    static uint8_t odd[NL_MAX_BLOCKS / 8];  // first page blank but its first main byte not: factory bad or torn
    struct hdr h;
    uint8_t sp[NL_SPARE];
    ops = o;
    nblocks = MIN(n, NL_MAX_BLOCKS);
    memset(seq0, 0xFF, sizeof(seq0));
    memset(bad, 0, sizeof(bad));
    memset(retire, 0, sizeof(retire));
    memset(odd, 0, sizeof(odd));
    nfiles = 0;
    wfile = rd_seq = cur_file = INV;
    pg_len = pg_off = 0;
    up = u;

    // every block's first page: bad marks (0x800, ours and the factory's), seq0, blank
    for (uint32_t b = 0; b < nblocks; b++) {
        int r = read_hdr(b * NL_PPB, &h, sp), ecc;
        uint8_t m0 = 0xFF;
        if (sp[0] != 0xFF) {
            set_bit(bad, b);
        } else if (r == H_OK) {
            seq0[b] = h.seq;
        } else if (r == H_ERASED && !ops->read(b * NL_PPB, 0, &m0, 1, &ecc) && m0 != 0xFF) {
            set_bit(odd, b);  // W25N02KV marks some factory bad blocks in the main area's first byte only
        }
    }

    // the newest block, its last page: halves on "blank"; that page whole, else the one before it (a page cut
    // mid-program is the newest, and only that one); none in it, it is not the newest
    uint32_t last = INV, head = INV;
    for (;;) {
        head = INV;
        for (uint32_t b = 0; b < nblocks; b++) {
            if (seq0[b] != INV && (head == INV || seq0[b] > seq0[head])) {
                head = b;
            }
        }
        if (head == INV) {
            break;
        }
        uint32_t lo = 0, hi = NL_PPB;
        while (hi - lo > 1) {
            uint32_t mid = (lo + hi) / 2;
            if (read_hdr(head * NL_PPB + mid, &h, NULL) == H_ERASED) {
                hi = mid;
            } else {
                lo = mid;
            }
        }
        bool crc_ok;
        if (!read_whole(head * NL_PPB + lo, &h, &crc_ok) && crc_ok && h.seq == seq0[head] + lo) {
            last = h.seq;
            break;
        }
        if (lo) {
            last = seq0[head] + lo - 1;
            break;
        }
        seq0[head] = INV;
    }
    // blank-but-marked first pages are factory bad, but the block after the newest: that one may be ours, cut mid-program
    uint32_t after = INV;
    for (uint32_t b = head == INV ? 0 : (head + 1) % nblocks, i = 0; i < nblocks; b = (b + 1) % nblocks, i++) {
        if (!bit(bad, b)) {
            after = b;
            break;
        }
    }
    for (uint32_t b = 0; b < nblocks; b++) {
        if (bit(odd, b) && b != after) {
            set_bit(bad, b);
        }
    }
    next_seq = last == INV ? 0 : last + 1;
    next_seq = next_seq > up ? next_seq : up;

    // writing goes on in the next block, erased now: nothing is programmed twice, and an erase cut short is done again
    wblock = head;
    wpage = NL_PPB;
    if (advance()) {
        return -EIO;
    }
    build();
    return 0;
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
        if (i >= 0 && i != nfiles - 1) {
            return -EROFS;  // its pages would not be one run
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
    uint32_t p = page_of(s);
    if (s != rd_seq) {
        rd_seq = INV;
        if (p == INV || read_whole(p, &h, &crc_ok) || h.seq != s) {
            return -EIO;
        }
        if (!crc_ok) {
            nl_err[NL_ECC_BAD]++;  // sent as read: most of it is right
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
    memmove(files + i, files + i + 1, sizeof(files[0]) * (nfiles - i - 1));
    nfiles--;
    uint32_t u = nfiles ? files[0].seq : next_seq;
    if (u > up) {
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
