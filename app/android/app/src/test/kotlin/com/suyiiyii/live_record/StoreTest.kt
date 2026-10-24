package com.suyiiyii.live_record

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files
import org.json.JSONObject

/** The record stream as the phone takes it: mirror (LIVE with holes READ again), pieces dated by the nearest 'T',
 *  frames held for a boot without one, closed files finished and deletable once their pieces are uploaded. */
class StoreTest {
    private val fixture = JSONObject(javaClass.getResource("/record-stream.json")!!.readText())
    private fun hex(s: String) = s.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    private fun frames(file: File): Long {
        val b = file.readBytes()
        val last = String(b, Charsets.ISO_8859_1).lastIndexOf("OggS")
        return ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN).getLong(last + 6) / 480
    }

    @Test fun sharedArchive() {
        val v = fixture.getJSONObject("archive")
        val files = v.getJSONArray("files")
        val m = Mirror(tmp()); val out = tmp(); val undated = tmp()
        val sizes = mutableMapOf<Long, Long>()
        for (i in 0 until files.length()) {
            val f = files.getJSONObject(i)
            val bytes = hex(f.getString("hex"))
            m.put(f.getLong("file"), 0, bytes)
            sizes[f.getLong("file")] = bytes.size.toLong()
        }
        m.listed(sizes, 4L shl 16)
        m.pieces(out, undated, "X", 15, 4)
        val runs = v.getJSONArray("runs")
        for (i in 0 until runs.length()) {
            val r = runs.getJSONArray(i)
            val file = if (r.isNull(0)) undated.listFiles()!!.single() else File(out, "${r.getLong(0)}@X.ogg")
            assertEquals(r.getLong(1), frames(file))
        }
        assertEquals(runs.length() - 1, out.listFiles()!!.size)
    }
    private fun pieceNames(v: JSONObject, count: Int) = (0 until count).map {
        "${v.getJSONArray("pieces").getJSONArray(it).getLong(0)}@LR-P1-BDAE.ogg"
    }

    @Test fun sharedReuseBoundaries() {
        val v = fixture.getJSONObject("reuse")
        val cases = v.getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i)
            val m = Mirror(tmp())
            val f = v.getLong("file")
            m.put(f, 0, hex(v.getString("hex")))
            assertEquals(c.getBoolean("reused"), m.reused(f shr 16, c.getLong("frame"),
                c.getLong("at"), tmp(), tmp(), "X"))
        }
    }
    private fun le(n: Int, fill: ByteBuffer.() -> Unit) = ByteBuffer.allocate(n).order(ByteOrder.LITTLE_ENDIAN).apply(fill).array()
    private fun a(first: Int, n: Int) = le(6) { put('A'.code.toByte()); put(n.toByte()); putInt(first) } +
        (0 until n).flatMap { listOf<Byte>(2, it.toByte(), it.toByte()) }.toByteArray()
    private fun t(frame: Int, epoch: Long) = le(13) { put('T'.code.toByte()); putInt(frame); putLong(epoch) }
    private fun tmp() = Files.createTempDirectory("lr").toFile()
    private fun names(out: File) = out.list()!!.filter { it.endsWith(".ogg") }.sortedBy { it.substringBefore("@").toLong() }

    @Test fun mirror() {
        val dir = tmp(); val out = tmp(); val undated = tmp()
        val v = fixture.getJSONObject("mirror")
        val f = v.getLong("file")
        val stream = hex(v.getString("hex"))
        val m = Mirror(dir)
        val deliveries = v.getJSONArray("deliveries")
        for (i in 0 until deliveries.length()) {
            val d = deliveries.getJSONObject(i)
            val hole = if (d.isNull("hole")) null else d.getLong("hole")
            assertEquals(hole, m.put(f, d.getLong("start"), stream.copyOfRange(d.getInt("start"), d.getInt("end"))))
        }
        assertEquals(stream.size.toLong(), m.have(f))
        m.listed(mapOf(f to 80L), f)

        // the open file: pieces of >= 15 frames at record ends, a gap ends one, the last frames wait
        assertEquals(2, m.pieces(out, undated, "LR-P1-BDAE", 15, 1))
        assertEquals(pieceNames(v, 2), names(out))

        // the stream moved on (the board rebooted: boot 2): this file is closed and whole, its last frames go too
        val g = 2L shl 16
        assertNull(m.put(g, 0, a(0, 5)))  // boot 2, no 'T' yet: waits
        m.listed(mapOf(f to stream.size.toLong(), g to 21L), g)
        assertEquals(1, m.pieces(out, undated, "LR-P1-BDAE", 15, 2))
        assertEquals(pieceNames(v, 3).last(), names(out).last())
        assertEquals(emptyList<Long>(), m.done(out))  // pieces not uploaded yet
        out.listFiles()!!.forEach { it.delete() }   // the uploader took them
        assertEquals(listOf(f), m.done(out))

        // state survives a restart; boot 2 gets its 'T' later in the stream and its frames go then
        val m2 = Mirror(dir)
        assertEquals(listOf(f), m2.done(out))
        assertNull(m2.put(g, 21, t(5, 2_000_000)))
        assertEquals(0, m2.pieces(out, undated, "LR-P1-BDAE", 15, 2))  // 5 frames of the open file: wait for more
        assertNull(m2.put(g, 34, a(5, 10)))
        assertEquals(1, m2.pieces(out, undated, "LR-P1-BDAE", 15, 2))
        assertEquals(listOf("1999950@LR-P1-BDAE.ogg"), names(out))
        m2.drop(f)
        assertEquals(0L, m2.have(f))
    }

    /** The hold goes once the backlog is all up: the last READ's bytes cut into pieces first, and all of it up to
     *  where LIVE began, not to the hold a minute before (10-08: let go 1-3 s early, the backlog's last minute queued). */
    @Test fun backlogUp() {
        val dir = tmp(); val out = tmp(); val undated = tmp()
        val f = 1L shl 16; val g = 1L shl 16 or 1  // the backlog file, and the one LIVE goes on in
        val backlog = t(0, 1_000_000) + a(0, 10) + a(10, 10)  // 200 ms from 1_000_000
        val m = Mirror(dir)
        m.listed(mapOf(f to backlog.size.toLong()), g)
        assertNull(m.put(f, 0, backlog))  // READ to its end
        val live = 1_000_200L; val hold = live - 60_000  // holdAhead
        assertTrue("its bytes not cut yet: up, so cut first", m.up(out, live))
        assertEquals(1, m.pieces(out, undated, "LR-P3-BDAE", 15, 1))
        assertFalse("its piece is queued, after the hold, before LIVE began", m.up(out, live))
        assertTrue("up to the hold only: it would go now", m.up(out, hold))
        out.listFiles()!!.forEach { it.delete() }  // the uploader took it
        assertTrue(m.up(out, live))
    }

    /** A boot number counted before. 10-10's boot 1: file 3 uploaded, file 4 cut but for its last frames; boot 1
     *  again on 10-13 (the card's count read as 0), at frame 1000. The mirror's boot 1, read back from its state.txt,
     *  is that earlier boot's: file 4's last frames go up by 10-10's 'T', both files and the 'T' go, and today's file 3
     *  (the same name) is taken from 0 and dated by today's 'T', not 10-10's. */
    @Test fun bootReused() {
        val dir = tmp(); val out = tmp(); val undated = tmp()
        val old = 1_791_000_000_000L; val now = old + 3 * 86_400_000
        val f3 = 1L shl 16 or 3; val f4 = 1L shl 16 or 4
        val m0 = Mirror(dir)
        m0.put(f3, 0, t(7_000_000, old) + a(7_000_000, 10))
        m0.put(f4, 0, a(7_000_010, 10) + a(7_000_020, 10) + a(7_000_030, 5))
        m0.listed(mapOf(f3 to 49L, f4 to 108L), f4)
        assertEquals(2, m0.pieces(out, undated, "X", 15, 1))  // 10-10's pieces
        out.listFiles()!!.forEach { it.delete() }  // uploaded
        assertEquals(listOf(f3), m0.done(out))
        val m = Mirror(dir)  // the state as the app left it
        assertTrue("boot 1 again: an earlier boot's", m.reused(1, 1000, now, out, undated, "X"))
        assertEquals(listOf("${old + 300}@X.ogg"), names(out))  // file 4's last frames, dated by 10-10's 'T'
        assertEquals(listOf(0L, 0L, 0L), listOf(m.files.size.toLong(), m.have(f3), m.have(f4)))  // boot 1's files gone
        assertEquals(emptyList<Long>(), m.done(out))  // nothing of the card's to delete
        out.listFiles()!!.forEach { it.delete() }
        val today = t(900, now - 1000) + a(1000, 10) + a(1010, 10)
        assertNull(m.put(f3, 0, today))  // today's file 3 from offset 0
        assertEquals(today.size.toLong(), m.have(f3))  // taken, not passed over as bytes the mirror had
        m.listed(mapOf(f3 to today.size.toLong()), f3)
        assertEquals(1, m.pieces(out, undated, "X", 15, 1))
        assertEquals(listOf("$now@X.ogg"), names(out))  // dated by today's 'T'
        // the same boot later: its start 30 s off the 'T' it wrote (lost frames, drift) is still it
        assertFalse(Mirror(dir).reused(1, 7000, now + 90_000, out, undated, "X"))
        assertEquals(today.size.toLong(), Mirror(dir).have(f3))

        // a boot that began within the margin of the last one (10-13 11:18 and 11:24 were both boot 1): its frames are behind
        val n = Mirror(tmp()); val g = 7L shl 16
        n.put(g, 0, t(5000, old + 50_000) + a(5000, 10))
        assertTrue("frames went back", n.reused(7, 3000, old + 55_000 + 30_000, out, undated, "X"))
        // the same boot, the link gone before the READ reached its 'T' (older firmware: the 'T' at the end of a
        // file recorded for hours): the mirror has its frames and no 'T'. Reconnected, they stay, and are dated once it comes
        val u = Mirror(tmp()); val offline = a(0, 10) + a(10, 10)
        u.put(g, 0, offline)
        assertFalse("no 'T' yet: this boot's", u.reused(7, 360_000, now, out, undated, "X"))
        assertEquals(offline.size.toLong(), u.have(g))  // its frames kept
        u.put(g, offline.size.toLong(), t(359_000, now - 10_000))
        assertFalse("its 'T' agrees", u.reused(7, 360_100, now + 1000, out, undated, "X"))
        assertFalse("boot 0: no count to reuse", u.reused(0, 100, now, out, undated, "X"))
    }

    /** What is said now sent ahead of a backlog still on the card: where its first piece starts, before it is made. */
    @Test fun nextStart() {
        val dir = tmp(); val out = tmp(); val undated = tmp()
        val m = Mirror(dir)
        val old = 1L shl 16 or 1; val open = 1L shl 16 or 2  // a closed file not read yet; the one the stream goes on in
        m.listed(mapOf(old to 72L, open to 85L), open)
        assertNull(m.nextStart(open))  // nothing of the open file yet
        m.put(open, 0, t(100, 5_000_000) + a(100, 10) + a(110, 10))
        assertEquals(5_000_000L, m.nextStart(open))  // its first frame, by its 'T'
        assertEquals(1, m.pieces(out, undated, "X", 15, 1))
        assertEquals(listOf("5000000@X.ogg"), names(out))  // starts where nextStart said
        assertNull(m.nextStart(open))  // all of it in pieces
        m.put(old, 0, a(80, 10) + a(90, 10))  // the backlog, read off the card afterwards
        assertNull(m.nextStart(open))  // older files are not ahead
        assertEquals(4_999_800L, m.nextStart(old))  // dated by the same boot's 'T'
        assertEquals(1, m.pieces(out, undated, "X", 15, 1))
        assertEquals(listOf("4999800@X.ogg", "5000000@X.ogg"), names(out))  // older than what went ahead
    }

    /** A piece appends one line to the state instead of writing all of it again (at 1 s pieces that grew with
     *  every piece of the open file); a restart reads the lines back, finds the 'T' again and goes on where it was. */
    @Test fun stateAppended() {
        val dir = tmp(); val out = tmp(); val undated = tmp()
        val f = 1L shl 16 or 1; val state = File(dir, "state.txt")
        fun key() = Files.readAttributes(state.toPath(), java.nio.file.attribute.BasicFileAttributes::class.java).fileKey()
        val m = Mirror(dir)
        var off = t(0, 1_000_000).size.toLong()
        m.put(f, 0, t(0, 1_000_000))
        m.listed(mapOf(f to off), f)  // as LIST leaves it
        val key = key(); val size = state.length()
        for (s in 0 until 60) {  // a minute in 1 s pieces (the foreground slice), one pieces() a second
            for (i in 0 until 10) { val r = a(s * 100 + i * 10, 10); m.put(f, off, r); off += r.size }
            assertEquals(1, m.pieces(out, undated, "LR-P1-BDAE", 100, 1))
        }
        assertEquals(key, key())  // appended to, never replaced
        assertEquals(true, (state.length() - size) / 60 < 40)  // a line a piece
        val m2 = Mirror(dir)
        assertEquals(m.files[f]!!.cut, m2.files[f]!!.cut)
        assertEquals(m.files[f]!!.pieces, m2.files[f]!!.pieces)
        assertEquals(0, m2.pieces(out, undated, "LR-P1-BDAE", 100, 1))  // nothing made again
        for (i in 0 until 10) { val r = a(6000 + i * 10, 10); m2.put(f, off, r); off += r.size }
        assertEquals(1, m2.pieces(out, undated, "LR-P1-BDAE", 100, 1))
        assertEquals(61, names(out).size)
        assertEquals("1060000@LR-P1-BDAE.ogg", names(out).last())  // dated by the 'T' scanned again
        m2.listed(mapOf(f to off), f)  // the next whole save folds the lines in
        assertEquals(false, state.readText().contains("\nC "))
        assertEquals(61, Mirror(dir).files[f]!!.pieces.size)
    }

    @Test fun undated() {
        val dir = tmp(); val out = tmp(); val undated = tmp()
        val m = Mirror(dir)
        val f = 4L shl 16  // boot 4 lost power before any phone came: no 'T', and boot 5 runs now
        m.put(f, 0, a(0, 10) + a(20, 3))  // two runs (frames 10..19 lost): one undated file
        m.listed(mapOf(f to 51L, (5L shl 16) to 0L, (3L shl 16) to 0L), 5L shl 16)  // boot 3's file: listed, empty
        assertEquals(0, m.pieces(out, undated, "LR-X1-567E", 500, 5))
        assertEquals(1, undated.list()!!.size)
        assertEquals(listOf(3L shl 16, f), m.done(out))  // the empty file too: nothing in it, nothing to wait for
    }

    @Test fun fullList() {
        val m = Mirror(tmp())
        val old = 6L shl 16; val open = 7L shl 16 or 2
        m.put(old, 0, a(0, 10))
        // 64 other files: a full LIST, which may have left out `old` and the open file: neither is closed
        m.listed((0L until 64L).associate { (1L shl 16 or it) to 0L }, open)
        assertEquals(false, m.files[old]!!.closed)
        assertEquals(false, m.files[open]!!.closed)
        m.listed(mapOf(open to 0L), open)  // a short LIST: `old` is gone from the card
        assertEquals(true, m.files[old]!!.closed)
    }

    @Test fun ogg() {
        val v = fixture.getJSONObject("ogg")
        val ogg = Store.oggOpus(List(v.getInt("count")) { hex(v.getString("packetHex")) })
        assertEquals(v.getInt("pages"), Regex("OggS").findAll(String(ogg, Charsets.ISO_8859_1)).count())
    }
}

/** The list names a recorder without its scan response (10-04: a recorder busy with another phone gave none). */
class NameTest {
    @Test fun name() {
        assertEquals("LR-X1-3F2A", Ble.fullName(listOf("LR-X1-3F2A", null), "C0:FF:EE:00:3F:2A"))  // scan response
        assertEquals("LR-X1-3F2A", Ble.fullName(listOf("LR-X1", null), "C0:FF:EE:00:3F:2A"))       // advertisement only
        assertEquals("LR-P1-1A2B", Ble.fullName(listOf(null, "LR-P1-1A2B"), "C0:FF:EE:00:1A:2B"))  // the name cache
        assertEquals(null, Ble.fullName(listOf("Omi", null), "C0:FF:EE:00:1A:2B"))
    }
}

/** The USB disk switch after CTRL 0x06: what the board reports decides, an answer that came late is no failure.
 *  10-13 BDAE: the card lent after more than 15 s, the app said 录音设备没有回应, a second tap took the disk back. */
class UsbDiskTest {
    private fun status(store: Int) = ByteArray(48).also { java.nio.ByteBuffer.wrap(it).order(java.nio.ByteOrder.LITTLE_ENDIAN).putShort(6, store.toShort()) }

    @Test fun note() {
        assertEquals(-16, Ble.store(status(-16)))
        assertEquals(-5, Ble.store(status(-5)))
        val lent = "U 盘模式：电脑上出现 OMI 盘（约 20 秒）；用完先在电脑上推出，再切回"
        assertEquals(lent, Ble.usbNote(true, 0, -16))
        assertEquals(lent, Ble.usbNote(true, null, -16))  // no answer in 15 s, yet the board reports the card lent
        assertEquals("还没切到 U 盘模式（录音设备没有及时回应，卡慢时会晚一些；开关跟着录音设备的状态）", Ble.usbNote(true, null, 0))
        assertEquals(null, Ble.usbNote(false, 0, 0))
        assertEquals(null, Ble.usbNote(false, null, 0))  // back, the answer late
        assertEquals("还在 U 盘模式（录音设备没有及时回应，卡慢时会晚一些；开关跟着录音设备的状态）", Ble.usbNote(false, null, -16))
        assertEquals("录音设备没接在电脑的 USB 上", Ble.usbNote(true, -107, 0))
        assertEquals("切换失败 -19", Ble.usbNote(true, -19, -19))
    }
}
