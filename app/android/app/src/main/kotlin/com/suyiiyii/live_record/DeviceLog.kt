package com.suyiiyii.live_record

import android.Manifest
import android.app.ActivityManager
import android.content.Context
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.os.BatteryManager
import android.os.Build
import android.os.Debug
import android.os.PowerManager
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.BlockingQueue
import java.util.concurrent.TimeUnit

/** What happened on the capture side, for the server's device page (/device/<name>, POST /api/device-log): the
 *  recorder's own event log (firmware lr_log.h), read over BLE from where the last read ended, and the app's key events
 *  (connect with each step's time, disconnect, offline sync, uploads failing and coming back). An event is first
 *  appended to a local file; a thread of its own sends the file every 30 s and keeps it while that fails, so a phone
 *  offline loses nothing, and neither reading nor sending ever waits on the audio's upload or holds it up. */
object DeviceLog {
    /** A record of the recorder's log: boot:u16 t:u32 code:u8 x:u8 a:u32 b:u32 (lr_log.h), and its seq. */
    data class Rec(val seq: Long, val boot: Int, val t: Long, val code: Int, val x: Int, val a: Long, val b: Long)
    private const val TIME = 14       // a | b << 32: the epoch ms the phone said frame t was at
    private const val UPTIME = 0x80   // before the boot's first frame: t is ms since power-up

    private lateinit var file: File            // the queue: one JSON event per line, the device it belongs to inside
    private lateinit var prefs: SharedPreferences
    private val lock = Any()
    private lateinit var src: String           // this install's app events: "app:<id made at the first start>"
    private var lastSeq = 0L                   // an app event's seq: its time in ms, kept increasing (also across restarts)
    private val steps = StringBuilder()        // the connection's steps so far, for its connect event
    private lateinit var ctx: Context
    @Volatile var version = "?"                // this build, 1.4.6+21: in every app event and status
        private set

    /** Once per process, by whichever starts first (`by`: the service, or the UI): the queue and its sender, and what
     *  the app says of itself: this start, why the process before it ended, a crash handler, what it
     *  is allowed to do. */
    fun start(ctx: Context, by: String) {
        synchronized(lock) {
            if (::file.isInitialized) return
            this.ctx = ctx.applicationContext
            version = ctx.packageManager.getPackageInfo(ctx.packageName, 0).let { "${it.versionName}+${it.longVersionCode}" }
            file = File(ctx.filesDir, "devlog.jsonl")
            prefs = ctx.getSharedPreferences("recorder", Context.MODE_PRIVATE)
            // src + seq is the server's key: another phone, or this one reinstalled, never writes over these events
            src = prefs.getString("logSrc", null) ?: "app:${java.util.UUID.randomUUID()}".also { prefs.edit().putString("logSrc", it).apply() }
            lastSeq = prefs.getLong("logSeq", 0)
        }
        crashes()
        app(mine(), "launch", "App 启动（$by）")
        exits()
        permissions()
        Thread { while (true) { try { send() } catch (e: Exception) { Log.w("live-record", "devlog send: $e") }; Thread.sleep(30_000) } }
            .apply { isDaemon = true; name = "devlog" }.start()
    }

    /** Whose page the app's own events go on: the bound recorder's, else the phone's (its microphone's audio name). */
    fun mine() = prefs.getString("bleName", null)?.let(Ble::tag) ?: prefs.getString("device", null)?.takeIf { it.isNotEmpty() } ?: "phone"

    //
    // The recorder's log
    //

    /** DATA notifications of a LOG (seq:u32 + 16 B records), in any order, maybe overlapping -> records by seq. */
    fun records(packets: List<ByteArray>): List<Rec> {
        val out = sortedMapOf<Long, Rec>()
        for (p in packets) {
            val b = ByteBuffer.wrap(p).order(ByteOrder.LITTLE_ENDIAN)
            val seq = b.getInt(0).toLong() and 0xffffffffL
            for (i in 0 until (p.size - 4) / 16) {
                val o = 4 + i * 16
                out[seq + i] = Rec(seq + i, b.getShort(o).toInt() and 0xffff, b.getInt(o + 2).toLong() and 0xffffffffL, p[o + 6].toInt() and 0xff,
                                   p[o + 7].toInt() and 0xff, b.getInt(o + 8).toLong() and 0xffffffffL, b.getInt(o + 12).toLong() and 0xffffffffL)
            }
        }
        return out.values.toList()
    }

    /** Each record's wall time (epoch ms) or null, as lr_sync.py dates them: a frame by the boot's TIME record nearest
     *  to it, among these and `known` (boot -> (frame, epoch) of earlier reads), at the boot's rate (Store.at). A
     *  record from before the boot's first frame goes at that frame: a few seconds late at most (the log has no record
     *  of the first frame's uptime). A boot that ended before any phone set its clock stays undated. */
    fun dates(recs: List<Rec>, known: Map<Int, Pair<Long, Long>>): List<Long?> {
        val sync = known.mapValues { mutableListOf(longArrayOf(it.value.first, it.value.second)) }.toMutableMap()
        for (r in recs) if (r.code == TIME)
            sync.getOrPut(r.boot) { mutableListOf() } += longArrayOf(r.t, r.b shl 32 or r.a)
        val rates = sync.mapValues { Store.bootRate(it.value) }
        return recs.map { r ->
            val frame = if (r.code and UPTIME != 0) 0L else r.t
            sync[r.boot]?.let { Store.at(it, frame, rates.getValue(r.boot)) }
        }
    }

    /** Where the next read starts: after the last record of an unbroken run from the first one received (a lost
     *  notify is read again), or `next` when nothing came at all. */
    fun cursor(recs: List<Rec>, next: Long): Long {
        var c = recs.firstOrNull()?.seq ?: return next
        for (r in recs) if (r.seq == c) c++ else break
        return if (c == recs.last().seq + 1) next else c
    }

    /** LOG (CTRL 0x07 from:u32 -> DATA notifications, then CTRL 0x87 next:u32) from where the last read of this
     *  recorder ended, on the BLE session thread with nothing else in flight. A log that started over (erased: next
     *  below where we were) is read from 0. */
    fun read(tag: String, ask: (ByteArray) -> Unit, ctrl: BlockingQueue<ByteArray>, data: BlockingQueue<ByteArray>) {
        try {
            var from = prefs.getLong("logNext:$tag", 0)
            for (attempt in 0..1) {
                data.clear(); ctrl.removeIf { it[0] == 0x87.toByte() }
                ask(ByteBuffer.allocate(5).order(ByteOrder.LITTLE_ENDIAN).put(7).putInt(from.toInt()).array())
                var r: ByteArray
                do { r = ctrl.poll(10, TimeUnit.SECONDS) ?: throw Exception("no LOG reply") } while (r[0] != 0x87.toByte())
                val next = ByteBuffer.wrap(r).order(ByteOrder.LITTLE_ENDIAN).getInt(1).toLong() and 0xffffffffL
                if (next < from) { Log.i("live-record", "devlog $tag: log restarted at $next (had $from)"); from = 0; continue }
                val recs = records(generateSequence { data.poll() }.toList())
                val known = prefs.getString("logTime:$tag", null)?.split(',')?.map(String::toLong)?.let { mapOf(it[0].toInt() to (it[1] to it[2])) }
                val at = dates(recs, known ?: emptyMap())
                add(recs.mapIndexed { i, x -> event(tag, "rec", x.seq, at[i], JSONObject().put("boot", x.boot).put("t", x.t).put("code", x.code)
                                                                                     .put("x", x.x).put("a", x.a).put("b", x.b)) })
                val e = prefs.edit().putLong("logNext:$tag", cursor(recs, next))
                recs.lastOrNull { it.code == TIME }?.let { e.putString("logTime:$tag", "${it.boot},${it.t},${it.b shl 32 or it.a}") }
                e.apply()
                Log.i("live-record", "devlog $tag: ${recs.size} records from $from, next $next")
                return
            }
        } catch (e: Exception) { Log.w("live-record", "devlog $tag read: $e") }
    }

    //
    // The app's events
    //

    /** One of the app's events, filed under the device it is about (a recorder's name, or the phone's for its upload);
     *  `fields` go into its data beside kind and text, for the server to read. */
    fun app(device: String, kind: String, text: String, level: String? = null, fields: Map<String, Any?> = emptyMap(), at: Long? = null) {
        if (!::file.isInitialized) return
        val now = System.currentTimeMillis()
        val seq = synchronized(lock) { maxOf(now, lastSeq + 1).also { lastSeq = it; prefs.edit().putLong("logSeq", it).apply() } }
        add(listOf(event(device, src, seq, at ?: now, JSONObject(fields).put("kind", kind).put("text", text).put("app", version)
            .apply { if (level != null) put("level", level) })))
    }

    /** The phone, for the status sent with its recorder's: this build, the system, the model as the
     *  maker names it (never the name the person gave the phone), its battery and whether it saves power or runs hot
     *  (the system slows or stops apps then), and this app's memory. */
    fun phone(): Map<String, Any?> {
        val bm = ctx.getSystemService(BatteryManager::class.java)
        val pm = ctx.getSystemService(PowerManager::class.java)
        return mapOf("app" to version, "os" to "Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})",
            "phone" to "${Build.MANUFACTURER} ${Build.MODEL}",
            "phone_battery" to bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY), "phone_charging" to bm.isCharging,
            "low_power" to pm.isPowerSaveMode,
            "thermal" to listOf("none", "light", "moderate", "severe", "critical", "emergency", "shutdown").getOrNull(pm.currentThermalStatus),
            "app_mb" to Debug.getPss() / 1024)  // proportional set size, as iOS's phys_footprint counts against the app
            .also { permissions() }  // with each status: a change while the app runs
    }

    /** Why the app's processes before this one ended (Android 11+; not the WebView's sandboxed ones), each once, at the
     *  time it ended: low memory, a crash, an ANR, stopped by the person or for using too much, an update. */
    private fun exits() {
        if (Build.VERSION.SDK_INT < 30) return
        val seen = prefs.getLong("exitSeen", 0)
        val got = ctx.getSystemService(ActivityManager::class.java).getHistoricalProcessExitReasons(null, 0, 16)
            .filter { it.timestamp > seen && it.processName == ctx.packageName }.sortedBy { it.timestamp }
        for (x in got) {
            val (why, level) = EXITS[x.reason] ?: ("其他原因 ${x.reason}" to "warn")
            app(mine(), "exit", "App 进程结束：$why" + (x.description?.takeIf { it.isNotBlank() }?.let { "（${it.take(120)}）" } ?: ""), level,
                mapOf("reason" to x.reason, "importance" to x.importance, "pss_kb" to x.pss, "rss_kb" to x.rss), at = x.timestamp)
        }
        got.lastOrNull()?.let { prefs.edit().putLong("exitSeen", it.timestamp).apply() }
        // the home page tells of it once (MainActivity "cleaned"): the link was down from then until the app was opened
        got.lastOrNull { cleaned(it.reason, it.description, xiaomi) }?.let { prefs.edit().putLong("cleanedAt", it.timestamp).apply() }
    }

    /** A Xiaomi phone (Xiaomi, Redmi, POCO; MIUI or HyperOS): its cleanup force-stops an app not locked in recents. */
    val xiaomi by lazy {
        listOf(Build.MANUFACTURER, Build.BRAND).any { it.lowercase() in setOf("xiaomi", "redmi", "poco") } ||
            listOf("ro.mi.os.version.name", "ro.miui.ui.version.name").any { k ->
                runCatching { Class.forName("android.os.SystemProperties").getMethod("get", String::class.java).invoke(null, k) as String }
                    .getOrDefault("").isNotEmpty()
            }
    }

    /** A force stop by Xiaomi's cleanup: REASON_USER_REQUESTED, "[FORCE STOP] stop <package> due to SwipeUpClean" (a
     *  swipe up in recents; OneKeyClean, GarbageClean). The foreground service and the link stay down until the app is
     *  opened again. */
    fun cleaned(reason: Int, description: String?, xiaomi: Boolean) = xiaomi && reason == 10 && description?.contains("Clean") == true

    private val EXITS = mapOf(  // ApplicationExitInfo.REASON_*: what it says, how bad
        1 to ("自己退出" to null), 2 to ("被信号结束" to "warn"), 3 to ("内存不足，被系统结束" to "error"), 4 to ("崩溃" to "error"),
        5 to ("原生代码崩溃" to "error"), 6 to ("无响应（ANR）" to "error"), 7 to ("启动失败" to "error"), 8 to ("权限变了" to "warn"),
        9 to ("耗电或占资源过多，被系统结束" to "error"), 10 to ("被强行停止" to "warn"), 11 to ("被停用" to "warn"),
        12 to ("依赖的进程结束了" to "warn"), 13 to ("系统的其他原因" to "warn"), 14 to ("被冻结后结束" to "warn"),
        15 to ("应用状态变了" to null), 16 to ("应用更新" to null))

    /** An exception nothing caught goes into the queue before the process dies (the file is written at once),
     *  then the system's own handler runs. */
    private fun crashes() {
        val next = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            try { app(mine(), "crash", "未捕获的异常（线程 ${t.name}）：${trace(e)}", "error") } catch (_: Throwable) {}
            next?.uncaughtException(t, e)
        }
    }

    /** An exception as its classes and first frames (class.method:line), with its causes; no message: it may carry data. */
    fun trace(e: Throwable): String = generateSequence(e) { it.cause }.take(3).joinToString(" ← ") { x ->
        x.javaClass.name + " " + x.stackTrace.take(4).joinToString(" < ") { "${it.className.substringAfterLast('.')}.${it.methodName}:${it.lineNumber}" }
    }

    /** What the app may do, logged when it changes and the first time: without Bluetooth nothing connects;
     *  without notifications the foreground service is hidden; without the battery exemption, or with the background
     *  restricted, the system stops the link. */
    private fun permissions() {
        val ok = { p: String -> ctx.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED }
        val ble = Ble.permissions.all(ok)
        val notify = Build.VERSION.SDK_INT < 33 || ok(Manifest.permission.POST_NOTIFICATIONS)
        val battery = ctx.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(ctx.packageName)
        val bg = !ctx.getSystemService(ActivityManager::class.java).isBackgroundRestricted
        val line = listOf(if (ble) "蓝牙权限 有" else "蓝牙权限 无", if (notify) "通知权限 有" else "通知权限 无",
                          if (battery) "电池优化 已豁免" else "电池优化 未豁免", if (bg) "后台 不受限" else "后台 受限").joinToString(" · ")
        if (line == prefs.getString("perms", null)) return
        prefs.edit().putString("perms", line).apply()
        app(mine(), "permissions", "权限：$line", if (ble && notify && battery && bg) null else "warn")
    }

    /** A step of connecting (BleLink.step), kept for the connect event. */
    fun step(what: String, ms: Long) {
        synchronized(steps) { steps.append(if (steps.isEmpty()) "" else "，").append("$what $ms ms") }
    }

    /** The link is up and the audio flows: the steps it took, as one event. */
    fun connected(device: String) {
        val s = synchronized(steps) { steps.toString().also { steps.clear() } }
        app(device, "connect", "连上：$s")
    }

    /** The link went down (GATT status: 8 timeout, 19 the recorder ended it, 0 the phone did, 133 never came up). */
    fun disconnected(device: String, status: Int) {
        synchronized(steps) { steps.clear() }
        app(device, "disconnect", "断开（status $status）", "warn")
    }

    //
    // The queue
    //

    private fun event(device: String, src: String, seq: Long, at: Long?, data: JSONObject) =
        JSONObject().put("device", device).put("src", src).put("seq", seq).put("at", at ?: JSONObject.NULL).put("data", data)

    private fun add(events: List<JSONObject>) {
        if (events.isEmpty()) return
        synchronized(lock) { file.appendText(events.joinToString("") { "$it\n" }) }
    }

    /** The queue moved aside (new events start a new file) and sent per device; kept for the next round while a
     *  request fails (the server keeps a record sent again once). A batch the server cannot take (400, 422) is dropped. */
    private fun send() {
        val server = prefs.getString("server", null)?.takeIf { it.isNotEmpty() } ?: return
        synchronized(lock) { if (file.exists()) file.renameTo(File(file.parentFile, "devlog-${System.currentTimeMillis()}.send")) }
        val queued = { file.parentFile!!.listFiles { x -> x.name.endsWith(".send") }!!.sortedBy { it.name } }
        trim(queued(), CAP).takeIf { it > 0 }?.let { app(mine(), "log_dropped", "离线太久，待发的日志超过 ${CAP shr 20} MB，丢掉最早的 ${it / 1024} KB", "warn") }
        for (f in queued()) {
            val byDevice = f.readLines().filter(String::isNotBlank).map(::JSONObject).groupBy { it.remove("device") as String }
            var kept = false
            for ((device, events) in byDevice) {
                val code = post("$server/api/device-log?device=${URLEncoder.encode(device, "UTF-8")}", JSONArray(events).toString().toByteArray())
                if (code == 200) continue
                Log.w("live-record", "devlog send $device: HTTP $code")
                if (code != 400 && code != 422) kept = true
            }
            if (kept) return
            f.delete()
        }
    }

    const val CAP = 10L shl 20  // the queue kept while sends fail: a recorder's log offline for weeks

    /** The oldest files of the queue past `cap` bytes deleted, the newest always kept; how many bytes went. */
    fun trim(files: List<File>, cap: Long): Long {
        var total = files.sumOf { it.length() }
        var gone = 0L
        for (f in files.dropLast(1)) {
            if (total <= cap) break
            total -= f.length(); gone += f.length(); f.delete()
        }
        return gone
    }

    private fun post(url: String, body: ByteArray): Int = try {
        val c = URL(url).openConnection() as HttpURLConnection
        c.requestMethod = "POST"; c.connectTimeout = 15_000; c.readTimeout = 30_000; c.doOutput = true
        c.setRequestProperty("Authorization", "Bearer ${prefs.getString("token", "")}")
        c.setRequestProperty("Content-Type", "application/json")
        c.outputStream.use { it.write(body) }
        c.responseCode.also { code -> (if (code < 400) c.inputStream else c.errorStream)?.use { it.readBytes() } }
    } catch (e: Exception) { Log.w("live-record", "devlog send: $e"); -1 }
}
