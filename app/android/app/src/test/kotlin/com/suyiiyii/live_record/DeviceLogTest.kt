package com.suyiiyii.live_record

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.json.JSONObject

/** The event log case of firmware/devkit/lr_sync.py --selftest: boot 7's TIME fell out of the log, boot 8's frames
 *  are dated by its TIME at frame 1000; LOG packets out of order, one sent twice. */
class DeviceLogTest {
    private val T = 1_700_000_000_000L
    private fun r(boot: Int, t: Int, code: Int, x: Int = 0, a: Long = 0, b: Long = 0) = ByteBuffer.allocate(16).order(ByteOrder.LITTLE_ENDIAN)
        .putShort(boot.toShort()).putInt(t).put(code.toByte()).put(x.toByte()).putInt(a.toInt()).putInt(b.toInt()).array()
    private fun pkt(seq: Int, vararg recs: ByteArray) = ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(seq).array() + recs.reduce(ByteArray::plus)

    private val packets = listOf(
        pkt(5003, r(8, 400, 9, 0, 24, 400), r(8, 1000, 14, 0, T and 0xffffffffL, T shr 32)),
        pkt(5000, r(7, 123456, 22, 200, 2048, 7), r(8, 1500, 1 or 0x80, 205, 0x4, 0x1abcdefL or (1L shl 28))),
        pkt(5002, r(8, 3200, 3 or 0x80, 1, -5L and 0xffffffffL)),
        pkt(5003, r(8, 400, 9, 0, 24, 400)),
        pkt(5005, r(8, 2000, 10, 0x13), r(8, 2100, 0x63, 1, 2, 3)))

    @Test fun records() {
        val recs = DeviceLog.records(packets)
        assertEquals((5000L..5006L).toList(), recs.map { it.seq })
        assertEquals(DeviceLog.Rec(5004, 8, 1000, 14, 0, T and 0xffffffffL, T shr 32), recs[4])
        assertEquals(DeviceLog.Rec(5002, 8, 3200, 0x83, 1, 0xfffffffbL, 0), recs[2])  // u32 fields stay unsigned
        assertEquals(0x1abcdefL or (1L shl 28), recs[1].b)
    }

    @Test fun dates() {
        val recs = DeviceLog.records(packets)
        // boot 7 undated; boot 8's uptime records at its frame 0 (T - 10 s); frames 10 ms each from the TIME at 1000
        assertEquals(listOf(null, T - 10_000, T - 10_000, T - 6_000, T, T + 10_000, T + 11_000), DeviceLog.dates(recs, emptyMap()))
        // a later read of the same boot, its TIME known from the read before
        val later = DeviceLog.records(listOf(pkt(5007, r(8, 61_000, 22, 190))))
        assertEquals(listOf(T + 600_000), DeviceLog.dates(later, mapOf(8 to (1000L to T))))
        assertEquals(listOf<Long?>(null), DeviceLog.dates(later, mapOf(7 to (1000L to T))))  // another boot's TIME: no
    }

    /** fixtures/record-stream.json's rate cases: their 'T's as TIME records, then one record at each frame asked,
     *  dated as the pieces are (a boot's rate learned from its TIMEs). */
    @Test fun sharedRate() {
        val fixture = JSONObject(javaClass.getResource("/record-stream.json")!!.readText())
        val cases = fixture.getJSONObject("rate").getJSONArray("cases")
        for (i in 0 until cases.length()) {
            val c = cases.getJSONObject(i); val syncs = c.getJSONArray("syncs"); val at = c.getJSONArray("at")
            val recs = (0 until syncs.length()).map { syncs.getJSONArray(it) }
                .map { Triple(it.getLong(0), 14, it.getLong(1)) } +
                (0 until at.length()).map { Triple(at.getJSONArray(it).getLong(0), 9, 0L) }
            val got = DeviceLog.dates(recs.mapIndexed { n, (t, code, e) ->
                DeviceLog.Rec(n.toLong(), 1, t, code, 0, e and 0xffffffffL, e shr 32) }, emptyMap())
            val want = (0 until at.length()).map { at.getJSONArray(it).getLong(1) }
            assertEquals(c.getString("what"), want, got.takeLast(at.length()))
        }
    }

    @Test fun cursor() {
        val recs = DeviceLog.records(packets)
        assertEquals(5007L, DeviceLog.cursor(recs, 5007))                   // all there
        assertEquals(5002L, DeviceLog.cursor(recs.filter { it.seq != 5002L }, 5007))  // a notify lost: read again from it
        assertEquals(5007L, DeviceLog.cursor(emptyList(), 5007))            // nothing new
    }

    /** A queue past the cap loses its oldest files first, never the newest; under it nothing goes. */
    @Test fun trim() {
        val dir = kotlin.io.path.createTempDirectory().toFile()
        val q = (1..4).map { File(dir, "devlog-$it.send").apply { writeBytes(ByteArray(400)) } }
        assertEquals(0L, DeviceLog.trim(q, 1600))
        assertEquals(800L, DeviceLog.trim(q, 1000))
        assertEquals(listOf(false, false, true, true), q.map { it.exists() })
        assertEquals(400L, DeviceLog.trim(q.drop(2), 0))  // the newest stays, whatever the cap
        assertTrue(q[3].exists())
        dir.deleteRecursively()
    }

    /** A crash as classes and frames, its cause after it; the message (it may carry data) left out. */
    @Test fun trace() {
        val e = IllegalStateException("secret", java.io.IOException("also secret"))
        val t = DeviceLog.trace(e)
        assertTrue(t, t.startsWith("java.lang.IllegalStateException DeviceLogTest.trace:"))
        assertTrue(t, " ← java.io.IOException DeviceLogTest.trace:" in t)
        assertTrue(t, "secret" !in t)
    }
}
