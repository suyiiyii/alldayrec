package com.suyiiyii.live_record

import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.random.Random

/** The BLE recorder's record stream (firmware src/lr_store.h, reference client firmware/devkit/lr_sync.py): files of
 *  'A' records (consecutive 10 ms Opus frames from `first`) and 'T' records (frame `frame` was at the phone's
 *  `epochMs`), named boot << 16 | segment. A frame's wall time is its boot's nearest 'T' + 10 ms per frame. Plain
 *  Kotlin: the unit test runs it. */
object Store {
    const val FRAME_MS = 10L
    const val OLD_PIECE_FRAMES = 6000  // a file the board has closed goes up in 60 s pieces

    sealed class Rec
    class Audio(val first: Long, val frames: List<ByteArray>) : Rec()
    class Sync(val frame: Long, val epochMs: Long) : Rec()

    /** The whole records of `b` and the offset each ends at (relative to `base`); a record cut short ends the list. */
    fun scan(b: ByteArray, base: Long = 0): List<Pair<Rec, Long>> {
        val out = mutableListOf<Pair<Rec, Long>>()
        val v = ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN)
        var i = 0
        while (i < b.size) {
            if (b[i] == 'A'.code.toByte() && i + 6 <= b.size) {
                val n = b[i + 1].toInt() and 0xff
                val first = v.getInt(i + 2).toLong() and 0xffffffffL
                var j = i + 6
                val frames = ArrayList<ByteArray>(n)
                repeat(n) {
                    if (j >= b.size) return out
                    val len = b[j].toInt() and 0xff
                    if (j + 1 + len > b.size) return out
                    frames += b.copyOfRange(j + 1, j + 1 + len)
                    j += 1 + len
                }
                i = j
                out += Audio(first, frames) to base + i
            } else if (b[i] == 'T'.code.toByte() && i + 13 <= b.size) {
                out += Sync(v.getInt(i + 1).toLong() and 0xffffffffL, v.getLong(i + 5)) to base + i + 13
                i += 13
            } else return out
        }
        return out
    }

    private val CRC = IntArray(256) { i ->
        var r = i shl 24
        repeat(8) { r = if (r and 0x80000000.toInt() != 0) (r shl 1) xor 0x04c11db7 else r shl 1 }
        r
    }

    private fun page(packets: List<ByteArray>, flags: Int, granule: Long, serial: Int, seq: Int): ByteArray {
        val lacing = ByteArrayOutputStream()
        for (p in packets) {
            var n = p.size
            while (true) { lacing.write(minOf(n, 255)); if (n < 255) break; n -= 255 }
        }
        val lace = lacing.toByteArray()
        val body = packets.sumOf { it.size }
        val out = ByteBuffer.allocate(27 + lace.size + body).order(ByteOrder.LITTLE_ENDIAN)
        out.put("OggS".toByteArray()).put(0).put(flags.toByte()).putLong(granule).putInt(serial).putInt(seq).putInt(0)
            .put(lace.size.toByte()).put(lace)
        packets.forEach { out.put(it) }
        val a = out.array()
        var crc = 0
        for (b in a) crc = (crc shl 8) xor CRC[((crc ushr 24) xor (b.toInt() and 0xff)) and 0xff]
        ByteBuffer.wrap(a, 22, 4).order(ByteOrder.LITTLE_ENDIAN).putInt(crc)
        return a
    }

    /** 16 kHz Opus packets of [frameMs] each into one Ogg Opus file (RFC 7845), as web/src/lib/ogg.ts does: pre-skip 0
     *  (the encoder runs on across pieces), granule counting every frame from the piece's start. The bean's (Bean.kt):
     *  20 ms frames, stereo. */
    fun oggOpus(frames: List<ByteArray>, frameMs: Long = FRAME_MS, channels: Int = 1): ByteArray {
        val serial = Random.nextInt()
        val head = ByteBuffer.allocate(19).order(ByteOrder.LITTLE_ENDIAN)
            .put("OpusHead".toByteArray()).put(1).put(channels.toByte()).putShort(0).putInt(16000).putShort(0).put(0).array()
        val tags = "OpusTags".toByteArray() + ByteArray(8)
        val out = ByteArrayOutputStream()
        out.write(page(listOf(head), 2, 0, serial, 0))
        out.write(page(listOf(tags), 0, 0, serial, 1))
        var seq = 2
        for (i in frames.indices step 50) {
            val end = minOf(i + 50, frames.size)
            out.write(page(frames.subList(i, end), if (end == frames.size) 4 else 0, end * frameMs * 48, serial, seq++))
        }
        return out.toByteArray()
    }
}

/** The recorder's files mirrored byte for byte in `dir` (<file>.BIN, as on its card), and how far each has become Ogg
 *  pieces for the upload queue. One thread uses it (the BLE session's). State in `dir`/state.txt:
 *    F file closed size cut tscan finished piece,piece...   per file: closed (the stream goes on in another), its
 *                                  size on the card (-1: unknown), bytes turned into pieces, bytes scanned for 'T',
 *                                  all of it in pieces, the pieces written
 *    T clock frame epochMs                         the 'T' records seen; clock: the boot, or the file for boot 0 (no
 *                                                  card to count boots: each such boot has its own files)
 *    C file cut [piece]                            a piece made since (or frames passed over): appended, see cut() */
class Mirror(private val dir: File) {
    companion object {
        const val LIST_MAX = 64
        /** How far one boot's 'T' records may disagree on when it began (epochMs - frame * 10 ms): the frame clock runs
         *  within ~30 ppm of the phone's (59998-60000 frames in 10 min), frames a stalled codec lost move it on. */
        const val SAME_BOOT_MS = 60_000L
    }

    class F(var closed: Boolean = false, var size: Long = -1, var cut: Long = 0, var tscan: Long = 0,
            val pieces: MutableList<String> = mutableListOf(), var finished: Boolean = false)

    val files = sortedMapOf<Long, F>()
    private val syncs = HashMap<Long, MutableList<LongArray>>()
    private val state = File(dir, "state.txt")

    init {
        dir.mkdirs()
        if (state.exists()) for (l in state.readLines()) {
            val p = l.split(' ')
            when (p[0]) {
                "F" -> files[p[1].toLong(16)] = F(p[2] == "1", p[3].toLong(), p[4].toLong(), p[5].toLong(),
                    p.getOrElse(7) { "" }.split(',').filter { it.isNotEmpty() }.toMutableList(), p[6] == "1")
                "T" -> syncs.getOrPut(p[1].toLong(16)) { mutableListOf() } += longArrayOf(p[2].toLong(), p[3].toLong())
                "C" -> if (p.size >= 3) files.getOrPut(p[1].toLong(16)) { F() }.apply { cut = p[2].toLong(); if (p.size > 3) pieces += p[3] }
            }
        }
    }

    private fun save() {
        val tmp = File(dir, "state.tmp")
        tmp.writeText(buildString {
            for ((f, s) in files) append("F %08X %d %d %d %d %d %s\n".format(f, if (s.closed) 1 else 0, s.size, s.cut, s.tscan, if (s.finished) 1 else 0, s.pieces.joinToString(",")))
            for ((c, ts) in syncs) for (t in ts) append("T %08X %d %d\n".format(c, t[0], t[1]))
        })
        tmp.renameTo(state)
    }

    /** A piece made (`name`) or frames passed over: one line appended, not the whole state again. save() per piece wrote
     *  every piece name of the open file each time: at 1 s pieces ~150 MB an hour, more the longer the file. The
     *  next save() folds these lines in; tscan is not in them: a restart scans again from the saved one, finding the
     *  same 'T' records. */
    private fun cut(f: Long, name: String?) {
        if (!state.exists()) return save()
        state.appendText("C %08X %d%s\n".format(f, files[f]!!.cut, name?.let { " $it" } ?: ""))
    }

    private fun bin(f: Long) = File(dir, "%08X.BIN".format(f))
    fun have(f: Long) = bin(f).length()
    private fun clock(f: Long) = if (f shr 16 == 0L) f else f shr 16

    /** Bytes of file `f` at `off` (LIVE, or READ's DATA). Returns where a hole starts when they come after one (the
     *  caller READs from there), null when taken. Bytes the mirror has already are the same: skipped. */
    fun put(f: Long, off: Long, b: ByteArray): Long? {
        val s = files.getOrPut(f) { F() }
        val h = have(f)
        if (off > h) return h
        if (off + b.size > h) FileOutputStream(bin(f), true).use { it.write(b, (h - off).toInt(), (off + b.size - h).toInt()) }
        if (s.finished && have(f) > h) s.finished = false
        return null
    }

    /** What LIST says: files on the card with their sizes, and the one the stream goes on in (kept, listed or not). A
     *  LIST gives at most LIST_MAX files (firmware list()), so only a shorter one says that a file it leaves out is no
     *  longer on the card (deleted elsewhere, another card): closed at what the mirror has. */
    fun listed(card: Map<Long, Long>, open: Long) {
        for ((f, size) in card) files.getOrPut(f) { F() }.apply { this.size = size; closed = f != open }
        if (open >= 0) files.getOrPut(open) { F() }.closed = false
        if (card.size < LIST_MAX) for ((f, s) in files) if (f !in card && f != open) { s.closed = true; s.size = have(f) }
        save()
    }

    /** Files the stream has moved on from (a LIVE packet of `open` arrived): closed; LIST tells their size. */
    fun moved(open: Long) {
        for ((f, s) in files) if (f != open && !s.closed) s.closed = true
        save()
    }

    /** Files with bytes on the card the mirror lacks: (file, from). */
    fun missing(): List<Pair<Long, Long>> = files.filter { (f, s) -> s.size > have(f) }.map { (f, _) -> f to have(f) }

    /** A file whose card copy cannot give more (its READ ends where the mirror does): take what the mirror has. */
    fun noMore(f: Long) { files[f]?.let { it.size = minOf(it.size, have(f)); save() } }

    private fun bytes(f: Long, from: Long, max: Long = Long.MAX_VALUE): ByteArray = if (!bin(f).exists()) ByteArray(0) else RandomAccessFile(bin(f), "r").use { r ->
        r.seek(from); ByteArray(minOf(r.length() - from, max).toInt()).also { r.readFully(it) }
    }

    /** Every 'T' record the mirror has: a later file may date an earlier one. */
    private fun scanSyncs() {
        for ((f, s) in files) if (have(f) > s.tscan) {
            for ((r, end) in Store.scan(bytes(f, s.tscan), s.tscan)) {
                if (r is Store.Sync) syncs.getOrPut(clock(f)) { mutableListOf() }.let { ts -> if (ts.none { it[0] == r.frame }) ts += longArrayOf(r.frame, r.epochMs) }
                s.tscan = end
            }
        }
    }

    /** Where the next piece of the files from `f0` on will start (unix ms), as pieces() will name it; null when none of
     *  them has a dated frame yet. A phone sending these ahead of older audio still on the card says so first. */
    fun nextStart(f0: Long): Long? {
        scanSyncs()
        for ((f, s) in files.tailMap(f0)) {
            val ts = syncs[clock(f)]
            if (s.finished || ts.isNullOrEmpty()) continue
            for ((r, _) in Store.scan(bytes(f, s.cut, 8192), s.cut)) if (r is Store.Audio) {  // a record is at most 2.6 KB
                val near = ts.minBy { Math.abs(it[0] - r.first) }
                return near[1] + (r.first - near[0]) * Store.FRAME_MS
            }
        }
        return null
    }

    /** Turns what the mirror has into Ogg pieces in `out`, `start@tag.ogg`, `start` = the frame's boot's nearest 'T' +
     *  10 ms per frame. Pieces end at a gap in the frames, at `liveFrames` (the open file) or 60 s (a closed one), and
     *  at the end of a closed file the mirror has whole; a file's last frames otherwise wait for more. Frames of a boot
     *  with no 'T' yet wait for one; a file of a boot that is over (`boot` is the live one) and never had one goes to
     *  `undated` once the mirror has it whole, as one Ogg of all its frames (its runs back to back: no time to put
     *  them at anyway).
     *  Returns the number of pieces written. */
    fun pieces(out: File, undated: File, tag: String, liveFrames: Int, boot: Int): Int {
        scanSyncs()
        var n = 0
        for ((f, s) in files) {
            val whole = s.closed && s.size in 0..have(f)
            if (s.finished || (have(f) <= s.cut && !whole)) continue
            val max = if (s.closed) Store.OLD_PIECE_FRAMES else liveFrames
            val ts = syncs[clock(f)]
            val dead = if (f shr 16 == 0L) whole else (f shr 16) < boot
            if (ts.isNullOrEmpty() && !(dead && whole)) continue  // waits for this boot's first 'T'
            var first = -1L
            val run = mutableListOf<ByteArray>()
            val lost = mutableListOf<ByteArray>()  // frames that will never have a time
            fun emit(end: Long) {
                var piece: String? = null
                if (run.isNotEmpty()) {
                    if (ts.isNullOrEmpty()) {
                        lost += run
                    } else {
                        val near = ts.minBy { Math.abs(it[0] - first) }
                        val name = "${near[1] + (first - near[0]) * Store.FRAME_MS}@$tag"
                        val part = File(out, "$name.part")
                        part.writeBytes(Store.oggOpus(run))
                        part.renameTo(File(out, "$name.ogg"))
                        s.pieces += name
                        piece = name
                        n++
                    }
                }
                s.cut = end
                run.clear()
                cut(f, piece)
            }
            var last = s.cut  // where the last whole record ends
            for ((r, end) in Store.scan(bytes(f, s.cut), s.cut)) {
                if (r is Store.Audio) {
                    if (run.isNotEmpty() && r.first != first + run.size) emit(last)  // a gap: frames lost on the way
                    if (run.isEmpty()) first = r.first
                    run += r.frames
                }
                last = end
                if (run.isEmpty()) s.cut = end else if (run.size >= max) emit(end)
            }
            if (whole) {
                emit(last)
                if (lost.isNotEmpty()) undated.mkdirs().also { File(undated, "$tag-%08X.ogg".format(f)).writeBytes(Store.oggOpus(lost)) }
                s.finished = true
                save()
            }
        }
        return n
    }

    /** Closed files the mirror has whole, all turned into pieces, the pieces uploaded (gone from `out`): the card may
     *  delete them. */
    fun done(out: File): List<Long> = files.filter { (_, s) ->
        s.closed && s.finished && s.pieces.none { File(out, "$it.ogg").exists() || File(out, "$it.part").exists() }
    }.keys.toList()

    /** None of the pieces it cut that start before [until] is still in [out] waiting to go up. With [until] where LIVE
     *  began and pieces() run just before, the backlog is all up and the server's hold may go. 10-08: the hold
     *  went 1-3 s early, before the last READ's bytes were cut, and while the backlog's last minute (between the hold and
     *  where LIVE began) was still queued; those pieces came in behind the server's queue. */
    fun up(out: File, until: Long): Boolean = files.values.none { s ->
        s.pieces.any { n -> (n.substringBefore("@").toLongOrNull() ?: Long.MAX_VALUE) < until && (File(out, "$n.ogg").exists() || File(out, "$n.part").exists()) }
    }

    /** A file the card has deleted: gone from the mirror too (its 'T' records stay for the rest of the boot). */
    fun drop(f: Long) {
        bin(f).delete()
        files.remove(f)
        save()
    }

    /** The board is on boot [b] at [frame] now ([at]: the phone's clock then). A boot number can come back: the card's
     *  count read as 0 and it counted from 1 again (10-13: the new audio dated by 10-10's 'T' records of boot 1,
     *  three days back; its files taken for 10-10's of the same name: not READ, deleted from the card). Firmware e4cefed
     *  counts past the chip's own count too, which an erased chip still loses. The mirror's files and 'T' records of
     *  boot [b] are an earlier boot's when a 'T' is at a later frame than now (a boot's frames only go on) or puts the
     *  boot's start elsewhere. Files of [b] with no 'T' at all are taken for this boot's: a link that dropped before the
     *  READ reached this boot's 'T' leaves just that (older firmware wrote it at the end of a file of hours).
     *  Those files are cut into pieces as far as the mirror has them, by their own 'T' records, then gone with the 'T'
     *  records: the card's files of that name are this boot's, READ from 0. True: there was such a boot. */
    fun reused(b: Long, frame: Long, at: Long, out: File, undated: File, tag: String): Boolean {
        if (b == 0L) return false  // no card at boot: each file is its own clock
        scanSyncs()
        val mine = files.keys.filter { it shr 16 == b }; val ts = syncs[b].orEmpty(); val began = at - frame * Store.FRAME_MS
        if (ts.none { it[0] > frame || Math.abs(it[1] - it[0] * Store.FRAME_MS - began) > SAME_BOOT_MS }) return false
        for (f in mine) files[f]!!.apply { closed = true; size = have(f) }
        pieces(out, undated, tag, Store.OLD_PIECE_FRAMES, (b + 1).toInt())  // that boot is over
        for (f in mine) { bin(f).delete(); files.remove(f) }
        syncs.remove(b)
        save()
        return true
    }
}
