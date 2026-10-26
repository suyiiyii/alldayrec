package com.suyiiyii.live_record

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.IOException
import java.math.BigInteger
import java.nio.ByteBuffer
import java.nio.file.Files
import java.security.KeyFactory
import java.security.KeyPair
import java.security.interfaces.ECPublicKey
import java.security.spec.ECPrivateKeySpec

/** The bean's protocol against ws/soundcore's byte vectors (fixtures/bean-crypto.json, 0BSD), and BeanSync against a
 *  fake bean (FakeBean below: synthetic keys, files and audio, a made-up serial and MAC; nothing of a real bean). */
class BeanTest {
    private val v = JSONObject(javaClass.getResource("/bean-crypto.json")!!.readText())
    private fun hex(s: String) = s.chunked(2).map { it.toInt(16).toByte() }.toByteArray()
    private fun field(o: String, k: String) = hex(v.getJSONObject(o).getString(k))

    @Test fun commands() {  // ws/soundcore's command vectors
        assertEquals("08ee0000000101" + "0a0002", Bean.hex(Bean.info()))
        assertEquals("08ee0000001a0e0c0000002a", Bean.hex(Bean.list(0)))
        assertEquals("04030201fb", Bean.hex(Bean.clock(0x01020304, -5).let { it.copyOfRange(9, it.size - 1) }))
        assertEquals("a60000000403020101", Bean.hex(Bean.transfer(0x01020304, 166, true).let { it.copyOfRange(9, it.size - 1) }))
        for (f in listOf(Bean.info(), Bean.list(3), Bean.clock(1, 8), Bean.transfer(1, 0, false), Bean.hello(ByteArray(65)))) assertTrue(Bean.allowed(f))
    }

    @Test fun onlyFiveCommandsGoOut() {
        assertEquals(setOf(0x0101, 0x01A6, 0x2E01, 0x1A0E, 0x1A07), Bean.SENDS)
        // bind, auth, unbind, the record button, delete, Wi-Fi, completion ack, SPP reads, factory reset and OTA, a log read
        for (cmd in listOf(0x0B87, 0x0B8B, 0x0B01, 0x01B8, 0x1882, 0x1A10, 0x1A05, 0x1A02, 0x1A0A, 0x1A0F, 0x1A11, 0xFF20, 0xFF01, 0x1B0E))
            assertThrows(IllegalArgumentException::class.java) { Bean.frame(cmd shr 8, cmd and 255) }
        // a frame made some other way: well formed, and still not sent
        fun made(type: Int, id: Int, vararg p: Byte) = byteArrayOf(0x08, 0xEE.toByte(), 0, 0, 0, type.toByte(), id.toByte(), (10 + p.size).toByte(), 0)
            .plus(p).let { it + it.sum().toByte() }
        assertFalse(Bean.allowed(made(0x0B, 0x87)))
        // the record button: its start alone (the frame sent to the owner's bean, 10-25), never a stop (00) or pause (02)
        assertEquals("08ee00000018820b00019c", Bean.hex(Bean.start()))
        assertTrue(Bean.allowed(Bean.start()))
        assertArrayEquals(Bean.start(), made(0x18, 0x82, 1))
        for (p in listOf<Byte>(0, 2)) assertFalse(Bean.allowed(made(0x18, 0x82, p)))
        assertFalse(Bean.allowed(made(0x18, 0x82)))
        assertFalse(Bean.allowed(made(0x18, 0x82, 1, 0)))
        val info = Bean.info()
        assertFalse(Bean.allowed(info.copyOf().also { it[9] = 0 }))  // a wrong sum
        // a delete: only of a file the server has whole, framed and sent for that file alone (ws/soundcore's vector)
        assertThrows(IllegalArgumentException::class.java) { Bean.delete(0x01020304, emptySet()) }
        assertThrows(IllegalArgumentException::class.java) { Bean.delete(0x01020304, setOf(7)) }
        val del = Bean.delete(0x01020304, setOf(0x01020304))
        assertEquals("08ee0000001a100e00" + "04030201", Bean.hex(del).dropLast(2))
        assertTrue(Bean.allowed(del, setOf(0x01020304)))
        assertFalse(Bean.allowed(del))
        assertFalse(Bean.allowed(del, setOf(7)))
    }

    @Test fun reader() {  // ws/soundcore's framing vectors: one frame, and one split over notifications after junk
        val r = Bean.Reader()
        val f = r.push(hex("09ff00000110a20a00c5")).single()
        assertEquals(listOf(0x10, 0xA2, 1, 0), listOf(f.type, f.id, f.status, f.payload.size))
        assertTrue(r.push(hex("de09")).isEmpty())
        assertTrue(r.push(hex("ff00000110a2")).isEmpty())
        assertEquals(0x10A2, r.push(hex("0a00c5")).single().cmd)
        // a frame whose sum is off is skipped, the next one is read
        assertEquals(0x10A2, r.push(hex("09ff00000110a20a00c4") + hex("09ff00000110a20a00c5")).single().cmd)
        assertFalse(Bean.Frame(0x01, 0x01, 0x11, ByteArray(0)).ok)  // device commands: status 1 only
        assertTrue(Bean.Frame(0x1A, 0x07, 0x11, ByteArray(0)).ok)
    }

    @Test fun crypto() {
        assertArrayEquals(field("aes_256_ctr", "ciphertext_hex"),
            Bean.ctr(field("aes_256_ctr", "key_hex"), field("aes_256_ctr", "iv_hex"), field("aes_256_ctr", "plaintext_hex")))
        assertArrayEquals(field("hkdf_sha256", "derived_key_hex"), Bean.hkdf(field("hkdf_sha256", "shared_secret_hex")))
        // ECDH: the app's private value 3, the device's public key (its private value 4)
        val like = Bean.keys().public as ECPublicKey
        val app = Bean.point(field("ecdh_p256", "app_public_key_hex"), like)
        assertArrayEquals(field("ecdh_p256", "app_public_key_hex"), Bean.pub(app))
        val mine = KeyPair(app, KeyFactory.getInstance("EC").generatePrivate(ECPrivateKeySpec(BigInteger.valueOf(3), like.params)))
        val device = field("ecdh_p256", "device_public_key_hex")
        val shared = field("ecdh_p256", "shared_secret_hex")
        assertArrayEquals(shared, Bean.shared(mine, Bean.point(device, like)))
        assertArrayEquals(Bean.hkdf(shared), Bean.session(mine, device + shared))
        assertThrows(IllegalArgumentException::class.java) { Bean.session(mine, device + ByteArray(32)) }  // not the secret we got
        assertArrayEquals(field("file_key", "file_key_hex"),
            Bean.fileKey(field("file_key", "session_key_hex"), field("file_key", "session_nonce_hex"), field("file_key", "encrypted_file_key_hex")))
        val nonce = field("audio_counter", "nonce_hex")
        assertArrayEquals(field("audio_counter", "counter_hex"), Bean.iv(nonce, 3))
        assertArrayEquals(field("audio_counter", "max_counter_hex"), Bean.iv(nonce, Bean.MAX_SEQ))
        assertThrows(IllegalArgumentException::class.java) { Bean.iv(nonce, Bean.MAX_SEQ + 1) }
        assertArrayEquals(field("audio_slice", "plaintext_hex"),
            Bean.opus(field("file_key", "file_key_hex"), nonce, Bean.Slice(3, 0, field("audio_slice", "ciphertext_hex"))))
    }

    @Test fun parsing() {
        val unit = Bean.le32(7) + byteArrayOf(5) + ByteArray(160) { it.toByte() } + byteArrayOf(0)
        val s = Bean.slices(unit + unit).first()
        assertEquals(7L, s.seq); assertEquals(5, s.flags)
        assertArrayEquals(ByteArray(160) { it.toByte() }, s.data)
        assertThrows(IllegalArgumentException::class.java) { Bean.slices(unit.copyOf(165)) }
        val hw = Bean.info(FakeBean(0).info())
        assertEquals(listOf(80, "battery", "01.00", 20_000_000L, 60, false), listOf(hw["battery"], hw["charge"], hw["fw"], hw["free_kb"], hw["case_battery"], hw["recording"]))
        assertEquals("fake000000000001", Bean.serial(FakeBean(0).info()))
        assertEquals(listOf(10, 100, 100), listOf(Bean.pct(0), Bean.pct(9), Bean.pct(100)))
        val l = Bean.listing(Bean.u16le(2) + Bean.le32(11) + Bean.le32(2000) + Bean.le32(12) + Bean.le32(4000) + Bean.le32(13) + ByteArray(4))
        assertEquals(listOf(11L to 2000L, 12L to 4000L), l.files)
        assertEquals(13L, l.open)
        // the name is from the MAC in the manufacturer data (company id first), whatever address it comes from
        val adv = byteArrayOf(0, 0, 0x0A, 0x0B, 0x91.toByte(), 0, 0)
        assertEquals("D3200-0A0B", Bean.name(0x0002, adv))
        assertEquals(0x91, Bean.flags(0x0002, adv))
        assertNull(Bean.name(0x0002, byteArrayOf(1, 2, 3)))
        assertTrue(Bean.isBean("D3200-0A0B")); assertFalse(Bean.isBean("LR-P3-0A0B"))
    }

    /** What a fake bean records goes up as pieces, each unit once and dated by its seq: live from where the bean is
     *  (begun 30 s before), on in the next file it rotates to, a drop mid-file resumed by its byte offset, a hole
     *  asked for again once the recording stops, the start of the first file pulled as backlog; the hold until all of
     *  it is up, then the end. A file from before the bean was added is never asked for. */
    @Test fun sync() {
        val dir = Files.createTempDirectory("bean").toFile()
        val chunks = File(dir, "chunks"); val state = File(dir, "state.json")
        val t0 = 1_793_000_000_000L
        val since = t0 - 3_600_000
        val bean = FakeBean(t0)
        val a = (t0 - 30_000) / 1000; val old = since / 1000 - 100
        var b = 0L
        bean.recording = true; bean.open = a; bean.listed[old] = 60_000
        bean.onTransfer = { file, from, live ->
            when {
                live && file == a -> {  // live from where the bean is, whatever was asked: 1500 units (30 s) in
                    bean.header(file, 0xFFFFFFFFL)
                    bean.units(file, (1500L..1749L).toList(), last = 1749)
                    bean.complete(file)
                    b = (bean.now + 5000) / 1000 + 1
                    bean.rotation(b); bean.open = b; bean.listed[a] = 35_000
                }
                live && file == b && from == 0L -> {
                    bean.header(file, 0xFFFFFFFFL)
                    bean.units(file, (0L..199L).toList())
                    bean.script += { throw IOException("link dropped") }
                }
                live && file == b -> {  // again after the drop, from its offset; ten units lost on the way
                    bean.header(file, 0xFFFFFFFFL)
                    bean.units(file, (200L..249L) + (260L..299L), last = 299)
                    bean.complete(file)
                    bean.push(0x1882, byteArrayOf(0))
                    bean.recording = false; bean.open = 0; bean.listed[b] = 6000
                }
                !live && file == a -> { bean.header(file, 1750L * Bean.UNIT); bean.units(file, (from..1749L).toList(), last = 1749); bean.complete(file) }
                !live && file == b -> { bean.header(file, 300L * Bean.UNIT); bean.units(file, (from..299L).toList(), last = 299); bean.complete(file) }
                else -> fail("asked for $file from $from live $live")
            }
        }
        val one = BeanSync("D3200-0A0B", state, chunks, bean)
        assertEquals("link dropped", assertThrows(IOException::class.java) { one.session(since, "") }.message)
        val anchorA = one.files[a]!!.anchor
        val anchors = one.files.mapValues { it.value.anchor }  // the files go once deleted on the bean
        assertTrue("the phone's clock, near the bean's", anchorA - a * 1000 in 0L..3000L)
        assertEquals("live began past the file's start: the hold", anchorA + 1500 * 20 - 60_000, bean.held)
        assertTrue(Bean.u32(bean.sentOf(0x01A6), 0) in t0 / 1000 - 1..t0 / 1000 + 1)

        // a new session (what the phone knows of the files read back): on in B from its offset, then the backlog
        val pieces = mutableListOf<Pair<Long, List<ByteArray>>>()
        bean.script += {  // the uploader takes every piece, then time goes on
            for (f in Uploader.queue(chunks)) { pieces += Uploader.startMs(f) to packets(f); f.delete() }
            bean.now += 1000
        }
        bean.script += { bean.now += 6000 }
        val two = BeanSync("D3200-0A0B", state, chunks, bean)
        bean.confirmed = { two.deletable }
        assertThrows(IOException::class.java) { two.session(since, "") }
        assertEquals(listOf(Triple(a, 0L, true), Triple(b, 0L, true), Triple(b, 200L, true), Triple(a, 0L, false), Triple(b, 250L, false)), bean.transfers)
        assertTrue(bean.logs.any { it.first == "bean_gap" && "250..259" in it.second })

        val seen = mutableMapOf<Long, MutableList<Long>>()
        for ((start, ps) in pieces) {
            assertEquals("stereo", 2, ps[0][9].toInt())
            for ((i, p) in ps.drop(2).withIndex()) {
                val seq = ByteBuffer.wrap(p, 1, 4).int.toLong()
                val file = if (p[5] == (a % 251).toByte()) a else b
                assertEquals(0xBC.toByte(), p[0])  // decrypted
                assertEquals("dated by its seq", anchors[file]!! + seq * 20, start + i * 20)
                seen.getOrPut(file) { mutableListOf() } += seq
            }
        }
        assertEquals("each unit once", (0L..1749L).toList(), seen[a]!!.sorted())
        assertEquals((0L..299L).toList(), seen[b]!!.sorted())
        assertNull("all up: no hold", bean.held)
        assertEquals(listOf(anchors[b]!! + 300 * 20), bean.ends)
        assertFalse(bean.transfers.any { it.first == old })
        assertEquals("whole on the server: off the bean, once", listOf(a, b), bean.deleted)
    }

    /** A transfer asked for right after the one before ended got code 4 (10-25): the next waits 200 ms as the SDK does,
     *  and a code 4 is asked again after a moment without counting as a try. */
    @Test fun paced() {
        val dir = Files.createTempDirectory("bean").toFile()
        val t0 = 1_793_000_000_000L
        val bean = FakeBean(t0)
        val p = t0 / 1000 - 600; val q = p + 100
        bean.listed[p] = 200; bean.listed[q] = 200
        var busy = true
        bean.onTransfer = { file, _, _ ->
            if (file == q && busy) { busy = false; bean.header(file, 10L * Bean.UNIT, code = 4) }
            else { bean.header(file, 10L * Bean.UNIT); bean.units(file, (0L..9L).toList(), last = 9); bean.complete(file) }
        }
        repeat(20) { bean.script += {} }
        val sync = BeanSync("D3200-0A0B", File(dir, "state.json"), File(dir, "chunks"), bean)
        assertThrows(IOException::class.java) { sync.session(t0 - 3_600_000, "") }
        assertEquals(listOf(p, q, q), bean.transfers.map { it.first })
        val asked = bean.sent.indices.filter { bean.sent[it][5] == 0x1A.toByte() && bean.sent[it][6] == 0x07.toByte() }.map { bean.sentAt[it] }
        assertTrue("200 ms after p's end", asked[1] - asked[0] >= 200 + 10 * 20)
        assertTrue("and after the code 4", asked[2] - asked[1] >= 200)
        assertEquals("not a try", 0, sync.files[q]!!.tries)
        assertNull(sync.gap(sync.files[q]!!))
        assertEquals(1, bean.logs.count { "code 4" in it.second })
    }

    /** Live began 36 units into the file (the bean starts realtime where it is, 10-25) and missed its last 7 (10-25: a
     *  pause). The re-pull from 0 is cut once unit 35 is in (the link goes: the bean cannot be stopped otherwise), not
     *  7287 more units; the next connection asks from the tail's offset. */
    @Test fun headAndTail() {
        val dir = Files.createTempDirectory("bean").toFile()
        val t0 = 1_793_000_000_000L
        val bean = FakeBean(t0)
        val c = t0 / 1000
        bean.recording = true; bean.open = c
        bean.onTransfer = { file, from, live ->
            if (live) {
                bean.header(file, 0xFFFFFFFFL)
                bean.units(file, (36L..7322L).toList())
                bean.push(0x1882, byteArrayOf(0)); bean.complete(file)
                bean.recording = false; bean.open = 0; bean.listed[c] = 7330L * 20
            } else { bean.header(file, 7330L * Bean.UNIT); bean.units(file, (from..7329L).toList(), last = 7329); bean.complete(file) }
        }
        val sync = BeanSync("D3200-0A0B", File(dir, "state.json"), File(dir, "chunks"), bean)
        assertThrows(BeanSync.Again::class.java) { sync.session(t0 - 3_600_000, "") }
        assertTrue("the rest not taken", bean.queued > 2000)
        assertEquals(7323L, sync.gap(sync.files[c]!!))
        assertTrue(bean.logs.any { "断开连接" in it.second })
        assertThrows(IOException::class.java) { sync.session(t0 - 3_600_000, "") }  // connected again
        assertEquals(listOf(Triple(c, 0L, true), Triple(c, 0L, false), Triple(c, 7323L, false)), bean.transfers)
        assertNull("all of it on the phone", sync.gap(sync.files[c]!!))
        assertEquals("the cut is not a try", 0, sync.files[c]!!.tries)
        assertEquals("recording at connect: no start", 0, bean.starts)
    }

    /** 10-25 T2: live, the link drops (the phone's Bluetooth off 30 s), live again from where the bean is, the bean
     *  stops; the head and the middle in one re-pull (cut), the tail on the next connection; then the app is killed
     *  before the pieces are up. The recording's end still goes to the server, once, after all of it is up. */
    @Test fun endAfterDropFillsAndKill() {
        val dir = Files.createTempDirectory("bean").toFile()
        val chunks = File(dir, "chunks")
        val state = File(dir, "state.json")
        val t0 = 1_793_000_000_000L
        val bean = FakeBean(t0)
        val c = t0 / 1000
        bean.recording = true; bean.open = c; bean.listed[c] = 0
        var live = 0
        bean.onTransfer = { file, from, isLive ->
            if (isLive && live++ == 0) {
                bean.header(file, 0xFFFFFFFFL); bean.units(file, (36L..1092L).toList())
                bean.script += { throw IOException("link gone") }
            } else if (isLive) {
                bean.header(file, 0xFFFFFFFFL); bean.units(file, (3120L..5687L).toList())
                bean.push(0x1882, byteArrayOf(0)); bean.complete(file)
                bean.recording = false; bean.open = 0; bean.listed[c] = 5699L * 20
            } else {
                bean.header(file, 5699L * Bean.UNIT); bean.units(file, (from..5698L).toList(), last = 5698); bean.complete(file)
                if (from > 0) bean.script += { throw IOException("app killed") }
            }
        }
        val since = t0 - 3_600_000
        val sync = BeanSync("D3200-0A0B", state, chunks, bean)
        bean.confirmed = { sync.deletable }
        assertEquals("link gone", assertThrows(IOException::class.java) { sync.session(since, "") }.message)
        assertThrows(BeanSync.Again::class.java) { sync.session(since, "") }
        assertEquals("app killed", assertThrows(IOException::class.java) { sync.session(since, "") }.message)
        assertEquals(listOf(Triple(c, 0L, true), Triple(c, 0L, true), Triple(c, 0L, false), Triple(c, 5688L, false)), bean.transfers)
        val anchor = sync.files[c]!!.anchor
        assertEquals("not yet: its pieces are not up", emptyList<Long>(), bean.ends)

        val again = BeanSync("D3200-0A0B", state, chunks, bean)  // the app back
        bean.confirmed = { again.deletable }
        bean.script += { for (f in Uploader.queue(chunks)) f.delete(); bean.now += 1000 }  // all of it goes up
        repeat(12) { bean.script += {} }
        assertThrows(IOException::class.java) { again.session(since, "") }
        assertEquals("the end, once", listOf(anchor + 5699L * 20), bean.ends)
        assertEquals(4, bean.transfers.size)
        assertEquals(listOf(c), bean.deleted)
    }

    /** A double tap is logged once, not again when a re-pull reads its units; a realtime unit far later on the wall
     *  clock than its seq says is logged (a pause would date what follows early). */
    @Test fun marksOnceAndTimeJumps() {
        val dir = Files.createTempDirectory("bean").toFile()
        val t0 = 1_793_000_000_000L
        val bean = FakeBean(t0)
        val d = t0 / 1000
        bean.recording = true; bean.open = d
        bean.onTransfer = { file, from, live ->
            if (live) {
                bean.header(file, 0xFFFFFFFFL)
                bean.units(file, (10L..60L).toList(), marks = setOf(40, 41, 42))
                bean.units(file, (61L..99L).toList(), last = 99, wait = 10_000)
                bean.push(0x1882, byteArrayOf(0)); bean.complete(file)
                bean.recording = false; bean.open = 0; bean.listed[d] = 100L * 20
            } else { bean.header(file, 100L * Bean.UNIT); bean.units(file, (from..99L).toList(), last = 99, marks = setOf(40, 41, 42)); bean.complete(file) }
        }
        val sync = BeanSync("D3200-0A0B", File(dir, "state.json"), File(dir, "chunks"), bean)
        assertThrows(IOException::class.java) { sync.session(t0 - 3_600_000, "") }
        assertEquals(listOf(Triple(d, 0L, true), Triple(d, 0L, false)), bean.transfers)
        assertNull(sync.gap(sync.files[d]!!))
        assertEquals(1, bean.logs.count { it.first == "mark" })
        assertEquals(1, bean.logs.count { it.first == "bean_gap_time" })
    }

    /** The owner, 10-26: a bean connected records. An idle bean is started once a connection (18/82 01, nothing
     *  else of the button), goes live from its new file; stopped by hand, it is not started again in the connection,
     *  nor in the reconnect that cuts the head's re-pull; the next connection starts it again (here the bean only echoes:
     *  logged, not asked again). */
    @Test fun autoStart() {
        val dir = Files.createTempDirectory("bean").toFile()
        val t0 = 1_793_000_000_000L
        val since = t0 - 3_600_000
        val bean = FakeBean(t0)
        bean.startable = true
        bean.onTransfer = { file, from, live ->
            if (live) {  // where the bean is (36 units in, 10-25), until the button stops it
                bean.header(file, 0xFFFFFFFFL)
                bean.units(file, (36L..1999L).toList())
                bean.push(0x1882, byteArrayOf(0)); bean.complete(file)
                bean.recording = false; bean.open = 0; bean.listed[file] = 2000L * 20
            } else { bean.header(file, 2000L * Bean.UNIT); bean.units(file, (from..1999L).toList(), last = 1999); bean.complete(file) }
        }
        val sync = BeanSync("D3200-0A0B", File(dir, "state.json"), File(dir, "chunks"), bean)
        assertThrows(BeanSync.Again::class.java) { sync.session(since, "") }
        val c = t0 / 1000  // the file the start began
        assertEquals(listOf(Triple(c, 0L, true), Triple(c, 0L, false)), bean.transfers)
        assertEquals(1, bean.starts)
        assertEquals(listOf("自动开始录音"), bean.logs.filter { it.first == "bean_start" }.map { it.second })
        assertThrows(IOException::class.java) { sync.session(since, "") }  // the app's own reconnect: the stop holds
        assertEquals(1, bean.starts)
        assertNull("all of it on the phone", sync.gap(sync.files[c]!!))
        bean.startable = false
        bean.script += {}  // the wait for its new file
        assertThrows(IOException::class.java) { sync.session(since, "") }  // a new connection
        assertEquals(2, bean.starts)
        assertTrue(bean.logs.last { it.first == "bean_start" }.second.startsWith("自动开始录音没成功"))
        assertTrue(bean.sent.filter { it[5] == 0x18.toByte() }.all { it.contentEquals(Bean.start()) })
    }

    /** A live transfer never polls info: reports must follow the new file, stop, and battery/charge pushes. */
    @Test fun liveStatusFollowsPushes() {
        val dir = Files.createTempDirectory("bean").toFile()
        val t0 = 1_793_000_000_000L
        val bean = FakeBean(t0)
        bean.startable = true
        bean.onTransfer = { file, _, live ->
            assertTrue(live)
            bean.header(file, 0xFFFFFFFFL)
        }
        val sync = BeanSync("D3200-0A0B", File(dir, "state.json"), File(dir, "chunks"), bean)
        bean.script += {
            assertTrue("new file updates hardware immediately", sync.hw["recording"] == true)
            bean.push(0x0103, byteArrayOf(8, 6))
            bean.push(0x0104, byteArrayOf(1, 1))
        }
        bean.script += { bean.now += 60_000 }
        bean.script += {
            val hw = bean.reports.last()
            assertEquals(listOf(true, 90, 70, "charging", true),
                listOf(hw["recording"], hw["battery"], hw["case_battery"], hw["charge"], hw["case_charging"]))
            bean.push(0x1882, byteArrayOf(0))
            bean.push(0x0104, byteArrayOf(0, 0))
            bean.push(0x0103, byteArrayOf(7, 5))
        }
        bean.script += { bean.now += 60_000 }
        assertThrows(IOException::class.java) { sync.session(t0 - 3_600_000, "") }
        assertFalse(sync.recording)
        assertEquals(listOf(false, 80, 60, "battery", false), bean.reports.last().let {
            listOf(it["recording"], it["battery"], it["case_battery"], it["charge"], it["case_charging"])
        })
        assertEquals("no info command during live", 1, bean.sent.count { it[5] == 1.toByte() && it[6] == 1.toByte() })
        assertEquals("free space remains the last info reading", 20_000_000L, bean.reports.last()["free_kb"])
        assertEquals("previous reports are immutable snapshots", false, bean.reports.first()["recording"])
    }

    /** A button's record push updates status even before a file notification. */
    @Test fun buttonStatusFollowsPushes() {
        val dir = Files.createTempDirectory("bean").toFile()
        val t0 = 1_793_000_000_000L
        val bean = FakeBean(t0)
        val sync = BeanSync("D3200-0A0B", File(dir, "state.json"), File(dir, "chunks"), bean)
        repeat(3) { bean.script += {} }  // auto-start only echoes: this is still idle
        bean.script += {
            assertFalse(sync.recording)
            assertEquals(false, sync.hw["recording"])
            bean.push(0x1882, byteArrayOf(1))
        }
        bean.script += {
            assertTrue(sync.recording)
            assertEquals(true, sync.hw["recording"])
            bean.push(0x1882, byteArrayOf(0))
        }
        bean.script += {
            assertFalse(sync.recording)
            assertEquals(false, sync.hw["recording"])
        }
        assertThrows(IOException::class.java) { sync.session(t0 - 3_600_000, "") }
    }

    /** A one-byte charge notification updates the bean alone; an empty push changes nothing. */
    @Test fun shortChargePushKeepsCaseStatus() {
        val dir = Files.createTempDirectory("bean").toFile()
        val t0 = 1_793_000_000_000L
        val bean = FakeBean(t0)
        bean.recording = true; bean.open = t0 / 1000
        bean.onTransfer = { file, _, _ ->
            bean.header(file, 0xFFFFFFFFL)
            bean.push(0x0104, byteArrayOf(0, 1))
        }
        val sync = BeanSync("D3200-0A0B", File(dir, "state.json"), File(dir, "chunks"), bean)
        bean.script += {
            assertEquals("battery", sync.hw["charge"])
            assertEquals(true, sync.hw["case_charging"])
            bean.push(0x0104, byteArrayOf(1))
            bean.push(0x0104, byteArrayOf())
            bean.push(0x0103, byteArrayOf(0))
        }
        assertThrows(IOException::class.java) { sync.session(t0 - 3_600_000, "") }
        assertEquals("charging", sync.hw["charge"])
        assertEquals(true, sync.hw["case_charging"])
        assertEquals(80, sync.hw["battery"])
        assertEquals(60, sync.hw["case_battery"])
    }

    /** The bean echoes the start and records nothing (03.44 does, per ws/soundcore): not taken for a start; logged,
     *  not asked again, and the backlog goes on. */
    @Test fun startEchoedOnly() {
        val dir = Files.createTempDirectory("bean").toFile()
        val t0 = 1_793_000_000_000L
        val bean = FakeBean(t0)
        val p = t0 / 1000 - 600
        bean.listed[p] = 200
        bean.onTransfer = { file, _, _ -> bean.header(file, 10L * Bean.UNIT); bean.units(file, (0L..9L).toList(), last = 9); bean.complete(file) }
        repeat(3) { bean.script += {} }
        val sync = BeanSync("D3200-0A0B", File(dir, "state.json"), File(dir, "chunks"), bean)
        assertThrows(IOException::class.java) { sync.session(t0 - 3_600_000, "") }
        assertEquals(1, bean.starts)
        assertEquals(listOf("自动开始录音没成功（答了，3 s 内没开始录新文件），这次连接不再试"), bean.logs.filter { it.first == "bean_start" }.map { it.second })
        assertFalse(sync.recording)
        assertEquals(false, sync.hw["recording"])
        assertEquals(listOf(Triple(p, 0L, false)), bean.transfers)
    }

    /** Recording at connect, its button pressed to stop it during the handshake (after the info, before the start is
     *  settled): that stop holds, no start. */
    @Test fun stopInHandshake() {
        val dir = Files.createTempDirectory("bean").toFile()
        val t0 = 1_793_000_000_000L
        val bean = FakeBean(t0)
        bean.recording = true; bean.open = t0 / 1000
        bean.onSend = { if (it == 0x01A6) { bean.push(0x1882, byteArrayOf(0)); bean.recording = false; bean.open = 0 } }
        repeat(3) { bean.script += {} }
        val sync = BeanSync("D3200-0A0B", File(dir, "state.json"), File(dir, "chunks"), bean)
        assertThrows(IOException::class.java) { sync.session(t0 - 3_600_000, "") }
        assertTrue(bean.logs.any { it.first == "bean_stop" })
        assertEquals("the stop holds", 0, bean.starts)
        assertEquals(false, sync.hw["recording"])
    }

    /** A transfer stalled 30 s ends the session (the link is let go), as a cut does: no new connection either, so a
     *  stop by hand still holds. */
    @Test fun stopHoldsAfterAStall() {
        val dir = Files.createTempDirectory("bean").toFile()
        val t0 = 1_793_000_000_000L
        val since = t0 - 3_600_000
        val bean = FakeBean(t0)
        bean.startable = true
        var pulls = 0
        bean.onTransfer = { file, from, live ->
            if (live) {
                bean.header(file, 0xFFFFFFFFL)
                bean.units(file, (36L..1999L).toList())
                bean.push(0x1882, byteArrayOf(0)); bean.complete(file)
                bean.recording = false; bean.open = 0; bean.listed[file] = 2000L * 20
            } else if (pulls++ == 0) { bean.header(file, 2000L * Bean.UNIT); bean.units(file, (0L..9L).toList()) }  // then nothing
            else { bean.header(file, 2000L * Bean.UNIT); bean.units(file, (from..1999L).toList(), last = 1999); bean.complete(file) }
        }
        repeat(40) { bean.script += {} }
        val sync = BeanSync("D3200-0A0B", File(dir, "state.json"), File(dir, "chunks"), bean)
        assertEquals("file ${t0 / 1000}: no audio for 30 s", assertThrows(Exception::class.java) { sync.session(since, "") }.message)
        assertEquals(1, bean.starts)
        bean.script.clear()
        assertThrows(BeanSync.Again::class.java) { sync.session(since, "") }  // back for the rest of the head
        assertEquals("the bean stays stopped", 1, bean.starts)
    }

    /** Off the bean go only files the server has whole: not one with a hole, one with a piece the server refused or
     *  still queued, the file being recorded, or one from before the bean was added here; a whole one, once. */
    @Test fun prune() {
        val dir = Files.createTempDirectory("bean").toFile()
        val chunks = File(dir, "chunks").apply { mkdirs() }; val state = File(dir, "state.json")
        val t0 = 1_793_000_000_000L
        val since = t0 - 3_600_000
        val old = since / 1000 - 100; val hole = t0 / 1000 - 900; val refused = hole + 100; val queued = hole + 200
        val whole = hole + 300; val open = hole + 400; val nay = hole + 500
        fun f(id: Long, h: String, tries: Int = 0) = "\"$id\":{\"a\":${id * 1000},\"t\":10,\"r\":$tries,\"h\":$h}"
        state.writeText("{" + listOf(f(old, "[[0,10]]"), f(hole, "[[0,4],[6,10]]", tries = 4), f(refused, "[[0,10]]"), f(queued, "[[0,10]]"),
            f(whole, "[[0,10]]"), f(open, "[[0,10]]"), f(nay, "[[0,10]]")).joinToString(",") + "}")
        File(chunks, "${queued * 1000}@D3200-0A0B.ogg").writeBytes(ByteArray(8))
        val bean = FakeBean(t0)
        for (id in listOf(old, hole, refused, queued, whole, nay)) bean.listed[id] = 200
        bean.refuseDelete = setOf(nay)  // the bean says no: it stays, and is not asked again
        bean.open = open
        bean.refused = setOf(refused * 1000 + 60)
        val sync = BeanSync("D3200-0A0B", state, chunks, bean)
        bean.confirmed = { sync.deletable }
        repeat(6) { bean.script += {} }
        bean.script += { File(chunks, "${queued * 1000}@D3200-0A0B.ogg").delete() }  // its last piece up
        repeat(12) { bean.script += {} }
        assertThrows(IOException::class.java) { sync.session(since, "") }
        assertEquals(listOf(whole, nay, queued), bean.deleted)
        assertEquals(setOf(old, hole, refused, nay), bean.listed.keys)
        assertTrue(nay in sync.files)
        assertTrue(bean.logs.any { it.first == "bean_delete" && "$whole" in it.second && "10 个单元" in it.second })
        assertFalse(whole in sync.files)
        assertTrue(sync.deletable.isEmpty())
    }

    /** An Ogg file's packets (OpusHead, OpusTags, then the audio). */
    private fun packets(f: File): List<ByteArray> {
        val b = f.readBytes(); val out = mutableListOf<ByteArray>(); var cur = ByteArrayOutputStream(); var p = 0
        while (p < b.size) {
            assertEquals("OggS", String(b, p, 4))
            val n = b[p + 26].toInt() and 255
            var body = p + 27 + n
            for (i in 0 until n) {
                val l = b[p + 27 + i].toInt() and 255
                cur.write(b, body, l); body += l
                if (l < 255) { out += cur.toByteArray(); cur = ByteArrayOutputStream() }
            }
            p = body
        }
        return out
    }
}

/** A bean as the protocol has it, scripted, for BeanSync: it answers the five commands and the start (`startable`: it
 *  records in a new file, else it only echoes it), wraps file keys in the session key of each connection and encrypts each
 *  unit as a bean does; `onTransfer` says what a 1A/07 starts and `script` what happens when the phone waits a second
 *  or more and nothing is on its way (out of script: the link goes). */
class FakeBean(var now: Long) : BeanSync.Io {
    private val keys = Bean.keys()
    private var session = ByteArray(0)
    private val rnd = java.util.Random(1)
    private val out = ArrayDeque<Pair<ByteArray, Long>>()  // notifications, and the ms before each
    private val fileKeys = mutableMapOf<Long, Pair<ByteArray, ByteArray>>()
    var recording = false
    var open = 0L
    val listed = linkedMapOf<Long, Long>()
    val sent = mutableListOf<ByteArray>()
    val sentAt = mutableListOf<Long>()
    var completedAt = -1L  // when the last 1A/0A came
    val queued get() = out.size  // notifications not taken yet
    val transfers = mutableListOf<Triple<Long, Long, Boolean>>()
    var onTransfer: (Long, Long, Boolean) -> Unit = { _, _, _ -> }
    val script = ArrayDeque<() -> Unit>()
    val logs = mutableListOf<Pair<String, String>>()
    var held: Long? = null
    val ends = mutableListOf<Long>()
    var refused = emptySet<Long>()
    val deleted = mutableListOf<Long>()  // each 1A/10 asked
    var refuseDelete = emptySet<Long>()
    var confirmed: () -> Set<Long> = { emptySet() }  // what the phone may delete (BeanSync.deletable)
    var startable = false
    var starts = 0  // each 18/82 asked
    var onSend: (Int) -> Unit = {}  // after the bean's answer to each command

    fun sentOf(cmd: Int) = sent.last { (it[5].toInt() and 255) shl 8 or (it[6].toInt() and 255) == cmd }.let { it.copyOfRange(9, it.size - 1) }

    override fun send(frame: ByteArray) {
        assertTrue("only the five commands, a delete of a confirmed file and the start reach the bean", Bean.allowed(frame, confirmed()))
        sent += frame; sentAt += now
        val p = frame.copyOfRange(9, frame.size - 1)
        when ((frame[5].toInt() and 255) shl 8 or (frame[6].toInt() and 255)) {
            0x2E01 -> {  // a new connection: nothing of the one before is on its way
                out.clear()
                val s = Bean.shared(keys, Bean.point(p, keys.public as ECPublicKey))
                session = Bean.hkdf(s)
                push(0x2E01, Bean.pub(keys.public as ECPublicKey) + s)
            }
            0x0101 -> push(0x0101, info())
            0x01A6 -> push(0x01A6, ByteArray(0))
            0x1A0E -> push(0x1A0E, if (Bean.u16(p, 0) > 0) Bean.u16le(0) + Bean.le32(open) + ByteArray(4)
                else listed.entries.fold(Bean.u16le(listed.size)) { acc, (id, ms) -> acc + Bean.le32(id) + Bean.le32(ms) } + Bean.le32(open) + ByteArray(4))
            0x1A10 -> Bean.u32(p, 0).let {
                deleted += it
                if (it in refuseDelete) push(0x1A10, ByteArray(0), status = 2) else { listed.remove(it); push(0x1A10, ByteArray(0)) }
            }
            0x1882 -> {  // as the owner's bean (10-25): the command echoed (01 9c) whatever it does, the new file 850 ms on
                starts++
                push(0x1882, byteArrayOf(p[0], frame.last()))
                if (startable) { recording = true; open = now / 1000; rotation(open, wait = 850) }
            }
            0x1A07 -> Triple(Bean.u32(p, 4), Bean.u32(p, 0) / Bean.UNIT, p[8] == 1.toByte()).let { transfers += it; onTransfer(it.first, it.second, it.third) }
        }
        onSend((frame[5].toInt() and 255) shl 8 or (frame[6].toInt() and 255))
    }

    override fun receive(ms: Long): ByteArray? {
        if (out.isEmpty() && ms < 1000) { now += ms; return null }  // the pause between transfers: not a script step
        if (out.isEmpty()) (script.removeFirstOrNull() ?: throw IOException("out of script"))()
        val (b, wait) = out.removeFirstOrNull() ?: run { now += ms; return null }
        now += wait
        if (b[0] == 0x09.toByte() && b[5] == 0x1A.toByte() && b[6] == 0x0A.toByte()) completedAt = now
        return b
    }

    override fun now() = now
    override fun liveUnits() = 50
    override fun log(kind: String, text: String, level: String?, fields: Map<String, Any?>, at: Long?) { logs += kind to text }
    val reports = mutableListOf<Map<String, Any?>>()
    override fun report(hw: Map<String, Any?>) { reports += hw.toMap() }
    override fun hold(at: Long?) { held = at }
    override fun held() = held
    override fun end(at: Long) { ends += at }
    override fun piece() {}
    override fun refused() = refused

    fun info() = ByteArray(57).also { b ->
        b[0] = 1; b[1] = 7; b[38] = 5
        "01.00".toByteArray().copyInto(b, 3); "fake000000000001".toByteArray().copyInto(b, 8)
        Bean.le32(30_000_000).copyInto(b, 24); Bean.le32(20_000_000).copyInto(b, 28)
        byteArrayOf(2, 0, 0, 0, 0x0A, 0x0B).copyInto(b, 39)
        b[50] = if (recording) 1 else 0
    }

    /** A frame from the bean (status 1: done), in notifications of up to 200 B. */
    fun push(cmd: Int, payload: ByteArray, wait: Long = 0, status: Int = 1) {
        val n = payload.size + 10
        val f = byteArrayOf(0x09, 0xFF.toByte(), 0, 0, status.toByte(), (cmd shr 8).toByte(), cmd.toByte(), n.toByte(), (n shr 8).toByte()) + payload
        val whole = f + f.sum().toByte()
        for ((i, c) in whole.toList().chunked(200).withIndex()) out += c.toByteArray() to if (i == 0) wait else 0L
    }

    fun header(file: Long, size: Long, code: Int = 0) {
        val (k, n) = fileKeys.getOrPut(file) { ByteArray(32).also(rnd::nextBytes) to ByteArray(16).also(rnd::nextBytes) }
        val sn = ByteArray(16).also(rnd::nextBytes)
        push(0x1A07, Bean.le32(file) + Bean.le32(size) + n + Bean.ctr(session, sn, "soundcored3200".toByteArray() + k) + sn + byteArrayOf(code.toByte()))
    }

    /** Units three to a frame, 20 ms each; the audio says its file and seq. */
    fun units(file: Long, seqs: List<Long>, last: Long = -1, marks: Set<Long> = emptySet(), wait: Long = 0) {
        val (k, n) = fileKeys[file]!!
        for ((i, c) in seqs.chunked(3).withIndex()) push(0x1A08, c.fold(ByteArray(0)) { acc, s ->
            val flags = (if (s == last) 4 else if (s == 0L) 2 else 0) or (if (s in marks) 1 else 0)
            acc + Bean.le32(s) + byteArrayOf(flags.toByte()) + Bean.ctr(k, Bean.iv(n, s), audio(file, s)) + byteArrayOf(0)
        }, 20L * c.size + if (i == 0) wait else 0)
    }

    fun complete(file: Long) = push(0x1A0A, byteArrayOf(0) + Bean.le32(0) + Bean.le32(file))
    fun rotation(file: Long, wait: Long = 0) = push(0x1A06, Bean.le32(file) + byteArrayOf(0), wait)
    private fun audio(file: Long, seq: Long) = ByteArray(160).also { ByteBuffer.wrap(it).put(0xBC.toByte()).putInt(seq.toInt()).put((file % 251).toByte()) }
}
