package com.suyiiyii.live_record

import android.content.SharedPreferences
import org.junit.Assert.assertEquals
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
}
