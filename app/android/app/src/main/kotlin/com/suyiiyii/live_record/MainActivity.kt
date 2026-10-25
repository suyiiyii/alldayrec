package com.suyiiyii.live_record

import android.Manifest
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothManager
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaCodec
import android.media.MediaFormat
import android.media.MediaMuxer
import android.media.MediaRecorder
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.view.WindowManager
import androidx.core.content.FileProvider
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.nio.ByteBuffer

class MainActivity : FlutterActivity() {
    private var nearby: Nearby? = null  // the add-recorder page's scan, while it is open

    private fun granted(p: String) = checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    private val update get() = File(cacheDir, "update.apk")  // the app's own update, downloaded by update.dart

    override fun configureFlutterEngine(engine: FlutterEngine) {
        super.configureFlutterEngine(engine)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)  // the page is meant to be watched
        DeviceLog.start(this, "打开界面")
        MethodChannel(engine.dartExecutor.binaryMessenger, "live_record/recorder").setMethodCallHandler { call, result ->
            when (call.method) {
                "start" -> {
                    if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
                        requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
                    if (call.argument<String>("source") == "ble") {
                        val need = Ble.permissions.filterNot(::granted)
                        if (need.isNotEmpty()) {
                            requestPermissions(need.toTypedArray(), 2)
                            return@setMethodCallHandler result.error("permission", null, null)
                        }
                    }
                    val i = Intent(this, RecorderService::class.java)
                    for (k in listOf("server", "token", "device", "source")) i.putExtra(k, call.argument<String>(k))
                    for (k in listOf("foreground", "background", "screenOff")) i.putExtra(k, call.argument<Int>(k)!!)
                    i.putExtra("archive", call.argument<Boolean>("archive") ?: true)
                    i.putExtra("nonce", System.currentTimeMillis())
                    startForegroundService(i)
                    result.success(null)
                }
                "stop" -> { startService(Intent(this, RecorderService::class.java).setAction("stop")); result.success(null) }
                "foreground" -> {
                    val on = call.argument<Boolean>("on")!!
                    if (on != RecorderService.foregroundApp) DeviceLog.app(DeviceLog.mine(), if (on) "foreground" else "background", if (on) "App 回到前台" else "App 进了后台")
                    RecorderService.foregroundApp = on
                    RecorderService.slicer?.foreground = on
                    result.success(null)
                }
                "slices" -> {  // new lengths from the server: the running slicer cuts its next piece with them, a restart reads them
                    val fg = call.argument<Int>("foreground")!!; val bg = call.argument<Int>("background")!!; val off = call.argument<Int>("screenOff")!!
                    getSharedPreferences("recorder", MODE_PRIVATE).edit().putInt("foreground", fg).putInt("background", bg).putInt("screenOff", off).apply()
                    RecorderService.slicer?.apply { fgS = fg; bgS = bg; offS = off }
                    result.success(null)
                }
                "upload" -> {  // pieces left from a stop, a kill or a lost network: send them without recording
                    if (RecorderService.slicer == null && RecorderService.uploader == null)
                        startForegroundService(Intent(this, RecorderService::class.java).setAction("upload"))
                    result.success(null)
                }
                "status" -> result.success(RecorderService.status(File(filesDir, "chunks")))
                "version" -> result.success(DeviceLog.version)  // 我的 › 关于
                "error" -> {  // an error the Flutter side did not catch: its type and first frames, no message
                    DeviceLog.app(DeviceLog.mine(), "app_error", "界面出错：${call.argument<String>("type")} ${call.argument<String>("stack")}", "error")
                    result.success(null)
                }
                "deviceName" -> result.success(Settings.Global.getString(contentResolver, Settings.Global.DEVICE_NAME) ?: Build.MODEL)  // the name in the system settings
                "recorder" -> {  // the bound BLE recorder, what it last said, and how a firmware update goes
                    val p = getSharedPreferences("recorder", MODE_PRIVATE)
                    result.success(mapOf("name" to p.getString("bleName", null), "model" to p.getString("bleModel", null),
                        "fw" to p.getString("bleFw", null), "dfu" to Dfu.state,
                        "kicked" to p.getBoolean("bleKicked", false)))
                }
                "permissions" -> result.success(mapOf(  // what adding a recorder asks for, and what is granted
                    "bluetooth" to Ble.permissions.all(::granted),
                    "notifications" to (Build.VERSION.SDK_INT < 33 || granted(Manifest.permission.POST_NOTIFICATIONS)),
                    "battery" to getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName),
                    "bluetoothOn" to getSystemService(BluetoothManager::class.java).adapter.isEnabled))
                "askPermission" -> {
                    when (call.argument<String>("which")) {
                        "bluetooth" -> requestPermissions(Ble.permissions.toTypedArray(), 2)
                        "notifications" -> if (Build.VERSION.SDK_INT >= 33) requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
                        // the link lives in the background all day: without this, the system stops it
                        "battery" -> startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")))
                    }
                    result.success(null)
                }
                "scan" -> {  // the add-recorder page opens (true) or closes
                    nearby?.stop(); nearby = null
                    if (call.argument<Boolean>("on")!! && Ble.permissions.all(::granted)) nearby = Nearby(this).also { it.start() }
                    result.success(null)
                }
                "nearby" -> result.success(nearby?.list() ?: emptyList<Map<String, Any>>())
                "bind" -> {  // the recorder the person chose: the link pairs with it next (force: it then forgets its other phones)
                    val addr = call.argument<String>("address")!!
                    val d = getSystemService(BluetoothManager::class.java).adapter.getRemoteDevice(addr)
                    // an old bond the recorder may have forgotten; a bean is never paired (one Feishu paired stays as it is)
                    if (d.bondState == BluetoothDevice.BOND_BONDED && !Bean.isBean(call.argument<String>("name"))) Ble.removeBond(d)
                    val p = getSharedPreferences("recorder", MODE_PRIVATE)
                    val e = p.edit().putString("bleName", call.argument<String>("name"))
                        .putString("bleAddress", addr).putBoolean("bindPending", true).putBoolean("bindForce", call.argument<Boolean>("force")!!)
                        .remove("bleKicked").remove("bleModel").remove("bleFw")
                    // the one bound before: its backlog no longer comes from here (Dart: releaseHold)
                    p.getString("bleName", null)?.takeIf { it != call.argument<String>("name") }?.let { e.remove("hold:${Ble.tag(it)}") }
                    e.apply()
                    result.success(null)
                }
                "usbDisk" -> {  // the connected board's card to the computer's USB, or back
                    val link = RecorderService.link
                    if (link == null) result.error("notStarted", null, null)
                    else { link.usbDisk(call.argument<Boolean>("on")!!); result.success(null) }
                }
                "unbind" -> Thread {  // the recorder forgets every phone (when connected), then this phone forgets it
                    val p = getSharedPreferences("recorder", MODE_PRIVATE)
                    val addr = p.getString("bleAddress", null); val name = p.getString("bleName", null)
                    // first: the link must not reconnect when the recorder drops it
                    val e = p.edit().remove("bleName").remove("bleAddress").remove("bleModel").remove("bleFw").remove("bindPending")
                        .remove("bindForce").remove("bleKicked")
                    name?.let { e.remove("hold:${Ble.tag(it)}") }  // its backlog no longer comes from here
                    e.commit()
                    val forgot = RecorderService.link?.forgetAll() ?: false
                    name?.let { DeviceLog.app(Ble.tag(it), "unbind", if (forgot) "解除绑定：录音设备忘掉了所有手机"
                        else if (Bean.isBean(it)) "解除绑定：这台手机不再连这颗录音豆（豆子那边什么也不改）" else "解除绑定：录音设备没连上，只有这台手机忘掉了它") }
                    if (addr != null && BluetoothAdapter.checkBluetoothAddress(addr) && !Bean.isBean(name))
                        Ble.removeBond(getSystemService(BluetoothManager::class.java).adapter.getRemoteDevice(addr))
                    runOnUiThread { result.success(forgot) }
                }.start()
                "listen" -> {  // the recorder's sound played on this phone as it comes (Listen.kt), or not; off when the app leaves the front
                    val on = call.argument<Boolean>("on")!!
                    val link = RecorderService.link
                    if (link == null) { if (on) result.error("notStarted", null, null) else result.success(null) }
                    else try { link.listen(on); result.success(null) } catch (e: Exception) { result.error("listen", e.message, null) }
                }
                "pause" -> {  // the connected recorder keeps nothing for s seconds (0: records again)
                    try { RecorderService.link!!.pause(call.argument<Int>("s")!!); result.success(null) }
                    catch (e: Exception) { result.error("notConnected", null, null) }
                }
                "dfu" -> {  // a package from the server for `model`: only onto a recorder that said it is that model
                    val p = getSharedPreferences("recorder", MODE_PRIVATE)
                    val addr = p.getString("bleAddress", null); val model = p.getString("bleModel", null)
                    val want = call.argument<String>("model"); val zip = call.argument<ByteArray>("zip")!!
                    val sha = java.security.MessageDigest.getInstance("SHA-256").digest(zip).joinToString("") { "%02x".format(it) }
                    val no = Dfu.refusal(want, model, addr, RecorderService.slicer != null, sha, call.argument<String>("sha256"),
                                         call.argument<String>("version"), p.getString("bleFw", null))
                    if (no != null) result.error(no["code"] as String, null, no)
                    else {  // refusal() checked both
                        val f = File(cacheDir, "$model.zip").apply { writeBytes(zip) }
                        Dfu.log = DfuLog(Ble.tag(p.getString("bleName", null) ?: model!!), p.getString("bleFw", null),
                                         call.argument<String>("version") ?: "?", zip.size, sha, call.argument<Int>("battery"))
                        Dfu.start(this, addr!!, p.getString("bleName", null) ?: model!!, f); result.success(null)
                    }
                }
                "app" -> packageManager.getPackageInfo(packageName, 0).let {  // this build, and where update.dart puts a newer one
                    result.success(mapOf("code" to it.longVersionCode, "apk" to update.path))
                }
                "allowInstalls" -> {  // before the download: false opens the setting (the installer's own prompt for it comes back here, not to the install)
                    val ok = packageManager.canRequestPackageInstalls()
                    if (!ok) startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
                    result.success(ok)
                }
                "install" -> {  // the downloaded update to the system installer
                    startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(FileProvider.getUriForFile(this, "$packageName.update", update),
                        "application/vnd.android.package-archive").addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION))
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
    }
}

/** Opus frames from the microphone's encoder cut into self-contained Ogg files whose length depends on the app state,
 *  named by the wall time of their first frame. With the BLE recorder as the source (`ble`) the microphone stays off:
 *  BleLink makes the pieces, as long as sliceS() says, and counts them in `files`. */
class Slicer(private val dir: File, @Volatile var fgS: Int, @Volatile var bgS: Int, @Volatile var offS: Int,
             private val ble: Boolean = false) {
    @Volatile var foreground = true
    @Volatile var screenOff = false
    @Volatile private var running = false
    @Volatile var files = 0
    @Volatile var error: String? = null
    /** The BLE recorder's name: pieces carry it in their file name (start@name.ogg) and go up as that device. */
    @Volatile var tag: String? = null
    private var thread: Thread? = null
    private val sr = 16000

    // the piece being written; only the slicer thread touches these
    private var format: MediaFormat? = null
    private var muxer: MediaMuxer? = null
    private var track = 0
    private var part: File? = null
    private var fileStartUs = 0L
    private var fileSlice = 0
    private var lastState = 0

    fun sliceS() = if (screenOff) offS else if (foreground) fgS else bgS

    fun start() {
        android.util.Log.i("live-record", "slicer start fg=$fgS bg=$bgS off=$offS ble=$ble")
        running = true
        lastState = sliceS()
        if (!ble) thread = Thread { try { micLoop() } catch (e: Exception) { error = e.toString() } }.also { it.start() }
    }

    fun stop() { android.util.Log.i("live-record", "slicer stop"); running = false; thread?.join(5000) }

    /** One Opus packet at `ptsUs` after `wallMs`: rotates at a frame boundary when the slice length is up, or the
     *  state changed to a shorter slice that is already over. */
    private fun write(buf: ByteBuffer, offset: Int, size: Int, flags: Int, wallMs: Long, ptsUs: Long) {
        val state = sliceS()
        if (muxer != null && (ptsUs - fileStartUs >= fileSlice * 1_000_000L ||
                (state != lastState && state < fileSlice && ptsUs - fileStartUs >= state * 1_000_000L))) closeFile()
        lastState = state
        if (muxer == null) {
            part = File(dir, "${wallMs + ptsUs / 1000}${tag?.let { "@$it" } ?: ""}.part")
            muxer = MediaMuxer(part!!.path, MediaMuxer.OutputFormat.MUXER_OUTPUT_OGG).also {
                track = it.addTrack(format!!)
                it.start()
            }
            fileStartUs = ptsUs
            fileSlice = state
        }
        muxer!!.writeSampleData(track, buf, MediaCodec.BufferInfo().apply { set(offset, size, ptsUs - fileStartUs, flags) })
    }

    private fun closeFile() {
        muxer?.let { it.stop(); it.release() }
        muxer = null
        part?.let { it.renameTo(File(it.path.removeSuffix(".part") + ".ogg")); files++ }
        part = null
    }

    private fun micLoop() {
        val startWall = System.currentTimeMillis()
        val minBuf = AudioRecord.getMinBufferSize(sr, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val rec = AudioRecord(MediaRecorder.AudioSource.MIC, sr, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, minBuf * 4)
        val codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS)
        codec.configure(MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, sr, 1).apply {
            setInteger(MediaFormat.KEY_BIT_RATE, 24000)
            setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, sr * 2)  // one second of PCM
        }, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
        codec.start()
        rec.startRecording()
        // The encoder's own header says pre-skip 104: its 6.5 ms lookahead counted at 16 kHz, in a 48 kHz field
        // (AOSP C2SoftOpusEnc), and every piece repeated it, cutting 2.2 ms of real audio at each seam. The
        // encoder runs on across pieces, so only the run's first piece has the delay to skip: 6.5 ms = 312 at 48 kHz.
        val rest = Ble.opusFormat(0)
        format = Ble.opusFormat(312)
        val pcm = ByteArray(sr * 2 / 50)  // 20 ms
        var samples = 0L                  // fed to the encoder
        val info = MediaCodec.BufferInfo()

        fun drain(eos: Boolean) {
            while (true) {
                val i = codec.dequeueOutputBuffer(info, if (eos) 10000 else 0)
                if (i == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) continue
                if (i < 0) { if (eos && i == MediaCodec.INFO_TRY_AGAIN_LATER) continue else break }
                if (info.size > 0 && info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG == 0) {
                    write(codec.getOutputBuffer(i)!!, info.offset, info.size, info.flags, startWall, info.presentationTimeUs)
                    format = rest
                }
                codec.releaseOutputBuffer(i, false)
                if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) return
            }
        }

        while (running) {
            val n = rec.read(pcm, 0, pcm.size)
            if (n <= 0) continue
            val i = codec.dequeueInputBuffer(100_000)
            if (i >= 0) {
                codec.getInputBuffer(i)!!.apply { clear(); put(pcm, 0, n) }
                codec.queueInputBuffer(i, 0, n, samples * 1_000_000L / sr, 0)
                samples += n / 2
            }
            drain(false)
        }
        rec.stop(); rec.release()
        val i = codec.dequeueInputBuffer(100_000)
        if (i >= 0) codec.queueInputBuffer(i, 0, 0, samples * 1_000_000L / sr, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
        drain(true)
        closeFile()
        codec.stop(); codec.release()
    }
}
