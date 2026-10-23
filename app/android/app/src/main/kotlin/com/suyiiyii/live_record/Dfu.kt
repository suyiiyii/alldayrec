package com.suyiiyii.live_record

import android.content.Context
import android.net.Uri
import no.nordicsemi.android.dfu.DfuBaseService
import no.nordicsemi.android.dfu.DfuProgressListenerAdapter
import no.nordicsemi.android.dfu.DfuServiceInitiator
import no.nordicsemi.android.dfu.DfuServiceListenerHelper
import java.io.File

/** Firmware updates over Bluetooth with Nordic's DFU library: the app asks the running firmware to reboot into the
 *  Adafruit bootloader (legacy DFU, as firmware/devkit/lr_dfu.py does) and sends the package for its model, which
 *  the recorder page downloaded from the server (firmware.dart, server/firmware.py). Only while not recording: the
 *  recorder takes one connection at a time. */
object Dfu {
    @Volatile var state: String? = null
    @Volatile var busy = false  // from start until done, aborted or failed: the app keeps the recorder link down
    @Volatile var log: DfuLog? = null  // the update under way, or the last one until its recorder is back

    /** The recorder's revision as a status read it (BleLink): the first one after an update ends goes into its log. */
    @Synchronized fun back(fw: String?) {
        val l = log?.takeIf { it.ended != null } ?: return
        log = null
        l.back(fw)
    }

    /** Why the package may not go onto the bound recorder (error code, message), as Dfu.refusal in Dfu.swift: the model
     *  the recorder said it is must be the package's, its revision as last read ([running]) not the package's [version]
     *  (the same build sent again, a page that offered it from a stale revision), and the bytes must be the ones
     *  the server listed ([expected]). Null: go on. */
    fun refusal(want: String?, model: String?, address: String?, linked: Boolean, sha256: String, expected: String?,
                version: String?, running: String?): Pair<String, String>? = when {
        busy -> "busy" to "正在升级"
        linked -> "busy" to "录音设备的连接还没断开，稍后再试"
        address == null || model == null -> "none" to "还没连上过录音设备，不知道它的型号"
        want != model -> "model" to "这个固件是 $want 的，录音设备是 $model，不能刷"
        version != null && version == running -> "same" to "录音设备已经是 $version，不用再升级"
        sha256 != expected -> "sha256" to "固件下载不完整，再试一次"
        else -> null
    }

    fun start(ctx: Context, address: String, name: String, zip: File) {
        DfuServiceInitiator.createDfuNotificationChannel(ctx)
        DfuServiceListenerHelper.registerProgressListener(ctx, object : DfuProgressListenerAdapter() {
            var asked = false  // the recorder was told to reboot into its bootloader: the next start is there
            override fun onDeviceConnecting(a: String) { state = "正在连接录音设备" }
            override fun onEnablingDfuMode(a: String) { state = "录音设备重启进入升级模式"; asked = true }
            override fun onDfuProcessStarting(a: String) { state = "开始升级"; if (asked) log?.reached("boot") }
            override fun onDfuProcessStarted(a: String) { log?.reached("send") }
            override fun onProgressChanged(a: String, percent: Int, speed: Float, avgSpeed: Float, part: Int, parts: Int) {
                state = "升级中 $percent% · ${"%.1f".format(avgSpeed)} KB/s"
                log?.progress(percent, avgSpeed.toDouble())
            }
            override fun onDfuCompleted(a: String) {
                log?.done(); state = "升级完成，录音设备已重启"; busy = false
                // what the page shows until the link reads it
                log?.let { ctx.getSharedPreferences("recorder", Context.MODE_PRIVATE).edit().putString("bleFw", it.to).apply() }
                ended()
            }
            override fun onDfuAborted(a: String) { log?.fail(null, "升级已取消"); state = "升级已取消"; busy = false; ended() }
            override fun onError(a: String, error: Int, errorType: Int, message: String?) {
                log?.fail(error, message ?: "")
                state = "升级失败：$message（录音设备还在就再试一次，不见了就插 USB 用 lr.sh flash 刷）"; busy = false
                android.util.Log.w("live-record", "dfu error $error type $errorType: $message")
                ended()
            }
            fun ended() { DfuServiceListenerHelper.unregisterProgressListener(ctx, this); RecorderService.relink(ctx) }
        })
        state = "准备升级"; busy = true
        android.util.Log.i("live-record", "dfu ${zip.name} to $address")
        DfuServiceInitiator(address).setDeviceName(name).setKeepBond(true)
            .setForceDfu(false)
            .setForceScanningForNewAddressInLegacyDfu(true)  // the bootloader advertises as AdaDFU on another address
            // As lr_dfu.py: the Adafruit bootloader stalls mid-image on PRN 16 (XIAO) and on big packets.
            // The library asks for MTU 517 and sends MTU-3 bytes a packet (244 at the bootloader's 247): keep 20 B.
            .setPacketsReceiptNotificationsValue(8)
            .disableMtuRequest()
            .setZip(Uri.fromFile(zip))
            .start(ctx, DfuService::class.java)
    }
}

class DfuService : DfuBaseService() {
    override fun getNotificationTarget(): Class<MainActivity> = MainActivity::class.java
}

/** What an update leaves in the device log (DeviceLog, kind "dfu"), as DfuLog in Dfu.swift: its start, the recorder
 *  found in its bootloader ("boot"), the transfer's start ("send") and every 25 % ("progress"), its end ("done", or
 *  "fail" with the stage it got to) and the recorder's first status after it ("back": the revision it runs); each with
 *  `step` and `ms` since the start (since the end for "back"). The server's upgrades() (db.py) joins them with the
 *  recorder's own DFU and BOOT records. */
class DfuLog(val device: String, from: String?, val to: String, bytes: Int, sha256: String, battery: Int?,
             private val out: (String, String, String?, Map<String, Any?>) -> Unit = { d, t, l, f -> DeviceLog.app(d, "dfu", t, l, f) }) {
    var t0 = System.currentTimeMillis(); @Volatile var ended: Long? = null  // the test moves them back; Dfu.back reads it on the BLE thread
    private var stage = "start"; private var pct = 0; private var failed = false

    init {
        note("start", "开始升级 ${from ?: "?"} → $to，${bytes / 1024} KB，sha ${sha256.take(8)}，电量 ${battery?.let { "$it%" } ?: "未知"}",
             mapOf("ms" to 0L, "from" to from, "to" to to, "bytes" to bytes, "sha" to sha256.take(8), "battery" to battery))  // by definition, not the clock
    }

    /** "boot" (connected to the bootloader) or "send" (the image goes out). */
    fun reached(s: String) {
        stage = s
        note(s, "${if (s == "boot") "进入升级模式" else "开始传输"}，开始后 ${span(System.currentTimeMillis() - t0)}")
    }

    fun progress(p: Int, kbps: Double) {
        if (p < 100 && p / 25 > pct / 25) note("progress", "传输 $p%，平均 ${"%.1f".format(kbps)} KB/s", mapOf("pct" to p, "kbps" to Math.round(kbps * 10) / 10.0))
        pct = p
    }

    fun done() {
        ended = System.currentTimeMillis()
        note("done", "升级完成，用时 ${span(ended!! - t0)}")
    }

    /** `code`: the library's error code, null for the app's own reasons. */
    fun fail(code: Int?, error: String) {
        ended = System.currentTimeMillis(); failed = true
        val at = when (stage) { "start" -> "进入升级模式前"; "boot" -> "进入升级模式后、传输前"; else -> "传输到 $pct%" }
        note("fail", "升级失败（$at）：$error${code?.let { "（错误 $it）" } ?: ""}",
             mapOf("stage" to stage, "pct" to pct, "code" to code, "error" to error), "error")
    }

    fun back(fw: String?) {
        val ok = fw == to; val ms = System.currentTimeMillis() - (ended ?: t0)
        note("back", "${if (failed) "升级失败后" else "升级后"}连回，用时 ${span(ms)}，固件 ${fw ?: "?"}${if (ok) "（目标版本）" else "，不是目标版本"}",
             mapOf("ms" to ms, "fw" to fw, "ok" to ok), if (ok || failed) null else "warn")
    }

    private fun note(step: String, text: String, fields: Map<String, Any?> = emptyMap(), level: String? = null) =
        out(device, text, level, mapOf("step" to step, "ms" to System.currentTimeMillis() - t0) + fields)

    companion object {
        fun span(ms: Long) = if (ms < 60_000) "${ms / 1000} 秒" else "${ms / 60_000} 分 ${ms / 1000 % 60} 秒"
    }
}
