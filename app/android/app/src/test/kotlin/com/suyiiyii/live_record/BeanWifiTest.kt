package com.suyiiyii.live_record

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import kotlin.concurrent.thread

/** BeanWifi against a fake Net (no Android): the join's outcomes, and its WebSocket against one served here (plain TCP on
 *  127.0.0.1 in place of the bean's pinned TLS, which AndroidNet does). */
class BeanWifiTest {
    private val endpoint = byteArrayOf(1, 0x2B, 0xA8.toByte(), 0xC0.toByte(), 0xBB.toByte(), 1)  // 192.168.43.1:443

    private class Net(val joined: Boolean?, val port: Int = 0, val pin: String? = null) : BeanWifi.Net {
        var joins = 0; var connects = 0; var leaves = 0
        override fun join(ssid: String, password: String, ms: Long): Boolean? { joins++; return joined }
        override fun address() = "192.168.43.7"
        override fun connect(host: String, port: Int, ms: Int): Socket {
            connects++
            pin?.let { throw BeanWifi.Pin(it) }
            return Socket(InetAddress.getLoopbackAddress(), this.port)
        }
        override fun udp() = DatagramSocket().apply { close() }  // no keep-alive goes anywhere from a test
        override fun leave() { leaves++ }
    }

    private val logs = mutableListOf<Pair<String, String>>()
    private fun wifi(net: Net) = BeanWifi("LR-00000000", "0123456789abcdef", net) { k, t, _, _ -> synchronized(logs) { logs += k to t } }

    @Test fun onlyTheBeansEndpoint() {
        val net = Net(true)
        assertThrows(IOException::class.java) { wifi(net).open(byteArrayOf(1, 0, 168.toByte(), 192.toByte(), 0xBB.toByte(), 1)) }
        assertEquals("not joined for another address", 0, net.joins)
    }

    @Test fun joinRefusedIsNotAskedAgain() {  // the person cancelled Android's dialog (or it found nothing): one ask only
        val net = Net(false)
        assertThrows(IOException::class.java) { wifi(net).open(endpoint) }
        assertEquals(1, net.joins)
        assertTrue(logs.any { it.first == "bean_wifi_fail" && "取消" in it.second })
    }

    @Test fun anotherCertificateEndsAtOnce() {
        val net = Net(true, pin = "00")
        assertThrows(IOException::class.java) { wifi(net).open(endpoint) }
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
}
