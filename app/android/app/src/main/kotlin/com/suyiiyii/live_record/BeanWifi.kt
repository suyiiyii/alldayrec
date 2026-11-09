package com.suyiiyii.live_record

import android.app.ActivityManager
import android.content.Context
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiNetworkSpecifier
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.Base64
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.X509TrustManager

/*
 * The Feishu recorder bean's Wi-Fi fast transfer (#431) on Android, as BeanWifi.swift on iOS: its hotspot joined (a
 * WifiNetworkSpecifier request: Android asks the person each time, the name being new each time), then a WebSocket over
 * TLS to the bean, the one certificate the bean has pinned (expired and not for its address: only for this endpoint,
 * nothing else trusts it). Requests go as hex text, answers come as binary 09 FF frames (ws/soundcore wifi.py, 0BSD;
 * Eliqi797/open-feishu-recording-bean PinnedRecorderLink.swift, MIT, for the endpoint, the pin, the keep-alive and the
 * request headers; see THIRD_PARTY_NOTICES.md). BeanSync (Bean.kt) does the rest, as over BLE; BeanLink carries its
 * bytes here meanwhile. Only these sockets are bound to the hotspot (never the process): uploads keep the phone's own
 * network. What it needs of Android is `Net` (AndroidNet below; a fake in the tests).
 */
class BeanWifi(private val ssid: String, private val password: String, private val net: Net,
               private val log: (kind: String, text: String, level: String?, fields: Map<String, Any?>) -> Unit) {
    interface Net {
        /** Asks to join the hotspot (the system asks the person): true once on it, false refused or not found (Android
         *  does not say which), null no answer in `ms`. */
        fun join(ssid: String, password: String, ms: Long): Boolean?
        /** The phone's IPv4 address on the hotspot, if it has one now. */
        fun address(): String?
        /** TLS to the bean over the hotspot alone, its certificate pinned (BeanWifi.tls); Pin: another certificate. */
        fun connect(host: String, port: Int, ms: Int): Socket
        /** A UDP socket on the hotspot alone, for the keep-alive. */
        fun udp(): DatagramSocket
        /** The hotspot left (the request withdrawn: Android goes back to the network it had). */
        fun leave()
        /** For the logs: a VPN up, the app in front. */
        fun context(): Map<String, Any?> = emptyMap()
    }

    class Pin(val seen: String) : CertificateException("certificate $seen")

    private var socket: Socket? = null
    private var out: OutputStream? = null
    private var udp: DatagramSocket? = null
    private var threads = listOf<Thread>()
    private val inbox = LinkedBlockingQueue<ByteArray>()
    @Volatile private var bytes = 0L

    private fun mono() = System.nanoTime() / 1_000_000

    /** The hotspot (checked: the phone's address on it), then the socket on it: up to 90 s while it comes up. `endpoint`:
     *  the 1A/05 answer, only ever the bean's fixed one. */
    fun open(endpoint: ByteArray) {
        val at = Bean.endpoint(endpoint) ?: "?"
        if (at != "$HOST:$PORT") {
            log("bean_wifi_fail", "热点地址 $at 不是豆子固定的那个，不连", "warn", mapOf("step" to "endpoint", "endpoint" to at))
            throw Bean.Fail("hotspot at $at, not the bean's", "beanWifiNoHotspot")
        }
        val t0 = mono()
        var joined = false
        for ((n, wait) in listOf(0L, 2000, 4000, 8000).withIndex()) {
            Thread.sleep(wait)
            val t1 = mono()
            when (net.join(ssid, password, JOIN_MS)) {
                null -> {
                    log("bean_wifi_fail", "加入热点 120 s 没有结果", "warn", mapOf("step" to "join", "ms" to mono() - t1, "try" to n + 1) + net.context())
                    throw Bean.Fail("joining the hotspot: no answer in 120 s", "beanWifiJoinTimeout")
                }
                false -> {  // the system's own dialog scans again by itself: again here would ask the person again
                    val ms = mono() - t1
                    val late = ms >= JOIN_MS - 1000  // requestNetwork's own timeout: onUnavailable then too
                    log("bean_wifi_fail", "没加入豆子的热点（第 ${n + 1} 次，$ms ms）：" +
                        if (late) "系统弹窗 120 s 没人答复" else "系统弹窗里取消了，或没找到它的网络", "warn",
                        mapOf("step" to "join", "name" to if (late) "timeout" else "unavailable", "ms" to ms, "try" to n + 1) + net.context())
                    throw if (late) Bean.Fail("joining the hotspot: no answer in 120 s", "beanWifiJoinTimeout")
                        else Bean.Fail("join: unavailable", "beanWifiUnavailable")
                }
                true -> {}
            }
            val mine = onHotspot()
            if (mine != null) {
                log("bean_wifi_join", "加入了豆子的热点（第 ${n + 1} 次，${mono() - t1} ms，手机拿到了热点的地址 $mine）", null,
                    mapOf("ms" to mono() - t1, "try" to n + 1, "ip" to mine) + net.context())
                joined = true
                break
            }
            log("bean_wifi_fail", "系统说加入了，20 s 内手机没拿到热点的地址（第 ${n + 1} 次，${mono() - t1} ms）", "warn",
                mapOf("step" to "join_unverified", "ms" to mono() - t1, "try" to n + 1) + net.context())
            net.leave()
        }
        if (!joined) {
            log("bean_wifi_fail", "试了 4 次都没加入豆子的热点", "warn", mapOf("step" to "join", "tries" to 4) + net.context())
            throw Bean.Fail("join: not on the hotspot after 4 tries", "beanWifiNoJoin")
        }
        val joinedAt = mono()
        var tries = 0
        var why = ""
        val said = mutableMapOf<String, Long>()  // each failure logged once in 10 s: a refused connect comes back at once
        while (mono() - joinedAt < 90_000) {
            tries++
            try { connect(); break } catch (e: Pin) {
                log("bean_wifi_fail", "豆子的证书不是认得的那张（${e.seen}）", "warn", mapOf("step" to "pin", "pin" to e.seen))
                throw Bean.Fail("certificate ${e.seen}", "beanWifiNoSocket")
            } catch (e: InterruptedException) { throw e } catch (e: Exception) {  // bindSocket refused (a VPN) included: tried again
                why = "$e"
                if (said[why]?.let { mono() - it < 10_000 } != true) {
                    said[why] = mono()
                    log("bean_wifi_socket", "连豆子的 WebSocket 第 $tries 次没成：$why", "warn", mapOf("tries" to tries, "error" to why) + net.context())
                }
                Thread.sleep(700)
            }
        }
        if (socket == null) {
            val vpn = net.context()["vpn"] == true
            log("bean_wifi_fail", "连不上豆子的 WebSocket（$tries 次，$why）" + if (vpn) "；检测到 VPN，快传可能失败，可临时关闭" else "", "warn",
                mapOf("step" to "socket", "tries" to tries) + net.context())
            throw Bean.Fail("socket: $why", "beanWifiNoSocket")
        }
        log("bean_wifi_socket", "连上豆子的 WebSocket（第 $tries 次，${mono() - joinedAt} ms），证书指纹对上 $PIN", null,
            mapOf("ms" to mono() - joinedAt, "tries" to tries, "pin" to PIN) + net.context())
    }

    /** The phone's address on the hotspot (its DHCP gives 192.168.43.x), within 20 s; null: said joined and is not. */
    private fun onHotspot(): String? {
        repeat(40) {
            net.address()?.takeIf { it.startsWith("192.168.43.") }?.let { return it }
            Thread.sleep(500)
        }
        return null
    }

    /** The socket, its handshake and the keep-alive's: kept (socket set) only once all are up and its threads run. */
    private fun connect() {
        val s = net.connect(HOST, PORT, 15_000)
        val u = try {
            s.soTimeout = 15_000
            handshake(s.getInputStream(), s.getOutputStream())
            s.soTimeout = READ_MS
            net.udp()
        } catch (e: Exception) { s.close(); throw e }
        out = s.getOutputStream(); udp = u
        val reader = Thread({ pump(s) }, "bean-wifi-ws")
        val keep = Thread({  // ws/soundcore's 100 ms (1 s till 45)
            val p = "soundcore-keep-alive-unicast".toByteArray()
            val to = InetSocketAddress(HOST, 32003)
            try { while (!u.isClosed) { u.send(DatagramPacket(p, p.size, to)); Thread.sleep(100) } } catch (_: Exception) {}
        }, "bean-wifi-keepalive")
        threads = listOf(reader, keep)
        threads.forEach { it.isDaemon = true; it.start() }
        socket = s
    }

    /** The client's opening handshake (RFC 6455), with the SDK's User-Agent, as the bean knows it. */
    private fun handshake(i: InputStream, o: OutputStream) {
        val key = Base64.getEncoder().encodeToString(ByteArray(16).also(SecureRandom()::nextBytes))
        o.write(("GET / HTTP/1.1\r\nHost: $HOST:$PORT\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: $key\r\n" +
            "Sec-WebSocket-Version: 13\r\nUser-Agent: okhttp/3.12.13.18\r\n\r\n").toByteArray())
        o.flush()
        val head = StringBuilder()
        while (!head.endsWith("\r\n\r\n")) {
            val c = i.read()
            if (c < 0 || head.length > 8192) throw IOException("WebSocket handshake: ${head.lines().firstOrNull()}")
            head.append(c.toChar())
        }
        val lines = head.lines()
        if (lines[0].split(" ").getOrNull(1) != "101") throw IOException("WebSocket handshake: ${lines[0]}")
        val accept = lines.firstOrNull { it.startsWith("sec-websocket-accept:", true) }?.substringAfter(':')?.trim()
        if (accept != accept(key)) throw IOException("WebSocket handshake: accept $accept")
    }

    /** Each message as it comes: binary frames, or frames as hex text (OK, ACK and the like are not); pings answered.
     *  Whatever ends it (a bad frame, READ_MS of silence) closes the socket: a write stuck on it ends too. */
    private fun pump(s: Socket) {
        try {
            val i = s.getInputStream()
            var message = ByteArray(0)
            var type = 0  // the message's, 0 between messages
            while (true) {
                val f = readFrame(i)
                when (f.op) {
                    0x8 -> break
                    0x9 -> write(0xA, f.data)
                    0x0, 0x1, 0x2 -> {
                        if (f.op != 0) { type = f.op; message = ByteArray(0) }
                        else if (type == 0) throw IOException("a continuation of no message")
                        message += f.data
                        if (message.size > MAX) throw IOException("a message over 4 MB")
                        if (!f.fin) continue
                        bytes += message.size
                        if (type == 2) inbox.put(message) else unhex(String(message, Charsets.US_ASCII))?.let(inbox::put)
                        message = ByteArray(0); type = 0
                    }
                }
            }
        } catch (_: Throwable) {  // an Error too: on this thread it would end the app
        } finally {
            try { s.close() } catch (_: Exception) {}
            inbox.put(GONE)
        }
    }

    /** A frame BeanSync made, as hex text (the bean ignores binary requests). */
    fun send(frame: ByteArray) = text(Bean.hex(frame))

    private fun text(s: String) = write(0x1, s.toByteArray())

    @Synchronized private fun write(op: Int, data: ByteArray) {
        val o = out ?: throw IOException("Wi-Fi socket closed")
        o.write(frame(op, data, ByteArray(4).also(SecureRandom()::nextBytes)))
        o.flush()
    }

    /** The next message, null after `ms` without one; throws once the socket is gone. */
    fun receive(ms: Long): ByteArray? {
        val m = inbox.poll(ms, TimeUnit.MILLISECONDS) ?: return null
        if (m === GONE) { inbox.put(GONE); throw IOException("Wi-Fi socket closed") }
        return m
    }

    /** FINISH (the SDK's goodbye), the socket closed and the hotspot left: Android goes back to the network it had. */
    fun close() {
        try { text("FINISH") } catch (_: Exception) {}
        try { socket?.close() } catch (_: Exception) {}
        udp?.close()
        threads.forEach { it.interrupt() }
        socket = null; out = null; udp = null
        net.leave()
        log("bean_wifi_leave", "离开豆子的热点（收到 ${bytes / 1024} KB）：网络请求已撤回", null, mapOf("bytes" to bytes))
    }

    class Frame(val fin: Boolean, val op: Int, val data: ByteArray)

    companion object {
        const val HOST = "192.168.43.1"  // the bean's, whatever else it answers
        const val PORT = 443
        /** SHA-256 of the bean's certificate (DER), as Eliqi797 found it */
        const val PIN = "3f2667135cafff135614944392d6b3bc0e991b2ca55dbe9e469caca44d5a0125"
        private val GONE = ByteArray(0)
        const val JOIN_MS = 120_000L
        /** No byte from the bean this long: it or the hotspot is gone (BeanSync's own waits are 30 s at most). */
        const val READ_MS = 60_000
        const val MAX = 4 shl 20  // a frame or message

        fun sha256(b: ByteArray) = Bean.hex(MessageDigest.getInstance("SHA-256").digest(b))
        fun accept(key: String): String = Base64.getEncoder().encodeToString(
            MessageDigest.getInstance("SHA-1").digest((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").toByteArray()))

        /** One frame, FIN set, masked with `mask` (a client's are; null: unmasked, as a server's). */
        fun frame(op: Int, data: ByteArray, mask: ByteArray?): ByteArray {
            val m = if (mask != null) 0x80 else 0
            val len = when {
                data.size < 126 -> byteArrayOf((m or data.size).toByte())
                data.size < 65536 -> byteArrayOf((m or 126).toByte(), (data.size shr 8).toByte(), data.size.toByte())
                else -> byteArrayOf((m or 127).toByte()) + ByteArray(8) { (data.size.toLong() shr (56 - 8 * it)).toByte() }
            }
            val body = if (mask == null) data else ByteArray(data.size) { (data[it].toInt() xor mask[it % 4].toInt()).toByte() }
            return byteArrayOf((0x80 or op).toByte()) + len + (mask ?: ByteArray(0)) + body
        }

        fun readFrame(i: InputStream): Frame {
            fun byte() = i.read().also { if (it < 0) throw IOException("Wi-Fi socket closed") }
            fun bytes(n: Int) = ByteArray(n).also { var got = 0; while (got < n) { val r = i.read(it, got, n - got); if (r < 0) throw IOException("Wi-Fi socket closed"); got += r } }
            val b0 = byte(); val b1 = byte()
            var n = (b1 and 0x7F).toLong()
            if (n == 126L) n = ((byte() shl 8) or byte()).toLong()
            else if (n == 127L) n = bytes(8).fold(0L) { a, b -> (a shl 8) or (b.toLong() and 255) }
            if (n < 0 || n > MAX) throw IOException("a frame of $n B")  // n < 0: the top bit of a 64-bit length
            val mask = if (b1 and 0x80 != 0) bytes(4) else null
            val d = bytes(n.toInt())
            if (mask != null) for (k in d.indices) d[k] = (d[k].toInt() xor mask[k % 4].toInt()).toByte()
            return Frame(b0 and 0x80 != 0, b0 and 0x0F, d)
        }

        /** A 09 FF frame sent as hex text; null: other text. */
        fun unhex(s: String): ByteArray? {
            val c = s.trim().lowercase()
            if (c.length < 20 || c.length % 2 != 0 || !c.startsWith("09ff")) return null
            return try { ByteArray(c.length / 2) { c.substring(2 * it, 2 * it + 2).toInt(16).toByte() } } catch (_: NumberFormatException) { null }
        }

        /** TLS over `s`, trusting the bean's pinned leaf alone (no expiry or name check: it fails both), no SNI; its
         *  handshake given `ms` for each read (a bean or hotspot gone mid-way would hold it for ever). `pin`: for tests. */
        fun tls(s: Socket, host: String, port: Int, ms: Int, pin: String = PIN): SSLSocket {
            val tm = object : X509TrustManager {
                override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = throw CertificateException("no clients")
                override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                    val seen = chain.firstOrNull()?.encoded?.let(::sha256) ?: ""
                    if (seen != pin) throw Pin(seen)
                }
                override fun getAcceptedIssuers() = arrayOf<X509Certificate>()
            }
            val ctx = SSLContext.getInstance("TLS").apply { init(null, arrayOf(tm), null) }
            s.soTimeout = ms
            val t = ctx.socketFactory.createSocket(s, host, port, true) as SSLSocket
            try { t.startHandshake() } catch (e: IOException) {
                generateSequence<Throwable>(e) { it.cause }.filterIsInstance<Pin>().firstOrNull()?.let { t.close(); throw it }
                t.close(); throw e
            }
            return t
        }
    }
}

/** BeanWifi's Android side: a WifiNetworkSpecifier request (no internet asked: the hotspot has none, so it never becomes
 *  the default network), sockets bound to its network alone, the default network logged while on it (bean_wifi_path). */
class AndroidNet(private val ctx: Context, private val log: (String, String, String?, Map<String, Any?>) -> Unit) : BeanWifi.Net {
    private val cm = ctx.getSystemService(ConnectivityManager::class.java)
    @Volatile private var network: Network? = null
    @Volatile private var props: LinkProperties? = null
    private var request: ConnectivityManager.NetworkCallback? = null
    private var path: ConnectivityManager.NetworkCallback? = null

    override fun join(ssid: String, password: String, ms: Long): Boolean? {
        leave()
        val got = LinkedBlockingQueue<Boolean>()
        val spec = WifiNetworkSpecifier.Builder().setSsid(ssid).setWpa2Passphrase(password).build()
        val req = NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET).setNetworkSpecifier(spec).build()
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(n: Network) { network = n; got.offer(true) }
            override fun onLinkPropertiesChanged(n: Network, lp: LinkProperties) { if (n == network) props = lp }
            override fun onUnavailable() { got.offer(false) }
            override fun onLost(n: Network) { if (n == network) { network = null; props = null } }
        }
        request = cb
        cm.requestNetwork(req, cb, ms.toInt())  // onUnavailable after `ms`, or when the person cancels the system's dialog
        watch()
        return got.poll(ms + 5000, TimeUnit.MILLISECONDS)
    }

    override fun address(): String? = (props ?: network?.let(cm::getLinkProperties))?.linkAddresses
        ?.map { it.address }?.filterIsInstance<Inet4Address>()?.firstOrNull()?.hostAddress

    private fun on() = network ?: throw IOException("not on the hotspot")

    override fun connect(host: String, port: Int, ms: Int): java.net.Socket {
        val s = on().socketFactory.createSocket()
        try { s.connect(InetSocketAddress(InetAddress.getByName(host), port), ms) } catch (e: Exception) { s.close(); throw e }
        return BeanWifi.tls(s, host, port, ms)
    }

    override fun udp() = DatagramSocket().also { on().bindSocket(it) }

    override fun leave() {
        request?.let { try { cm.unregisterNetworkCallback(it) } catch (_: Exception) {} }
        path?.let { try { cm.unregisterNetworkCallback(it) } catch (_: Exception) {} }
        request = null; path = null; network = null; props = null
    }

    /** The phone's default network while on the hotspot (uploads go there): each change of its transports logged. */
    private fun watch() {
        var said = ""
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onCapabilitiesChanged(n: Network, c: NetworkCapabilities) {
                val uses = mapOf(NetworkCapabilities.TRANSPORT_WIFI to "wifi", NetworkCapabilities.TRANSPORT_CELLULAR to "cellular",
                    NetworkCapabilities.TRANSPORT_ETHERNET to "wiredEthernet", NetworkCapabilities.TRANSPORT_VPN to "vpn")
                    .filterKeys(c::hasTransport).values.toList()
                val metered = !c.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
                val now = "$uses $metered"
                if (now == said) return
                said = now
                log("bean_wifi_path", "手机网络：默认走 ${uses.joinToString("+")}" + if (metered) "（计流量）" else "", null,
                    mapOf("status" to "satisfied", "uses" to uses, "expensive" to metered))
            }
            override fun onLost(n: Network) { said = ""; log("bean_wifi_path", "手机网络：没有默认网络", null, mapOf("status" to "unsatisfied")) }
        }
        path = cb
        cm.registerDefaultNetworkCallback(cb)
    }

    override fun context(): Map<String, Any?> {
        val me = ActivityManager.RunningAppProcessInfo().also(ActivityManager::getMyMemoryState)
        return mapOf("vpn" to vpn(ctx), "front" to (me.importance <= ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND))
    }

    companion object {
        /** A VPN up: the bean's sockets may be kept off the hotspot (bindSocket refused). */
        fun vpn(ctx: Context): Boolean = ctx.getSystemService(ConnectivityManager::class.java).let { cm ->
            cm.getNetworkCapabilities(cm.activeNetwork)?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true
        }
    }
}
