package com.suyiiyii.live_record

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.PowerManager
import android.os.Process
import android.util.Log
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Foreground service that owns recording and uploading, so neither needs the Flutter UI alive. Uploading does not
 *  depend on recording: after a stop (or a launch, or a kill, with pieces left) the service stays up as a dataSync
 *  service until the queue is empty, sends the end signal after the last piece and stops itself.
 *  START_STICKY: when the system kills the process the service comes back with a null intent and
 *  resumes from the config saved in SharedPreferences, noting the interruption. */
class RecorderService : Service() {
    companion object {
        const val CHANNEL = "live_record"
        @Volatile var slicer: Slicer? = null
        @Volatile var uploader: Uploader? = null
        @Volatile var link: BleLink? = null  // the BLE recorder, when it is the source instead of the microphone
        @Volatile var interruptedAt: Long = 0
        @Volatile var foregroundApp = true
        fun status(dir: File): Map<String, Any?> {
            val q = Uploader.queue(dir)
            return mapOf(
                "running" to (slicer != null), "uploading" to (uploader != null), "files" to slicer?.files, "slice" to slicer?.sliceS(),
                "error" to slicer?.error, "sent" to uploader?.sent, "note" to uploader?.note,
                "pending" to q.size, "pendingBytes" to q.sumOf { it.length() }, "oldest" to q.firstOrNull()?.let(Uploader::startMs),
                "interruptedAt" to if (interruptedAt > 0) interruptedAt else null, "link" to link?.state,
                "connected" to (link?.state?.startsWith("已连接") == true), "tag" to slicer?.tag, "hw" to link?.hw,  // link is text to show; this, to test
                "dfu" to Dfu.state, "dfuBusy" to Dfu.busy, "source" to (if (slicer == null) null else if (link != null) "ble" else "mic"), "usbNote" to link?.usbNote, "lastSync" to link?.syncNote,
                "syncDone" to link?.syncDoneKb, "syncTotal" to link?.syncTotalKb, "syncLive" to link?.syncLiveKb, "pausedUntil" to link?.pausedUntil,
                "listen" to link?.listening?.lines)
        }

        /** A firmware update ended (Dfu.kt): the BLE link back at once, set up as the last start left it, as the home
         *  page's ticker would bring it (on iOS the ticker does not run in the background: 18 min without LIVE).
         *  From the background the system may refuse a foreground service start: the ticker is left to it then. */
        fun relink(ctx: Context) {
            val p = ctx.getSharedPreferences("recorder", Context.MODE_PRIVATE)
            if (slicer != null || p.getString("source", "mic") != "ble") return
            val i = Intent(ctx, RecorderService::class.java).putExtra("nonce", System.currentTimeMillis())
            for (k in listOf("server", "token", "device", "source")) i.putExtra(k, p.getString(k, null))
            for (k in listOf("foreground", "background", "screenOff")) i.putExtra(k, p.getInt(k, 20))
            i.putExtra("archive", p.getBoolean("archive", true))
            try { ctx.startForegroundService(i) } catch (e: Exception) { Log.w("live-record", "relink after dfu: $e") }
        }

        /** The recorder's card when it does not record to it, as main.dart cardState says it: store an errno, total_kb 0
         *  when none mounted this boot (no card, or one not seated: -19, a mount that timed out -116). */
        fun cardLine(store: Int?, totalKb: Long?): String? = when {
            store == null || store == 0 -> null
            store == -28 -> "存储卡已满"
            store == -16 -> "存储卡在 U 盘模式"
            (totalKb ?: 0L) == 0L -> "没插存储卡"
            else -> "存储卡出错"
        }

        const val BACKLOG_MS = 120_000L  // a piece waiting longer (the longest slice is 60 s, then its upload) is a backlog

        /** The notification for a status(): title and text, as the app's status sheet puts it (home.dart _bleBody). The BLE
         *  recorder's battery only while connected: the last read stays in hw after the link is gone. */
        fun notice(s: Map<String, Any?>, now: Long = System.currentTimeMillis()): Pair<String, String> {
            val note = s["note"] as String?
            if (s["running"] != true) return "alldayrec 正在上传 · 剩 ${s["pending"]} 块" to (note ?: "")
            val total = s["syncTotal"] as Long? ?: 0L
            val title = when {
                s["source"] != "ble" -> "alldayrec 正在录音"
                // LIVE goes on while the backlog is read; before that only the file being recorded is waited for
                total > 0 -> if ((s["link"] as String?).orEmpty().startsWith("已连接"))
                    "实时上传 · 后台补传 %.1f / %.1f MB".format((s["syncDone"] as Long? ?: 0L) / 1024.0, total / 1024.0)
                else "接上实时前先取正在录的这段 · 还差 %.1f MB".format((s["syncLive"] as Long? ?: 0L) / 1024.0)
                else -> s["link"] as String? ?: "正在启动"
            }
            val hw = (s["hw"] as Map<*, *>?).takeIf { title.startsWith("已连接") || total > 0 }
            val paused = (s["pausedUntil"] as Long? ?: 0L) - now
            val oldest = s["oldest"] as Long?
            return title to listOfNotNull(
                (s["error"] as String?)?.let { "录音错误 $it" },
                note,
                if (paused > 0) "已暂停，还剩 ${(paused + 59_999) / 60_000} 分钟" else null,
                hw?.get("battery")?.let { "电量 $it%" },
                mapOf("battery" to "用电池", "charging" to "充电中", "full" to "已充满")[hw?.get("charge")],
                cardLine((hw?.get("store") as Number?)?.toInt(), (hw?.get("total_kb") as Number?)?.toLong()),
                if (oldest != null && now - oldest > BACKLOG_MS) "待传 ${s["pending"]} 块" else null,
                (s["interruptedAt"] as Long?)?.let { "曾在 ${SimpleDateFormat("HH:mm", Locale.getDefault()).format(Date(it))} 被系统中断，已自动恢复" },
            ).joinToString(" · ")
        }
    }

    private val screen = object : BroadcastReceiver() {
        override fun onReceive(c: Context, i: Intent) { slicer?.screenOff = i.action == Intent.ACTION_SCREEN_OFF }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
            .createNotificationChannel(NotificationChannel(CHANNEL, getString(R.string.recording_channel), NotificationManager.IMPORTANCE_LOW))
        registerReceiver(screen, IntentFilter().apply { addAction(Intent.ACTION_SCREEN_OFF); addAction(Intent.ACTION_SCREEN_ON) })
        DeviceLog.start(this, "录音服务启动")
        Thread {
            while (alive) {
                try { refresh() } catch (e: Exception) { Log.w("live-record", "notification: $e") }
                Thread.sleep(2000)
            }
        }.apply { isDaemon = true }.start()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val prefs = getSharedPreferences("recorder", MODE_PRIVATE)
        Log.i("live-record", "onStartCommand action=${intent?.action} extras=${intent?.extras != null} slicer=${slicer != null} pid=${Process.myPid()} lastPid=${prefs.getInt("pid", 0)}")
        val dir = File(filesDir, "chunks").apply { mkdirs() }
        if (intent?.action == "stop") {
            stopRecording(prefs, dir)
            return START_STICKY
        }
        if (intent?.action == "upload") {  // from the UI: pieces left (and maybe an end) after a kill or a lost network
            if (slicer == null) uploadOnly(prefs, dir, prefs.getLong("endAt", 0L).takeIf { it > 0 })
            return START_STICKY
        }
        // a start from the UI carries a fresh nonce; after a kill the system restarts us with no intent or
        // redelivers the old one, so: same or no nonce while the last run was still active = we were killed
        val nonce = intent?.getLongExtra("nonce", 0L) ?: 0L
        if (nonce != 0L && nonce != prefs.getLong("nonce", 0L)) {
            interruptedAt = 0
            prefs.edit().putLong("nonce", nonce).apply()
        } else if (slicer == null && prefs.getBoolean("active", false) && prefs.getInt("pid", 0) != Process.myPid()) {
            interruptedAt = System.currentTimeMillis()
            Log.w("live-record", "restarted after being killed")
            DeviceLog.app(DeviceLog.mine(), "restart", "被系统结束后自动恢复录音", "warn")
        }
        if (intent?.extras != null) {
            prefs.edit().apply {
                for (k in listOf("server", "token", "device", "source")) putString(k, intent.getStringExtra(k))
                for (k in listOf("foreground", "background", "screenOff")) putInt(k, intent.getIntExtra(k, 20))
                putBoolean("archive", intent.getBooleanExtra("archive", true))
            }.apply()
        } else if (!prefs.getBoolean("active", false)) {  // restarted after a kill while only uploading, or nothing to do
            uploadOnly(prefs, dir, prefs.getLong("endAt", 0L).takeIf { it > 0 })
            return START_STICKY
        }
        prefs.edit().putBoolean("active", true).putInt("pid", Process.myPid()).apply()
        val ble = prefs.getString("source", "mic") == "ble"
        foreground(if (ble) ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE else ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        if (slicer == null) {
            dir.listFiles { f -> f.name.endsWith(".part") }?.forEach { it.renameTo(File(it.path.removeSuffix(".part") + ".ogg")) }  // cut short by a kill: still valid Ogg
            Log.i("live-record", "starting slicer")
            slicer = Slicer(dir, prefs.getInt("foreground", 5), prefs.getInt("background", 20), prefs.getInt("screenOff", 60), ble).also {
                it.foreground = foregroundApp
                it.screenOff = !(getSystemService(POWER_SERVICE) as PowerManager).isInteractive
                it.start()
            }
            if (ble) link = BleLink(this, prefs, dir, File(getExternalFilesDir(null), "undated"), { slicer?.sliceS() ?: 20 },
                { n -> slicer?.let { it.files += n } }, { slicer?.tag = it }, { name, hw -> uploader?.report(name, hw) }).also { it.start() }
            prefs.edit().remove("endAt").remove("endDevice").apply()  // a new recording carries on the conversation: no end for the old stop
            ensureUploader(prefs, dir)
            refresh()  // recording now, not the upload foreground() saw a moment ago
        }
        return START_STICKY
    }

    private fun ensureUploader(prefs: android.content.SharedPreferences, dir: File): Uploader {
        uploader?.let { return it }
        val archive = if (prefs.getBoolean("archive", true)) File(getExternalFilesDir(null), "recordings") else null
        return Uploader(prefs, dir, prefs.getString("server", "")!!, prefs.getString("token", "")!!, prefs.getString("device", "phone")!!, archive)
            .also { uploader = it; it.start() }
    }

    /** The microphone goes now; the queue keeps uploading, the end signal goes after its last piece. */
    private fun stopRecording(prefs: android.content.SharedPreferences, dir: File) {
        val at = System.currentTimeMillis()
        prefs.edit().putBoolean("active", false).putLong("endAt", at).putString("endDevice", slicer?.tag).apply()  // kept: a kill before the queue empties still ends it
        link?.stop(); link = null
        slicer?.stop(); slicer = null
        uploadOnly(prefs, dir, at)
    }

    /** Stays up while pieces are left and nothing records; then sends `endAt` (if any) and stops. */
    private fun uploadOnly(prefs: android.content.SharedPreferences, dir: File, endAt: Long?) {
        if (Uploader.queue(dir).isEmpty() && endAt == null) { background(); stopSelf(); return }
        foreground(ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        val u = ensureUploader(prefs, dir)
        if (draining) return
        draining = true
        Thread {
            while (slicer == null && Uploader.queue(dir).isNotEmpty()) Thread.sleep(2000)
            draining = false
            if (slicer != null) return@Thread  // recording again: the service carries on as before
            endAt?.let { u.end(it, prefs.getString("endDevice", null)) }  // the server may close the conversation now
            prefs.edit().remove("endAt").remove("endDevice").apply()
            u.running = false; uploader = null
            background(); stopSelf()
        }.start()
    }

    @Volatile private var draining = false
    @Volatile private var alive = true
    private var shown: Pair<String, String>? = null  // what the notification says; null: not in the foreground

    private fun foreground(type: Int) = synchronized(this) {
        shown = notice(status(File(filesDir, "chunks"))).also { startForeground(1, notification(it), type) }
    }

    /** The notification follows the status (link, catch-up, upload errors, battery): checked every 2 s, posted only
     *  when what it says changed, far below the 5 posts a second Android allows an app before dropping them (10-06:
     *  it was set once at the start and never again). Never out of the foreground: a post after stopForeground
     *  would leave a stray notification. */
    private fun refresh() = synchronized(this) {
        val n = notice(status(File(filesDir, "chunks")))
        if (shown != null && n != shown) { shown = n; getSystemService(NotificationManager::class.java).notify(1, notification(n)) }
    }

    private fun background() = synchronized(this) { shown = null; stopForeground(STOP_FOREGROUND_REMOVE) }

    private fun notification(n: Pair<String, String>): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL).setContentTitle(n.first).setContentText(n.second)
            .setStyle(Notification.BigTextStyle().bigText(n.second))  // a long upload error in full when expanded
            .setSmallIcon(if (slicer == null) android.R.drawable.stat_sys_upload else android.R.drawable.ic_btn_speak_now)
            .setContentIntent(open).setOngoing(true).setOnlyAlertOnce(true).build()
    }

    override fun onDestroy() {
        Log.w("live-record", "onDestroy slicer=${slicer != null}")
        alive = false
        background()
        unregisterReceiver(screen)
        link?.stop(); link = null
        slicer?.stop(); slicer = null
        uploader?.running = false; uploader = null
        super.onDestroy()
    }
}

/** Uploads the .ogg files in start order, retrying forever; a file is archived or deleted once the server has it,
 *  so the directory is the offline queue. Back-to-back pieces waiting together go as one request (each piece is a
 *  whole Ogg stream, so their bytes in a row decode as one): with 1 s pieces one request per piece cannot keep up
 *  when a request takes over a second (10-03: the queue grew to 16 s). */
class Uploader(private val prefs: android.content.SharedPreferences, private val dir: File, private val server: String, private val token: String, private val device: String,
               private val archive: File?) : Thread() {
    @Volatile var running = true
    @Volatile var sent = 0
    @Volatile var note: String? = null
    @Volatile private var fails = 0      // in a row, and since when (wall ms)
    private var failedAt = 0L
    @Volatile private var okAt = 0L      // the last upload the server took (wall ms; 0: none since the start)

    companion object {
        const val MAX_BATCH = 30        // pieces in one request
        const val BATCH_GAP_MS = 1500L  // starts further apart than this are not one stretch (only 1 s pieces get batched)
        /** The pieces waiting, oldest first: the directory is the queue. */
        fun queue(dir: File) = dir.listFiles { f -> f.name.endsWith(".ogg") }?.sortedBy { it.name } ?: emptyList()
        /** A piece's file name is its start (ms) and, for the BLE recorder, @ its name: start@LR-P1-BDAE.ogg */
        fun startMs(f: File) = f.nameWithoutExtension.substringBefore('@').toLong()
        fun deviceOf(f: File) = f.nameWithoutExtension.substringAfter('@', "")

        /** The first pieces of the queue `q` of one device that follow each other without a gap (a stop or a kill
         *  leaves one), at most MAX_BATCH: one request, one stretch from its first piece's start. Following means
         *  later: with what is said now first, the backlog after it is older and goes on its own. */
        fun batch(q: List<File>): List<File> {
            val out = mutableListOf(q[0])
            for (f in q.drop(1)) {
                if (out.size >= MAX_BATCH || startMs(f) - startMs(out.last()) !in 1..BATCH_GAP_MS || deviceOf(f) != deviceOf(out[0])) break
                out += f
            }
            return out
        }
    }

    /** A recorder's pieces from its hold on go first (what is said now, ahead of a backlog read off its card). */
    private fun files(): List<File> {
        val q = queue(dir)
        val ahead = { f: File -> hold(deviceOf(f))?.let { startMs(f) >= it } ?: false }
        return q.filter(ahead) + q.filterNot(ahead)
    }

    /** Where the server's finalization waits for a recorder's backlog (BleLink.holdAhead), null: it does not. */
    private fun hold(device: String) = prefs.getLong("hold:$device", -1).takeIf { device.isNotEmpty() && it >= 0 }


    override fun run() {
        while (running) {
            val q = files()
            if (q.isEmpty()) { sleep(200); continue }
            val b = batch(q)
            try {
                // the hold with every upload while there is one: the server lets go when an upload comes without it
                val code = post("$server/api/chunks?device=${enc(deviceOf(b[0]).ifEmpty { device })}&start=${startMs(b[0])}" +
                                (hold(deviceOf(b[0]))?.let { "&hold=$it" } ?: ""),
                                b.fold(ByteArray(0)) { acc, f -> acc + f.readBytes() }, "audio/ogg")
                Log.i("live-record", "upload ${b.size} pieces of ${deviceOf(b[0]).ifEmpty { device }} from ${startMs(b[0])}: HTTP $code")
                when {
                    code == 200 -> {
                        b.forEach(::keep); sent += b.size; note = null
                        okAt = System.currentTimeMillis()
                        if (fails > 0) {
                            val s = (okAt - failedAt) / 1000
                            DeviceLog.app(deviceOf(b[0]).ifEmpty { device }, "upload_ok", "上传恢复（之前连续失败 $fails 次，${if (s < 120) "$s 秒" else "${s / 60} 分钟"}）",
                                fields = mapOf("fails" to fails, "ms" to okAt - failedAt))
                        }
                        fails = 0
                    }
                    code == 400 -> {  // the server keeps the bytes (recordings/rejected/); this phone says it lost them
                        val bytes = b.sumOf { it.length() }
                        DeviceLog.app(deviceOf(b[0]).ifEmpty { device }, "upload_rejected", "服务端无法解码 ${b.size} 块" +
                            "（起于 ${SimpleDateFormat("HH:mm:ss", Locale.US).format(Date(startMs(b[0])))}，${bytes / 1024} KB），已丢弃",
                            "error", mapOf("start" to startMs(b[0]), "pieces" to b.size, "bytes" to bytes))
                        b.forEach { it.delete() }; note = "服务端无法解码一块，已丢弃"
                    }
                    else -> throw Exception("HTTP $code")
                }
            } catch (e: Exception) {
                Log.w("live-record", "upload failed (${fails + 1}): ${e.message}")
                if (++fails == 1) {
                    failedAt = System.currentTimeMillis()
                    DeviceLog.app(deviceOf(b[0]).ifEmpty { device }, "upload_fail", "上传失败：${e.message}", "error")
                }
                note = "上传失败 ${e.message}"
                sleep(5000L * minOf(fails, 6))
            }
        }
    }

    /** What the phone last read from its BLE recorder, for the server's progress page, with the phone's own state and
     *  this recorder's upload queue; best effort, off this thread. */
    fun report(device: String, hw: Map<String, Any?>) = Thread {
        try { post("$server/api/device-status?device=${enc(device)}", org.json.JSONObject(hw + DeviceLog.phone() + queued(device)).toString().toByteArray(), "application/json") } catch (_: Exception) {}
    }.start()

    /** `device`'s pieces waiting (how many, KB, the oldest's age in s) and how uploads go: failures in a row, s since the
     *  last one the server took (null: none since the app started). */
    fun queued(device: String, now: Long = System.currentTimeMillis()): Map<String, Any?> {
        val q = queue(dir).filter { deviceOf(it) == device }
        return mapOf("queue_n" to q.size, "queue_kb" to q.sumOf { it.length() } / 1024, "queue_age_s" to q.firstOrNull()?.let { (now - startMs(it)) / 1000 },
                     "upload_fails" to fails, "upload_ok_s" to okAt.takeIf { it > 0 }?.let { (now - it) / 1000 })
    }

    fun end(atMs: Long, bleName: String? = null) {
        try { post("$server/api/chunks/end?device=${enc(bleName ?: device)}&t=$atMs", ByteArray(0), null) } catch (e: Exception) { note = "结束信号失败 ${e.message}" }
    }

    private fun keep(f: File) {
        if (archive == null) { f.delete(); return }
        val day = File(archive, SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date(startMs(f)))).apply { mkdirs() }
        try { f.copyTo(File(day, f.name), overwrite = true) } catch (e: Exception) { note = "归档失败 ${e.message}" }  // internal -> external: no rename
        f.delete()
    }

    private fun post(url: String, body: ByteArray, type: String?): Int {
        val c = URL(url).openConnection() as HttpURLConnection
        c.requestMethod = "POST"; c.connectTimeout = 15_000; c.readTimeout = 60_000; c.doOutput = true
        c.setRequestProperty("Authorization", "Bearer $token")
        if (type != null) c.setRequestProperty("Content-Type", type)
        c.outputStream.use { it.write(body) }
        val code = c.responseCode
        (if (code < 400) c.inputStream else c.errorStream)?.use { it.readBytes() }  // read to the end: the connection is reused
        return code
    }

    private fun enc(s: String) = URLEncoder.encode(s, "UTF-8")
}
