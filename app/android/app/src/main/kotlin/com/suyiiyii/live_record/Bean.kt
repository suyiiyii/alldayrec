package com.suyiiyii.live_record

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.math.BigInteger
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.KeyFactory
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.MessageDigest
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECPoint
import java.security.spec.ECPublicKeySpec
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyAgreement
import javax.crypto.Mac
import javax.crypto.spec.IvParameterSpec
import javax.crypto.spec.SecretKeySpec

/** Timing is diagnostic only: storage/logging failures must not change protocol or cancellation outcomes. */
internal fun beanTiming(block: () -> Unit) { try { block() } catch (_: Exception) {} }

/*
 * The Feishu recorder bean (Anker soundcore D3200, firmware 4.92): its BLE protocol, as bytes in and bytes out (no
 * Android Bluetooth here: BeanLink.kt carries the bytes, the unit tests run BeanSync against a fake bean).
 * Ported from ws/soundcore (0BSD, Will Smidlein) and Eliqi797/open-feishu-recording-bean (MIT, Copyright (c) 2026
 * Eliqi797); see THIRD_PARTY_NOTICES.md. Not affiliated with Anker or Feishu.
 */
object Bean {
    val SERVICE: UUID = UUID.fromString("020cf5da-0000-1000-8000-00805f9b34fb")
    val WRITE: UUID = UUID.fromString("00007777-0000-1000-8000-00805f9b34fb")
    val NOTIFY: UUID = UUID.fromString("00008888-0000-1000-8000-00805f9b34fb")
    const val UNIT = 166        // a transport unit: seq u32 LE, flags, 160 B of encrypted Opus, a pad byte
    const val FRAME_MS = 20L    // the Opus packet in a unit: 20 ms of 16 kHz stereo, 160 B (64 kbps CBR)
    const val PIECE_UNITS = 3000  // a backlog piece: 60 s
    const val MAX_SEQ = 429_496_729L  // seq × 10 still fits the counter's 32 bits

    /** The commands a bean is sent: its info (01/01), its clock (01/A6), the key exchange (2E/01), its files (1A/0E),
     *  a file's audio (1A/07); a delete (1A/10, DELETE) only of a file the server has whole (delete()); and the start of
     *  a recording (18/82 01, START: start()), never its stop (00) or pause (02). Nothing that binds or unbinds (0B/xx),
     *  turns on Wi-Fi (1A/05), resets, updates firmware (FF/xx, and never the OTA service) or reads device logs. */
    val SENDS = setOf(0x0101, 0x01A6, 0x2E01, 0x1A0E, 0x1A07)
    const val DELETE = 0x1A10
    const val START = 0x1882

    fun isBean(name: String?) = name != null && Regex("D3200-[0-9A-F]{4}").matches(name)

    /** A bean's name from its advertisement's manufacturer data (company id and payload, as the SDK reads them: one
     *  buffer, the bean's MAC first): the address it advertises from changes, its MAC does not. */
    fun name(company: Int, payload: ByteArray): String? {
        val b = byteArrayOf(company.toByte(), (company shr 8).toByte()) + payload
        return if (b.size < 6) null else "D3200-%02X%02X".format(b[4], b[5])
    }

    /** The advertisement's flags byte (bit 7: bound to a Feishu account), -1 without one. */
    fun flags(company: Int, payload: ByteArray): Int = payload.getOrNull(4)?.toInt()?.and(255) ?: -1

    //
    // Frames: phone to bean 08 EE 00 00 00 type id len16 payload sum8, bean to phone 09 FF 00 00 status type id len16
    // payload sum8; len counts the whole frame, sum8 every byte before it
    //

    class Frame(val type: Int, val id: Int, val status: Int, val payload: ByteArray) {
        val cmd get() = type shl 8 or id
        /** Answered as done: status 1 (device commands), else its low nibble 1. */
        val ok get() = if (type == 0x01) status == 1 else status and 0x0F == 1
    }

    fun frame(type: Int, id: Int, payload: ByteArray = ByteArray(0)): ByteArray {
        require(type shl 8 or id in SENDS) { "%02X/%02X is never sent to a bean".format(type, id) }
        return build(type, id, payload)
    }

    private fun build(type: Int, id: Int, payload: ByteArray): ByteArray {
        val n = payload.size + 10
        val b = byteArrayOf(0x08, 0xEE.toByte(), 0, 0, 0, type.toByte(), id.toByte(), n.toByte(), (n shr 8).toByte()) + payload
        return b + sum(b, 0, b.size)
    }

    /** A frame on its way to the radio is one of SENDS, the delete of a file in `confirmed`, or the start: checked again
     *  right before the write (BeanSync.send, BeanLink.write). */
    fun allowed(f: ByteArray, confirmed: Set<Long> = emptySet()) = f.size >= 10 && f[0] == 0x08.toByte() && f[1] == 0xEE.toByte() &&
        ((f[5].toInt() and 255) shl 8 or (f[6].toInt() and 255)).let { it in SENDS || it == DELETE && f.size == 14 && u32(f, 9) in confirmed ||
            it == START && f.size == 11 && f[9] == 1.toByte() } &&
        u16(f, 7) == f.size && f.last() == sum(f, 0, f.size - 1)

    private fun sum(b: ByteArray, from: Int, to: Int): Byte {
        var s = 0
        for (i in from until to) s += b[i]
        return s.toByte()
    }

    fun info() = frame(0x01, 0x01)
    /** The bean's clock: unix seconds and the time zone's hours; its file ids are its clock as each file began. */
    fun clock(unixS: Long, tzHours: Int) = frame(0x01, 0xA6, le32(unixS) + tzHours.toByte())
    fun hello(publicKey: ByteArray) = frame(0x2E, 0x01, publicKey)
    fun list(page: Int) = frame(0x1A, 0x0E, u16le(page))
    /** A file's audio from `offset` bytes on (whole units); realtime: the file being recorded, as it is. */
    fun transfer(file: Long, offset: Long, realtime: Boolean) = frame(0x1A, 0x07, le32(offset) + le32(file) + (if (realtime) 1 else 0).toByte())
    /** Deletes a file on the bean: only one in `confirmed`, the files the server has whole (BeanSync.deletable). */
    fun delete(file: Long, confirmed: Set<Long>): ByteArray {
        require(file in confirmed) { "file $file is not whole on the server: never deleted" }
        return build(0x1A, 0x10, le32(file))
    }
    /** Starts recording, as a press of the button does when the bean is idle, but silently (10-25: no vibration). */
    fun start() = build(0x18, 0x82, byteArrayOf(1))

    /** Frames out of notifications: one may span several, junk before one is skipped, as is a frame whose sum is off. */
    class Reader {
        private var buf = ByteArray(0)
        fun push(b: ByteArray): List<Frame> {
            buf += b
            val out = mutableListOf<Frame>()
            var i = 0
            while (buf.size - i >= 10) {
                val n = u16(buf, i + 7)
                if (buf[i] != 0x09.toByte() || buf[i + 1] != 0xFF.toByte() || buf[i + 2] != 0.toByte() || buf[i + 3] != 0.toByte() || n < 10) { i++; continue }
                if (buf.size - i < n) break
                if (sum(buf, i, i + n - 1) != buf[i + n - 1]) { i++; continue }
                out += Frame(buf[i + 5].toInt() and 255, buf[i + 6].toInt() and 255, buf[i + 4].toInt() and 255, buf.copyOfRange(i + 9, i + n - 1))
                i += n
            }
            buf = buf.copyOfRange(i, buf.size)
            return out
        }
    }

    //
    // Keys: P-256 ECDH, the session key by HKDF-SHA256 (salt and info 01 02 03), each file's key wrapped in it (AES-CTR)
    //

    fun keys(): KeyPair = KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair()

    /** An uncompressed P-256 point: 04, X, Y. */
    fun pub(k: ECPublicKey): ByteArray = byteArrayOf(4) + fixed(k.w.affineX) + fixed(k.w.affineY)
    private fun fixed(n: BigInteger) = n.toByteArray().takeLast(32).toByteArray().let { ByteArray(32 - it.size) + it }

    fun point(b: ByteArray, like: ECPublicKey): ECPublicKey {
        require(b.size >= 65 && b[0] == 4.toByte()) { "not a P-256 point" }
        return KeyFactory.getInstance("EC").generatePublic(ECPublicKeySpec(ECPoint(BigInteger(1, b.copyOfRange(1, 33)),
            BigInteger(1, b.copyOfRange(33, 65))), like.params)) as ECPublicKey
    }

    fun shared(mine: KeyPair, theirs: ECPublicKey): ByteArray =
        KeyAgreement.getInstance("ECDH").apply { init(mine.private); doPhase(theirs, true) }.generateSecret()

    /** The session key from the bean's answer to hello: its public key (65 B) and the secret it got (32 B), ours too. */
    fun session(mine: KeyPair, reply: ByteArray): ByteArray {
        require(reply.size >= 97) { "key exchange answer of ${reply.size} B" }
        val s = shared(mine, point(reply, mine.public as ECPublicKey))
        require(MessageDigest.isEqual(s, reply.copyOfRange(65, 97))) { "the bean's shared secret is not ours" }
        return hkdf(s)
    }

    fun hkdf(shared: ByteArray): ByteArray = hmac(hmac(byteArrayOf(1, 2, 3), shared), byteArrayOf(1, 2, 3, 1))
    private fun hmac(key: ByteArray, v: ByteArray) = Mac.getInstance("HmacSHA256").run { init(SecretKeySpec(key, "HmacSHA256")); doFinal(v) }

    fun ctr(key: ByteArray, iv: ByteArray, b: ByteArray): ByteArray =
        Cipher.getInstance("AES/CTR/NoPadding").run { init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), IvParameterSpec(iv)); doFinal(b) }

    private val MAGIC = "soundcored3200".toByteArray()

    /** A file's audio key: unwrapped with the session key, behind a fixed text. */
    fun fileKey(session: ByteArray, sessionNonce: ByteArray, wrapped: ByteArray): ByteArray {
        val k = ctr(session, sessionNonce, wrapped)
        require(k.size == MAGIC.size + 32 && k.copyOfRange(0, MAGIC.size).contentEquals(MAGIC)) { "the file key does not unwrap" }
        return k.copyOfRange(MAGIC.size, k.size)
    }

    /** A unit's counter: the file's nonce (12 B), then seq × 10 (each unit is ten AES blocks). */
    fun iv(nonce: ByteArray, seq: Long): ByteArray {
        require(seq in 0..MAX_SEQ) { "unit $seq out of range" }
        return nonce.copyOf(12) + ByteBuffer.allocate(4).putInt((seq * 10).toInt()).array()
    }

    //
    // What the bean says
    //

    /** A 1A/07 answer: the file, its bytes (0xFFFFFFFF: still recording), its nonce, its key (none when code is not 0:
     *  1 no such file, 2 the bean could not read it, 3 nothing left from that offset). */
    class Header(val file: Long, val size: Long, val nonce: ByteArray, val code: Int, val key: ByteArray?)

    fun header(session: ByteArray, p: ByteArray): Header {
        require(p.size >= 87) { "file header of ${p.size} B" }
        val code = p[86].toInt() and 255
        val key = if (code == 0 || code == 255) fileKey(session, p.copyOfRange(70, 86), p.copyOfRange(24, 70)) else null
        return Header(u32(p, 0), u32(p, 4), p.copyOfRange(8, 24), if (code == 255) 0 else code, key)
    }

    /** flags: 0x01 a double-tap mark, 0x02 the file's first unit, 0x04 its last. */
    class Slice(val seq: Long, val flags: Int, val data: ByteArray)

    fun slices(p: ByteArray): List<Slice> {
        require(p.size % UNIT == 0) { "audio of ${p.size} B is not whole units" }
        return (p.indices step UNIT).map { Slice(u32(p, it), p[it + 4].toInt() and 255, p.copyOfRange(it + 5, it + 165)) }
    }

    fun opus(key: ByteArray, nonce: ByteArray, s: Slice) = ctr(key, iv(nonce, s.seq), s.data)

    /** 01/01: battery (a level 0-9, or a percent above), charging, firmware, card, the case, recording now. */
    fun info(p: ByteArray): Map<String, Any?> {
        require(p.size >= 45) { "device info of ${p.size} B" }
        return mapOf("model" to "D3200", "battery" to pct(p[1]), "charge" to if (p[2] == 1.toByte()) "charging" else "battery",
            "fw" to ascii(p, 3, 8), "total_kb" to u32(p, 24), "free_kb" to u32(p, 28), "case_battery" to pct(p[38]),
            "case_charging" to (p[32] == 1.toByte()), "recording" to (p.getOrNull(50) == 1.toByte()))
    }

    fun serial(p: ByteArray) = ascii(p, 8, 24).lowercase()
    fun pct(b: Byte) = (b.toInt() and 255).let { if (it <= 9) (it + 1) * 10 else minOf(it, 100) }
    private fun ascii(p: ByteArray, from: Int, to: Int) = String(p, from, to - from, Charsets.US_ASCII).trimEnd('\u0000', ' ')

    /** 1A/0E, a page of the bean's files: (id, ms) each, and the file being recorded (0: none). */
    class Listing(val files: List<Pair<Long, Long>>, val open: Long)

    fun listing(p: ByteArray): Listing {
        if (p.isEmpty()) return Listing(emptyList(), 0)
        val n = u16(p, 0)
        require(p.size >= 2 + n * 8 + 4) { "file list of ${p.size} B for $n files" }
        return Listing((0 until n).map { u32(p, 2 + it * 8) to u32(p, 6 + it * 8) }, u32(p, 2 + n * 8))
    }

    fun u32(b: ByteArray, i: Int) = ByteBuffer.wrap(b, i, 4).order(ByteOrder.LITTLE_ENDIAN).int.toLong() and 0xFFFFFFFFL
    fun u16(b: ByteArray, i: Int) = (b[i].toInt() and 255) or ((b[i + 1].toInt() and 255) shl 8)
    fun le32(v: Long) = ByteArray(4) { (v ushr (8 * it)).toByte() }
    fun u16le(v: Int) = byteArrayOf(v.toByte(), (v shr 8).toByte())
    fun hex(b: ByteArray) = b.joinToString("") { "%02x".format(it) }
}

/**
 * One bean, session after session (BeanLink connects, then calls session()): the key exchange, its info and clock, its
 * files, its start when idle (start(), once a connection), then one transfer at a time, as the bean allows no more
 * and cannot be stopped mid-file: the file being recorded, from where the phone lacks it on to live (plan()), else the
 * oldest file this phone lacks units of (the backlog, files begun after the bean was added here: `since`). Units go
 * into dated Ogg pieces in `chunks` (the Uploader's queue).
 *
 * Per file (`state`, JSON, kept after every piece): when its unit 0 was on the phone's clock (its anchor: the file id,
 * the bean's clock as it began, set at each connect; the phone's own clock when the file is first met by a realtime=1
 * read and the two agree within seconds), the anchors it was dated by anew while live (reanchor(): the bean's units do
 * not keep the phone's time) and which units are in pieces already. A unit's time is the anchor in force at its seq +
 * seq × 20 ms; a transfer after a drop starts at the first unit missing (its byte offset), units had already are dropped, a
 * hole is asked for again (three times); when a long stretch the phone has follows it, the session ends once the hole is in and the
 * next hole is asked for from its own offset (rest()), not by taking that stretch again. A pause inside a file dates
 * what follows it early until the next anchor (the app never pauses the bean): logged as bean_gap_time. Live transfers never hold
 * finals; closed files are fetched only after recording stops. The end of the last recording goes to the server once
 * its pieces are up. A file the server has whole is then deleted on the
 * bean (prune()); nothing else is.
 */
class BeanSync(private val tag: String, private val stateFile: File, private val chunks: File, private val io: Io) {
    interface Io {
        fun send(frame: ByteArray)
        /** The next notification, null after `ms` without one; throws once the link is gone. */
        fun receive(ms: Long): ByteArray?
        fun now(): Long
        fun monotonicNow(): Long = System.nanoTime() / 1_000_000
        /** Units in a piece while recording live: the app's slice. */
        fun liveUnits(): Int
        /** The live page is open: the latest upload's answer said so (slice.watched) and is under a minute old. */
        fun watched(): Boolean
        fun log(kind: String, text: String, level: String? = null, fields: Map<String, Any?> = emptyMap(), at: Long? = null)
        fun report(hw: Map<String, Any?>)
        /** prefs hold:<tag>, sent with every upload while set (Uploader); null: let go. */
        fun hold(at: Long?)
        fun held(): Long?
        /** The bean's last recording ended at `at` (wall ms) and all of it is up: the server is told (BeanLink: kept in the
         *  prefs until it takes it). */
        fun end(at: Long)
        fun piece()
        /** Starts of this bean's pieces the server could not decode (Uploader.answer BAD_AUDIO): their files stay on the bean. */
        fun refused(): Set<Long>
        /** A debug capture of the session (off unless asked for: BeanLink). */
        fun trace(what: String, v: Map<String, Any?>) {}
        /** The phone's side of a slow link, for bean_lag and too_slow (diag()): its audio route, a call, power, front. */
        fun phoneState(): Map<String, Any?> = emptyMap()
    }

    /** anchors: (seq, anchor), from that unit on the file is dated by that anchor instead (step()), in order. */
    class F(var anchor: Long, val have: MutableList<LongArray> = mutableListOf(), var total: Long = -1, var tries: Int = 0,
            val anchors: MutableList<LongArray> = mutableListOf()) {
        /** A unit's time on the phone's clock: by the anchor in force at its seq. */
        fun at(seq: Long) = (anchors.lastOrNull { it[0] <= seq }?.get(1) ?: anchor) + seq * Bean.FRAME_MS
        val latest get() = anchors.lastOrNull()?.get(1) ?: anchor
    }

    /** live: the file being recorded (pieces of the app's slice, the stall guard); realtime: the wire's flag (plan());
     *  watched: begun for the live page (tail_short, live_now): ended when the page closes or opens (loop()). */
    private class Xfer(val file: Long, val live: Boolean, val from: Long, val realtime: Boolean = false, val watched: Boolean = false) {
        var until = -1L  // the phone has the rest of the file from here: the link goes once this unit is in (rest())
        var cut = false
        var key: ByteArray? = null
        var nonce = ByteArray(16)
        var first = -1L
        var last = -1L
        var units = 0
        var dupes = 0
        var lost = 0L
        var at = 0L
        var seen = 0L  // when its last unit came
        var firstAt = 0L  // when its first unit came (pace)
        var half = -1L  // reanchor(): this half minute's first seq and fastest, the last one's
        var fastest = Long.MAX_VALUE
        var before: Long? = null
        var target: Long? = null  // the anchor the units' timing says, live (reanchor()); pieces go there a step at a time (step())
        var clockSaid = 0L  // when bean_clock was last logged
        var resent = false  // live, the bean went back in the file: what comes after is late by the resend, no clock
        var lag: Long? = null  // live, once within 10 s: how long after its end its last unit came (report(), bean_lag)
        var gainAt = 0L  // slow(): this minute's start, how far behind then and its last unit
        var gainBehind = 0L
        var gainLast = 0L
    }

    @Volatile var state: Map<String, Any?> = say("encrypting")
    // Immutable status snapshots; free_kb stays at the last 01/01 reading while a transfer is live.
    @Volatile var hw: Map<String, Any?> = emptyMap()
    @Volatile var syncDoneKb = 0L
    @Volatile var syncTotalKb = 0L
    /** What of the bean's recording this phone lacks, for the status and its reports (owe()). */
    @Volatile var owed: Map<String, Any?> = emptyMap()
    var recording: Boolean
        get() = hw["recording"] == true
        private set(value) { hw = hw + ("recording" to value) }
    /** The file being deleted, while it is: the one id a 1A/10 may carry (Bean.allowed). */
    @Volatile var deletable: Set<Long> = emptySet()
        private set
    private val kept = mutableSetOf<Long>()  // a delete the bean did not confirm: not asked again until the app restarts
    val files = sortedMapOf<Long, F>()
    private var key = ByteArray(0)
    private var reader = Bean.Reader()
    private val inbox = ArrayDeque<Bean.Frame>()
    private var listing = Bean.Listing(emptyList(), 0)
    private var needList = true
    private var listedAt = 0L
    private var askedAt = 0L
    private var liveAgain = 0L   // when a refused realtime transfer is tried again
    private var quietUntil = 0L  // no 1A/07 before: the bean has just ended a transfer (code 4 when asked at once)
    private var busy = 0         // code 4s in a row
    private var paused = false  // only the bean's button state can suspend the live silence guard
    private var liveFile = 0L
    private var liveSeen = 0L   // includes waiting for a header and gaps between code-4 retries
    private var liveStalled = false  // one warning until audio resumes, across reconnects
    private var lagging = false  // live audio over a minute behind: one warning until it is back within 10 s
    private var xfer: Xfer? = null
    private var since = 0L
    private var pendingEnd = 0L  // kept in the state file: a kill before the pieces are up does not lose it (complete()
                                 // is not run again for a file that is whole)
    private var caught = 0L      // backlog units taken since the backlog began
    private var caughtSince = 0L
    private var pace = 2.0  // a realtime=0 read's speed, times real time: the last one measured (10-27 probe: about 2)
    private var marked = false
    private val unknown = mutableSetOf<Int>()
    private val run = mutableListOf<ByteArray>()
    private val fills = mutableMapOf<Long, Int>()  // realtime=0 reads of the open file that came to nothing (missed())
    private var runFile = -1L
    private var runFrom = 0L
    private var startDone = false  // this connection's start is settled (sent, the bean was recording, or its button was
                                   // pressed): a stop by hand holds until the link goes (an IOException), not across the
                                   // app's own reconnects (a cut re-pull, a stalled transfer)

    init {
        chunks.mkdirs()
        if (stateFile.exists()) try {
            val o = JSONObject(stateFile.readText())
            for (k in o.keys()) {
                if (k == "end") { pendingEnd = o.getLong(k); continue }
                val f = o.getJSONObject(k)
                fun pairs(a: JSONArray) = MutableList(a.length()) { longArrayOf(a.getJSONArray(it).getLong(0), a.getJSONArray(it).getLong(1)) }
                files[k.toLong()] = F(f.getLong("a"), pairs(f.getJSONArray("h")), f.getLong("t"), f.getInt("r"), pairs(f.optJSONArray("p") ?: JSONArray()))
            }
        } catch (e: Exception) { io.log("bean_state", "手机上的录音豆文件记录读不了（$e），从头记", "warn") }
    }

    private fun save() {
        val o = JSONObject()
        for ((id, f) in files) o.put("$id", JSONObject().put("a", f.anchor).put("t", f.total).put("r", f.tries)
            .put("h", JSONArray(f.have.map { JSONArray(listOf(it[0], it[1])) })).put("p", JSONArray(f.anchors.map { JSONArray(listOf(it[0], it[1])) })))
        if (pendingEnd > 0) o.put("end", pendingEnd)
        stateFile.parentFile?.mkdirs()
        val tmp = File(stateFile.path + ".tmp")
        tmp.writeText(o.toString())
        tmp.renameTo(stateFile)
    }

    /** One connection, from the key exchange until the link goes (io.receive throws). `steps`: the link's own, for the log. */
    fun session(since: Long, steps: String) {
        this.since = since
        reader = Bean.Reader(); inbox.clear(); xfer = null; needList = true; askedAt = io.now(); liveFile = 0; paused = false
        val t = StringBuilder(steps)
        fun <T> step(code: String, what: String, block: () -> T): T {
            state = say(code)
            val t0 = io.monotonicNow()
            try { return block() }
            finally {
                val elapsed = io.monotonicNow() - t0
                t.append(if (t.isEmpty()) "" else "，").append("$what $elapsed ms")
                beanTiming { io.log("bean_connect_step", what, fields = mapOf("step" to what, "elapsedMs" to elapsed)) }
            }
        }
        try {
            val mine = Bean.keys()
            key = step("encrypting", "密钥交换") { Bean.session(mine, ask(Bean.hello(Bean.pub(mine.public as ECPublicKey)), 0x2E01).payload) }
            io.trace("session", mapOf("key" to Bean.hex(key)))
            val p = step("status", "读设备信息") { ask(Bean.info(), 0x0101).payload }
            hw = Bean.info(p)
            step("clock", "对时") { clock() }
            listing = step("status", "读文件列表") { list() }
            io.log("connect", "连上：$t；固件 ${hw["fw"]}，序列号 ${Bean.serial(p)}，电量 ${hw["battery"]}%，" +
                (if (recording) "在录音（文件 ${listing.open}）" else "没在录音") + "，豆子上 ${listing.files.size} 个文件",
                fields = hw + mapOf("files" to listing.files.size, "open" to listing.open))
            owe()
            io.report(hw + owed)
            if (!startDone) { startDone = true; if (!recording) start() }
            loop()
        } catch (e: IOException) {
            startDone = false  // the link went: the next connection starts the bean again
            throw e
        } finally {
            flush()
            xfer = null
            syncDoneKb = 0; syncTotalKb = 0; owed = emptyMap()
        }
    }

    private fun loop() {
        var reported = io.now()
        var settled = 0L
        while (true) {
            if (xfer == null && io.now() >= quietUntil) {
                if (needList || recording && listing.open == 0L && io.now() - listedAt > 10_000) listing = list()
                val open = listing.open
                val x = when {
                    recording && open != 0L && io.now() >= liveAgain -> plan(open)
                    recording -> null
                    else -> backlog()
                }
                if (x != null) begin(x) else state = say("beanIdle")
            }
            val quiet = quietUntil - io.now()
            next(if (xfer == null && quiet > 0) quiet else 1000)?.let(::take)
            xfer?.takeIf { it.cut }?.let { x ->
                complete(x, 0)
                if (files[x.file]?.let(::gap)?.let { it <= x.from } == true) missed(x)
                throw Again("file ${x.file}: the rest is here already")
            }
            xfer?.takeIf { it.live && !it.realtime && !it.watched && io.watched() }?.let { x ->  // the live page opened
                val at = if (x.last >= 0) x.last + 1 else x.from
                val behind = edge(x.file) - at
                if (behind > WATCHED_GAP_UNITS) {
                    flush()
                    io.log("bean_xfer", "实时页开了：实时文件 ${x.file} 补到单元 $at，离实时还差 ${behind * Bean.FRAME_MS / 1000} s，" +
                        "超过 1 分钟：断开重连再定，没取的留在豆子上", fields = mapOf("file" to x.file, "realtime" to false,
                        "reason" to "watched_abort", "from" to at, "hole_units" to behind, "hole_ms" to behind * Bean.FRAME_MS, "watched" to true))
                    throw Again("file ${x.file}: the live page opened $behind units behind")
                }
            }
            xfer?.takeIf { it.live && it.realtime && it.watched && it.first >= 0 && !io.watched() }?.let { x ->  // the page closed
                val f = files[x.file] ?: return@let
                val g = gap(f)?.takeIf { it < x.first } ?: return@let  // a hole live_now left: the next start fills it
                val hole = (f.have.firstOrNull { it[0] > g }?.get(0) ?: x.first) - g
                flush()
                io.log("bean_xfer", "实时页关了：实时文件 ${x.file} 单元 $g 起缺 ${hole * Bean.FRAME_MS / 1000} s，断开重连去补",
                    fields = mapOf("file" to x.file, "realtime" to true, "reason" to "watched_end", "from" to g, "hole_units" to hole,
                        "hole_ms" to hole * Bean.FRAME_MS, "watched" to false))
                throw Again("file ${x.file}: the live page closed, a hole from $g")
            }
            xfer?.let { x ->
                val (behind, pace) = slow(x) ?: return@let
                flush()
                fills[x.file] = TRIES
                io.log("bean_xfer", "实时文件 ${x.file} 补读 1 分钟没追近：读到单元 ${x.last}，离实时还差 ${behind * Bean.FRAME_MS / 1000} s，" +
                    "读速 ${"%.2f".format(Locale.US, pace)} 倍：这个文件改实时读，缺的停录后补", "warn",
                    mapOf("file" to x.file, "realtime" to false, "reason" to "too_slow", "from" to x.last + 1, "hole_units" to behind,
                        "hole_ms" to behind * Bean.FRAME_MS, "pace" to Math.round(pace * 100) / 100.0, "watched" to x.watched) +
                        diag(pace * 1000 / Bean.FRAME_MS))
                throw Again("file ${x.file}: read at $pace of real time, $behind units behind")
            }
            val now = io.now()
            if (paused) liveSeen = now
            if ((recording || xfer?.live == true) && !paused && liveFile != 0L && now - liveSeen > 30_000) {
                flush()
                if (!liveStalled) io.log("bean_stall", "实时文件 $liveFile 30 s 没收到单元，断开重连", "warn", mapOf("file" to liveFile))
                liveStalled = true
                xfer?.takeIf { it.units == 0 }?.let(::missed)
                throw Again("file $liveFile: no audio for 30 s")
            }
            xfer?.let { x ->  // the bean did not answer, or a transfer stopped: on with the next
                if (x.key == null && now - x.at > 15_000) failed(x, "no answer")
                // the bean cannot be stopped mid-file: a new connection starts it afresh, no stray units of this file
                // then go to the next one's
                else if (!x.live && now - x.seen > 30_000) {
                    complete(x, -1, "；30 s 没收到单元，断开重连")
                    throw Again("file ${x.file}: no audio for 30 s")
                }
            }
            if (xfer == null && now - askedAt > 60_000) {  // not during a transfer: its pushes say battery and charging
                askedAt = now
                val info = Bean.info(ask(Bean.info(), 0x0101).payload)
                if (info["recording"] == true && !recording) needList = true
                hw = info
            }
            if (now - settled >= 5000) { settled = now; settle(); owe(); if (xfer == null && !recording) prune() }
            if (now - reported >= 60_000) {
                reported = now
                io.report(hw + owed + (xfer?.lag?.let { mapOf("live_lag_s" to it / 1000) } ?: emptyMap()))
            }
        }
    }

    /** The owner, 10-26: a bean connected records. Its answer only echoes the command (01 9c, 10-25); it has started
     *  once its new file comes (1A/06, 850 ms on, 10-25) or its button's state says so (take()). Not asked again in the
     *  connection; never stopped or paused by the app. */
    private fun start() {
        val why = try {
            ask(Bean.start(), Bean.START, 3000)
            val until = io.now() + 3000
            while (!recording) take(next(until - io.now()) ?: break)
            if (recording) { io.log("bean_start", "自动开始录音"); return }
            "答了，3 s 内没开始录新文件"
        } catch (e: IOException) { throw e } catch (e: InterruptedException) { throw e } catch (e: Exception) { e.message }
        io.log("bean_start", "自动开始录音没成功（$why），这次连接不再试", "warn")
    }

    private fun clock() {
        val now = io.now()
        val tz = TimeZone.getDefault().getOffset(now) / 3_600_000
        try { ask(Bean.clock(now / 1000, tz), 0x01A6, 3000) }
        catch (e: Exception) { io.log("bean_clock", "对时没有回音（${e.message}），文件时间按豆子自己的钟", "warn") }
    }

    /** Every page of the bean's files: page by page until one adds none. */
    private fun list(): Bean.Listing {
        val all = LinkedHashMap<Long, Long>()
        var open = 0L
        for (page in 0 until 64) {
            val l = Bean.listing(ask(Bean.list(page), 0x1A0E).payload)
            if (page == 0) open = l.open
            val n = all.size
            l.files.forEach { all[it.first] = it.second }
            if (all.size == n) break
        }
        needList = false; listedAt = io.now()
        // A just-closed file can be absent briefly. Keep its ranges until the server has every piece.
        val q = Uploader.queue(chunks).filter { Uploader.deviceOf(it) == tag }.map(Uploader::startMs) + io.refused()
        for (id in files.filter { (id, f) -> id !in all && id != open && (f.have.isEmpty() ||
            gap(f) == null && q.none { it in f.at(0) until f.at(f.total) }) }.keys) {
            val f = files.remove(id)!!
            if (id * 1000 >= since) {
                val missing = gap(f) != null
                io.log("bean_gone", "文件 $id 已不在豆子上，" + (if (missing) "手机还缺它的一部分" else "服务端都有了，清掉手机记录"),
                    if (missing) "warn" else null, mapOf("file" to id))
            }
        }
        save()
        return Bean.Listing(all.toList(), open)
    }

    /** The oldest file begun since the bean was added here that this phone lacks units of. */
    private fun pending() = listing.files.filter { (id, _) -> id * 1000 >= since && id != listing.open && files[id]?.let { !done(it) } ?: true }
        .sortedBy { it.first }

    /** Units of these files the phone lacks. */
    private fun lacking(p: List<Pair<Long, Long>>) =
        p.sumOf { (id, ms) -> files[id]?.let { f -> maxOf(0L, (if (f.total >= 0) f.total else ms / Bean.FRAME_MS) - covered(f)) } ?: (ms / Bean.FRAME_MS) }

    /** What of the bean's recording this phone lacks (the owner, 11-07: the app said 已同步 with 42 min on the bean, held
     *  back by the live page), in the status and its reports: `backlog_s`, how long until it is all here at the measured
     *  pace (`backlog_eta_s`; the file being recorded grows meanwhile) and why it waits (`backlog_held`: `live`, the live
     *  page is open, plan()'s live_now; `recording`, it goes after the bean stops: files closed meanwhile, a file read
     *  live only); none: being read. A minute or less behind is no backlog (WATCHED_GAP_UNITS: the live page's own line). */
    private fun owe() {
        val open = if (recording) listing.open else 0L
        // units of the file being recorded, begun since the bean was added here
        var now = if (open != 0L && open * 1000 >= since) maxOf(0L, edge(open) - (files[open]?.let(::covered) ?: 0L)) else 0L
        if (now <= WATCHED_GAP_UNITS) now = 0
        val closed = lacking(pending())
        owed = owedFields(now, if (recording) closed else 0, if (recording) 0 else closed, io.watched(), (fills[open] ?: 0) >= TRIES, pace)
    }

    private fun backlog(): Xfer? {
        val p = pending()
        if (p.isEmpty()) {
            if (caught > 0) io.log("sync", "补传 ${caught * Bean.UNIT / 1024} KB，用时 ${(io.now() - caughtSince) / 1000} s")
            caught = 0; syncDoneKb = 0; syncTotalKb = 0
            return null
        }
        if (caught == 0L) caughtSince = io.now()
        val left = lacking(p)
        syncTotalKb = (caught + left) * Bean.UNIT / 1024; syncDoneKb = caught * Bean.UNIT / 1024
        val id = p.first().first
        return Xfer(id, false, files[id]?.let(::gap) ?: 0)
    }

    /** The file being recorded, from where the phone lacks it (the owner, 10-27). realtime=0 reads it from the offset at
     *  about twice real time, then goes on live with no end (10-27 probe); realtime=1 starts where the bean is, whatever
     *  the offset. While the live page is open a tail hole of up to a minute is read first, a longer one is left until
     *  the page closes (watched_end) and live starts now; else the holes go oldest first, each cut where a long stretch the phone has begins
     *  (rest(); the link goes, the next hole from its own offset), then the tail, which carries on live. */
    private fun plan(open: Long): Xfer {
        val f = files[open]
        val tail = f?.have?.lastOrNull()?.get(1) ?: 0L  // the first unit after the phone's latest stretch
        val gap = f?.let(::gap) ?: tail  // all of it here (its length known): nothing older to fill, on from the tail
        val watched = io.watched()
        val edge = edge(open)
        val mine = open * 1000 >= since  // a file begun before the bean was added here: live only, its start is not this phone's
        val fallback = (fills[open] ?: 0) >= TRIES  // realtime=0 came to nothing TRIES times: live, the holes after stop
        val x = when {
            !mine || fallback -> Xfer(open, true, tail, realtime = true)  // not for the page: the page closing never ends it
            watched -> Xfer(open, true, tail, realtime = edge - tail > WATCHED_GAP_UNITS, watched = true)
            gap < tail -> Xfer(open, true, gap).also { it.until = rest(f!!, gap) }
            else -> Xfer(open, true, tail)
        }
        val older = x.from < tail
        val hole = if (older) f!!.have.first { it[0] > x.from }[0] - x.from else maxOf(0L, edge - tail)
        val reason = if (mine && fallback) "live_fallback" else if (x.realtime) "live_now" else if (watched) "tail_short"
            else if (older) "fill_hole" else "fill_tail"
        val s = hole * Bean.FRAME_MS / 1000
        io.log("bean_xfer", "实时文件 $open 从单元 ${x.from} 起" + when (reason) {
            "live_now" -> "直接实时（realtime=1）：" + if (mine) "实时页开着，尾部缺 $s s，超过 1 分钟，留到以后补" else "文件在豆子加到这台手机前就开始了"
            "live_fallback" -> "直接实时（realtime=1）：realtime=0 取不到或追不上实时，这个文件改回实时，缺的停录后补"
            "tail_short" -> "补尾部 $s s（realtime=0）：实时页开着，不超过 1 分钟，补上接着实时"
            "fill_hole" -> "补前面的洞 $s s（realtime=0）" + if (x.until > 0) "，单元 ${x.until} 起手机已有，到那里断开" else "，补完接着实时"
            else -> "补尾部 $s s（realtime=0），补上接着实时"
        }, fields = mapOf("file" to open, "realtime" to x.realtime, "reason" to reason, "from" to x.from, "hole_units" to hole,
            "hole_ms" to hole * Bean.FRAME_MS, "watched" to watched))
        return x
    }

    /** The unit the bean is at in the file it records, by the phone's clock (a pause would put it early). */
    /** A realtime=0 read of the file being recorded that does not gain on it never gets to live: 11-07/08 the bean sent it
     *  at 0.67 of real time for hours (one unit a notification, 33 a second; the same bean's closed files 5 times faster;
     *  nothing in 1A/07 sets a rate), the server's audio ever later. Each minute of such a read: (units behind, its pace)
     *  when still over a minute behind and no nearer than a minute before (then live_fallback: realtime=1, the holes after
     *  the stop), else null. */
    private fun slow(x: Xfer): Pair<Long, Double>? {
        val now = io.now()
        if (!x.live || x.realtime || x.last < 0 || paused) { x.gainAt = 0; return null }
        val behind = edge(x.file) - x.last - 1
        if (x.gainAt == 0L) { x.gainAt = now; x.gainBehind = behind; x.gainLast = x.last; return null }
        if (now - x.gainAt < 60_000) return null
        val pace = (x.last - x.gainLast) * Bean.FRAME_MS.toDouble() / (now - x.gainAt)
        val stuck = behind > WATCHED_GAP_UNITS && behind >= x.gainBehind
        x.gainAt = now; x.gainBehind = behind; x.gainLast = x.last
        return if (stuck) behind to pace else null
    }

    /** What may slow a live read, logged with bean_lag and too_slow (11-08: 0.67 for hours, cause unknown; a Bluetooth
     *  headset in call mode that day?): the phone's side (Io.phoneState), the bean's battery and case, units a second. */
    private fun diag(unitsS: Double): Map<String, Any?> = io.phoneState() +
        listOf("battery", "charge", "case_battery", "case_charging").associate { "bean_$it" to hw[it] } +
        ("units_s" to Math.round(unitsS * 10) / 10.0)

    private fun edge(file: Long) = maxOf(0L, (io.now() - (files[file]?.latest ?: (file * 1000))) / Bean.FRAME_MS)

    private fun begin(x: Xfer) {
        xfer = x; x.at = io.now(); x.seen = x.at; marked = false
        if (x.live && liveFile != x.file) { liveFile = x.file; liveSeen = x.at }
        state = say(if (x.live) "joining" else "beanIdle")
        if (x.live) { syncDoneKb = 0; syncTotalKb = 0 }  // a backlog waits while the bean records
        send(Bean.transfer(x.file, x.from * Bean.UNIT, x.realtime))
    }

    /** Closed files still to fetch. The bean reads them only after recording stops. */
    private fun older() = pending().isNotEmpty()

    /** Release legacy holds while recording: even closed-file backlog cannot be fetched then. After a stop,
     *  finish reading it first. Already-cut older pieces must be uploaded before letting go in either case. */
    private fun settle() {
        if (!recording && older()) return
        val q = Uploader.queue(chunks).filter { Uploader.deviceOf(it) == tag }.map(Uploader::startMs)
        io.held()?.let { h -> if (q.none { it < h + HOLD_MARGIN_MS }) { io.hold(null); io.log("hold", "旧录音不再压住定稿") } }
        if (!recording && xfer == null && pendingEnd > 0 && q.isEmpty()) {
            io.log("end", "录音到 ${SimpleDateFormat("HH:mm:ss", Locale.US).format(Date(pendingEnd))} 结束，都上传了：告诉服务端", fields = mapOf("at" to pendingEnd))
            io.end(pendingEnd); pendingEnd = 0; save()
        }
    }

    /** Files the server has whole: begun since the bean was added here, closed on the bean, every unit on the phone and
     *  every piece of them taken by the server (none queued still, none refused). */
    private fun confirmed(): List<Long> {
        val q = Uploader.queue(chunks).filter { Uploader.deviceOf(it) == tag }.map(Uploader::startMs) + io.refused()
        return listing.files.map { it.first }.filter { id ->
            val f = files[id]
            f != null && id * 1000 >= since && id != listing.open && gap(f) == null && q.none { it in f.at(0) until f.at(f.total) }
        }
    }

    /** The files the server has whole go off the bean, one at a time, each once (the owner, 10-25: what stays on it can
     *  be read by anyone near while no phone is connected; Feishu deletes too). An unclear answer leaves the file. */
    private fun prune() {
        for (id in confirmed() - kept) {
            val f = files[id]!!
            deletable = setOf(id)
            try { ask(Bean.delete(id, deletable), Bean.DELETE, 3000) }  // the SDK's 3 s
            catch (e: Exception) {
                kept += id
                io.log("bean_delete", "豆子上的文件 $id 没删掉（${e.message}），留在豆子上", "warn", mapOf("file" to id))
                continue
            } finally { deletable = emptySet() }
            files.remove(id)
            listing = Bean.Listing(listing.files.filter { it.first != id }, listing.open)
            save()
            io.log("bean_delete", "豆子上的文件 $id 删了：服务端都有了（${f.total} 个单元，${f.total * Bean.FRAME_MS / 1000} 秒）",
                fields = mapOf("file" to id, "units" to f.total, "s" to f.total * Bean.FRAME_MS / 1000))
        }
    }

    private fun take(f: Bean.Frame) {
        val x = xfer
        val p = f.payload
        when (f.cmd) {
            0x1A07 -> if (x != null && x.key == null) header(x, f)
            0x1A08, 0x1A12 -> if (x?.key != null) Bean.slices(p).forEach { unit(x, it) }
            0x1A0A -> if (x != null && (p.size < 9 || Bean.u32(p, 5).let { it == 0L || it == x.file })) complete(x, p.getOrNull(0)?.toInt()?.and(255) ?: 0)
            0x1A06 -> if (p.size >= 4) {  // a new file: recording began, or the bean went on in the next file
                val open = Bean.u32(p, 0)
                val naturalRollover = x != null && liveFile == x.file && recording && !paused
                paused = false
                if (x != null && x.live && x.file != open) {
                    // The bean may skip 1A/0A at rollover: flush as usual, fetch the old tail after stop.
                    complete(x, 0, "；豆子开始录新文件 $open，缺的停录后补传")
                    liveAgain = 0
                    throw Again("file ${x.file}: rolled over to $open", naturalRollover = naturalRollover)
                }
                if (x != null && !x.live) {
                    // Recording began mid catch-up: the bean sends no more of it. Live at once on a new connection, not a try.
                    flush()
                    io.log("bean_xfer", "豆子开始录音，补传文件 ${x.file} 停在单元 ${maxOf(x.last + 1, x.from)}，停录后接着补",
                        fields = mapOf("file" to x.file, "open" to open, "units" to x.units, "last" to x.last))
                    xfer = null
                    liveAgain = 0
                    save()
                    throw Again("file ${x.file}: the bean began recording $open")
                }
                recording = true
                listing = Bean.Listing(listing.files, open)
                io.log("bean_file", "豆子开始录新文件 ${listing.open}", fields = mapOf("file" to listing.open, "pre" to (p.getOrNull(if (p.size > 8) 8 else 4)?.toInt() ?: -1)))
            }
            0x1882 -> when (p.getOrNull(0)?.toInt()) {  // the bean's button: 0 stopped, 1 recording, 2 paused
                0 -> { liveFile = 0; paused = false; recording = false; startDone = true; io.log("bean_stop", "豆子停止录音") }
                1 -> { if (!recording) needList = true; if (paused) liveSeen = io.now(); paused = false; recording = true; io.log("bean_record", "豆子开始录音") }
                2 -> { paused = true; startDone = true; io.log("bean_pause", "豆子暂停录音") }
            }
            0x0103 -> if (p.size >= 2) hw = hw + mapOf("battery" to Bean.pct(p[0]), "case_battery" to Bean.pct(p[1]))
            0x0104 -> if (p.isNotEmpty()) {
                hw = hw + mapOf("charge" to if (p[0] == 1.toByte()) "charging" else "battery") +
                    (p.getOrNull(1)?.let { mapOf("case_charging" to (it == 1.toByte())) } ?: emptyMap())
            }
            else -> if (unknown.add(f.cmd)) io.log("bean_frame", "豆子发来 %02X/%02X（%d B），不认识，略过".format(f.type, f.id, p.size))
        }
    }

    private fun header(x: Xfer, fr: Bean.Frame) {
        if (!fr.ok) return failed(x, "status ${fr.status}")
        val h = Bean.header(key, fr.payload)
        if (h.file != x.file) return failed(x, "it answered with file ${h.file}")
        when (h.code) {
            0 -> {
                x.key = h.key; x.nonce = h.nonce; if (!x.live) busy = 0
                if (x.live) liveSeen = io.now()
                if (!x.live || h.size != 0xFFFFFFFFL) {
                    if (h.size % Bean.UNIT != 0L) return failed(x, "file of ${h.size} B")
                    file(x.file, x.file * 1000).let { f -> f.total = h.size / Bean.UNIT; if (!x.live) x.until = rest(f, x.from) }
                }
                io.trace("file", mapOf("file" to x.file, "key" to Bean.hex(h.key!!), "nonce" to Bean.hex(h.nonce), "size" to h.size))
            }
            3 -> if (x.live) failed(x, "code 3")  // the file being recorded goes on: never its end
                else { file(x.file, x.file * 1000).let { if (it.total < 0) it.total = x.from }; xfer = null; save() }  // nothing after `from`: all of it is here
            1 -> { file(x.file, x.file * 1000).tries = TRIES + 1; failed(x, "no such file") }
            4 -> {  // still at the transfer before (10-25: asked 2 ms after its end): again in a moment, not a try
                if (busy++ == 0) io.log("bean_xfer", "${if (x.live) "实时" else "补传"}文件 ${x.file} 豆子还忙（code 4），稍后再要", fields = mapOf("file" to x.file))
                quietUntil = io.now() + minOf(5000L, PACE_MS * busy)
                xfer = null
            }
            else -> failed(x, "code ${h.code}")
        }
    }

    private fun failed(x: Xfer, why: String) {
        io.log("bean_xfer", "${if (x.live) "实时" else "补传"}文件 ${x.file} 没开始：$why", "warn",
            mapOf("file" to x.file, "from" to x.from, "realtime" to x.realtime))
        if (x.live) { liveAgain = io.now() + 30_000; missed(x) } else file(x.file, x.file * 1000).tries++
        xfer = null
        save()
    }

    /** A realtime=0 read of the open file came to nothing: refused, no answer, no unit in 30 s, or cut with its hole
     *  where it was (the bean did not honour the offset). After TRIES in a row (a read from its offset clears them:
     *  first()) that file is read realtime=1, as before (plan()). */
    private fun missed(x: Xfer) { if (x.live && !x.realtime) fills[x.file] = (fills[x.file] ?: 0) + 1 }

    private fun file(id: Long, anchor: Long) = files.getOrPut(id) { F(anchor) }

    private fun unit(x: Xfer, s: Bean.Slice) {
        val audio = Bean.opus(x.key!!, x.nonce, s)
        io.trace("unit", mapOf("file" to x.file, "seq" to s.seq, "flags" to s.flags, "enc" to Bean.hex(s.data), "opus" to Bean.hex(audio)))
        if (x.first < 0) first(x, s, audio)
        val f = files[x.file]!!
        if (x.last >= 0 && s.seq > x.last + 1) {  // units the bean did not send: the file has a hole, asked for again later
            x.lost += s.seq - x.last - 1
            io.log("bean_gap", "文件 ${x.file} 缺单元 ${x.last + 1}..${s.seq - 1}（${(s.seq - x.last - 1) * Bean.FRAME_MS} ms）", "warn",
                mapOf("file" to x.file, "from" to x.last + 1, "to" to s.seq))
        }
        val now = io.now()
        val late = now - x.seen - (s.seq - x.last) * Bean.FRAME_MS
        // the bean paused mid-file (the app never asks it to) or the link stalled; a unit sent again (seq ≤ the highest seen)
        // is not late, else a resend logs one warn per unit (11-08 16:42 would have been ~20,000)
        if (x.live && x.last >= 0 && s.seq > x.last && late > 5000)
            io.log("bean_gap_time", "文件 ${x.file} 实时单元 ${x.last}→${s.seq} 之间过了 ${(now - x.seen) / 1000} s：豆子若暂停过，" +
                "之后的单元按序号排会早 ${late / 1000} s", "warn", mapOf("file" to x.file, "seq" to s.seq, "late_ms" to late))
        if (x.live && x.last >= 0 && s.seq <= x.last && !x.resent) {  // 11-08 16:42 (iOS): 20704→0, then 41 s behind all along
            x.resent = true
            io.log("bean_xfer", "实时文件 ${x.file} 豆子从单元 ${x.last} 退回到 ${s.seq} 重发：这次传输之后的单元不再用来对钟", "warn",
                mapOf("file" to x.file, "from" to x.last, "to" to s.seq))
        }
        x.units++; x.last = maxOf(x.last, s.seq); x.seen = now
        // a read behind (not caught up to live yet): how fast the bean sends, for owe()'s estimate
        if (!x.realtime && x.lag == null && s.seq - x.first >= REST_UNITS && now > x.firstAt)
            pace = (s.seq - x.first) * Bean.FRAME_MS.toDouble() / (now - x.firstAt)
        if (x.live) {
            liveSeen = now; liveStalled = false; busy = 0
            // the link can slow to a fraction of real time and stay up (iOS in the background, 10-28): how far behind, once
            // the transfer has caught up (a realtime=0 read starts behind by the hole it fills)
            if (!x.resent) reanchor(x, f, s.seq, now - (s.seq + 1) * Bean.FRAME_MS)
            val lag = now - f.at(s.seq + 1)
            if (x.lag != null || lag <= 10_000) {
                x.lag = lag
                if (if (lagging) lag <= 10_000 else lag > 60_000) {
                    lagging = !lagging
                    val fields = mapOf("file" to x.file, "seq" to s.seq, "lag_s" to lag / 1000)
                    io.log("bean_lag", "实时文件 ${x.file} " + if (lagging) "落后 ${lag / 1000} s" else "追上了，落后 ${lag / 1000} s",
                        if (lagging) "warn" else null,
                        if (lagging) fields + diag((s.seq - x.first + 1) * 1000.0 / maxOf(1L, now - x.firstAt)) else fields)
                }
            }
        }
        if (s.flags and 4 != 0) f.total = s.seq + 1
        val dupe = has(f, s.seq) || runFile == x.file && s.seq in runFrom until runFrom + run.size
        val mark = s.flags and 1 != 0
        if (mark && !marked && !dupe) io.log("mark", "双击标记", fields = mapOf("file" to x.file, "seq" to s.seq), at = f.at(s.seq))
        marked = mark  // also on units had already: a mark that goes on past them is not a new one
        if (x.until in 1..s.seq + 1) x.cut = true
        if (dupe) { x.dupes++; return }
        val n = run.size  // a piece is dated by one anchor
        if (run.isNotEmpty() && (runFile != x.file || s.seq != runFrom + n || f.at(s.seq) != f.at(runFrom) + n * Bean.FRAME_MS)) flush()
        if (run.isEmpty()) { runFile = x.file; runFrom = s.seq; if (x.live) step(x, f, s.seq) }
        run += audio
        if (!x.live) { caught++; syncDoneKb = caught * Bean.UNIT / 1024 }
        // off the target (step()): a live piece ends at 5 s, the next one a step nearer, whatever the slice (iOS in the
        // background 20 s, Android with the screen off 60 s: a step a piece could not keep up with 750 ppm)
        val owed = x.target?.let { kotlin.math.abs(it - f.latest) >= 5 } ?: false
        if (run.size >= if (!x.live) Bean.PIECE_UNITS else if (owed) minOf(io.liveUnits(), STEP_UNITS) else io.liveUnits()) flush()
    }

    /** The bean's units do not keep the phone's time (#233: up to 750 ppm slow for half an hour, then on time, 2–3 s over
     *  six hours): while live the file's anchor is measured each half minute. A unit never comes before its end, so the
     *  fastest one of a half minute (`est`: now − (seq + 1) × 20 ms) says it; when two halves in a row agree within
     *  50 ms the units come as recorded (not a fill catching up, not a link slowed to a fraction) and that is the target. */
    private fun reanchor(x: Xfer, f: F, seq: Long, est: Long) {
        if (x.half < 0 || seq < x.half) { x.half = seq; x.fastest = est; x.before = null; return }
        x.fastest = minOf(x.fastest, est)
        if (seq - x.half + 1 < CLOCK_UNITS) return
        val b = x.before
        if (b != null && kotlin.math.abs(x.fastest - b) <= 50) {
            val d = x.fastest - f.latest
            val now = io.now()
            if (kotlin.math.abs(d) >= 200 || kotlin.math.abs(d) >= 20 && now - x.clockSaid >= 600_000) {  // a jump, else at most once in 10 min
                x.clockSaid = now
                io.log("bean_clock", "文件 ${x.file} 到单元 $seq 按序号排比手机的钟${if (d >= 0) "早" else "晚"} ${kotlin.math.abs(d)} ms，之后的块逐段对齐",
                    fields = mapOf("file" to x.file, "seq" to seq, "ms" to d))
            }
            x.target = x.fastest
        }
        x.before = x.fastest; x.half = seq + 1; x.fastest = Long.MAX_VALUE
    }

    /** A live piece begins at `seq`: from it on the file is dated a step nearer the target, at most 9 ms so the server's
     *  decoder goes on from the piece before (audio.before(): a join within 10 ms, else a click), at once when 0.2 s or
     *  more off. Units before keep their dates, a hole among them filled later too.
     *  ponytail: what is read on return from an absence (the tail before live) goes by the anchor from before it, off by
     *  what the bean drifted meanwhile (≤ 750 ppm × the absence); extrapolate by the last rate if that matters. */
    private fun step(x: Xfer, f: F, seq: Long) {
        val t = x.target ?: return
        if (seq <= (f.anchors.lastOrNull()?.get(0) ?: 0)) return
        val d = t - f.latest
        if (kotlin.math.abs(d) >= 5) f.anchors += longArrayOf(seq, f.latest + if (kotlin.math.abs(d) >= 200) d else d.coerceIn(-9L, 9L))
    }

    /** A transfer's first unit: establish the file's anchor. Live never holds finals: backlog is read only after stop. */
    private fun first(x: Xfer, s: Bean.Slice, audio: ByteArray) {
        x.first = s.seq; x.firstAt = io.now()
        if (x.live && !x.realtime && s.seq == x.from) fills.remove(x.file)  // the bean honoured the offset: misses start again
        val now = io.now()
        val byId = x.file * 1000
        val est = now - s.seq * Bean.FRAME_MS
        if (x.file !in files) {
            val phone = x.realtime && est - byId in -2000L..3000L  // realtime=0 starts behind: its first unit is not now
            file(x.file, if (phone) est else byId)
            if (x.realtime) io.log("bean_clock", "文件 ${x.file} 的起点按${if (phone) "手机" else "豆子"}的钟：豆子的钟比手机${if (est >= byId) "慢" else "快"} ${kotlin.math.abs(est - byId)} ms",
                fields = mapOf("file" to x.file, "ms" to est - byId))
        }
        io.log("bean_xfer", "${if (x.live) "实时" else "补传"}文件 ${x.file} 开始：首单元 ${s.seq}（请求从 ${x.from} 起），Opus 配置 ${(audio.getOrNull(0)?.toInt() ?: 0) and 255 shr 3}",
            fields = mapOf("file" to x.file, "seq" to s.seq, "from" to x.from, "realtime" to x.realtime, "toc" to ((audio.getOrNull(0)?.toInt() ?: 0) and 255)))
        if (x.live) state = say("connected")
    }

    private fun complete(x: Xfer, result: Int, why: String = "") {
        flush()
        xfer = null
        quietUntil = io.now() + PACE_MS
        val f = files[x.file]
        io.log("bean_xfer", "${if (x.live) "实时" else "补传"}文件 ${x.file} ${if (x.cut) "缺的取到了" else "传完（结果 $result）"}：${x.units} 个单元，seq ${x.first}..${x.last}，" +
            "重复 ${x.dupes}，缺 ${x.lost}，用时 ${(io.now() - x.at) / 1000} s" +
            (if (x.cut) "；单元 ${x.until} 起的一段手机已有，断开连接让豆子停下，缺的再从各自的位置要" else "") + why, if (result == 0 && x.lost == 0L) null else "warn",
            mapOf("file" to x.file, "units" to x.units, "first" to x.first, "last" to x.last, "dupes" to x.dupes, "lost" to x.lost, "result" to result, "cut" to x.cut))
        if (x.live) {
            listing = Bean.Listing(listing.files, 0); needList = true; askedAt = 0  // recording on? the info says
            if (x.units == 0) liveAgain = io.now() + 30_000
        }
        if (f == null) return
        if (x.live) {
            if (x.last >= 0) pendingEnd = maxOf(pendingEnd, f.at(x.last + 1))
        } else if (gap(f) == null) {
            pendingEnd = maxOf(pendingEnd, f.at(f.total))
        } else if (!(x.cut && gap(f)!! > x.from) && ++f.tries > TRIES) {  // cut with the hole filled: not a try
            io.log("bean_xfer", "文件 ${x.file} 从单元 ${gap(f)} 起取了 $TRIES 次仍不全，不再取", "error", mapOf("file" to x.file, "gap" to gap(f)))
        }
        save()
    }

    /** The units taken so far into a piece, dated by its file's anchor. */
    private fun flush() {
        if (run.isEmpty()) return
        val f = files[runFile] ?: return run.clear()
        val start = f.at(runFrom)
        val part = File(chunks, "$start@$tag.part")
        part.writeBytes(Store.oggOpus(run, Bean.FRAME_MS, 2))
        part.renameTo(File(chunks, "$start@$tag.ogg"))
        add(f, runFrom, runFrom + run.size)
        run.clear()
        save()
        io.piece()
    }

    private fun has(f: F, seq: Long) = f.have.any { seq >= it[0] && seq < it[1] }
    private fun covered(f: F) = f.have.sumOf { it[1] - it[0] }

    private fun add(f: F, from: Long, to: Long) {
        f.have += longArrayOf(from, to)
        f.have.sortBy { it[0] }
        val merged = mutableListOf<LongArray>()
        for (r in f.have) if (merged.isNotEmpty() && r[0] <= merged.last()[1]) merged.last()[1] = maxOf(merged.last()[1], r[1]) else merged += r
        f.have.clear(); f.have += merged
    }

    /** The first unit this phone lacks; null: it has the whole file (its length known). */
    fun gap(f: F): Long? {
        var at = 0L
        for (r in f.have) if (r[0] > at) break else at = maxOf(at, r[1])
        return if (f.total in 0..at) null else at
    }

    private fun done(f: F) = gap(f) == null || f.tries > TRIES

    /** Where the first range the phone has after `from` that is long enough to skip begins: a new connection costs less
     *  than taking it again (the bean sends from the offset to the end and cannot be stopped mid-file, 10-25: 1831
     *  units in 6 s to fill 36). The next hole is then asked for from its own offset. -1: take it all. */
    private fun rest(f: F, from: Long): Long = f.have.firstOrNull { it[0] > from && it[1] - it[0] >= REST_UNITS }?.get(0) ?: -1

    /** The session ends on purpose: the link goes, which is how a transfer stops early; BeanLink connects again. */
    class Again(why: String, val naturalRollover: Boolean = false) : Exception(why)

    private fun send(frame: ByteArray) {
        check(Bean.allowed(frame, deletable)) { "not sent: ${Bean.hex(frame)}" }  // the last check before the radio
        io.trace("tx", mapOf("hex" to Bean.hex(frame)))
        io.send(frame)
    }

    private fun next(ms: Long): Bean.Frame? {
        val until = io.now() + ms
        while (inbox.isEmpty()) {
            val left = until - io.now()
            if (left <= 0) return null
            val b = io.receive(left) ?: return null
            io.trace("rx", mapOf("hex" to Bean.hex(b)))
            inbox += reader.push(b)
        }
        return inbox.removeFirst()
    }

    /** A command and its answer; what else comes meanwhile (a push, audio) is taken as it comes. */
    private fun ask(frame: ByteArray, cmd: Int, ms: Long = 10_000): Bean.Frame {
        send(frame)
        val until = io.now() + ms
        while (true) {
            val f = next(until - io.now()) ?: throw Exception("no answer to %02X/%02X".format(cmd shr 8, cmd and 255))
            if (f.cmd != cmd) { take(f); continue }
            if (!f.ok) throw Exception("%02X/%02X refused (status %d)".format(f.type, f.id, f.status))
            return f
        }
    }

    companion object {
        const val TRIES = 3
        const val PACE_MS = 200L  // between transfers (ws/soundcore: the SDK's 200 ms)
        const val REST_UNITS = 1500L  // 30 s of audio, 5 s to send (about 300 units/s): about what a new connection costs
        /** owe()'s fields from units lacking: `now` of the file being recorded, `closed` of files that wait for the bean to
         *  stop, `idle` of files read now (the bean stopped); `live`: the recorded file is read live only (live_fallback). */
        fun owedFields(now: Long, closed: Long, idle: Long, watched: Boolean, live: Boolean, pace: Double): Map<String, Any?> {
            val s = (now + closed + idle) * Bean.FRAME_MS / 1000
            if (s <= 0) return emptyMap()
            // closed files wait for the stop whatever the page does (11-08: 3.5 h of one said 补传中)
            val held = if (now > 0 && live || closed > 0) "recording" else if (now > 0 && watched) "live" else null
            // the recorded file grows by real time while it is read: it comes nearer at pace - 1
            val eta = when {
                held == "recording" -> null
                now > 0 -> if (pace > 1.05) now * Bean.FRAME_MS / 1000.0 / (pace - 1) else null
                else -> idle * Bean.FRAME_MS / 1000.0 / pace
            }
            return mapOf("backlog_s" to s, "backlog_eta_s" to eta?.toLong(), "backlog_held" to held)
        }
        const val WATCHED_GAP_UNITS = 3000L  // the owner, 10-27: a hole of up to a minute is read first even while /live watches
        const val CLOCK_UNITS = 1500L  // half a minute of live units: the fastest of each dates the file (reanchor())
        const val STEP_UNITS = 250  // while a live file is off its target, pieces of 5 s: a step each (step(), ≤ 1800 ppm)
        const val HOLD_MARGIN_MS = 60_000L  // the hold a minute before what is said now: the clocks' drift
    }
}
