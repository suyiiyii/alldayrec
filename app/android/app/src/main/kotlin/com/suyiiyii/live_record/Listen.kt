package com.suyiiyii.live_record

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTimestamp
import android.media.AudioTrack
import android.media.MediaCodec
import android.media.MediaFormat
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.log10
import kotlin.math.pow
import kotlin.math.roundToInt

/** Listening on the phone: the recorder's LIVE records decoded and played here as they come, a few tenths of a second
 *  behind its mic and no server on the way, to hear what a solder joint, a case or foam does while changing it. A port
 *  of firmware/devkit/lr_listen.py (and of the iPhone's Listen.swift), with its line a second for the second just
 *  played, on the stream's wall clock ('T'): level (RMS), 50 Hz hum (the 50..250 Hz harmonics together), ms in runs of
 *  exact zeros of 10 ms or more (the mic sent nothing), full-scale samples, ms lost on the link, the delay from the mic
 *  to the output (and how much of it is the queue here). It only reads the packets the BLE session takes (Ble.kt), on
 *  its own thread: the mirror, the pieces and their upload go on as before. Listener and Jitter are plain Kotlin
 *  (ListenTest runs them on the JVM).
 *
 *  LIVE notifications (file:u32 offset:u32 bytes) -> each frame's PCM to `play`, which says in how many s it is heard,
 *  and a line a second to `say`. A record comes in MTU pieces. After a gap in the offsets (records left out of LIVE, a
 *  new file, listening just turned on, a link encrypted again: the recorder sends before the app subscribes) the bytes
 *  may begin inside a record: they are taken as a record's start only at offset 0 or where two 'A' records parse in a
 *  row with their frame numbers going on, and what comes before is dropped. Bytes that stop parsing (a record this app
 *  does not know) are dropped the same way once there are more than a record can hold. */
class Listener(private val decode: (ByteArray) -> ShortArray, private val play: (ShortArray) -> Double,
               private val say: (Map<String, Any>) -> Unit) {
    companion object {
        const val RATE = 16000
        const val ZERO = RATE / 100  // a run of exact zeros this long (10 ms) is the mic sending nothing
        const val RECORD_MAX = 6 + 10 * 256  // 'A', 10 frames of up to 255 B

        class Stats(val rms: Double, val hum: Double, val zero: Int, val full: Int, val z: Int)

        /** Samples (a second) -> RMS dBFS, hum dBFS (each harmonic by Goertzel), ms in zero runs of 10 ms or more,
         *  full-scale samples, and the zero run going on at the end (the next second's z). */
        fun stats(pcm: ShortArray, z0: Int): Stats {
            fun db(ms: Double) = 10 * log10(maxOf(ms, 1e-3) / (32768.0 * 32768))  // mean square -> dBFS
            val n = maxOf(pcm.size, 1).toDouble()
            var hum = 0.0
            for (f in 50..250 step 50) {
                val w = 2 * cos(2 * PI * f / RATE)
                var s1 = 0.0; var s2 = 0.0
                for (x in pcm) { val s = x + w * s1 - s2; s2 = s1; s1 = s }
                hum += 2 * (s1 * s1 + s2 * s2 - w * s1 * s2) / n / n
            }
            var z = z0; var zero = 0; var full = 0; var sq = 0.0
            for (x in pcm) {
                z = if (x.toInt() == 0) z + 1 else 0
                zero += if (z == ZERO) ZERO else if (z > ZERO) 1 else 0
                if (abs(x.toInt()) >= 32767) full++
                sq += x.toDouble() * x
            }
            return Stats(db(sq / n), db(hum), zero * 1000 / RATE, full, z)
        }

        /** Where a record begins in `b`: the first 'A' from which two audio records parse with frame numbers going on. */
        fun start(b: ByteArray): Int? = b.indices.firstOrNull { p ->
            b[p] == 'A'.code.toByte() && Store.scan(b.copyOfRange(p, b.size)).map { it.first }.filterIsInstance<Store.Audio>()
                .let { a -> a.size >= 2 && a[0].first + a[0].frames.size == a[1].first }
        }
    }

    private var atFile = -1L; private var atOff = -1L  // where the last piece ended
    private var buf = ByteArray(0); private var aligned = false
    private var sync: Store.Sync? = null; private var next: Long? = null; private var sec: Long? = null  // the latest 'T'; the frame expected next; the second
    private var pcm = ShortArray(0); private val delay = mutableListOf<Double>(); private val queued = mutableListOf<Double>()
    private var lost = 0L; private var z = 0
    // a new decoder's first 30 ms, decoded but not played: it starts inside the stream with no state, and the first frame
    // came out up to 4.5 times too loud from a recording (a click in the ears; a 0.5 sine clipped on the emulator)
    private var warm = 3

    /** One notification, `nowMs` the wall ms it came. */
    fun feed(p: ByteArray, nowMs: Long) {
        if (p.size <= 8) return
        val h = ByteBuffer.wrap(p).order(ByteOrder.LITTLE_ENDIAN)
        val f = h.getInt(0).toLong() and 0xffffffffL; val off = h.getInt(4).toLong() and 0xffffffffL
        if (f != atFile || off != atOff) { buf = ByteArray(0); aligned = off == 0L }
        atFile = f; atOff = off + p.size - 8
        buf += p.copyOfRange(8, p.size)
        if (!aligned) {
            val s = start(buf) ?: run {
                if (buf.size > 2 * RECORD_MAX) buf = buf.copyOfRange(buf.size - RECORD_MAX, buf.size)
                return
            }
            buf = buf.copyOfRange(s, buf.size)
            aligned = true
        }
        var used = 0
        for ((r, end) in Store.scan(buf)) {
            used = end.toInt()
            when (r) {
                is Store.Sync -> sync = r
                is Store.Audio -> r.frames.forEachIndexed { i, o -> frame(r.first + i, o, nowMs) }
            }
        }
        buf = buf.copyOfRange(used, buf.size)
        if (buf.size > RECORD_MAX) aligned = false  // nothing parses any more: look for a start again
    }

    private fun frame(n: Long, opus: ByteArray, nowMs: Long) {
        val x = decode(opus)
        val gap = next?.let { maxOf(n - it, 0) } ?: 0
        next = n + 1
        if (warm > 0) { warm--; return }
        val ahead = play(x)
        val s = sync ?: return  // not on the wall clock yet: heard, not counted
        val t = s.epochMs + (n - s.frame) * Store.FRAME_MS  // when the mic took it
        if (t / 1000 != sec) {
            flush()
            sec = t / 1000
        }
        pcm += x
        lost += gap * Store.FRAME_MS
        delay += (nowMs - t) + ahead * 1000
        queued += ahead * 1000
    }

    /** The line for the second so far: t (its wall ms), rms and hum (dBFS), zero (ms), full (samples), lost, delay and
     *  queued (ms). */
    fun flush() {
        val s = sec ?: return
        val st = stats(pcm, z)
        z = st.z
        fun mean(v: List<Double>) = if (v.isEmpty()) 0 else v.average().roundToInt()
        say(mapOf("t" to s * 1000, "rms" to st.rms, "hum" to st.hum, "zero" to st.zero, "full" to st.full, "lost" to lost,
            "delay" to mean(delay), "queued" to mean(queued)))
        pcm = ShortArray(0); delay.clear(); queued.clear(); lost = 0
    }
}

/** PCM queued for the output: playing (re)starts once PRIME samples are in; past MAX the oldest go (a burst after a
 *  stall; an output clock a little slower than the recorder's: the MacBook's ran 0.3% slow, 10-07). */
class Jitter {
    companion object {
        const val PRIME = Listener.RATE * 15 / 100  // 0.15 s
        const val MAX = Listener.RATE / 4           // 0.25 s
    }
    private var q = ShortArray(0)
    private var on = false

    /** Queues `pcm`; s until it is heard (what is queued before it). */
    @Synchronized fun put(pcm: ShortArray): Double {
        q += pcm
        if (q.size > MAX) q = q.copyOfRange(q.size - MAX, q.size)
        return (q.size - pcm.size).toDouble() / Listener.RATE
    }

    /** `out` filled for the output: silence until PRIME are in, and again after running dry (the link held back). */
    @Synchronized fun take(out: ShortArray) {
        on = on || q.size >= PRIME
        val k = if (on) minOf(out.size, q.size) else 0
        q.copyInto(out, 0, 0, k)
        out.fill(0, k, out.size)
        q = q.copyOfRange(k, q.size)
        on = on && k == out.size
    }
}

/** The recorder as it is heard: `db` louder, as the server plays it (PLAY_GAIN_DB in server/alldayrec/audio.py, the same
 *  12 dB: it comes out 11-13 dB quieter than a bean or a phone; this app cannot read the server's setting). Lower only
 *  where a frame's peaks or its neighbours' would pass -1 dBFS, ramped across each frame: no hard clip, no click; never
 *  below the frame (squeeze.louder's limiter, a frame at a time; Louder in Listen.swift). A frame waits for the next
 *  one's peaks: 10 ms later. The line a second (Listener.stats) stays on the frames as they came: it measures the
 *  recorder. */
class Louder(db: Double = DB) {
    companion object {
        const val DB = 12.0
        const val PEAK = 0.89 * 32768
    }
    private val up = 10.0.pow(db / 20)
    private var held = ShortArray(0); private var before = 0.0; private var last = 0.0; private var g = 1.0  // the frame held; the peaks of the one before it and its own; the gain at its start

    /** A decoded frame in; the frame before it out, louder. */
    fun put(x: ShortArray): ShortArray {
        val p = (x.maxOfOrNull { abs(it.toInt()) } ?: 0).toDouble()
        val end = (PEAK / maxOf(before, last, p, 1.0)).coerceAtMost(up).coerceAtLeast(1.0)  // the held frame's gain at its end
        val n = held.size.toDouble()
        val out = ShortArray(held.size) { i -> (held[i] * (g + (end - g) * (i + 1) / n)).roundToInt().coerceIn(-32768, 32767).toShort() }
        held = x; before = last; last = p; g = end
        return out
    }
}

/** The system's Opus decoder (MediaCodec audio/opus) for the recorder's frames, one at a time. AOSP's gives 48 kHz:
 *  back to 16 kHz by keeping every third sample (the stream holds nothing above 8 kHz). */
class OpusDecoder {
    private val c = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS).apply { configure(Ble.opusFormat(0), null, null, 0); start() }
    private val info = MediaCodec.BufferInfo()
    private var step = 1; private var phase = 0; private var pts = 0L

    fun decode(p: ByteArray): ShortArray {
        val i = c.dequeueInputBuffer(20_000)
        if (i < 0) return ShortArray(0)
        c.getInputBuffer(i)!!.apply { clear(); put(p) }
        c.queueInputBuffer(i, 0, p.size, pts, 0)
        pts += Store.FRAME_MS * 1000
        var out = ShortArray(0)
        var wait = 20_000L  // this frame's samples: the codec decodes on a thread of its own
        while (true) {
            val o = c.dequeueOutputBuffer(info, wait)
            if (o == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                step = c.outputFormat.getInteger(MediaFormat.KEY_SAMPLE_RATE) / Listener.RATE
                android.util.Log.i("live-record", "listen decoder ${c.name}: ${c.outputFormat}")
                continue
            }
            if (o < 0) return out
            val s = c.getOutputBuffer(o)!!.order(ByteOrder.nativeOrder()).asShortBuffer()
            val all = ShortArray(s.remaining()).also { s.get(it) }
            c.releaseOutputBuffer(o, false)
            val first = (step - phase) % step
            out += ShortArray(maxOf(0, (all.size - first + step - 1) / step)) { all[first + it * step] }
            phase = (phase + all.size) % step
            wait = 0
        }
    }

    fun release() { c.stop(); c.release() }
}

/** Listening switched on (the recorder page, the app in front): an AudioTrack on the output the person has (media, low
 *  latency; no audio focus taken: other audio goes on, as on the iPhone) fed from a Jitter by a thread of its own, the
 *  decoder and a Listener on another; off: all of it goes. */
class Listen {
    @Volatile var lines: List<Map<String, Any>> = emptyList()  // the last 20 s
    private val jitter = Jitter()
    private val louder = Louder()
    private val dec = OpusDecoder()
    private val exec = Executors.newSingleThreadExecutor()
    private val track = AudioTrack.Builder()
        .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
        .setAudioFormat(AudioFormat.Builder().setSampleRate(Listener.RATE).setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
        .setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY).setTransferMode(AudioTrack.MODE_STREAM).build()
    @Volatile private var on = true
    @Volatile private var written = 0L
    private val writer = Thread {
        val b = ShortArray(Listener.RATE / 100)
        while (on) {
            jitter.take(b)
            if (track.write(b, 0, b.size) < 0) break  // a blocking write (the output sets the pace); < 0: the track is dead, the lines go on
            written += b.size
        }
    }
    private val listener = Listener(dec::decode, { x ->  // the frame goes in after the one Louder held: heard that much later
        val y = louder.put(x)
        jitter.put(y) + y.size.toDouble() / Listener.RATE + out()
    }, { lines = (lines + it).takeLast(20); android.util.Log.i("live-record", "listen $it") })

    init {
        track.play()
        writer.start()
    }

    /** s from what is written to the track to the output: its buffer and the device's own latency. */
    private fun out(): Double {
        val ts = AudioTimestamp()
        if (!track.getTimestamp(ts)) return track.bufferSizeInFrames.toDouble() / Listener.RATE
        return (written - ts.framePosition - (System.nanoTime() - ts.nanoTime) * Listener.RATE / 1e9) / Listener.RATE
    }

    /** A LIVE notification as it came (on the Bluetooth callback thread). */
    fun feed(p: ByteArray) {
        val now = System.currentTimeMillis()
        try { exec.execute { listener.feed(p, now) } } catch (_: java.util.concurrent.RejectedExecutionException) {}  // stopped meanwhile
    }

    fun stop() {
        on = false
        exec.shutdown()
        exec.awaitTermination(2, TimeUnit.SECONDS)
        writer.join(1000)
        track.stop(); track.release()
        dec.release()
    }
}
