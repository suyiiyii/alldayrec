package com.suyiiyii.live_record

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.nio.file.Files
import java.security.KeyStore
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLServerSocket
import kotlin.concurrent.thread

/** BeanWifi against a fake Net (no Android): the join's outcomes, and its WebSocket against one served here (plain TCP on
 *  127.0.0.1 in place of the bean's pinned TLS, which AndroidNet does). */
class BeanWifiTest {
    private val endpoint = byteArrayOf(1, 0x2B, 0xA8.toByte(), 0xC0.toByte(), 0xBB.toByte(), 1)  // 192.168.43.1:443

    private class Net(val joined: Boolean?, val port: Int = 0, val pin: String? = null, var udpFails: Int = 0,
                      val sendBuffer: Int? = null) : BeanWifi.Net {
        var joins = 0; var connects = 0; var leaves = 0
        override fun join(ssid: String, password: String, ms: Long): Boolean? { joins++; return joined }
        override fun address() = "192.168.43.7"
        override fun connect(host: String, port: Int, ms: Int): Socket {
            connects++
            pin?.let { throw BeanWifi.Pin(it) }
            return Socket().apply { sendBuffer?.let { sendBufferSize = it }; connect(InetSocketAddress(InetAddress.getLoopbackAddress(), this@Net.port)) }
        }
        override fun udp(): DatagramSocket {
            if (udpFails > 0) { udpFails--; throw IOException("bindSocket refused") }  // a VPN's
            return DatagramSocket().apply { close() }  // no keep-alive goes anywhere from a test
        }
        override fun leave() { leaves++ }
    }

    private val logs = mutableListOf<Pair<String, String>>()
    private fun wifi(net: Net, readMs: Int = BeanWifi.READ_MS) =
        BeanWifi("LR-00000000", "0123456789abcdef", net, readMs) { k, t, _, _ -> synchronized(logs) { logs += k to t } }

    @Test fun onlyTheBeansEndpoint() {
        val net = Net(true)
        assertThrows(IOException::class.java) { wifi(net).open(byteArrayOf(1, 0, 168.toByte(), 192.toByte(), 0xBB.toByte(), 1)) }
        assertEquals("not joined for another address", 0, net.joins)
    }

    @Test fun joinRefusedIsNotAskedAgain() {  // the person cancelled Android's dialog (or it found nothing): one ask only
        val net = Net(false)
        assertEquals("beanWifiUnavailable", assertThrows(Bean.Fail::class.java) { wifi(net).open(endpoint) }.note)
        assertEquals(1, net.joins)
        assertTrue(logs.any { it.first == "bean_wifi_fail" && "取消" in it.second })
    }

    @Test fun anotherCertificateEndsAtOnce() {
        val net = Net(true, pin = "00")
        assertEquals("beanWifiNoSocket", assertThrows(Bean.Fail::class.java) { wifi(net).open(endpoint) }.note)
        assertEquals("no retry for a certificate not the bean's", 1, net.connects)
        assertTrue(logs.any { it.first == "bean_wifi_fail" && "证书" in it.second })
    }

    @Test fun webSocket() {
        val server = ServerSocket(0, 1, InetAddress.getLoopbackAddress())
        val request = Bean.wifiTransfer(7, 0)
        val answer = byteArrayOf(0x09, 0xFF.toByte()) + ByteArray(12) { it.toByte() }
        var got: String? = null; var pong: ByteArray? = null; var ua: String? = null
        val bean = thread {
            server.accept().use { s ->
                val i = s.getInputStream(); val o = s.getOutputStream()
                val head = StringBuilder()
                while (!head.endsWith("\r\n\r\n")) head.append(i.read().toChar())
                val key = head.lines().first { it.startsWith("Sec-WebSocket-Key:") }.substringAfter(':').trim()
                ua = head.lines().firstOrNull { it.startsWith("User-Agent:") }
                o.write("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${BeanWifi.accept(key)}\r\n\r\n".toByteArray())
                val f = BeanWifi.readFrame(i)  // the phone's request: masked hex text
                got = if (f.op == 1) String(f.data) else "op ${f.op}"
                o.write(BeanWifi.frame(0x9, "hi".toByteArray(), null))  // a ping
                o.write(BeanWifi.frame(0x2, answer, null))  // binary
                o.write(BeanWifi.frame(0x1, "OK".toByteArray(), null))  // text that is no frame: dropped
                o.write(BeanWifi.frame(0x1, Bean.hex(answer).toByteArray(), null))  // a frame as hex text
                // a binary message in two fragments: FIN off, then a continuation
                o.write(byteArrayOf(0x02, 4) + answer.copyOfRange(0, 4)); o.write(byteArrayOf(0x80.toByte(), 10) + answer.copyOfRange(4, 14))
                val p = BeanWifi.readFrame(i)
                if (p.op == 0xA) pong = p.data
                o.write(BeanWifi.frame(0x8, ByteArray(0), null))  // close
                o.flush()
                Thread.sleep(200)
            }
        }
        val net = Net(true, server.localPort)
        val w = wifi(net)
        w.open(endpoint)
        w.send(request)
        repeat(3) { assertArrayEquals(answer, w.receive(5000)) }
        assertThrows(IOException::class.java) { while (true) w.receive(5000) ?: break }
        bean.join(5000)
        assertEquals(Bean.hex(request), got)
        assertArrayEquals("the ping answered", "hi".toByteArray(), pong)
        assertEquals("User-Agent: okhttp/3.12.13.18", ua)
        assertTrue(logs.any { it.first == "bean_wifi_join" } && logs.any { it.first == "bean_wifi_socket" && "连上" in it.second })
        w.close()
        assertEquals(1, net.leaves)
        assertTrue(logs.any { it.first == "bean_wifi_leave" })
        server.close()
    }

    @Test fun frames() {
        for (n in listOf(0, 125, 126, 65_535, 65_536)) {
            val d = ByteArray(n) { it.toByte() }
            val f = BeanWifi.readFrame(BeanWifi.frame(0x2, d, byteArrayOf(1, 2, 3, 4)).inputStream())
            assertEquals(0x2, f.op); assertTrue(f.fin); assertArrayEquals("$n B", d, f.data)
        }
        assertNull(BeanWifi.unhex("OK"))
        assertNull(BeanWifi.unhex("09ffzz00000000000000"))
        assertArrayEquals(byteArrayOf(0x09, 0xFF.toByte(), 0, 0, 0, 0, 0, 0, 0, 0), BeanWifi.unhex(" 09FF0000000000000000\n"))
        // RFC 6455's example key and answer
        assertEquals("s3pPLMBiTxaQ9kYGzzhZRbK+xOo=", BeanWifi.accept("dGhlIHNhbXBsZSBub25jZQ=="))
    }

    /** The bean's side of one WebSocket connection: the phone's handshake answered (`accept` of another key when
     *  `badAccept`), then `then`. */
    private fun serve(server: ServerSocket, badAccept: Boolean = false, then: (InputStream, OutputStream) -> Unit = { _, _ -> }) =
        server.accept().use { s ->
            val i = s.getInputStream(); val o = s.getOutputStream()
            val head = StringBuilder()
            while (!head.endsWith("\r\n\r\n")) head.append(i.read().toChar())
            val key = head.lines().first { it.startsWith("Sec-WebSocket-Key:") }.substringAfter(':').trim()
            o.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n" +
                "Sec-WebSocket-Accept: ${BeanWifi.accept(if (badAccept) "x$key" else key)}\r\n\r\n").toByteArray())
            o.flush()
            then(i, o); o.flush()
        }

    private val server = ServerSocket(0, 4, InetAddress.getLoopbackAddress())
    @org.junit.After fun closeServer() = server.close()

    /** An answer whose Sec-WebSocket-Accept is not of the phone's key: not that socket, the next one taken. */
    @Test fun anotherAcceptIsRefused() {
        val bean = thread { serve(server, badAccept = true); serve(server) { _, _ -> Thread.sleep(300) } }
        val net = Net(true, server.localPort)
        val w = wifi(net)
        w.open(endpoint)
        assertEquals("the first socket refused for its accept", 2, net.connects)
        assertTrue(logs.any { it.first == "bean_wifi_socket" && "accept" in it.second })
        w.close(); bean.join(5000)
    }

    /** udp() failing (bindSocket refused): the socket of that try closed, nothing kept, said once in 10 s; then the next. */
    @Test fun udpFailureClosesTheSocket() {
        val first = mutableListOf<Int>()
        val bean = thread {
            repeat(3) { serve(server) { i, _ -> first += i.read() } }  // -1: closed by the phone
            serve(server) { _, _ -> Thread.sleep(300) }
        }
        val net = Net(true, server.localPort, udpFails = 3)
        val w = wifi(net)
        w.open(endpoint)
        bean.join(5000)
        assertEquals("each failed try's socket closed", listOf(-1, -1, -1), first)
        assertEquals(4, net.connects)
        assertEquals("the same failure said once", 1, logs.count { it.first == "bean_wifi_socket" && "bindSocket" in it.second })
        assertTrue(logs.last { it.first == "bean_wifi_socket" }.second.startsWith("连上"))
        w.close()
    }

    /** A frame's 64-bit length with its top bit set (negative) or over 4 MB: the socket ends, nothing allocated for it,
     *  the reader's end said to receive() (no Error left to end the app). */
    @Test fun badLengthsEndTheSocket() {
        val negative = byteArrayOf(0x82.toByte(), 0x7F, 0x80.toByte(), 0, 0, 0, 0x40, 0, 0, 0)
        val over = byteArrayOf(0x82.toByte(), 0x7F) + ByteArray(8) { ((4L shl 20) + 1 shr (56 - 8 * it)).toByte() }
        for (f in listOf(negative, over)) {
            assertThrows(IOException::class.java) { BeanWifi.readFrame(f.inputStream()) }
            val bean = thread { serve(server) { i, o -> o.write(f); o.flush(); i.read() } }
            val w = wifi(Net(true, server.localPort))
            w.open(endpoint)
            assertThrows(IOException::class.java) { w.receive(5000) }
            w.close(); bean.join(5000)
        }
    }

    /** A continuation after a message ended (the bean breaking RFC 6455): not that message again, longer; the socket ends. */
    @Test fun aContinuationOfNoMessage() {
        val answer = byteArrayOf(0x09, 0xFF.toByte()) + ByteArray(12) { it.toByte() }
        val bean = thread { serve(server) { i, o -> o.write(BeanWifi.frame(0x2, answer, null)); o.write(BeanWifi.frame(0x0, answer, null)); o.flush(); i.read() } }
        val w = wifi(Net(true, server.localPort))
        w.open(endpoint)
        assertArrayEquals(answer, w.receive(5000))
        assertThrows(IOException::class.java) { w.receive(5000) }
        w.close(); bean.join(5000)
    }

    /** A certificate of its own, made here (keytool): refused as not the bean's (Pin, what it is), taken when it is the pin. */
    @Test fun tlsPinsTheCertificate() {
        val dir = Files.createTempDirectory("tls").toFile(); val ks = File(dir, "k.p12")
        val keytool = File(System.getProperty("java.home"), "bin/keytool").path
        val p = ProcessBuilder(keytool, "-genkeypair", "-keyalg", "RSA", "-keysize", "2048", "-alias", "bean", "-dname", "CN=bean",
            "-validity", "2", "-storetype", "PKCS12", "-keystore", ks.path, "-storepass", "testpass", "-keypass", "testpass")
            .redirectErrorStream(true).start()
        assertEquals(p.inputStream.bufferedReader().readText(), 0, p.waitFor())
        val store = KeyStore.getInstance("PKCS12").apply { ks.inputStream().use { load(it, "testpass".toCharArray()) } }
        val mine = BeanWifi.sha256(store.getCertificate("bean").encoded)
        val ctx = SSLContext.getInstance("TLS").apply {
            init(KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm()).apply { init(store, "testpass".toCharArray()) }.keyManagers, null, null)
        }
        val tls = ctx.serverSocketFactory.createServerSocket(0, 4, InetAddress.getLoopbackAddress()) as SSLServerSocket
        val bean = thread { repeat(2) { try { tls.accept().use { it.getOutputStream().write(7); it.getInputStream().read() } } catch (_: IOException) {} } }
        fun dial(pin: String) = BeanWifi.tls(Socket(InetAddress.getLoopbackAddress(), tls.localPort), BeanWifi.HOST, BeanWifi.PORT, 5000, pin)
        assertEquals("seen: its certificate", mine, assertThrows(BeanWifi.Pin::class.java) { dial(BeanWifi.PIN) }.seen)
        dial(mine).use { assertEquals(7, it.getInputStream().read()) }
        bean.join(5000); tls.close()
    }

    /** A peer that takes the TCP connection and says nothing (the hotspot gone mid-way): the handshake ends in `ms`. */
    @Test fun tlsHandshakeTimesOut() {
        val bean = thread { server.accept().use { Thread.sleep(3000) } }
        val t0 = System.nanoTime()
        assertThrows(SocketTimeoutException::class.java) {
            BeanWifi.tls(Socket(InetAddress.getLoopbackAddress(), server.localPort), BeanWifi.HOST, BeanWifi.PORT, 500)
        }
        assertTrue("ended in about 500 ms", (System.nanoTime() - t0) / 1_000_000 < 2500)
        bean.join(5000)
    }

    /** The bean silent readMs once the socket is up (it or its hotspot gone, the socket still open): the reader ends,
     *  receive() says so. */
    @Test fun silenceEndsTheReader() {
        val done = CountDownLatch(1)
        val bean = thread { serve(server) { _, _ -> done.await(10, TimeUnit.SECONDS) } }
        val w = wifi(Net(true, server.localPort), readMs = 500)
        w.open(endpoint)
        assertThrows("ended within the 2 s asked, not a null", IOException::class.java) { w.receive(2000) }
        done.countDown(); w.close(); bean.join(5000)
    }

    /** A write stuck on a bean that reads nothing (small buffers: stuck at once), pinged once it is or not: the reader
     *  still ends readMs on (the pong not on its thread), closing the socket, and the stuck write ends with it. */
    @Test fun stuckWriteEndsWithTheReader() {
        server.receiveBufferSize = 4096
        for (ping in listOf(false, true)) {
            val stuck = CountDownLatch(1); val done = CountDownLatch(1)
            val bean = thread {
                serve(server) { _, o ->
                    if (ping) { stuck.await(5, TimeUnit.SECONDS); o.write(BeanWifi.frame(0x9, "hi".toByteArray(), null)); o.flush() }
                    done.await(15, TimeUnit.SECONDS)
                }
            }
            val w = wifi(Net(true, server.localPort, sendBuffer = 4096), readMs = 2000)
            w.open(endpoint)
            var ended: Exception? = null; val sent = AtomicInteger()
            val writer = thread { try { while (true) { w.send(ByteArray(1 shl 16)); sent.incrementAndGet() } } catch (e: Exception) { ended = e } }
            var was = -1
            while (sent.get() != was) { was = sent.get(); Thread.sleep(300) }  // no send done in 300 ms: stuck
            stuck.countDown()
            writer.join(8000)
            assertFalse("ping $ping: the stuck write ended", writer.isAlive)
            assertTrue("$ended", ended is IOException)
            assertThrows(IOException::class.java) { w.receive(1000) }
            done.countDown(); w.close(); bean.join(5000)
        }
    }
}
