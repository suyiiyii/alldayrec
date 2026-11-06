package com.suyiiyii.live_record

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.log10
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.sin

/** Listening on the phone (Listen.kt), the case of the iPhone's StoreCheck and lr_listen.py's selftest with a stand-in
 *  codec (a frame's "Opus" is its index: the decoder gives back those 160 samples exactly). 3 s: hum (50 Hz at -12 dBFS
 *  + 150 Hz at -24 dBFS), from 1.2 s 50 ms of nothing from the mic, from 2.3 s 20 ms clipped. The real decoder
 *  (MediaCodec) and output need a device: run on the emulator against a fake recorder (PR). */
class ListenTest {
    private val pcm = ShortArray(3 * Listener.RATE) { i ->
        when {
            i in 19200 until 20000 -> 0
            i in 36800 until 37120 -> if (i % 64 < 32) 32767 else -32767
            else -> (0.25 * 32767 * sin(2 * PI * 50 * i / 16000) + 0.0625 * 32767 * sin(2 * PI * 150 * i / 16000)).roundToInt()
        }.toShort()
    }
    private fun frame(k: Int) = pcm.copyOfRange(k * 160, (k + 1) * 160)
    private fun frames(r: IntRange) = r.map(::frame).fold(ShortArray(0)) { a, b -> a + b }
    private fun le(n: Int, fill: ByteBuffer.() -> Unit) = ByteBuffer.allocate(n).order(ByteOrder.LITTLE_ENDIAN).apply(fill).array()
    /** Frames of 2..8 B (as Opus's vary), or 2 + pad. */
    private fun rec(first: Int, pad: Int? = null) = le(6) { put('A'.code.toByte()); put(10); putInt(first) } +
        (first until first + 10).map { k -> val n = pad ?: (k % 7); le(3) { put((2 + n).toByte()); putShort((k - 1000).toShort()) } + ByteArray(n) { 0xAA.toByte() } }
            .reduce { a, b -> a + b }
    private fun decode(d: ByteArray) = frame(ByteBuffer.wrap(d).order(ByteOrder.LITTLE_ENDIAN).getShort(0).toInt())
    private val epoch = 1_791_000_000_000L  // a whole second

    /** Frames 1000.. in 'A' records of 10, a 'T' (frame 1030 was 10:00:00.300) after the third, sent in 20 B pieces;
     *  listening begins inside the second record (the first bytes it gets are mid-record); of frames 1240..1249 only the
     *  first piece goes (a notify that failed), from 1200 a new file. */
    @Test fun listener() {
        val recs = (0 until 30).map { 1000 + it * 10 to rec(1000 + it * 10) }.toMutableList()
        recs.add(3, 1030 to le(13) { put('T'.code.toByte()); putInt(1030); putLong(epoch + 300) })
        val sent = mutableListOf<Pair<ByteArray, Long>>()
        var f = 7; var off = 0
        for ((first, b) in recs) {
            if (first == 1200 && b[0] == 'A'.code.toByte()) { f = 8; off = 0 }
            val now = epoch + (first - 1000 + 10) * 10L + 20  // as a record arrives: 20 ms after its last frame
            for (i in 0 until (if (first == 1240) 1 else b.size) step 20)
                sent += (le(8) { putInt(f); putInt(off + i) } + b.copyOfRange(i, minOf(i + 20, b.size))) to now
            off += b.size
        }
        val begin = sent.indexOfFirst { ByteBuffer.wrap(it.first).order(ByteOrder.LITTLE_ENDIAN).getInt(4) > rec(1000).size } + 1  // inside record 1010
        var played = ShortArray(0)
        val lines = mutableListOf<Map<String, Any>>()
        val l = Listener(::decode, { played += it; 0.1 }, { lines += it })  // 0.1 s queued ahead of each frame
        for ((p, now) in sent.drop(begin)) l.feed(p, now)
        l.flush()
        assertArrayEquals("played from record 1020 on (its first 30 ms warm the decoder), but for the lost record",
            frames(23 until 240) + frames(250 until 300), played)
        assertEquals("a line a second on the wall clock", listOf(epoch, epoch + 1000, epoch + 2000), lines.map { it["t"] })
        fun num(k: String) = lines.map { (it[k] as Number).toDouble() }
        assertTrue("hum: 50+150 Hz at -14.8 dBFS RMS, ${num("hum")}", abs(num("hum")[0] + 14.8) < 0.3 && abs(num("rms")[0] - num("hum")[0]) < 0.2)
        assertEquals("50 ms of nothing from the mic", listOf(0.0, 50.0, 0.0), num("zero"))
        assertEquals("20 ms clipped", listOf(0.0, 0.0, 320.0), num("full"))
        assertEquals("the record lost on the link", listOf(0.0, 0.0, 100.0), num("lost"))
        // 130..220 ms over a record's frames; after the gap, record 1250 waits for 1260 (two in a row say where records
        // begin): its frames 100 ms later
        assertEquals(listOf(175.0, 175.0, 186.0), num("delay"))
        assertEquals("the queue's part", listOf(100.0, 100.0, 100.0), num("queued"))
    }

    /** A record type this app does not know (in a firmware after it): its bytes are dropped, listening goes on after it
     *  (once more is waiting than a record can hold: 64 B frames here, 4 KB/s from a recorder). */
    @Test fun unknownRecord() {
        val b = rec(1000) + rec(1010) + byteArrayOf(0x4D) + ByteArray(100) { 1 } + (2 until 30).map { rec(1000 + it * 10, 62) }.reduce { a, c -> a + c }
        var played = ShortArray(0)
        val u = Listener(::decode, { played += it; 0.0 }, {})
        for (at in b.indices step 200) u.feed(le(8) { putInt(9); putInt(at) } + b.copyOfRange(at, minOf(at + 200, b.size)), 0)
        assertArrayEquals("before the unknown record", frames(3 until 20), played.copyOfRange(0, 17 * 160))
        assertArrayEquals("after it", frames(150 until 300), played.copyOfRange(played.size - 150 * 160, played.size))
    }

    /** The output's queue: silence until 0.15 s are in, the oldest go past 0.25 s, silence again once it ran dry. */
    @Test fun jitter() {
        val j = Jitter()
        assertEquals("first in: heard at once (once playing)", 0.0, j.put(ShortArray(2000) { 16384 }), 0.0)
        val out = ShortArray(100)
        j.take(out)
        assertEquals("not 0.15 s in yet: silence", 0, out[0].toInt())
        assertEquals("queued ahead of it", 0.125, j.put(ShortArray(400) { 8192 }), 0.0)
        j.take(out)
        assertEquals("0.15 s in: playing", 16384, out[0].toInt())
        assertEquals("past 0.25 s the oldest go", (Jitter.MAX - 2000) / 16000.0, j.put(ShortArray(2000) { 1 }), 0.0)
        val all = ShortArray(4000)
        j.take(all)
        assertEquals("the oldest 300 went: 1600 + 400 + 2000 left", listOf(16384, 8192, 1, 1), listOf(all[1599], all[1600], all[2000], all[3999]).map { it.toInt() })
        j.put(shortArrayOf(7))
        val two = ShortArray(2)
        j.take(two)
        assertEquals("the last sample, then it ran dry", listOf(7, 0), two.map { it.toInt() })
        j.put(shortArrayOf(7))
        j.take(two)
        assertEquals("ran dry: waits for 0.15 s again", 0, two[0].toInt())
    }

    /** Heard louder (Louder): 12 dB up a frame later, held under -1 dBFS where it is loud, full scale left as it is. */
    @Test fun louder() {
        fun sine(db: Double, k: Int) = ShortArray(160) { i -> (sin(2 * PI * 440 * (k * 160 + i) / 16000) * 32767 * 10.0.pow(db / 20)).roundToInt().toShort() }
        fun rms(x: ShortArray) = 10 * log10(x.sumOf { it.toDouble() * it } / x.size / (32768.0 * 32768))
        fun run(frames: List<ShortArray>): ShortArray { val l = Louder(); return (frames + listOf(ShortArray(0))).map(l::put).reduce { a, b -> a + b } }
        fun join(frames: List<ShortArray>) = frames.reduce { a, b -> a + b }
        val quiet = (0 until 100).map { sine(-40.0, it) }
        val y = run(quiet)
        assertEquals("every frame out, a frame later", 100 * 160, y.size)
        assertEquals("-40 dBFS: 12 dB up (the first frame ramps up)", 12.0, rms(y.copyOfRange(160, y.size)) - rms(join(quiet.drop(1))), 0.05)
        val shout = quiet.toMutableList().also { it[50] = sine(-3.0, 50) }  // a shout in it: held under -1 dBFS, the rest 12 dB up
        val z = run(shout)
        assertTrue("under -1 dBFS: ${z.maxOf { abs(it.toInt()) }}", z.maxOf { abs(it.toInt()) } <= Louder.PEAK + 1)
        assertEquals("after it: 12 dB up again", 12.0, rms(z.copyOfRange(60 * 160, z.size)) - rms(join(shout.drop(60))), 0.05)
        val full = (0 until 20).map { sine(0.0, it) }
        assertArrayEquals("full scale: as it came, not clipped further", join(full), run(full))
        assertArrayEquals("short and empty frames: 12 dB up", shortArrayOf(20, -20), run(listOf(ShortArray(0), shortArrayOf(5, -5), ShortArray(0))))
        val off = Louder(0.0)
        assertArrayEquals("0 dB: as it came", shortArrayOf(100), off.put(shortArrayOf(100)) + off.put(ShortArray(0)))
    }
}
