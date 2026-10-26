package com.suyiiyii.live_record

import android.annotation.SuppressLint
import android.app.PendingIntent
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.BluetoothLeScanner
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.BroadcastReceiver
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.os.Build
import android.os.ParcelUuid
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.Writer
import java.util.concurrent.CompletableFuture
import java.util.concurrent.ExecutionException
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

/** One system scan at a time; duplicate or late deliveries cannot start a second connection. */
internal class BeanScanWait<T>(private val name: String, private val start: () -> Unit, private val stop: () -> Unit,
                               private val now: () -> Long = { System.nanoTime() / 1_000_000 }) {
    private var found: CompletableFuture<T>? = null
    private var retryAfter = 0L
    @Volatile var retrying = false
        private set
    private class ScanFailure(cause: Exception) : IOException(cause.message, cause)

    fun await(timeoutMs: Long = TimeUnit.MINUTES.toMillis(10)): T? {
        val f = CompletableFuture<T>()
        synchronized(this) { check(found == null); found = f }
        try {
            try { start() }
            catch (e: InterruptedException) { throw e }
            catch (e: Exception) { throw ScanFailure(e) }
            return f.get(timeoutMs, TimeUnit.MILLISECONDS)
        } catch (_: TimeoutException) { return null }  // registration failures may arrive without an error intent
        catch (e: ExecutionException) { throw e.cause!! }
        finally {
            synchronized(this) { found = null }
            stop()
        }
    }

    @Synchronized fun deliver(beanName: String, connectable: Boolean, value: T) =
        beanName == name && connectable && now() >= retryAfter && (found?.complete(value) ?: false)
    @Synchronized fun fail(e: Exception, scanError: Boolean = true) {
        found?.completeExceptionally(if (scanError) ScanFailure(e) else e)
    }

    /** Register the next scan immediately; only its results wait out a failed attempt. Log once per streak. */
    @Synchronized fun backoff(): Boolean {
        retryAfter = now() + 5000
        return (!retrying).also { retrying = true }
    }
    @Synchronized fun connected() { retryAfter = 0; retrying = false }

    /** The service and JVM regression tests use the same exception/retry path. */
    fun loop(on: () -> Boolean, attempt: () -> Boolean, retry: (Exception) -> Boolean, log: (Exception) -> Unit) {
        while (on()) {
            try { if (!attempt()) return }
            catch (_: InterruptedException) { return }
            catch (e: Exception) {
                if (retry(e) && backoff()) log(e)
                // No scan survives a registration/system error. Keep connection failures result-driven.
                if (e is ScanFailure) try { Thread.sleep(5000) } catch (_: InterruptedException) { return }
            }
        }
    }
}

/** The existing connectedDevice service owns the session; this receiver only wakes its scan waiter. */
class BeanScanReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) { RecorderService.bean?.scanResult(intent) }
}

/** A scan result's bean: its name (from the MAC in its manufacturer data) and the flags byte it advertises (bit 7:
 *  bound to a Feishu account); null when it is no bean. */
fun beanAdv(r: ScanResult): Pair<String, Int>? {
    val rec = r.scanRecord ?: return null
    if (rec.serviceUuids?.contains(ParcelUuid(Bean.SERVICE)) != true) return null
    val m = rec.manufacturerSpecificData ?: return null
    if (m.size() == 0) return null
    return (Bean.name(m.keyAt(0), m.valueAt(0)) ?: return null) to Bean.flags(m.keyAt(0), m.valueAt(0))
}

/** The Feishu recorder bean the person added (prefs bleName, D3200-XXXX), over BLE: found by scanning for its service
 *  and its MAC (its address changes), connected without pairing (the bean asks for none), its notifications into
 *  BeanSync, which does the rest. One thread: scan, connect, session, then scan again as soon as the link goes.
 *  What it advertises is logged when its flags or connectability change (the Feishu bound bit). A debug capture of
 *  each session's frames, keys and audio goes to <external files>/bean-capture/ while a file `on` is there (adb), for replays. */
@SuppressLint("MissingPermission")
class BeanLink(private val ctx: Context, private val prefs: SharedPreferences, chunks: File,
               private val sliceS: () -> Int, private val onPiece: (Int) -> Unit, private val onName: (String) -> Unit,
               private val onReport: (String, Map<String, Any?>) -> Unit) {
    private val name = prefs.getString("bleName", null) ?: ""
    private val adapter = ctx.getSystemService(BluetoothManager::class.java).adapter
    @Volatile private var on = true
    @Volatile private var gatt: BluetoothGatt? = null
    @Volatile private var linkState: Map<String, Any?> = say("scanning", "name" to name)
    @Volatile private var inSession = false
    @Volatile private var lastSeen: Long? = null
    @Volatile private var rssi: Int? = null
    @Volatile private var mtu = 23
    @Volatile private var downStatus = 0
    private var advFlags = -2
    private var advConnectable: Boolean? = null
    private var capture: Writer? = null
    private var captured = 0L
    private var linkEstablished = false

    private class Gone : IOException("link gone")  // an IOException: BeanSync starts the bean again on the next connection
    private val GONE = ByteArray(0)
    private val notes = LinkedBlockingQueue<ByteArray>()

    private val io = object : BeanSync.Io {
        override fun send(frame: ByteArray) = write(gatt ?: throw Gone(), frame)
        override fun receive(ms: Long): ByteArray? {
            if (adapter?.isEnabled != true) throw Gone()  // Bluetooth off: no disconnect callback comes
            val b = notes.poll(ms, TimeUnit.MILLISECONDS) ?: return null
            if (b === GONE) { notes.put(GONE); throw Gone() }
            return b
        }
        override fun now() = System.currentTimeMillis()
        override fun liveUnits() = sliceS() * 50
        override fun log(kind: String, text: String, level: String?, fields: Map<String, Any?>, at: Long?) {
            Log.i("live-record", "bean $kind: $text")
            DeviceLog.app(name, kind, text, level, fields, at)
        }
        override fun report(hw: Map<String, Any?>) = onReport(name, hw + mapOf("name" to name, "rssi" to rssi))
        override fun hold(at: Long?) = prefs.edit().apply { if (at == null) remove("hold:$name") else putLong("hold:$name", at) }.apply()
        override fun held() = prefs.getLong("hold:$name", -1).takeIf { it >= 0 }
        override fun refused() = prefs.getStringSet("refused:$name", null).orEmpty().map(String::toLong).toSet()
        override fun end(at: Long) { prefs.edit().putLong("end:$name", at).commit() }  // Uploader.ends() sends it
        override fun piece() = onPiece(1)
        override fun trace(what: String, v: Map<String, Any?>) {
            val w = capture ?: return
            val line = JSONObject(v + mapOf("t" to System.currentTimeMillis(), "what" to what)).toString()
            captured += line.length
            if (captured > 200L shl 20) { w.close(); capture = null; return }  // 200 MB: a capture is for a short test
            w.write(line); w.write("\n"); w.flush()  // each line: an app killed mid-session keeps what it had
        }
    }

    private val sync = BeanSync(name, File(ctx.filesDir, "bean/$name.json"), chunks, io)
    private val thread = Thread { loop() }

    val state get() = if (inSession) sync.state else linkState
    /** The session is up (its key exchange and first reads done): recording or not. */
    val connected get() = inSession && sync.state["code"] in setOf("beanIdle", "joining", "connected")
    val hw get() = sync.hw + mapOf("name" to name)
    val syncDoneKb get() = sync.syncDoneKb
    val syncTotalKb get() = sync.syncTotalKb

    fun start() = thread.start()

    fun stop() {
        on = false
        thread.interrupt()
        gatt?.disconnect()
    }

    private fun loop() = scan.loop({ on }, {
        linkEstablished = false
        if (!Bean.isBean(name) || prefs.getString("bleName", null) != name) {
            linkState = say("unbound")
            false
        } else {
            if (adapter?.isEnabled != true) { linkState = say("bluetoothOff"); Thread.sleep(5000) }
            else find()?.let { onName(name); connect(it) }
            true
        }
    }, { e ->
        when (e) {
            is BeanSync.Again -> false  // a transfer stopped by letting the link go (logged): straight back
            is Gone -> !linkEstablished
            else -> {
                if (on) linkState = say("linkError", "detail" to (e.message ?: "$e"))
                true
            }
        }
    }, { e -> Log.w("live-record", "bean: $e; retry on scan results after 5 s") })

    private val scanIntent by lazy {
        PendingIntent.getBroadcast(ctx, 0, Intent(ctx, BeanScanReceiver::class.java).setAction("${ctx.packageName}.BEAN_SCAN"),
            PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0))
    }
    @Volatile private var scanWait: BeanScanWait<BluetoothDevice>? = null

    @Suppress("DEPRECATION")
    internal fun scanResult(intent: Intent) {
        val wait = scanWait ?: return
        val error = intent.getIntExtra(BluetoothLeScanner.EXTRA_ERROR_CODE, 0)
        if (error != 0) { wait.fail(Exception("scan failed: $error")); return }
        val results = intent.getParcelableArrayListExtra<ScanResult>(BluetoothLeScanner.EXTRA_LIST_SCAN_RESULT).orEmpty()
        for (r in results) {
            val (n, flags) = beanAdv(r) ?: continue
            if (n != name || !on || prefs.getString("bleName", null) != name) continue
            rssi = r.rssi
            if (flags != advFlags || r.isConnectable != advConnectable) {
                advFlags = flags; advConnectable = r.isConnectable
                DeviceLog.app(name, "bean_adv", "豆子在广播：标志 0x%02X（%s），%s，信号 %d dBm".format(flags,
                    if (flags >= 0 && flags and 0x80 != 0) "已绑定飞书" else "未绑定飞书", if (r.isConnectable) "可连接" else "不可连接", r.rssi),
                    fields = mapOf("flags" to flags, "connectable" to r.isConnectable, "rssi" to r.rssi))
            }
            if (wait.deliver(n, r.isConnectable, r.device) && !wait.retrying) {
                val power = ctx.getSystemService(PowerManager::class.java)
                val interactive = power.isInteractive
                val idle = power.isDeviceIdleMode
                val text = "系统扫描发现豆子，开始重连（interactive=$interactive，deviceIdle=$idle）"
                Log.i("live-record", "bean connect: $text")
                DeviceLog.app(name, "connect", text, fields = mapOf("path" to "pendingIntent",
                    "interactive" to interactive, "deviceIdle" to idle))
            }
        }
    }

    /** A filtered PendingIntent scan wakes us in the background without a wake lock.
     *  The bean is unpaired and changes address, so autoConnect to an old address is insufficient. Android's BLE
     *  background guide recommends this API: https://developer.android.com/develop/connectivity/bluetooth/ble/background
     *  Keep ALL_MATCHES: a bean can first advertise as non-connectable, then become connectable at the same address.
     *  Renew after 10 minutes: Android can silently drop a failed PendingIntent scanner registration. */
    private val scan by lazy {
        BeanScanWait<BluetoothDevice>(name, {
            val scanner = adapter?.bluetoothLeScanner ?: throw Gone()
            scanner.stopScan(scanIntent)  // also removes a registration left by a killed service process
            val error = scanner.startScan(listOf(ScanFilter.Builder().setServiceUuid(ParcelUuid(Bean.SERVICE)).build()),
                ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_POWER).build(), scanIntent)
            check(error == 0) { "scan failed: $error" }
        }, { try { adapter?.bluetoothLeScanner?.stopScan(scanIntent) } catch (_: Exception) {} },
            now = { SystemClock.elapsedRealtime() })
    }

    private fun find(): BluetoothDevice? {
        val wait = scan
        scanWait = wait
        val bluetooth = object : BroadcastReceiver() {
            override fun onReceive(ctx: Context, intent: Intent) {
                if (intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, BluetoothAdapter.ERROR) != BluetoothAdapter.STATE_ON)
                    wait.fail(Gone(), scanError = false)  // the Bluetooth-off path already waits before retrying
            }
        }
        linkState = lastSeen?.let { say("away", "lastSeen" to it) } ?: say("scanning", "name" to name)
        ctx.registerReceiver(bluetooth, IntentFilter(BluetoothAdapter.ACTION_STATE_CHANGED))
        return try { wait.await() }
        finally { scanWait = null; ctx.unregisterReceiver(bluetooth) }
    }

    private fun connect(d: BluetoothDevice) {
        val steps = StringBuilder()
        fun <T> step(code: String, what: String, block: () -> T): T {
            linkState = say(code)
            val t0 = android.os.SystemClock.elapsedRealtime()
            return block().also { steps.append(if (steps.isEmpty()) "" else "，").append("$what ${android.os.SystemClock.elapsedRealtime() - t0} ms") }
        }
        notes.clear(); downStatus = 0
        var up = false
        try {
            step("connecting", "连接") { op("connect", 30) { d.connectGatt(ctx, false, cb, BluetoothDevice.TRANSPORT_LE).also { gatt = it } != null } }
            val g = gatt ?: throw Gone()
            up = true
            linkEstablished = true
            scan.connected()
            step("mtu", "协商包长") { op("mtu") { g.requestMtu(512) } }
            if (mtu < 78) throw Exception("MTU $mtu: too small for the key exchange")
            step("services", "读取设备服务") { op("services") { g.discoverServices() } }
            val s = g.getService(Bean.SERVICE) ?: throw Exception("no bean service on ${d.address}")
            cmd = s.getCharacteristic(Bean.WRITE) ?: throw Exception("no write characteristic")
            val n = s.getCharacteristic(Bean.NOTIFY) ?: throw Exception("no notify characteristic")
            step("services", "订阅通知") { subscribe(g, n) }
            if (prefs.getBoolean("bindPending", false) || !prefs.contains("beanSince:$name"))  // added now: its files from here on are this phone's
                prefs.edit().remove("bindPending").remove("bindForce").putLong("beanSince:$name", System.currentTimeMillis()).apply()
            capture = File(ctx.getExternalFilesDir("bean-capture") ?: ctx.filesDir, "on").takeIf { it.exists() }
                ?.let { File(it.parentFile, "${System.currentTimeMillis()}-$name.jsonl").bufferedWriter() }
            captured = 0
            inSession = true
            sync.session(prefs.getLong("beanSince:$name", 0), "$steps（MTU $mtu）")
        } finally {
            inSession = false
            capture?.close(); capture = null
            gatt?.let { it.disconnect(); it.close() }
            gatt = null
            if (up) { lastSeen = System.currentTimeMillis(); DeviceLog.disconnected(name, downStatus) }
        }
    }

    //
    // One GATT operation at a time (as BleLink): the session thread starts one and waits for its callback
    //
    @Volatile private var pending: CompletableFuture<ByteArray>? = null
    @Volatile private var cmd: BluetoothGattCharacteristic? = null

    private fun op(what: String, timeoutS: Long = 15, start: () -> Boolean): ByteArray {
        val f = CompletableFuture<ByteArray>()
        pending = f
        var tries = 0
        while (!start()) {  // the stack is busy with something else
            if (++tries > 20) throw Exception("$what could not start")
            Thread.sleep(50)
        }
        try { return f.get(timeoutS, TimeUnit.SECONDS) }
        catch (e: ExecutionException) { throw e.cause!! }
        catch (e: TimeoutException) { throw Exception("$what: no answer in $timeoutS s") }
    }

    private fun done(status: Int, value: ByteArray = ByteArray(0)) {
        val f = pending ?: return
        if (status == BluetoothGatt.GATT_SUCCESS) f.complete(value) else f.completeExceptionally(Exception("GATT $status"))
    }

    /** To the bean's command characteristic only (never the OTA service), and only what BeanSync lets through. */
    @Suppress("DEPRECATION")
    private fun write(g: BluetoothGatt, v: ByteArray) {
        check(Bean.allowed(v, sync.deletable) && v.size <= mtu - 3) { "not sent: ${v.size} B" }
        val ch = cmd ?: throw Gone()
        op("write") {
            if (Build.VERSION.SDK_INT >= 33) g.writeCharacteristic(ch, v, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) == BluetoothGatt.GATT_SUCCESS
            else { ch.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT; ch.value = v; g.writeCharacteristic(ch) }
        }
    }

    @Suppress("DEPRECATION")
    private fun subscribe(g: BluetoothGatt, ch: BluetoothGattCharacteristic) {
        g.setCharacteristicNotification(ch, true)
        val d = ch.getDescriptor(Ble.CCCD) ?: throw Exception("no CCCD")
        op("subscribe") {
            if (Build.VERSION.SDK_INT >= 33) g.writeDescriptor(d, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE) == BluetoothGatt.GATT_SUCCESS
            else { d.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE; g.writeDescriptor(d) }
        }
    }

    private val cb = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            Log.i("live-record", "bean state=$newState status=$status")
            if (newState == BluetoothProfile.STATE_CONNECTED) done(BluetoothGatt.GATT_SUCCESS)
            else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                downStatus = status
                pending?.completeExceptionally(Gone())
                notes.put(GONE)
            }
        }
        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) {
            Log.i("live-record", "bean mtu=$mtu status=$status")
            if (status == BluetoothGatt.GATT_SUCCESS) this@BeanLink.mtu = mtu
            done(BluetoothGatt.GATT_SUCCESS)
        }
        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) = done(status)
        override fun onCharacteristicWrite(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) = done(status)
        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) = done(status)
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray) {
            if (c.uuid == Bean.NOTIFY) notes.put(value)
        }
        @Deprecated("before Android 13")
        @Suppress("DEPRECATION")
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic) = onCharacteristicChanged(g, c, c.value.clone())
    }
}
