package com.suyiiyii.live_record

import android.content.SharedPreferences
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.lang.reflect.Proxy
import java.net.ServerSocket
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** One request is one stretch from its first piece's start: the backlog queued after what is said now is older
 *  and must not ride along in its batch (the emulator run lost 2 min of backlog that way). */
class UploaderTest {
    private fun f(start: Long, dev: String = "LR-EMU-BEEF") = File("$start@$dev.ogg")

    @Test fun batch() {
        val now = (0 until 3).map { f(1_000_000L + it * 1000) }
        assertEquals(now, Uploader.batch(now + f(900_000)))  // the backlog piece is older: not in it
        assertEquals(now.take(1), Uploader.batch(now.take(1) + f(1_000_000L + 2600)))  // a gap: not in it
        assertEquals(now.take(2), Uploader.batch(now.take(2) + f(1_002_000, "LR-P1-BDAE")))  // another device
        assertEquals(listOf(f(900_000)), Uploader.batch(listOf(f(900_000), f(960_000))))  // 60 s pieces go one by one
    }

    /** A recorder's queue in its status: only its own finished pieces, the oldest's age from its start. */
    @Test fun queued() {
        val dir = kotlin.io.path.createTempDirectory().toFile()
        File(dir, "1000@LR-EMU-BEEF.ogg").writeBytes(ByteArray(2048))
        File(dir, "2000@LR-EMU-BEEF.ogg").writeBytes(ByteArray(1024))
        File(dir, "500@LR-P1-BDAE.ogg").writeBytes(ByteArray(4096))   // another recorder's
        File(dir, "3000@LR-EMU-BEEF.part").writeBytes(ByteArray(4096))  // still being written
        val prefs = Proxy.newProxyInstance(javaClass.classLoader, arrayOf(SharedPreferences::class.java)) { _, _, _ -> null } as SharedPreferences
        assertEquals(mapOf<String, Any?>("queue_n" to 2, "queue_kb" to 3L, "queue_age_s" to 60L, "upload_fails" to 0, "upload_ok_s" to null),
                     Uploader(prefs, dir, "", "", "phone", null).queued("LR-EMU-BEEF", now = 61_000))
        dir.deleteRecursively()
    }

    /** A recorder that stopped with its backlog all up sends nothing more to say the hold is gone: the uploader tells the
     *  server on its own, once, after the upload that carried it (10-25: the bean's hold stayed until unbind). */
    @Test fun letGo() {
        val dir = kotlin.io.path.createTempDirectory().toFile()
        File(dir, "1000@D3200-0A0B.ogg").writeBytes(ByteArray(64))
        val kv = java.util.concurrent.ConcurrentHashMap<String, Long>(mapOf("hold:D3200-0A0B" to 500L))
        val prefs = Proxy.newProxyInstance(javaClass.classLoader, arrayOf(SharedPreferences::class.java)) { _, m, a ->
            if (m.name == "getLong") kv[a[0] as String] ?: a[1] else null } as SharedPreferences
        val got = LinkedBlockingQueue<String>()
        val http = ServerSocket(0)
        Thread {  // one request per connection: the line, the headers, the body
            while (!http.isClosed) try { http.accept().use { c ->
                val r = c.getInputStream().bufferedReader(Charsets.ISO_8859_1)
                val path = r.readLine().split(" ")[1]
                val n = generateSequence { r.readLine() }.takeWhile { it.isNotEmpty() }
                    .firstOrNull { it.startsWith("Content-Length:", true) }?.substringAfter(":")?.trim()?.toInt() ?: 0
                repeat(n) { r.read() }
                if (path.startsWith("/api/chunks")) kv.remove("hold:D3200-0A0B")  // the backlog's last piece: let go
                got += path
                c.getOutputStream().write("HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".toByteArray())
            } } catch (_: Exception) {}
        }.start()
        val up = Uploader(prefs, dir, "http://127.0.0.1:${http.localPort}", "", "phone", null).apply { start() }
        try {
            assertEquals("/api/chunks?device=D3200-0A0B&start=1000&hold=500", got.poll(5, TimeUnit.SECONDS))
            assertEquals("/api/hold/release?device=D3200-0A0B", got.poll(5, TimeUnit.SECONDS))
            assertEquals(null, got.poll(1, TimeUnit.SECONDS))  // once
        } finally { up.running = false; up.join(2000); http.close(); dir.deleteRecursively() }
    }

    /** A recording's end (`end:<device>`: the bean's own, or a stop on the phone) goes once its pieces are up, again
     *  until the server takes it (10-25: T2's end never reached the server); none when that device records again before
     *  it went (it carries on). */
    @Test fun ends() {
        val dir = kotlin.io.path.createTempDirectory().toFile()
        File(dir, "1000@D3200-0A0B.ogg").writeBytes(ByteArray(64))  // the recording's last piece, still queued
        File(dir, "9500@D3200-0C0D.ogg").writeBytes(ByteArray(64))  // recording again since its end at 9000
        File(dir, "2000.ogg").writeBytes(ByteArray(64))  // the microphone's last piece before a stop at 3000
        val kv = java.util.concurrent.ConcurrentHashMap<String, Long>(mapOf("end:D3200-0A0B" to 5000L, "end:D3200-0C0D" to 9000L, "end:" to 3000L))
        lateinit var edit: SharedPreferences.Editor
        edit = Proxy.newProxyInstance(javaClass.classLoader, arrayOf(SharedPreferences.Editor::class.java)) { _, m, a ->
            when (m.name) { "remove" -> { kv.remove(a[0] as String); edit }; "commit" -> true; else -> null } } as SharedPreferences.Editor
        val prefs = Proxy.newProxyInstance(javaClass.classLoader, arrayOf(SharedPreferences::class.java)) { _, m, a ->
            when (m.name) { "getLong" -> kv[a[0] as String] ?: a[1]; "getAll" -> kv; "edit" -> edit; else -> null } } as SharedPreferences
        val got = LinkedBlockingQueue<String>()
        val at = java.util.concurrent.ConcurrentHashMap<String, Long>()  // when each came
        var failed = false
        val http = ServerSocket(0)
        Thread {
            while (!http.isClosed) try { http.accept().use { c ->
                val r = c.getInputStream().bufferedReader(Charsets.ISO_8859_1)
                val path = r.readLine().split(" ")[1]
                val n = generateSequence { r.readLine() }.takeWhile { it.isNotEmpty() }
                    .firstOrNull { it.startsWith("Content-Length:", true) }?.substringAfter(":")?.trim()?.toInt() ?: 0
                repeat(n) { r.read() }
                val code = if (path.startsWith("/api/chunks/end?device=D3200-0A0B") && !failed) { failed = true; 500 } else 200
                at["$code $path"] = System.currentTimeMillis(); got += "$code $path"
                c.getOutputStream().write("HTTP/1.1 $code X\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".toByteArray())
            } } catch (_: Exception) {}
        }.start()
        val up = Uploader(prefs, dir, "http://127.0.0.1:${http.localPort}", "", "phone", null).apply { start() }
        try {
            val until = System.currentTimeMillis() + 15_000
            while (kv.isNotEmpty() && System.currentTimeMillis() < until) Thread.sleep(100)
            Thread.sleep(500)
            val all = got.toList()
            val (up1, upMic, upNew) = listOf("200 /api/chunks?device=D3200-0A0B&start=1000", "200 /api/chunks?device=phone&start=2000",
                                             "200 /api/chunks?device=D3200-0C0D&start=9500")
            val (lost, end1, endMic) = listOf("500 /api/chunks/end?device=D3200-0A0B&t=5000", "200 /api/chunks/end?device=D3200-0A0B&t=5000",
                                              "200 /api/chunks/end?device=phone&t=3000")
            assertEquals(listOf(up1, upMic, upNew, lost, end1, endMic).sorted(), all.sorted())  // no end for D3200-0C0D
            assertTrue(all.indexOf(up1) < all.indexOf(lost) && all.indexOf(lost) < all.indexOf(end1))
            assertTrue(all.indexOf(upMic) < all.indexOf(endMic))
            assertTrue("the microphone's piece, queued as the end failed, waits for nothing", at[upMic]!! - at[lost]!! < 1000)
            assertTrue("the end again after 5 s", at[end1]!! - at[lost]!! in 4900..7000)
            assertEquals(emptyMap<String, Long>(), kv.toMap())
        } finally { up.running = false; up.join(7000); http.close(); dir.deleteRecursively() }
    }

    /** The piece lengths in an upload's answer (shorter while the live page is open) go to the running slicer and to the
     *  prefs a restart reads; an answer without them changes nothing. */
    @Test fun slices() {
        val dir = kotlin.io.path.createTempDirectory().toFile()
        val kv = java.util.concurrent.ConcurrentHashMap<String, Int>(mapOf("foreground" to 1, "background" to 20, "screenOff" to 60))
        lateinit var edit: SharedPreferences.Editor
        edit = Proxy.newProxyInstance(javaClass.classLoader, arrayOf(SharedPreferences.Editor::class.java)) { _, m, a ->
            if (m.name == "putInt") kv[a[0] as String] = a[1] as Int
            edit } as SharedPreferences.Editor
        val prefs = Proxy.newProxyInstance(javaClass.classLoader, arrayOf(SharedPreferences::class.java)) { _, m, a ->
            when (m.name) { "getInt" -> kv[a[0] as String] ?: a[1]; "getLong" -> a[1]; "edit" -> edit; else -> null } } as SharedPreferences
        val http = ServerSocket(0)
        Thread {  // one request per connection; the piece at 5000 is answered while the live page is open
            while (!http.isClosed) try { http.accept().use { c ->
                val r = c.getInputStream().bufferedReader(Charsets.ISO_8859_1)
                val path = r.readLine().split(" ")[1]
                val n = generateSequence { r.readLine() }.takeWhile { it.isNotEmpty() }
                    .firstOrNull { it.startsWith("Content-Length:", true) }?.substringAfter(":")?.trim()?.toInt() ?: 0
                repeat(n) { r.read() }
                val body = if ("start=5000" in path) """{"seconds":1.0,"slice":{"foreground":1,"background":2,"screenOff":2}}""" else """{"seconds":1.0}"""
                c.getOutputStream().write("HTTP/1.1 200 OK\r\nContent-Length: ${body.length}\r\nConnection: close\r\n\r\n$body".toByteArray())
            } } catch (_: Exception) {}
        }.start()
        val slicer = Slicer(dir, 1, 20, 60).also { RecorderService.slicer = it }
        val up = Uploader(prefs, dir, "http://127.0.0.1:${http.localPort}", "", "phone", null).apply { start() }
        fun sent(start: Long) {  // gone from the queue: taken, and its answer applied
            val f = File(dir, "$start.ogg").apply { writeBytes(ByteArray(64)) }
            val until = System.currentTimeMillis() + 5000
            while (f.exists() && System.currentTimeMillis() < until) Thread.sleep(20)
            assertEquals(false, f.exists())
        }
        try {
            sent(1000)
            assertEquals(listOf(1, 20, 60), listOf(slicer.fgS, slicer.bgS, slicer.offS))
            assertEquals(mapOf("foreground" to 1, "background" to 20, "screenOff" to 60), kv.toMap())
            sent(5000)
            assertEquals(listOf(1, 2, 2), listOf(slicer.fgS, slicer.bgS, slicer.offS))
            assertEquals(mapOf("foreground" to 1, "background" to 2, "screenOff" to 2), kv.toMap())
        } finally { up.running = false; up.join(2000); http.close(); RecorderService.slicer = null; dir.deleteRecursively() }
    }
}
