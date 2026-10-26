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
 * and cannot be stopped mid-file: the file being recorded, as it is recorded (realtime), else the oldest file this
 * phone lacks units of (the backlog, files begun after the bean was added here: `since`). Units go into dated Ogg
 * pieces in `chunks` (the Uploader's queue).
 *
 * Per file (`state`, JSON, kept after every piece): when its unit 0 was on the phone's clock (its anchor: the file id,
 * the bean's clock as it began, set at each connect; the phone's own clock when the file is first met live and the two
 * agree within seconds) and which units are in pieces already. A unit's time is its anchor + seq × 20 ms; a transfer
 * after a drop starts at the first unit missing (its byte offset), units had already are dropped, a hole is asked
 * for again (three times); when a long stretch the phone has follows it, the session ends once the hole is in and the
 * next hole is asked for from its own offset (rest()), not by taking that stretch again. A pause inside a file would
 * date what follows it early (the app never pauses the bean): logged as bean_gap_time. Live transfers never hold
 * finals; backlog is fetched only after recording stops. The end of the last recording goes to the server once
 * its pieces are up. A file the server has whole is then deleted on the
 * bean (prune()); nothing else is.
 */
class BeanSync(private val tag: String, private val stateFile: File, private val chunks: File, private val io: Io) {
    interface Io {
        fun send(frame: ByteArray)
        /** The next notification, null after `ms` without one; throws once the link is gone. */
        fun receive(ms: Long): ByteArray?
        fun now(): Long
        /** Units in a piece while recording live: the app's slice. */
        fun liveUnits(): Int
        fun log(kind: String, text: String, level: String? = null, fields: Map<String, Any?> = emptyMap(), at: Long? = null)
        fun report(hw: Map<String, Any?>)
        /** prefs hold:<tag>, sent with every upload while set (Uploader); null: let go. */
        fun hold(at: Long?)
        fun held(): Long?
        /** The bean's last recording ended at `at` (wall ms) and all of it is up: the server is told (BeanLink: kept in the
         *  prefs until it takes it). */
        fun end(at: Long)
        fun piece()
        /** Starts of this bean's pieces the server refused (Uploader: HTTP 400): their files stay on the bean. */
        fun refused(): Set<Long>
        /** A debug capture of the session (off unless asked for: BeanLink). */
        fun trace(what: String, v: Map<String, Any?>) {}
    }

    class F(var anchor: Long, val have: MutableList<LongArray> = mutableListOf(), var total: Long = -1, var tries: Int = 0)

    private class Xfer(val file: Long, val live: Boolean, val from: Long) {
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
    }

    @Volatile var state: Map<String, Any?> = say("encrypting")
    // Immutable status snapshots; free_kb stays at the last 01/01 reading while a transfer is live.
    @Volatile var hw: Map<String, Any?> = emptyMap()
    @Volatile var syncDoneKb = 0L
    @Volatile var syncTotalKb = 0L
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
    private var xfer: Xfer? = null
    private var since = 0L
    private var pendingEnd = 0L  // kept in the state file: a kill before the pieces are up does not lose it (complete()
                                 // is not run again for a file that is whole)
    private var caught = 0L      // backlog units taken since the backlog began
    private var caughtSince = 0L
    private var marked = false
    private val unknown = mutableSetOf<Int>()
    private val run = mutableListOf<ByteArray>()
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
                val h = f.getJSONArray("h")
                files[k.toLong()] = F(f.getLong("a"), MutableList(h.length()) { longArrayOf(h.getJSONArray(it).getLong(0), h.getJSONArray(it).getLong(1)) },
                    f.getLong("t"), f.getInt("r"))
            }
        } catch (e: Exception) { io.log("bean_state", "手机上的录音豆文件记录读不了（$e），从头记", "warn") }
    }

    private fun save() {
        val o = JSONObject()
        for ((id, f) in files) o.put("$id", JSONObject().put("a", f.anchor).put("t", f.total).put("r", f.tries)
            .put("h", JSONArray(f.have.map { JSONArray(listOf(it[0], it[1])) })))
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
            val t0 = io.now()
            return block().also { t.append(if (t.isEmpty()) "" else "，").append("$what ${io.now() - t0} ms") }
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
            io.report(hw)
            if (!startDone) { startDone = true; if (!recording) start() }
            loop()
        } catch (e: IOException) {
            startDone = false  // the link went: the next connection starts the bean again
            throw e
        } finally {
            flush()
            xfer = null
            syncDoneKb = 0; syncTotalKb = 0
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
                    recording && open != 0L && io.now() >= liveAgain -> Xfer(open, true, files[open]?.let(::gap) ?: 0)
                    recording -> null
                    else -> backlog()
                }
                if (x != null) begin(x) else state = say("beanIdle")
            }
            val quiet = quietUntil - io.now()
            next(if (xfer == null && quiet > 0) quiet else 1000)?.let(::take)
            xfer?.takeIf { it.cut }?.let { complete(it, 0); throw Again("file ${it.file}: the rest is here already") }
            val now = io.now()
            if (paused) liveSeen = now
            if ((recording || xfer?.live == true) && !paused && liveFile != 0L && now - liveSeen > 30_000) {
                flush()
                if (!liveStalled) io.log("bean_stall", "实时文件 $liveFile 30 s 没收到单元，断开重连", "warn", mapOf("file" to liveFile))
                liveStalled = true
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
            if (now - settled >= 5000) { settled = now; settle(); if (xfer == null && !recording) prune() }
            if (now - reported >= 60_000) { reported = now; io.report(hw) }
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
            gap(f) == null && q.none { it in f.anchor until f.anchor + f.total * Bean.FRAME_MS }) }.keys) {
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

    private fun backlog(): Xfer? {
        val p = pending()
        if (p.isEmpty()) {
            if (caught > 0) io.log("sync", "补传 ${caught * Bean.UNIT / 1024} KB，用时 ${(io.now() - caughtSince) / 1000} s")
            caught = 0; syncDoneKb = 0; syncTotalKb = 0
            return null
        }
        if (caught == 0L) caughtSince = io.now()
        val left = p.sumOf { (id, ms) -> files[id]?.let { f -> maxOf(0L, (if (f.total >= 0) f.total else ms / Bean.FRAME_MS) - covered(f)) } ?: (ms / Bean.FRAME_MS) }
        syncTotalKb = (caught + left) * Bean.UNIT / 1024; syncDoneKb = caught * Bean.UNIT / 1024
        val id = p.first().first
        return Xfer(id, false, files[id]?.let(::gap) ?: 0)
    }

    private fun begin(x: Xfer) {
        xfer = x; x.at = io.now(); x.seen = x.at; marked = false
        if (x.live && liveFile != x.file) { liveFile = x.file; liveSeen = x.at }
        state = say(if (x.live) "joining" else "beanIdle")
        if (x.live) { syncDoneKb = 0; syncTotalKb = 0 }  // a backlog waits while the bean records
        send(Bean.transfer(x.file, x.from * Bean.UNIT, x.live))
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
            f != null && id * 1000 >= since && id != listing.open && gap(f) == null && q.none { it in f.anchor until f.anchor + f.total * Bean.FRAME_MS }
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
                paused = false
                if (x != null && x.live && x.file != open) {
                    // The bean may skip 1A/0A at rollover: flush as usual, fetch the old tail after stop.
                    complete(x, 0, "；豆子开始录新文件 $open，缺的停录后补传")
                    liveAgain = 0
                    throw Again("file ${x.file}: rolled over to $open")
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
            3 -> { file(x.file, x.file * 1000).let { if (it.total < 0) it.total = x.from }; xfer = null; save() }  // nothing after `from`: all of it is here
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
        io.log("bean_xfer", "${if (x.live) "实时" else "补传"}文件 ${x.file} 没开始：$why", "warn", mapOf("file" to x.file, "from" to x.from))
        if (x.live) liveAgain = io.now() + 30_000 else file(x.file, x.file * 1000).tries++
        xfer = null
        save()
    }

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
        if (x.live && x.last >= 0 && late > 5000)  // the bean paused mid-file (the app never asks it to) or the link stalled
            io.log("bean_gap_time", "文件 ${x.file} 实时单元 ${x.last}→${s.seq} 之间过了 ${(now - x.seen) / 1000} s：豆子若暂停过，" +
                "之后的单元按序号排会早 ${late / 1000} s", "warn", mapOf("file" to x.file, "seq" to s.seq, "late_ms" to late))
        x.units++; x.last = s.seq; x.seen = now
        if (x.live) { liveSeen = now; liveStalled = false; busy = 0 }
        if (s.flags and 4 != 0) f.total = s.seq + 1
        val dupe = has(f, s.seq) || runFile == x.file && s.seq in runFrom until runFrom + run.size
        val mark = s.flags and 1 != 0
        if (mark && !marked && !dupe) io.log("mark", "双击标记", fields = mapOf("file" to x.file, "seq" to s.seq), at = f.anchor + s.seq * Bean.FRAME_MS)
        marked = mark  // also on units had already: a mark that goes on past them is not a new one
        if (x.until in 1..s.seq + 1) x.cut = true
        if (dupe) { x.dupes++; return }
        if (run.isNotEmpty() && (runFile != x.file || s.seq != runFrom + run.size)) flush()
        if (run.isEmpty()) { runFile = x.file; runFrom = s.seq }
        run += audio
        if (!x.live) { caught++; syncDoneKb = caught * Bean.UNIT / 1024 }
        if (run.size >= if (x.live) io.liveUnits() else Bean.PIECE_UNITS) flush()
    }

    /** A transfer's first unit: establish the file's anchor. Live never holds finals: backlog is read only after stop. */
    private fun first(x: Xfer, s: Bean.Slice, audio: ByteArray) {
        x.first = s.seq
        val now = io.now()
        val byId = x.file * 1000
        val est = now - s.seq * Bean.FRAME_MS
        if (x.file !in files) {
            val phone = x.live && est - byId in -2000L..3000L
            file(x.file, if (phone) est else byId)
            if (x.live) io.log("bean_clock", "文件 ${x.file} 的起点按${if (phone) "手机" else "豆子"}的钟：豆子的钟比手机${if (est >= byId) "慢" else "快"} ${kotlin.math.abs(est - byId)} ms",
                fields = mapOf("file" to x.file, "ms" to est - byId))
        }
        io.log("bean_xfer", "${if (x.live) "实时" else "补传"}文件 ${x.file} 开始：首单元 ${s.seq}（请求从 ${x.from} 起），Opus 配置 ${(audio.getOrNull(0)?.toInt() ?: 0) and 255 shr 3}",
            fields = mapOf("file" to x.file, "seq" to s.seq, "from" to x.from, "toc" to ((audio.getOrNull(0)?.toInt() ?: 0) and 255)))
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
            if (x.last >= 0) pendingEnd = maxOf(pendingEnd, f.anchor + (x.last + 1) * Bean.FRAME_MS)
        } else if (gap(f) == null) {
            pendingEnd = maxOf(pendingEnd, f.anchor + f.total * Bean.FRAME_MS)
        } else if (!(x.cut && gap(f)!! > x.from) && ++f.tries > TRIES) {  // cut with the hole filled: not a try
            io.log("bean_xfer", "文件 ${x.file} 从单元 ${gap(f)} 起取了 $TRIES 次仍不全，不再取", "error", mapOf("file" to x.file, "gap" to gap(f)))
        }
        save()
    }

    /** The units taken so far into a piece, dated by its file's anchor. */
    private fun flush() {
        if (run.isEmpty()) return
        val f = files[runFile] ?: return run.clear()
        val start = f.anchor + runFrom * Bean.FRAME_MS
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
    class Again(why: String) : Exception(why)

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
        const val HOLD_MARGIN_MS = 60_000L  // the hold a minute before what is said now: the clocks' drift
    }
}
