package com.suyiiyii.live_record

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.media.MediaFormat
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import android.util.Log
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.UUID
import java.util.concurrent.BlockingQueue
import java.util.concurrent.CompletableFuture
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** The BLE recorder (XIAO / ProMicro nRF52840, our firmware): one record stream (firmware src/lr_store.h) it writes to
 *  its card and sends to a phone that follows it, the same bytes at the same file and offset. */
object Ble {
    val STORE: UUID = UUID.fromString("7c3e0000-1a2b-4c5d-8e9f-6c6976657265")
    val TIME: UUID = UUID.fromString("7c3e0001-1a2b-4c5d-8e9f-6c6976657265")
    val CTRL: UUID = UUID.fromString("7c3e0002-1a2b-4c5d-8e9f-6c6976657265")
    val DATA: UUID = UUID.fromString("7c3e0003-1a2b-4c5d-8e9f-6c6976657265")
    val LIVE: UUID = UUID.fromString("7c3e0004-1a2b-4c5d-8e9f-6c6976657265")
    val BAS: UUID = UUID.fromString("0000180f-0000-1000-8000-00805f9b34fb")
    val BATTERY: UUID = UUID.fromString("00002a19-0000-1000-8000-00805f9b34fb")
    val DIS: UUID = UUID.fromString("0000180a-0000-1000-8000-00805f9b34fb")
    val MODEL: UUID = UUID.fromString("00002a24-0000-1000-8000-00805f9b34fb")
    val FW_REV: UUID = UUID.fromString("00002a26-0000-1000-8000-00805f9b34fb")
    val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    const val TIME_EVERY_MS = 10 * 60_000L  // the phone's clock into the stream again: the board's drifts

    /** Runtime permissions the BLE recorder needs: scanning and connecting (location before Android 12). */
    val permissions = if (Build.VERSION.SDK_INT >= 31) listOf(android.Manifest.permission.BLUETOOTH_SCAN, android.Manifest.permission.BLUETOOTH_CONNECT)
                      else listOf(android.Manifest.permission.ACCESS_FINE_LOCATION)

    /** What the Ogg muxer needs for 16 kHz mono Opus: OpusHead, codec delay, seek pre-roll. The muxer writes csd-0
     *  into the file as is, so `preSkip` (48 kHz samples, RFC 7845 §5.1) is what the decoder drops at the start. */
    fun opusFormat(preSkip: Int = 0): MediaFormat = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, 16000, 1).apply {
        fun le(n: Int, fill: ByteBuffer.() -> Unit) = ByteBuffer.allocate(n).order(ByteOrder.LITTLE_ENDIAN).apply { fill(); flip() }
        setByteBuffer("csd-0", le(19) { put("OpusHead".toByteArray()); put(1); put(1); putShort(preSkip.toShort()); putInt(16000); putShort(0); put(0) })
        setByteBuffer("csd-1", le(8) { putLong(preSkip * 1_000_000_000L / 48000) })
        setByteBuffer("csd-2", le(8) { putLong(80_000_000) })
    }

    /** A unit's full name (LR-P1-XXXX: board and address); the advertisement alone carries only LR-P1. */
    fun fullName(n: String?) = n != null && Regex("LR-[A-Z0-9]+-[0-9A-F]{4}").matches(n)

    /** A unit's full name without its scan response: the board name in the advertisement (LR-P1) and the last two
     *  bytes of its address, as the firmware makes it (transport.c lr_set_name). A phone gets few scan responses from
     *  a recorder busy with another phone (slow non-connectable advertising, 10-04), so the list must not need them. */
    fun fullName(names: List<String?>, address: String): String? = names.firstOrNull(::fullName)
        ?: names.firstOrNull { it != null && Regex("LR-[A-Z0-9]+").matches(it) }?.let { "$it-" + address.takeLast(5).replace(":", "") }

    fun fullName(r: ScanResult) = fullName(listOf(r.scanRecord?.deviceName, r.device.name), r.device.address)

    /** The name pieces carry and the server sees: safe in a file name (the web gateway cleans it the same way). */
    fun tag(n: String) = n.replace(Regex("[^A-Za-z0-9._-]"), "_")

    /** The recorder's state in its scan response (firmware transport.c lr_status, company 0xFFFF); -1: an older
     *  firmware without it. */
    const val BOUND = 1
    const val CONNECTED = 2
    const val PAUSED = 4
    fun state(r: ScanResult) = r.scanRecord?.getManufacturerSpecificData(0xFFFF)?.firstOrNull()?.toInt()?.and(0xff) ?: -1
    /** The two bytes after it: the battery, mV / 20, and the card, the store's errno; -1: an older
     *  firmware, or no scan response in this result. */
    fun extra(r: ScanResult, i: Int) = r.scanRecord?.getManufacturerSpecificData(0xFFFF)?.getOrNull(i)?.toInt()?.and(0xff) ?: -1

    /** What the recorder runs on, from its status read (firmware lr_power.h, byte 47): "battery", "charging" (a board
     *  that cannot read its charger says so whenever it is on USB), "full"; null: an older firmware. */
    fun charge(status: ByteArray): String? = status.getOrNull(47)?.toInt()?.let { listOf("battery", "charging", "full").getOrNull(it) }
    const val CHARGE_EVERY_MS = 5_000L  // that byte read this often while no READ runs: a plug or unplug shows within seconds

    /** The card's state in a status read (bytes 6-7): 0 storing, else -errno (-16: lent as a USB disk). */
    fun store(status: ByteArray): Int = ByteBuffer.wrap(status).order(ByteOrder.LITTLE_ENDIAN).getShort(6).toInt()

    /** What the USB disk switch says after CTRL 0x06 asked for [on]: [err] its answer (null: none in 15 s), [store] the
     *  card's state the board reports since. A slow card answers late (lend() flushes, closes and unmounts before
     *  it answers; 10-13 BDAE went past 15 s and the app said it failed, a second tap took the disk back): no answer is
     *  not a failure, the board's state is what the switch shows and the note says. */
    fun usbNote(on: Boolean, err: Int?, store: Int): Map<String, Any?>? = when {
        err == -107 -> say("usbNoComputer")
        err != null && err != 0 -> say("usbFailed", "err" to err)
        (store == -16) != on -> say(if (on) "usbNotYetOn" else "usbStillOn")  // no answer in time: the switch follows the board
        on -> say("usbOn")
        else -> null
    }

    /** The phone's own bond goes too (hidden API, as BLE libraries call it): a bond the recorder has forgotten would
     *  only fail to encrypt when this phone binds it again. */
    fun removeBond(d: BluetoothDevice): Boolean = try {
        d.javaClass.getMethod("removeBond").invoke(d) as Boolean
    } catch (e: Exception) { Log.w("live-record", "ble removeBond: $e"); false }
}

/** The add-recorder page's list: the recorders around and the state each advertises. */
@SuppressLint("MissingPermission")
class Nearby(ctx: Context) {
    private class Seen(val address: String, val rssi: Int, val state: Int, val mv: Int, val card: Int, val busy: Boolean, val at: Long)
    private val seen = java.util.concurrent.ConcurrentHashMap<String, Seen>()
    private val scanner = ctx.getSystemService(BluetoothManager::class.java).adapter.bluetoothLeScanner
    private val cb = object : ScanCallback() {
        override fun onScanResult(type: Int, r: ScanResult) {
            val name = Ble.fullName(r) ?: return
            // the state byte is in the scan response: a result without one keeps the last known; a non-connectable
            // advertisement is a recorder connected to another phone, scan response or not
            val st = Ble.state(r).takeIf { it >= 0 } ?: seen[name]?.state ?: -1
            val mv = Ble.extra(r, 1).takeIf { it >= 0 }?.times(20) ?: seen[name]?.mv ?: -1
            val card = Ble.extra(r, 2).takeIf { it >= 0 } ?: seen[name]?.card ?: -1
            seen[name] = Seen(r.device.address, r.rssi, st, mv, card, !r.isConnectable, System.currentTimeMillis())
        }
    }

    fun start() = scanner?.startScan(listOf(ScanFilter.Builder().setServiceUuid(ParcelUuid(Ble.STORE)).build()),
        ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(), cb)

    fun stop() = scanner?.stopScan(cb)

    /** Seen in the last 5 s, nearest first. */
    fun list() = seen.entries.filter { System.currentTimeMillis() - it.value.at < 5000 }.sortedByDescending { it.value.rssi }
        .map { (n, s) -> mapOf("name" to n, "address" to s.address, "rssi" to s.rssi, "state" to s.state, "mv" to s.mv, "card" to s.card, "busy" to s.busy) }
}

/** Finds the recorder by scanning for its service (always: the board's address is random static, and a device made
 *  from a bare address after a Bluetooth restart is tried as public and times out, status 147, 10-03): the one the
 *  person chose on the add-recorder page (prefs bleName). Each connection runs a session on its own thread, one GATT
 *  operation at a time: pair, only right after that choice (prefs bindPending; with bindForce the recorder then
 *  forgets its other phones), follow the record stream into a mirror of the board's files (`recorder`/<name>, see
 *  Mirror), READ what the mirror lacks (what was recorded while away, a lost notify), put the phone's clock into the
 *  stream on connect and every 10 minutes, turn the mirror into dated Ogg pieces in `chunks` (the upload queue) and
 *  delete from the card what is mirrored and uploaded. Battery and card are read every minute. A dropped link is
 *  reconnected with autoConnect, which waits for the device to come back in range. Permissions are checked before the
 *  service starts.
 *  Binding is the app's soft limit: a recorder that forgot this phone (another phone took it) is not
 *  paired again behind the person's back; the link stops and says so (prefs bleKicked) until they add it again. */
@SuppressLint("MissingPermission")
class BleLink(private val ctx: Context, private val prefs: SharedPreferences, private val chunks: File, private val undated: File,
              private val sliceS: () -> Int, private val onPiece: (Int) -> Unit,
              private val onName: (String) -> Unit, private val report: (String, Map<String, Any?>) -> Unit) {
    companion object {
        // what Android reports when the link cannot be encrypted with the keys it has
        val AUTH_FAILED = setOf("GATT 5", "GATT 15", "GATT 137")
    }

    @Volatile var state: Map<String, Any?> = say("scanning")  // what the link does, as a code (say()) the app words
    @Volatile var hw: Map<String, Any?> = emptyMap()
    @Volatile private var on = true
    private val adapter = ctx.getSystemService(BluetoothManager::class.java).adapter
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var gatt: BluetoothGatt? = null

    /** Turning Bluetooth off tears the link down without a callback (10-03: the link stayed "connected" with no data). */
    private val adapterState = object : BroadcastReceiver() {
        override fun onReceive(c: Context, i: Intent) {
            when (i.getIntExtra(BluetoothAdapter.EXTRA_STATE, 0)) {
                BluetoothAdapter.STATE_TURNING_OFF -> {
                    Log.i("live-record", "ble adapter off")
                    // the link goes without a disconnect callback (gatt is gone first): said here
                    DeviceLog.app(DeviceLog.mine(), "bluetooth", "手机蓝牙关了" + if (gatt != null) "，连接随之断开" else "", "warn", mapOf("on" to false))
                    main.removeCallbacksAndMessages(null)
                    gatt?.let { abort(it) }
                    gatt?.close(); gatt = null
                    state = say("bluetoothOff")
                }
                BluetoothAdapter.STATE_ON -> {
                    DeviceLog.app(DeviceLog.mine(), "bluetooth", "手机蓝牙开了", fields = mapOf("on" to true))
                    if (on && gatt == null) { Log.i("live-record", "ble adapter on"); main.removeCallbacksAndMessages(null); scan() }
                }
            }
        }
    }

    fun start() {
        ctx.registerReceiver(adapterState, IntentFilter(BluetoothAdapter.ACTION_STATE_CHANGED))
        scan()
    }

    fun stop() {
        on = false
        listen(false)
        ctx.unregisterReceiver(adapterState)
        main.removeCallbacksAndMessages(null)
        try { adapter.bluetoothLeScanner?.stopScan(scanCb) } catch (_: Exception) {}
        gatt?.let { abort(it); it.disconnect(); it.close() }
        gatt = null
    }

    private fun scan() {
        val scanner = adapter.bluetoothLeScanner ?: return retry(say("bluetoothNotOn")) { scan() }
        val bound = prefs.getString("bleName", null) ?: run { state = say("unbound"); return }
        if (prefs.getBoolean("bleKicked", false)) { state = say("kicked"); return }
        state = say("scanning", "name" to bound)
        // filtered by service: unfiltered scans are paused while the screen is off
        scanner.startScan(listOf(ScanFilter.Builder().setServiceUuid(ParcelUuid(Ble.STORE)).build()),
            ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build(), scanCb)
    }

    private val scanCb = object : ScanCallback() {
        override fun onScanResult(type: Int, r: ScanResult) {
            val name = Ble.fullName(r) ?: return
            if (name != prefs.getString("bleName", null)) return
            val st = Ble.state(r)
            if (!r.isConnectable) { state = say("elsewhere", "name" to name); return }
            adapter.bluetoothLeScanner?.stopScan(this)
            Log.i("live-record", "ble found ${r.device.address} $name rssi=${r.rssi} state=$st")
            prefs.edit().putString("bleAddress", r.device.address).apply()  // for a firmware update
            onName(Ble.tag(name))
            if (on) connect(r.device, false)
        }
        override fun onScanFailed(code: Int) = retry(say("scanFailed", "err" to code)) { scan() }
    }

    private fun retry(why: Map<String, Any?>, delayMs: Long = 5000, what: () -> Unit) {
        state = why + ("s" to delayMs / 1000)
        if (on) main.postDelayed({ if (on) what() }, delayMs)
    }

    /** The first try connects directly (quick); after a drop autoConnect waits for the device without a timeout. */
    private fun connect(d: BluetoothDevice, auto: Boolean) {
        state = if (auto) say("away", "lastSeen" to lastSeen) else say("connecting")
        gatt = d.connectGatt(ctx, auto, cb, BluetoothDevice.TRANSPORT_LE) ?: return retry(say("connectFailed")) { connect(d, true) }
    }

    @Volatile private var pairingFailed = false
    @Volatile private var lastSeen: Long? = null  // wall ms the link last went down
    @Volatile var pausedUntil = 0L                   // wall ms the recorder's pause ends (0: recording), as last known

    //
    // One GATT operation at a time: the session thread starts one and waits for its callback
    //
    private class Gone : Exception("link gone")
    @Volatile private var pending: CompletableFuture<ByteArray>? = null
    private val ctrl = LinkedBlockingQueue<ByteArray>()
    private val data = LinkedBlockingQueue<ByteArray>()
    private val live = LinkedBlockingQueue<ByteArray>()

    private fun abort(@Suppress("UNUSED_PARAMETER") g: BluetoothGatt) { pending?.completeExceptionally(Gone()) }

    private fun op(what: String, timeoutS: Long = 15, start: () -> Boolean): ByteArray {
        val f = CompletableFuture<ByteArray>()
        pending = f
        var tries = 0
        while (!start()) {  // the stack is busy with something else (a notify subscription by Android itself)
            if (++tries > 20 || gatt == null) throw Exception("$what could not start")
            Thread.sleep(50)
        }
        try { return f.get(timeoutS, TimeUnit.SECONDS) } catch (e: java.util.concurrent.ExecutionException) { throw e.cause!! }
    }

    private fun done(status: Int, value: ByteArray = ByteArray(0)) {
        val f = pending ?: return
        if (status == BluetoothGatt.GATT_SUCCESS) f.complete(value) else f.completeExceptionally(Exception("GATT $status"))
    }

    private fun chr(g: BluetoothGatt, s: UUID, c: UUID) = g.getService(s)?.getCharacteristic(c) ?: throw Exception("no $c on the device")

    private fun read(g: BluetoothGatt, s: UUID, c: UUID) = chr(g, s, c).let { ch -> op("read $c") { g.readCharacteristic(ch) } }

    @Suppress("DEPRECATION")
    private fun write(g: BluetoothGatt, s: UUID, c: UUID, v: ByteArray) {
        val ch = chr(g, s, c)
        op("write $c") {
            if (Build.VERSION.SDK_INT >= 33) g.writeCharacteristic(ch, v, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) == BluetoothGatt.GATT_SUCCESS
            else { ch.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT; ch.value = v; g.writeCharacteristic(ch) }
        }
    }

    @Suppress("DEPRECATION")
    private fun subscribe(g: BluetoothGatt, s: UUID, c: UUID, on: Boolean = true) {
        val ch = chr(g, s, c)
        g.setCharacteristicNotification(ch, on)
        val d = ch.getDescriptor(Ble.CCCD)
        val v = if (on) BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE else BluetoothGattDescriptor.DISABLE_NOTIFICATION_VALUE
        op("${if (on) "subscribe" else "unsubscribe"} $c") {
            if (Build.VERSION.SDK_INT >= 33) g.writeDescriptor(d, v) == BluetoothGatt.GATT_SUCCESS
            else { d.value = v; g.writeDescriptor(d) }
        }
    }

    private fun le(b: ByteArray) = ByteBuffer.wrap(b).order(ByteOrder.LITTLE_ENDIAN)

    /** Bonded or not at all: the firmware asks for encryption on connect, so Android pairs by itself (Just Works). */
    private fun bond(d: BluetoothDevice): Boolean {
        if (d.bondState == BluetoothDevice.BOND_BONDED) return true
        state = say("pairing")
        if (d.bondState == BluetoothDevice.BOND_NONE) d.createBond()
        val until = System.currentTimeMillis() + 60_000
        Thread.sleep(500)
        while (d.bondState == BluetoothDevice.BOND_BONDING && System.currentTimeMillis() < until) Thread.sleep(200)
        return d.bondState == BluetoothDevice.BOND_BONDED
    }

    /** Runs one step of a session, shown on the app's status bar ([code]) and logged ([what]) with how long it took. */
    private fun <T> step(code: String, what: String, block: () -> T): T {
        state = say(code)
        val t0 = android.os.SystemClock.elapsedRealtime()
        return block().also { val ms = android.os.SystemClock.elapsedRealtime() - t0; Log.i("live-record", "ble $code: $ms ms"); DeviceLog.step(what, ms) }
    }

    private fun session(g: BluetoothGatt) {
        val name = prefs.getString("bleName", null) ?: g.device.name ?: g.device.address
        try {
            step("mtu", "已连接，协商包长") { op("mtu") { g.requestMtu(512) } }  // a record per notify
            val adding = prefs.getBoolean("bindPending", false)
            if (g.device.bondState != BluetoothDevice.BOND_BONDED) {
                if (!adding) return kicked(g, "this phone's bond is gone")
                if (!step("pairing", "正在配对") { bond(g.device) }) {
                    Log.w("live-record", "ble pairing with $name failed")
                    pairingFailed = true
                    state = say("pairingFailed")
                    g.disconnect()
                    return
                }
            }
            step("services", "读取设备服务") { op("services") { g.discoverServices() } }
            // the first encrypted read: a recorder that no longer knows this phone fails it
            try { read(g, Ble.STORE, Ble.TIME) } catch (e: Exception) {
                if (!adding && e.message in AUTH_FAILED) return kicked(g, "${e.message}") else throw e
            }
            live.clear(); ctrl.clear(); data.clear()
            syncDoneKb = 0; syncTotalKb = 0; syncLiveKb = 0
            subscribe(g, Ble.STORE, Ble.CTRL)  // replies to LIST/READ/DELETE and the app's USB disk switch
            subscribe(g, Ble.STORE, Ble.DATA)
            subscribe(g, Ble.STORE, Ble.LIVE)  // first: nothing the stream adds after LIST is missed
            var synced = step("clock", "给设备对时") { sync(g) }
            val st = step("status", "读取设备状态") { status(g, name, fresh = true) }
            Log.i("live-record", "ble status: boot ${st.boot} frame ${st.frame} " + hw.entries.joinToString(" ") { "${it.key}=${it.value}" })
            if (adding) {
                if (prefs.getBoolean("bindForce", false)) step("forgetOthers", "让录音设备忘掉其他手机") {
                    val r = reply(g, byteArrayOf(5), 0x85)  // FORGET: every phone but this one
                    Log.i("live-record", "ble forgot other phones: ${r?.let { le(it).getInt(1) }}")
                }
                prefs.edit().remove("bindPending").remove("bindForce").apply()
                Log.i("live-record", "ble bound $name")
            }
            val m = Mirror(File(ctx.filesDir, "recorder/${Ble.tag(name)}"))
            mirrorName = Ble.tag(name); boot = st.boot
            if (m.reused(st.boot.toLong(), st.frame, st.at, chunks, undated, mirrorName)) {
                Log.w("live-record", "ble boot ${st.boot} was counted before: the mirror's files and 'T' records of it dropped")
                DeviceLog.app(mirrorName, "boot_reused", "开机号 ${st.boot} 以前用过：手机上这个开机号的旧 TIME 记录和文件状态作废，卡上的文件从头再取",
                              "warn", mapOf("boot" to st.boot, "frame" to st.frame))
            }
            open = -1; liveFloor = -1; ahead = -1; reading = null; needList = true; listFull = false; liveRead = true; stalls.clear()
            holeAt = -1; refollow = false; abandoned = -1; liveGaps = 0; settleUntil = android.os.SystemClock.elapsedRealtime() + 3000
            caught = 0; caughtSince = 0
            state = say("joining")
            var live0 = false  // LIVE taken since the file it goes on in was caught up
            var lastStatus = android.os.SystemClock.elapsedRealtime()
            var lastCharge = lastStatus
            var lastPieces = 0L
            // One loop for the link: LIVE as it comes, and one READ at a time alongside it for what the mirror
            // lacks (nextRead). LIVE waits only while the file it goes on in is READ (at connect, after a hole, at a new
            // file): a backlog of days no longer holds back what is said now
            while (gatt === g && on) {
                if (refollow) {  // a hole in the live file no card can fill: LIVE goes on in a new file, dated at once
                    refollow = false
                    follow(g)
                    synced = sync(g)
                }
                var took = false
                if (!liveRead) {
                    var p = live.poll(if (reading == null) 1000L else 0L, TimeUnit.MILLISECONDS)  // nothing to READ: wait for LIVE here
                    if (gatt !== g || !on) break
                    while (p != null) {
                        took = true
                        if (takeLive(g, m, p)) break
                        p = live.poll()
                    }
                }
                if (reading != null) took = takeData(g, m) || took
                if (!live0 && !liveRead) {  // the live file caught up: LIVE from here on
                    live0 = true
                    DeviceLog.read(mirrorName, { write(g, Ble.STORE, Ble.CTRL, it) }, ctrl, data)
                    DeviceLog.connected(mirrorName)
                    state = say("connected")
                }
                if (reading == null) nextRead(g, m)
                val now = android.os.SystemClock.elapsedRealtime()
                if (now - lastPieces >= 500) { pieces(m); lastPieces = now }
                if (System.currentTimeMillis() - synced > Ble.TIME_EVERY_MS) { endRead(g, m); synced = sync(g) }
                // not while a READ is in flight: the backlog's DATA keeps the link; the minute's status reads it then
                // plugged or unplugged, the card lent or back (the USB disk switch follows the board)
                val changed = reading == null && now - lastCharge >= Ble.CHARGE_EVERY_MS &&
                    run { lastCharge = now; read(g, Ble.STORE, Ble.TIME).let { Ble.charge(it) != hw["charge"] || Ble.store(it) != hw["store"] } }
                if (changed || now - lastStatus > 60_000) {
                    status(g, name)
                    if (live0) {
                        endRead(g, m)
                        DeviceLog.read(mirrorName, { write(g, Ble.STORE, Ble.CTRL, it) }, ctrl, data)
                        clean(g, m)
                    }
                    unhold(m)
                    lastStatus = now
                    Log.i("live-record", "ble status: battery ${hw["battery"]}% card ${hw["store"]} free ${hw["free_kb"]} KB")
                }
                jobs.poll()?.let { endRead(g, m); it(g) }
                if (!took && reading != null) Thread.sleep(10)  // a READ's DATA on its way
            }
        } catch (e: Gone) {
        } catch (e: Exception) {
            Log.w("live-record", "ble session: $e")
            if (gatt === g) { state = say("linkError", "detail" to (e.message ?: "$e")); g.disconnect() }
        } finally {
            syncDoneKb = 0; syncTotalKb = 0; syncLiveKb = 0  // no link, no catch-up under way
        }
    }

    /** The phone's clock into the stream: the board puts a 'T' record (this frame was at this time) where it is. */
    private fun sync(g: BluetoothGatt): Long {
        val now = System.currentTimeMillis()
        write(g, Ble.STORE, Ble.TIME, le(ByteArray(8)).putLong(now).array())
        return now
    }

    private class Status(val boot: Int, val frame: Long, val store: Int, val at: Long)  // at: the phone's clock as it was read

    /** The recorder no longer knows this phone: no pairing behind the person's back, no reconnecting. */
    private fun kicked(g: BluetoothGatt, why: String) {
        Log.w("live-record", "ble kicked: $why")
        prefs.edit().putBoolean("bleKicked", true).apply()
        DeviceLog.app(Ble.tag(prefs.getString("bleName", null) ?: g.device.address), "kicked", "录音设备不再认这台手机（$why），不再重连", "error")
        state = say("kicked")
        g.disconnect()
    }

    /** Pause the recording for `s` seconds (0: go on now); the recorder keeps the time itself. */
    fun pause(s: Int) {
        if (gatt == null) throw Exception("not connected")
        jobs.put { g ->
            write(g, Ble.STORE, Ble.CTRL, le(ByteArray(5)).put(0x20).putInt(s).array())  // PAUSE
            pausedUntil = if (s > 0) System.currentTimeMillis() + s * 1000L else 0L
            DeviceLog.app(mirrorName, if (s > 0) "pause" else "resume", if (s > 0) "暂停录音 ${if (s % 60 == 0) "${s / 60} 分钟" else "$s 秒"}" else "恢复录音")
            Log.i("live-record", "ble pause $s s")
        }
    }

    @Volatile var listening: Listen? = null  // the recorder's sound played on this phone too (Listen.kt)
        private set

    /** Listening on this phone, or not. On, the session puts the phone's clock into the stream now: the lines go by the
     *  stream's 'T' records, which come only on connect and every 10 minutes otherwise. */
    fun listen(on: Boolean) {
        if (!on) { val l = listening; listening = null; l?.stop(); return }
        if (listening != null) return
        listening = Listen()
        jobs.put { g -> sync(g) }
    }

    /** Unbinding: the recorder forgets every phone, this one too (FORGET all), and the link goes. False: not connected. */
    fun forgetAll(): Boolean {
        if (gatt == null) return false
        val done = CompletableFuture<Boolean>()
        jobs.put { g -> done.complete(try { write(g, Ble.STORE, Ble.CTRL, byteArrayOf(5, 1)); true } catch (e: Exception) { false }) }
        // 5 s: a session that runs no jobs (still taking the card) would hold the unbinding up for nothing
        return try { done.get(5, TimeUnit.SECONDS) } catch (e: Exception) { false }
    }


    /** What the app asks of the connected board, run on the session thread between status reads. */
    private val jobs = LinkedBlockingQueue<(BluetoothGatt) -> Unit>()
    @Volatile var usbNote: Map<String, Any?>? = null
    private var usbAsk: Pair<Boolean, Int?>? = null  // the switch's last ask (on, its answer), until the board reports that state (status())
    @Volatile var syncNote: Map<String, Any?>? = null  // the last catch-up, for the app's recorder page
    @Volatile var syncDoneKb = 0L           // the catch-up under way: taken of total (0 of 0: none)
    @Volatile var syncTotalKb = 0L
    @Volatile var syncLiveKb = 0L           // of it, what LIVE still waits for: the file being recorded

    /** The board's SD card as a USB disk on the computer it is plugged into (CTRL 0x06), or back. */
    fun usbDisk(on: Boolean) {
        if (gatt == null) { usbNote = say("notConnected"); return }
        usbNote = say(if (on) "usbSwitchingOn" else "usbSwitchingOff")
        jobs.put { g ->
            val err = reply(g, byteArrayOf(6, if (on) 1 else 0), 0x86)?.get(5)?.toInt()
            Log.i("live-record", "ble usb disk $on: ${err ?: "no answer"}")
            usbAsk = on to err
            status(g, prefs.getString("bleName", null) ?: g.device.name ?: g.device.address)  // the note and the switch from what it reports
        }
    }

    /** A CTRL command and its reply (op), other replies passed over; null: none in 15 s. */
    private fun reply(g: BluetoothGatt, cmd: ByteArray, op: Int): ByteArray? {
        ctrl.removeIf { it[0] == op.toByte() }
        write(g, Ble.STORE, Ble.CTRL, cmd)
        val until = System.currentTimeMillis() + 15_000
        while (System.currentTimeMillis() < until) {
            val r = ctrl.poll(1, TimeUnit.SECONDS) ?: continue
            if (r[0] == op.toByte()) return r
        }
        return null
    }

    /** The board's state, kept for the app's page and sent to the server under the board's name. Its model and revision
     *  are read again on each connection ([fresh]): a board flashed over USB meanwhile came back on another one, and the
     *  page offered it the update it already ran. */
    private fun status(g: BluetoothGatt, name: String, fresh: Boolean = false): Status {
        val raw = read(g, Ble.STORE, Ble.TIME)
        val t = le(raw)
        val s = Status(t.getShort(0).toInt() and 0xffff, t.getInt(2).toLong() and 0xffffffffL, Ble.store(raw), System.currentTimeMillis())
        noCard = s.store < 0 && t.getInt(12) == 0
        val left = syncTotalKb - syncDoneKb
        val rate = if (caughtSince > 0) caught * 1000.0 / maxOf(1000L, android.os.SystemClock.elapsedRealtime() - caughtSince) else 0.0
        val h = mutableMapOf<String, Any?>("name" to name, "store" to s.store, "free_kb" to (t.getInt(8).toLong() and 0xffffffffL),
            "total_kb" to (t.getInt(12).toLong() and 0xffffffffL), "battery" to read(g, Ble.BAS, Ble.BATTERY).firstOrNull()?.toInt(),
            "charge" to Ble.charge(raw),
            // a backlog read in the background: what is left, and when it is done at this pace
            "backlog_kb" to left, "backlog_eta_s" to if (rate > 0 && left > 0) (left * 1024 / rate).toLong() else null,
            "live_gaps" to liveGaps,  // holes in LIVE the card could not fill this session (follow())
            "rssi" to rssi(g))
        if (t.limit() >= 47) pausedUntil = t.getInt(43).toLong().let { if (it > 0) System.currentTimeMillis() + it * 1000 else 0L }
        for ((k, c) in listOf("model" to Ble.MODEL, "fw" to Ble.FW_REV))
            h[k] = (if (fresh) null else hw[k]) ?: try { String(read(g, Ble.DIS, c)) } catch (e: Exception) { null }
        hw = h
        usbAsk?.let { (on, err) ->
            usbNote = Ble.usbNote(on, err, s.store)
            if (err != null || (s.store == -16) == on) usbAsk = null
        }
        prefs.edit().putString("bleModel", h["model"] as String?).putString("bleFw", h["fw"] as String?).apply()  // for an update later
        Dfu.back(h["fw"] as String?)  // the first status after an update, for its log
        report(Ble.tag(name), h - "name")
        return s
    }

    /** The link's signal in dBm (a timeout drop from distance, the case's effect); null: the stack had none. */
    private fun rssi(g: BluetoothGatt): Int? = try { op("rssi") { g.readRemoteRssi() }[0].toInt() } catch (e: Gone) { throw e } catch (e: Exception) { null }

    //
    // What the mirror lacks, READ alongside LIVE. At most one READ at a time; the board ends one at any other
    // command or TIME write, so the session ends it first (endRead) and the next READ goes on from what the mirror has.
    // Order: the file LIVE goes on in (and the one before it, after a new file) while LIVE waits; then the rest, oldest
    // first: what the board recorded while the phone was away, files of earlier boots, a lost notify
    //
    private class Reading(val f: Long, val from: Long, var last: Long)
    private var reading: Reading? = null
    private var open = -1L       // the file the stream goes on in
    private var liveFloor = -1L  // LIVE waits for the files from here on (liveRead); older ones are the backlog
    private var ahead = -1L      // a backlog under way: pieces of the files from here on go up ahead of it
    private var liveRead = true  // the live file (and the one before it) first: LIVE goes on from where its READ ends
    private var needList = true
    private var listFull = false  // the last LIST was cut at Mirror.LIST_MAX: more files once some are deleted
    private var holeAt = -1L      // LIVE went on at this offset past a hole in the live file: its READ has to reach it
    private var refollow = false  // that READ did not: no card to fill it from (none in the board, or it failed)
    private var abandoned = -1L   // the live file given up for a new one (follow()): what LIVE still sends of it is dropped
    private var liveGaps = 0
    // no card (store < 0, none mounted this boot): nothing to LIST or READ; each READ kept the link on its fast interval
    // (2420, 10-07: 15 ms for an hour). A hole in LIVE is a new file at once (follow())
    private var noCard = false
    private var settleUntil = 0L  // elapsedRealtime; after a subscribe LIVE still sends the file before the new one, from mid-way
    private val stalls = HashMap<Long, Int>()
    private var caught = 0L       // a catch-up under way: bytes taken since (elapsedRealtime; 0: none)
    private var caughtSince = 0L
    private var fastAt = -60_000L

    /** LIST: the card's files with their sizes into the mirror, and the file the stream goes on in. */
    private fun list(g: BluetoothGatt, m: Mirror) {
        val r = mutableMapOf<Long, Long>()
        ctrl.removeIf { it[0] == 0x81.toByte() || it[0] == 0x80.toByte() }
        write(g, Ble.STORE, Ble.CTRL, byteArrayOf(1))  // LIST
        var o = -1L
        while (true) {
            val x = ctrl.poll(10, TimeUnit.SECONDS) ?: throw Exception("LIST timed out")
            if (x[0] == 0x81.toByte()) r[le(x).getInt(1).toLong() and 0xffffffffL] = le(x).getInt(5).toLong() and 0xffffffffL
            if (x[0] == 0x80.toByte()) {
                if (x[1] != 0.toByte()) Log.w("live-record", "ble LIST error ${x[1]}")
                if (x.size >= 6) o = le(x).getInt(2).toLong() and 0xffffffffL
                break
            }
        }
        if (o >= 0) open = o
        if (liveFloor < 0) liveFloor = open
        m.listed(r, open)
        needList = false
        listFull = r.size >= Mirror.LIST_MAX
        Log.i("live-record", "ble LIST: ${r.size} files, open %08X, missing ${m.missing().map { "%08X".format(it.first) }}".format(open))
    }

    /** Bytes on the card the mirror lacks: the live file's while LIVE waits for it, and the backlog (older files). A
     *  backlog makes the pieces of what is said now go up ahead of it (pieces(), the hold); the progress of both is
     *  the app's and the server's (status()). */
    private fun behind(m: Mirror): Long {
        var backlog = 0L
        var cur = 0L
        for ((f, from) in m.missing()) {
            val n = maxOf(0L, m.files[f]!!.size - from)
            if (f < liveFloor) backlog += n else if (liveRead) cur += n
        }
        if (backlog > 0 && ahead < 0) ahead = liveFloor
        if (backlog + cur > 0 && caughtSince == 0L) caughtSince = android.os.SystemClock.elapsedRealtime()
        syncDoneKb = caught / 1024
        syncTotalKb = if (backlog + cur > 0) (caught + backlog + cur) / 1024 else 0
        syncLiveKb = cur / 1024
        return backlog + cur
    }

    /** The next READ, or LIST first; none: caught up. */
    private fun nextRead(g: BluetoothGatt, m: Mirror) {
        if (noCard) {  // what a LIST would say, without asking: no files, the stream goes on in the live one
            if (needList) { m.listed(emptyMap(), open); needList = false }
            liveRead = false
            return
        }
        if (needList) list(g, m)
        val missing = m.missing()
        if (liveRead && open < 0) liveRead = false  // a LIST without the open file: nothing to wait for
        if (liveRead) {
            // the file before the live one, its end (LIVE may have missed it), then the live one: once, up to the
            // stream's end as the READ starts; LIVE (subscribed first) has every record from there. Reading on never
            // ended while recording: each READ found what the last one took time for
            missing.firstOrNull { it.first >= liveFloor && it.first != open }?.let { return startRead(g, m, it.first) }
            return startRead(g, m, open)
        }
        missing.firstOrNull { it.first != open }?.let { return startRead(g, m, it.first) }
        if (caughtSince > 0 && behind(m) == 0L) {  // caught up
            val s = (android.os.SystemClock.elapsedRealtime() - caughtSince) / 1000
            Log.i("live-record", "ble caught up: $caught B in $s s")
            syncNote = say("synced", "at" to System.currentTimeMillis(), "kb" to caught / 1024)
            if (caught > 0) DeviceLog.app(mirrorName, "sync", "补传 ${caught / 1024} KB，用时 $s s")
            caught = 0; caughtSince = 0; ahead = -1
            unhold(m)
        }
    }

    private fun startRead(g: BluetoothGatt, m: Mirror, f: Long) {
        data.clear()
        ctrl.removeIf { it[0] == 0x82.toByte() }
        val from = m.have(f)
        val now = android.os.SystemClock.elapsedRealtime()
        // a short interval for it (logged by onConnectionUpdated); the recorder puts its slow one back after the last READ
        if (now - fastAt > 60_000) { g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH); fastAt = now }
        write(g, Ble.STORE, Ble.CTRL, le(ByteArray(9)).put(2).putInt(f.toInt()).putInt(from.toInt()).array())  // READ
        reading = Reading(f, from, System.currentTimeMillis())
    }

    /** The READ's DATA so far into the mirror; true when there was any. It ends at its 0x82 (the DATA came before it),
     *  at a lost notify (a hole: STOP, and the rest of it is not taken), or after 10 s without DATA. */
    private fun takeData(g: BluetoothGatt, m: Mirror): Boolean {
        val r = reading ?: return false
        var took = false
        while (true) {
            val d = data.poll() ?: break
            took = true
            r.last = System.currentTimeMillis()
            if (d.size <= 4) continue
            val had = m.have(r.f)
            m.put(r.f, le(d).getInt(0).toLong() and 0xffffffffL, d.copyOfRange(4, d.size))?.let { hole ->
                Log.i("live-record", "ble DATA hole in %08X at $hole (got ${le(d).getInt(0)}): STOP".format(r.f))
                endRead(g, m, keep = false)
                return true
            }
            caught += m.have(r.f) - had
            if (m.have(r.f) % 32768 < d.size) behind(m)
        }
        if (ctrl.any { it[0] == 0x82.toByte() } && data.isEmpty() || System.currentTimeMillis() - r.last > 10_000) finishRead(m, false)
        return took
    }

    /** The READ in flight ended before anything else goes on CTRL or TIME; what it sent so far kept (keep: false
     *  after a hole: what comes after it is not the mirror's yet). */
    private fun endRead(g: BluetoothGatt, m: Mirror, keep: Boolean = true) {
        val r = reading ?: return
        write(g, Ble.STORE, Ble.CTRL, byteArrayOf(3))  // STOP
        val until = System.currentTimeMillis() + 3000
        while (ctrl.none { it[0] == 0x82.toByte() } && System.currentTimeMillis() < until) Thread.sleep(50)
        // no end: its DATA may still come and be taken for what goes next on DATA (LOG): the link again instead
        if (ctrl.none { it[0] == 0x82.toByte() }) throw Exception("READ does not stop")
        while (keep) {
            val d = data.poll() ?: break
            if (d.size <= 4) continue
            val had = m.have(r.f)
            if (m.put(r.f, le(d).getInt(0).toLong() and 0xffffffffL, d.copyOfRange(4, d.size)) != null) break
            caught += m.have(r.f) - had
        }
        data.clear()
        finishRead(m, true)
    }

    private fun finishRead(m: Mirror, stopped: Boolean) {
        val r = reading ?: return
        reading = null
        ctrl.removeIf { it[0] == 0x82.toByte() }
        val got = m.have(r.f) - r.from
        when {
            r.f == open && !stopped -> {
                // a hole the card did not fill: without a card nothing ever will, and every LIVE packet after it found
                // the hole again and was dropped (2420 with no card, 10-07: an hour of LIVE, no piece)
                if (holeAt > m.have(r.f)) refollow = true
                holeAt = -1
                liveRead = false  // LIVE goes on from where this READ ended
            }
            got > 0 || stopped -> stalls.remove(r.f)
            (stalls[r.f] ?: 0) >= 3 -> m.noMore(r.f)  // the card has no more of it
            else -> stalls[r.f] = (stalls[r.f] ?: 0) + 1
        }
        behind(m)
    }

    /** One LIVE packet into the mirror; true when LIVE has to wait for a READ: a new file (LIST says how the last one
     *  ended), a hole in the live file (a lost notify). */
    private fun takeLive(g: BluetoothGatt, m: Mirror, p: ByteArray): Boolean {
        if (p.size <= 8) return false
        val b = le(p)
        val f = b.getInt(0).toLong() and 0xffffffffL
        if (f <= abandoned) return false
        if (f > open) {
            liveFloor = open; open = f; needList = true; liveRead = true
            endRead(g, m)
        }
        val hole = m.put(f, b.getInt(4).toLong() and 0xffffffffL, p.copyOfRange(8, p.size))
        if (hole != null && f == open && noCard) {
            if (android.os.SystemClock.elapsedRealtime() > settleUntil) refollow = true
            return refollow
        }
        if (hole != null && f == open) {
            Log.i("live-record", "ble LIVE hole in %08X at $hole: READ".format(f))
            liveFloor = open; liveRead = true; holeAt = b.getInt(4).toLong() and 0xffffffffL
            endRead(g, m)
        }
        return liveRead
    }

    /** LIVE from a new file: the board starts one for a phone that subscribes, so LIVE has it from offset 0 again.
     *  What the old one lacks is lost (the board had no card for it); the caller dates the new one (boot 0 dates each
     *  file by its own 'T'). */
    private fun follow(g: BluetoothGatt) {
        abandoned = open
        liveGaps++
        settleUntil = android.os.SystemClock.elapsedRealtime() + 3000
        Log.i("live-record", "ble LIVE hole in %08X the card cannot fill (card ${hw["store"]}): a new file".format(open))
        if (liveGaps == 1) DeviceLog.app(mirrorName, "live_gap", "实时音频断了一小段，录音设备上没有卡可以补，从新文件接着收", "warn")
        subscribe(g, Ble.STORE, Ble.LIVE, on = false)
        subscribe(g, Ble.STORE, Ble.LIVE)
    }

    /** Where the server's finalization waits while a backlog goes up after what is said now (prefs hold:<name>, sent
     *  with every upload): a minute before the first piece that goes ahead of it (its start, as pieces() will put
     *  it, may still move by the clock's drift). Set before that piece exists, so no upload has it without the hold. */
    private fun holdAhead(m: Mirror) {
        if (ahead < 0 || mirrorName.isEmpty() || prefs.contains("hold:$mirrorName")) return
        val at = m.nextStart(ahead) ?: return
        prefs.edit().putLong("hold:$mirrorName", at - HOLD_MARGIN_MS).apply()
        Log.i("live-record", "ble hold $mirrorName at ${at - HOLD_MARGIN_MS}: the backlog goes up after what is said now")
    }

    /** No backlog left, and none of its pieces waiting to go up: the server may finalize past the hold. The last READ's
     *  bytes are cut first, and the backlog runs up to where LIVE began, the hold plus its margin (Mirror.up). */
    private fun unhold(m: Mirror) {
        val h = prefs.getLong("hold:$mirrorName", -1)
        if (h < 0 || behind(m) > 0) return
        pieces(m)
        if (!m.up(chunks, h + HOLD_MARGIN_MS)) return
        prefs.edit().remove("hold:$mirrorName").apply()
        Log.i("live-record", "ble hold $mirrorName let go")
    }

    private val HOLD_MARGIN_MS = 60_000L  // the hold before where LIVE began: the clock's drift
    private var mirrorName = ""  // the session's: the board's name as pieces carry it, its boot now
    private var boot = 0

    /** The mirror into dated pieces (Mirror.pieces): the live file in pieces as long as the app's slice. */
    private fun pieces(m: Mirror) {
        holdAhead(m)
        val n = m.pieces(chunks, undated, mirrorName, sliceS() * 100, boot)
        if (n > 0) onPiece(n)
    }

    /** Files mirrored whole and uploaded: off the card (DELETE) and out of the mirror. */
    private fun clean(g: BluetoothGatt, m: Mirror) {
        if (noCard) return
        val done = m.done(chunks)
        if (done.isNotEmpty() && listFull) needList = true  // room in the next LIST for files it left out
        for (f in done) {
            val r = reply(g, le(ByteArray(5)).put(4).putInt(f.toInt()).array(), 0x84) ?: return  // DELETE
            val err = r[5].toInt()
            Log.i("live-record", "ble DELETE %08X: $err".format(f))
            if (err == 0 || err == -2) m.drop(f)  // gone (-2: not on this card)
        }
    }

    private val cb = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            Log.i("live-record", "ble state=$newState status=$status")
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                Thread { session(g) }.start()
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                abort(g)
                g.close()
                if (gatt !== g) return  // an old link, already given up (Bluetooth turned off)
                gatt = null
                DeviceLog.disconnected(mirrorName.ifEmpty { Ble.tag(g.device.name ?: g.device.address) }, status)
                lastSeen = System.currentTimeMillis()
                // a pairing that did not finish (a dialog left alone) waits a little; unbound or kicked: no reconnecting
                val wait = if (pairingFailed) 30_000L else 0L
                pairingFailed = false
                if (on && prefs.getString("bleName", null) != null && !prefs.getBoolean("bleKicked", false))
                    main.postDelayed({ if (on) connect(g.device, true) }, wait)
            }
        }

        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) {
            Log.i("live-record", "ble mtu=$mtu status=$status")
            done(BluetoothGatt.GATT_SUCCESS)
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) = done(status)

        override fun onCharacteristicRead(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray, status: Int) = done(status, value)

        @Deprecated("before Android 13")
        @Suppress("DEPRECATION")
        override fun onCharacteristicRead(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) =
            onCharacteristicRead(g, c, c.value ?: ByteArray(0), status)

        override fun onCharacteristicWrite(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) = done(status)

        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) = done(status)

        override fun onReadRemoteRssi(g: BluetoothGatt, rssi: Int, status: Int) = done(status, byteArrayOf(rssi.toByte()))

        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray) {
            when (c.uuid) {
                Ble.LIVE -> { live.put(value); listening?.feed(value) }
                Ble.CTRL -> ctrl.put(value)
                Ble.DATA -> data.put(value)
            }
        }

        @Deprecated("before Android 13")
        @Suppress("DEPRECATION")
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic) =
            onCharacteristicChanged(g, c, c.value.clone())

        /** Hidden in the SDK but called on every connection parameter update (the Nordic BLE library relies on it). */
        @Suppress("unused")
        fun onConnectionUpdated(g: BluetoothGatt, interval: Int, latency: Int, timeout: Int, status: Int) {
            Log.i("live-record", "ble connection interval=${interval * 1.25} ms latency=$latency timeout=${timeout * 10} ms status=$status")
        }
    }
}
