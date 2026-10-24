// The BLE recorder (LR-X1 / LR-P1, our firmware) on iOS: port of the Android app's Ble.kt over CoreBluetooth. The
// record stream itself (mirror, dated Ogg pieces) is Store.swift. Where iOS differs:
//  - no Bluetooth address: a recorder is its peripheral identifier (stable on this phone), and its full name
//    (LR-P1-XXXX) comes only with the scan response (the advertisement has LR-P1)
//  - no bond API: iOS pairs by itself when the recorder asks for encryption (its own dialog) and keeps the keys until
//    the person forgets the recorder in Settings › Bluetooth. A recorder that has forgotten this phone fails the
//    encryption ("peer removed pairing information") and iOS does not pair again until then: binding it again needs
//    that step, which the app asks for
//  - a connect request never times out (Android's autoConnect); the `bluetooth-central` background mode wakes the app
//    for the recorder's notifications, and state restoration relaunches it in the background after the system ended
//    it (not after the person swiped it away)
import CoreBluetooth
import Foundation

enum Ble {
  static func uuid(_ n: Int) -> CBUUID { CBUUID(string: String(format: "7c3e%04x-1a2b-4c5d-8e9f-6c6976657265", n)) }
  static let store = uuid(0), time = uuid(1), ctrl = uuid(2), data = uuid(3), live = uuid(4)
  static let bas = CBUUID(string: "180F"), battery = CBUUID(string: "2A19")
  static let dis = CBUUID(string: "180A"), model = CBUUID(string: "2A24"), fwRev = CBUUID(string: "2A26")
  static let timeEveryMs: Int64 = 10 * 60_000  // the phone's clock into the stream again: the board's drifts

  /// A unit's full name (LR-P1-XXXX: board and address).
  static func isFull(_ n: String?) -> Bool { n?.range(of: "^LR-[A-Z0-9]+-[0-9A-F]{4}$", options: .regularExpression) != nil }

  /// The best name of `names` (advertisement or scan response, iOS's name cache): a full one, else the board name.
  static func name(_ names: [String?]) -> String? {
    names.first(where: isFull) ?? names.first { $0?.range(of: "^LR-[A-Z0-9]+$", options: .regularExpression) != nil } ?? nil
  }

  /// The name pieces carry and the server sees: safe in a file name (the web gateway cleans it the same way).
  static func tag(_ n: String) -> String { n.replacingOccurrences(of: "[^A-Za-z0-9._-]", with: "_", options: .regularExpression) }

  /// The recorder's state in its scan response (firmware transport.c lr_status: company 0xFFFF, then bit 0 bound, bit 1
  /// connected, bit 2 paused); -1: not in this advertisement.
  static func state(_ adv: [String: Any]) -> Int {
    guard let d = adv[CBAdvertisementDataManufacturerDataKey] as? Data, d.count >= 3, d.prefix(2) == Data([0xFF, 0xFF]) else { return -1 }
    return Int(d[d.startIndex + 2])
  }

  /// The two bytes after it: the battery, mV / 20, and the card, the store's errno; -1: an older
  /// firmware, or no scan response in this advertisement.
  static func extra(_ adv: [String: Any], _ i: Int) -> Int {
    guard let d = adv[CBAdvertisementDataManufacturerDataKey] as? Data, d.count > 2 + i, d.prefix(2) == Data([0xFF, 0xFF]) else { return -1 }
    return Int(d[d.startIndex + 2 + i])
  }

  static func u32(_ b: [UInt8], _ at: Int) -> Int64 { Int64(Store.le(b, at, 4)) }

  /// What the recorder runs on, from its status read (firmware lr_power.h, byte 47): "battery", "charging" (a board that
  /// cannot read its charger says so whenever it is on USB), "full"; nil: an older firmware.
  static func charge(_ status: [UInt8]) -> String? {
    status.count > 47 && status[47] < 3 ? ["battery", "charging", "full"][Int(status[47])] : nil
  }
  static let chargeEvery: TimeInterval = 5  // that byte read this often while no READ runs: a plug or unplug shows within seconds

  /// The card's state in a status read (bytes 6-7): 0 storing, else -errno (-16: lent as a USB disk).
  static func store(_ status: [UInt8]) -> Int { Int(Int16(truncatingIfNeeded: Store.le(status, 6, 2))) }

  /// What the USB disk switch says after CTRL 0x06 asked for `on`: `err` its answer (nil: none in 15 s), `store` the
  /// card's state the board reports since. A slow card answers late (lend() flushes, closes and unmounts before it
  /// answers; 10-13 BDAE went past 15 s and the app said it failed, a second tap took the disk back): no answer is not a
  /// failure, the board's state is what the switch shows and the note says.
  static func usbNote(on: Bool, err: Int?, store: Int) -> String? {
    if err == -107 { return "录音设备没接在电脑的 USB 上" }
    if let err, err != 0 { return "切换失败 \(err)" }
    if (store == -16) != on {
      return (on ? "还没切到 U 盘模式" : "还在 U 盘模式") + "（录音设备没有及时回应，卡慢时会晚一些；开关跟着录音设备的状态）"
    }
    return on ? "U 盘模式：电脑上出现 OMI 盘（约 20 秒）；用完先在电脑上推出，再切回" : nil
  }

  static func hhmm() -> String {
    let f = DateFormatter()
    f.dateFormat = "HH:mm"
    return f.string(from: Date())
  }

  static func log(_ s: String) { NSLog("live-record ble %@", s) }
}

/// A value several threads read and one writes (the link's state for the app's page).
@propertyWrapper final class Shared<T> {
  private var v: T
  private let l = NSLock()
  init(wrappedValue: T) { v = wrappedValue }
  var wrappedValue: T {
    get { l.lock(); defer { l.unlock() }; return v }
    set { l.lock(); v = newValue; l.unlock() }
  }
}

/// A queue one thread waits on: notifications in, the session takes them.
final class Inbox<T> {
  private var items: [T] = []
  private let c = NSCondition()
  func put(_ x: T) { c.lock(); items.append(x); c.signal(); c.unlock() }
  /// The oldest, waiting up to `s` seconds for one.
  func poll(_ s: TimeInterval = 0) -> T? {
    c.lock(); defer { c.unlock() }
    let until = Date(timeIntervalSinceNow: s)
    while items.isEmpty { if !c.wait(until: until) { return nil } }
    return items.removeFirst()
  }
  func clear(where match: (T) -> Bool = { _ in true }) { c.lock(); items.removeAll(where: match); c.unlock() }
  func contains(where match: (T) -> Bool) -> Bool { c.lock(); defer { c.unlock() }; return items.contains(where: match) }
  var isEmpty: Bool { c.lock(); defer { c.unlock() }; return items.isEmpty }
}

/// The add-recorder page's list: the recorders around and the state each advertises.
final class Nearby: NSObject, CBCentralManagerDelegate {
  private struct Seen { let name: String, rssi: Int, state: Int, mv: Int, card: Int, busy: Bool, at: Date }
  private var seen: [UUID: Seen] = [:]  // on q
  private let q = DispatchQueue(label: "nearby")
  private var central: CBCentralManager?

  override init() {
    super.init()
    central = CBCentralManager(delegate: self, queue: q)
  }

  func centralManagerDidUpdateState(_ c: CBCentralManager) {
    if c.state == .poweredOn { c.scanForPeripherals(withServices: [Ble.store], options: [CBCentralManagerScanOptionAllowDuplicatesKey: true]) }
  }

  func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral, advertisementData a: [String: Any], rssi: NSNumber) {
    let old = seen[p.identifier]
    // the full name once seen is kept: a recorder busy with another phone seldom gets its scan response across
    guard let name = Ble.name([a[CBAdvertisementDataLocalNameKey] as? String, p.name, old?.name]) else { return }
    let st = Ble.state(a)
    // a non-connectable advertisement is a recorder connected to another phone, scan response or not
    let busy = (a[CBAdvertisementDataIsConnectable] as? Bool).map { !$0 } ?? old?.busy ?? false
    let mv = Ble.extra(a, 1), card = Ble.extra(a, 2)
    seen[p.identifier] = Seen(name: name, rssi: rssi.intValue == 127 ? old?.rssi ?? -100 : rssi.intValue, state: st >= 0 ? st : old?.state ?? -1,
                              mv: mv >= 0 ? mv * 20 : old?.mv ?? -1, card: card >= 0 ? card : old?.card ?? -1, busy: busy, at: Date())
  }

  func stop() { q.sync { if central?.state == .poweredOn { central?.stopScan() }; central = nil } }

  /// Seen in the last 5 s, nearest first; "address" is the peripheral identifier. One that can be bound only once its
  /// full name is known (its scan response; pieces go up under it): within a second for one not busy with a phone.
  func list() -> [[String: Any]] {
    q.sync {
      seen.filter { -$0.value.at.timeIntervalSinceNow < 5 && (Ble.isFull($0.value.name) || $0.value.busy) }.sorted { $0.value.rssi > $1.value.rssi }
        .map { ["name": $0.value.name, "address": $0.key.uuidString, "rssi": $0.value.rssi, "state": $0.value.state, "mv": $0.value.mv,
                "card": $0.value.card, "busy": $0.value.busy] }
    }
  }
}

/// The link to the bound recorder (prefs bleName, bleId: the one the person chose on the add-recorder page), as
/// BleLink in Ble.kt: each connection runs a session on its own thread, one GATT operation at a time: pairing (iOS's,
/// only right after that choice: prefs bindPending; with bindForce the recorder then forgets its other phones), follow
/// the record stream into a mirror of the board's files (Application Support/recorder/<name>, see Mirror), READ what
/// the mirror lacks (what was recorded while away, a lost notify), put the phone's clock into the stream on connect and
/// every 10 minutes, turn the mirror into dated Ogg pieces in `chunks` (the upload queue) and delete from the card what
/// is mirrored and uploaded. Battery and card are read every minute. A dropped link is connected again with a request
/// iOS keeps until the recorder is back in range.
/// Binding is the app's soft limit: a recorder that forgot this phone (another phone took it) is not
/// paired again behind the person's back; the link stops and says so (prefs bleKicked) until they add it again.
final class BleLink: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {
  static let kicked = "已被其他手机绑定：在「录音设备」页重新添加"
  static let restoreId = "live-record.recorder"

  @Shared var state = "正在找录音设备"
  @Shared var hw: [String: Any] = [:]
  @Shared var tag: String?             // the board's name as pieces carry it
  @Shared var files = 0                // pieces made
  @Shared var pausedUntil: Int64 = 0   // wall ms the recorder's pause ends (0: recording), as last known
  @Shared var usbNote: String?
  private var usbAsk: (on: Bool, err: Int?)?  // the switch's last ask, until the board reports that state (status())
  @Shared var syncNote: String?        // the last catch-up, for the app's recorder page
  @Shared var syncDoneKb: Int64 = 0    // the catch-up under way: taken of total (0 of 0: none)
  @Shared var syncTotalKb: Int64 = 0
  @Shared var syncLiveKb: Int64 = 0    // of it, what LIVE still waits for: the file being recorded
  @Shared private(set) var listening: Listen?  // the recorder's sound played on this phone too (Listen.swift)

  private let prefs = Recorder.defaults
  private let chunks: URL, undated: URL, mirrors: URL
  private let sliceS: () -> Int
  private let report: (String, [String: Any]) -> Void
  private let q = DispatchQueue(label: "ble")  // CoreBluetooth's: its callbacks, and every call into it
  private var central: CBCentralManager?
  @Shared private var on = true
  @Shared private var peripheral: CBPeripheral?
  @Shared private var conn: Conn?  // the connection a session belongs to (nil: none up)
  private let one = NSLock()       // one session at a time: an old one may still be finishing
  @Shared private var pairingFailed = false
  @Shared private var lastSeen: String?   // HH:mm the link last went down
  @Shared private var staleNote: String?  // iOS still has keys the recorder forgot: what the person has to do
  @Shared private var bluetoothOff = false  // as last logged
  @Shared private var waiting = false  // a reconnect request said in the device log, until the link is up again

  init(chunks: URL, undated: URL, mirrors: URL, sliceS: @escaping () -> Int, report: @escaping (String, [String: Any]) -> Void) {
    self.chunks = chunks; self.undated = undated; self.mirrors = mirrors; self.sliceS = sliceS; self.report = report
    super.init()
    tag = prefs.string(forKey: "bleName").map(Ble.tag)  // for the end signal, connected or not
  }

  /// One connection. iOS hands back the same CBPeripheral after a drop, so a session tells its own connection from the
  /// next one by this.
  private final class Conn {
    let p: CBPeripheral
    init(_ p: CBPeripheral) { self.p = p }
  }

  private func begin(_ p: CBPeripheral) {
    // its session runs already: iOS connected while the app was not running, the restored link started one at
    // poweredOn and iOS still says didConnect (10-09, 10-15: services read twice)
    if conn?.p === p { return }
    let c = Conn(p)
    conn = c
    Thread.detachNewThread { self.session(c) }
  }

  /// With a restore identifier: iOS keeps the connection (or the request for one) when it ends the app and relaunches
  /// it in the background for the recorder (AppDelegate starts the link again, centralManager(_:willRestoreState:)).
  func start() {
    q.sync { central = CBCentralManager(delegate: self, queue: q, options: [CBCentralManagerOptionRestoreIdentifierKey: BleLink.restoreId]) }
  }

  func stop() {
    on = false
    try? listen(false)
    q.sync {
      conn = nil
      abort()
      if central?.state == .poweredOn {
        central?.stopScan()
        if let p = peripheral { central?.cancelPeripheralConnection(p) }
      }
      peripheral = nil
      central?.delegate = nil
      central = nil
    }
  }

  // on q from here to the session

  func centralManagerDidUpdateState(_ c: CBCentralManager) {
    Ble.log("central state \(c.state.rawValue)")
    switch c.state {
    case .poweredOn:
      if bluetoothOff { bluetoothOff = false; DeviceLog.app(DeviceLog.mine(), "bluetooth", "手机蓝牙开了", fields: ["on": true]) }
      if let p = peripheral, p.state == .connected { begin(p) }  // restored, connected
      else if let p = peripheral { connect(p, auto: true) }  // restored while waiting for it: the request again
      else { find() }
    case .poweredOff, .resetting:  // the link goes without a callback: said here
      if !bluetoothOff {
        bluetoothOff = true
        DeviceLog.app(DeviceLog.mine(), "bluetooth", "手机蓝牙关了" + (conn != nil ? "，连接随之断开" : ""), level: "warn", fields: ["on": false])
      }
      conn = nil
      abort()
      peripheral = nil
      state = "蓝牙已关闭"
    case .unauthorized: state = "没有蓝牙权限：在 设置 › alldayrec 里允许蓝牙"
    default: break
    }
  }

  /// The app relaunched in the background for the recorder: what iOS kept for it. The launch's options never said so
  /// (no .bluetoothCentrals in 13 background launches).
  func centralManager(_ c: CBCentralManager, willRestoreState dict: [String: Any]) {
    let p = (dict[CBCentralManagerRestoredStatePeripheralsKey] as? [CBPeripheral])?.first
    Ble.log("restored \(p.map { "\($0.identifier) state \($0.state.rawValue)" } ?? "nothing")")
    let kept = switch p?.state {
    case .connected?: "已连上录音设备"
    case .connecting?: "连接请求还在等录音设备"
    case nil: "没有录音设备的连接或请求"
    default: "录音设备没有连着"
    }
    DeviceLog.app(tag ?? DeviceLog.mine(), "restore", "iOS 恢复蓝牙：\(kept)", fields: ["state": p.map { $0.state.rawValue } ?? NSNull()])
    guard let p else { return }
    p.delegate = self
    peripheral = p
  }

  /// The bound recorder by its identifier; scanning for it only when iOS does not know that one (anymore).
  private func find() {
    guard on, let c = central else { return }
    guard let bound = prefs.string(forKey: "bleName") else { state = "未绑定录音设备"; return }
    if prefs.bool(forKey: "bleKicked") { state = BleLink.kicked; return }
    if let id = prefs.string(forKey: "bleId").flatMap(UUID.init(uuidString:)), let p = c.retrievePeripherals(withIdentifiers: [id]).first {
      return connect(p, auto: false)
    }
    state = "正在找 \(bound)"
    Ble.log("scanning for \(bound)")
    c.scanForPeripherals(withServices: [Ble.store])
  }

  func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral, advertisementData a: [String: Any], rssi: NSNumber) {
    let name = Ble.name([a[CBAdvertisementDataLocalNameKey] as? String, p.name])
    // in the background iOS asks for no scan responses: a full name never comes (binding saves the identifier for that)
    guard let bound = prefs.string(forKey: "bleName"), name == bound else {
      return Ble.log("scan: \(name ?? "-") (advertised \(a[CBAdvertisementDataLocalNameKey] as? String ?? "no name")) \(p.identifier)")
    }
    if a[CBAdvertisementDataIsConnectable] as? Bool == false { state = "\(bound) 正连着其他手机"; return }
    c.stopScan()
    Ble.log("found \(p.identifier) \(bound) rssi=\(rssi)")
    prefs.set(p.identifier.uuidString, forKey: "bleId")
    connect(p, auto: false)
  }

  private func connect(_ p: CBPeripheral, auto: Bool) {
    guard on, let c = central, c.state == .poweredOn else { return }
    let away = "录音设备不在附近，它在离线录音" + (lastSeen.map { "（最后连接 \($0)）" } ?? "") + "，回来后自动补传"
    state = staleNote ?? (auto ? away : "正在连接录音设备")
    p.delegate = self
    peripheral = p
    c.connect(p)
    // the request waits for the recorder as long as it takes: after a while, say where it is
    if !auto { q.asyncAfter(deadline: .now() + 10) { if self.peripheral === p && p.state == .connecting && self.staleNote == nil { self.state = away } } }
  }

  func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) {
    Ble.log("connected")
    if p === peripheral { begin(p) }
  }

  func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) { gone(p, error) }

  func centralManager(_ c: CBCentralManager, didDisconnectPeripheral p: CBPeripheral, error: Error?) { gone(p, error) }

  private func gone(_ p: CBPeripheral, _ error: Error?) {
    Ble.log("disconnected: \(error.map { "\($0)" } ?? "-")")
    conn = nil  // its session ends at its next step
    abort()
    guard p === peripheral else { return }  // an old link, already given up
    DeviceLog.disconnected(tag ?? p.identifier.uuidString, error?.localizedDescription)
    lastSeen = Ble.hhmm()
    if BleLink.stale(error) { forgotten(p) }
    // a pairing that did not finish (a dialog left alone) waits a little; unbound or kicked: no reconnecting
    let wait = pairingFailed ? 30.0 : 0
    pairingFailed = false
    q.asyncAfter(deadline: .now() + wait) {
      if self.on && self.peripheral === p && self.prefs.string(forKey: "bleName") != nil && !self.prefs.bool(forKey: "bleKicked") {
        if !self.waiting {  // once an outage: the request stays until the recorder is back, the app ended or not
          self.waiting = true
          DeviceLog.app(self.tag ?? p.identifier.uuidString, "waiting", "在等录音设备：连接请求已交给 iOS，录音设备一回到附近就连上")
        }
        self.connect(p, auto: true)
      }
    }
  }

  /// iOS's keys for a recorder that no longer has its own: "peer removed pairing information".
  private static func stale(_ e: Error?) -> Bool { (e as? CBError)?.code == .peerRemovedPairingInformation }

  /// The recorder forgot this phone while iOS kept its keys. Not adding it: kicked. Adding it: iOS must forget it too
  /// (no API for that) before it pairs again; asked of the person, tried again every 30 s (gone()).
  private func forgotten(_ p: CBPeripheral) {
    if !prefs.bool(forKey: "bindPending") { return kicked(p, "peer removed pairing information") }
    let name = prefs.string(forKey: "bleName") ?? "录音设备"
    staleNote = "这台 iPhone 还留着与 \(name) 的旧配对：到 设置 › 蓝牙 › \(name) › 忽略此设备，再回来（30 秒后自动重试）"
    state = staleNote!
    pairingFailed = true
  }

  /// The recorder no longer knows this phone: no pairing behind the person's back, no reconnecting.
  private func kicked(_ p: CBPeripheral, _ why: String) {
    Ble.log("kicked: \(why)")
    prefs.set(true, forKey: "bleKicked")
    DeviceLog.app(tag ?? p.identifier.uuidString, "kicked", "录音设备不再认这台手机（\(why)），不再重连", level: "error")
    state = BleLink.kicked
    q.async { self.central?.cancelPeripheralConnection(p) }
  }

  //
  // One GATT operation at a time: the session thread starts one on q and waits for its callback
  //
  private struct Gone: Error {}
  private struct Fail: Error, CustomStringConvertible { let description: String }
  private final class Op { let sem = DispatchSemaphore(value: 0); var result: Result<Data, Error> = .failure(Gone()) }
  @Shared private var pending: Op?
  private let ctrl = Inbox<[UInt8]>(), data = Inbox<[UInt8]>(), live = Inbox<[UInt8]>()
  private let jobs = Inbox<(Conn) throws -> Void>()  // what the app asks of the connected board

  private func abort() { if let o = pending { pending = nil; o.result = .failure(Gone()); o.sem.signal() } }

  private func done(_ error: Error?, _ value: Data = Data()) {
    guard let o = pending else { return }
    pending = nil
    o.result = error.map { .failure($0) } ?? .success(value)
    o.sem.signal()
  }

  private func op(_ c: Conn, _ what: String, timeout: TimeInterval = 15, _ start: @escaping () -> Void) throws -> Data {
    guard alive(c) else { throw Gone() }
    let o = Op()
    pending = o
    q.async(execute: start)
    guard o.sem.wait(timeout: .now() + timeout) == .success else {
      q.sync { if pending === o { pending = nil } }  // a late callback must not complete the next operation
      throw Fail(description: "\(what) 超时")
    }
    return try o.result.get()
  }

  private func chr(_ c: Conn, _ s: CBUUID, _ u: CBUUID) throws -> CBCharacteristic {
    guard let ch = q.sync(execute: { c.p.services?.first { $0.uuid == s }?.characteristics?.first { $0.uuid == u } }) else {
      throw Fail(description: "设备上没有 \(u)")
    }
    return ch
  }

  private func read(_ c: Conn, _ s: CBUUID, _ u: CBUUID, timeout: TimeInterval = 15) throws -> [UInt8] {
    let ch = try chr(c, s, u)
    return [UInt8](try op(c, "read \(u)", timeout: timeout) { c.p.readValue(for: ch) })
  }

  private func write(_ c: Conn, _ u: CBUUID, _ v: [UInt8]) throws {
    let ch = try chr(c, Ble.store, u)
    _ = try op(c, "write \(u)") { c.p.writeValue(Data(v), for: ch, type: .withResponse) }
  }

  /// Already on: a connection iOS kept for the app while it was not running (state restoration).
  private func subscribe(_ c: Conn, _ u: CBUUID) throws {
    let ch = try chr(c, Ble.store, u)
    if q.sync(execute: { ch.isNotifying }) { return }
    _ = try op(c, "subscribe \(u)") { c.p.setNotifyValue(true, for: ch) }
  }

  func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) { done(error) }

  func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor s: CBService, error: Error?) { done(error) }

  func peripheral(_ p: CBPeripheral, didWriteValueFor c: CBCharacteristic, error: Error?) { done(error) }

  func peripheral(_ p: CBPeripheral, didUpdateNotificationStateFor c: CBCharacteristic, error: Error?) { done(error) }

  func peripheral(_ p: CBPeripheral, didReadRSSI rssi: NSNumber, error: Error?) { done(error, Data([UInt8(bitPattern: rssi.int8Value)])) }

  /// Notifications (LIVE, CTRL, DATA) and read responses (the rest) come here alike, in the order they arrived.
  func peripheral(_ p: CBPeripheral, didUpdateValueFor c: CBCharacteristic, error: Error?) {
    let v = c.value ?? Data()
    switch c.uuid {
    case Ble.live:
      let b = [UInt8](v)
      live.put(b)
      listening?.feed(b)
    case Ble.ctrl: ctrl.put([UInt8](v))
    case Ble.data: data.put([UInt8](v))
    default: done(error, v)
    }
  }

  /// Runs one step of a session, shown on the app's status bar and logged with how long it took.
  private func step<T>(_ what: String, _ block: () throws -> T) rethrows -> T {
    state = what
    let t0 = Date()
    defer { let ms = Int(-t0.timeIntervalSinceNow * 1000); Ble.log("\(what): \(ms) ms"); DeviceLog.step(what, ms) }
    return try block()
  }

  private func alive(_ c: Conn) -> Bool { on && conn === c }

  private func session(_ p: Conn) {
    one.lock()
    defer { syncDoneKb = 0; syncTotalKb = 0; syncLiveKb = 0; one.unlock() }  // no link, no catch-up under way
    guard alive(p) else { return }
    DeviceLog.connecting()
    let name = prefs.string(forKey: "bleName") ?? p.p.identifier.uuidString
    let adding = prefs.bool(forKey: "bindPending")
    do {
      try step("读取设备服务") {
        _ = try op(p, "services") { p.p.discoverServices([Ble.store, Ble.bas, Ble.dis]) }
        for s in q.sync(execute: { p.p.services ?? [] }) { _ = try op(p, "characteristics") { p.p.discoverCharacteristics(nil, for: s) } }
      }
      tag = Ble.tag(name)
      // the first encrypted read: iOS pairs here if it has no keys for the recorder (its dialog: up to the recorder's
      // 30 s for an unencrypted link); a recorder that no longer knows this phone fails it
      pairingFailed = adding  // set first: a pairing left alone ends with the recorder dropping the link (gone())
      do {
        _ = try step(adding ? "正在配对：在弹出的对话框里点「配对」" : "正在建立加密连接") { try read(p, Ble.store, Ble.time, timeout: 40) }
        staleNote = nil
        pairingFailed = false
      } catch {
        Ble.log("first read: \(error)")
        if BleLink.stale(error) { return q.async { self.forgotten(p.p); self.central?.cancelPeripheralConnection(p.p) } }
        if !adding, let e = error as? CBATTError, [.insufficientAuthentication, .insufficientEncryption].contains(e.code) {
          return kicked(p.p, "\(error)")  // no keys any more, and the person did not pair again in iOS's dialog
        }
        if adding { state = "配对没有完成，30 秒后重试" }
        throw error
      }
      live.clear(); ctrl.clear(); data.clear()
      syncDoneKb = 0; syncTotalKb = 0; syncLiveKb = 0
      try subscribe(p, Ble.ctrl)  // replies to LIST/READ/DELETE and the app's USB disk switch
      try subscribe(p, Ble.data)
      try subscribe(p, Ble.live)  // before LIST: nothing the stream adds after it is missed
      var synced = try step("给设备对时") { try sync(p) }
      let st = try step("读取设备状态") { try status(p, name, fresh: true) }
      Ble.log("status: boot \(st.boot) frame \(st.frame) mtu \(q.sync { p.p.maximumWriteValueLength(for: .withoutResponse) } + 3) \(hw)")
      if adding {
        if prefs.bool(forKey: "bindForce") {
          try step("让录音设备忘掉其他手机") {
            let r = try reply(p, [5], 0x85)  // FORGET: every phone but this one
            Ble.log("forgot other phones: \(r.map { Ble.u32($0, 1) } ?? -1)")
          }
        }
        prefs.removeObject(forKey: "bindPending")
        prefs.removeObject(forKey: "bindForce")
        Ble.log("bound \(name)")
      }
      let m = Mirror(mirrors.appendingPathComponent(Ble.tag(name)))
      if try m.reused(boot: Int64(st.boot), frame: st.frame, at: st.at, out: chunks, undated: undated, tag: Ble.tag(name)) {
        Ble.log("boot \(st.boot) was counted before: the mirror's files and 'T' records of it dropped")
        DeviceLog.app(Ble.tag(name), "boot_reused", "开机号 \(st.boot) 以前用过：手机上这个开机号的旧 TIME 记录和文件状态作废，卡上的文件从头再取",
                      level: "warn", fields: ["boot": st.boot, "frame": st.frame])
      }
      boot = st.boot
      open = -1; liveFloor = -1; ahead = -1; reading = nil; needList = true; listFull = false; liveRead = true; stalls = [:]
      holeAt = -1; refollow = false; abandoned = -1; liveGaps = 0; settleUntil = Date(timeIntervalSinceNow: 3)
      caught = 0; caughtSince = nil
      state = "正在接上实时"
      var live0 = false  // LIVE taken since the file it goes on in was caught up
      var lastStatus = Date(), lastPieces = Date.distantPast, lastCharge = Date()
      // One loop for the link: LIVE as it comes, and one READ at a time alongside it for what the mirror lacks
      // (nextRead). LIVE waits only while the file it goes on in is READ (at connect, after a hole, at a new file):
      // a backlog of days no longer holds back what is said now
      while alive(p) {
        // a pool per pass: this thread lives as long as the link, and what Foundation autoreleases here (each pieces()'s
        // read of the live file) would stay until it ends: 3.4 GB and killed by iOS within hours on 10-06
        try autoreleasepool {
          if refollow {  // a hole in the live file no card can fill: LIVE goes on in a new file, dated at once
            refollow = false
            try follow(p)
            synced = try sync(p)
          }
          var took = false
          if !liveRead {
            var pk = live.poll(reading == nil ? 1 : 0)  // nothing to READ: wait for LIVE here
            guard alive(p) else { return }
            while let x = pk {
              took = true
              if try takeLive(p, m, x) { break }
              pk = live.poll(0)
            }
          }
          if reading != nil { took = try takeData(p, m) || took }
          if !live0 && !liveRead {  // the live file caught up: LIVE from here on
            live0 = true
            readLog(p)
            DeviceLog.connected(Ble.tag(name))
            waiting = false
            state = "已连接 · 实时上传"
          }
          if reading == nil { try nextRead(p, m) }
          if -lastPieces.timeIntervalSinceNow >= 0.5 { try pieces(m); lastPieces = Date() }
          if Recorder.now() - synced > Ble.timeEveryMs { try endRead(p, m); synced = try sync(p) }
          var changed = false  // plugged or unplugged, the card lent or back (the USB disk switch follows the board)
          // not while a READ is in flight: the backlog's DATA keeps the link; the minute's status reads it then
          if reading == nil && -lastCharge.timeIntervalSinceNow >= Ble.chargeEvery {
            lastCharge = Date()
            let t = try read(p, Ble.store, Ble.time)
            changed = Ble.charge(t) != hw["charge"] as? String || Ble.store(t) != hw["store"] as? Int
          }
          if changed || -lastStatus.timeIntervalSinceNow > 60 {
            _ = try status(p, name)
            if live0 {
              try endRead(p, m)
              readLog(p)
              try clean(p, m)
            }
            try unhold(m)
            lastStatus = Date()
            Ble.log("status: battery \(hw["battery"] ?? "-")% card \(hw["store"] ?? "-") free \(hw["free_kb"] ?? "-") KB")
          }
          if let job = jobs.poll() { try endRead(p, m); try job(p) }
          if !took && reading != nil { Thread.sleep(forTimeInterval: 0.01) }  // a READ's DATA on its way
        }
      }
    } catch is Gone {
    } catch {
      Ble.log("session: \(error)")
      if alive(p) {
        if !state.hasPrefix("配对没有完成") { state = "录音设备出错：\(error)" }
        q.async { self.central?.cancelPeripheralConnection(p.p) }
      }
    }
  }

  /// The phone's clock into the stream: the board puts a 'T' record (this frame was at this time) where it is.
  private func sync(_ p: Conn) throws -> Int64 {
    let now = Recorder.now()
    try write(p, Ble.time, Store.le(UInt64(now)))
    return now
  }

  private struct Status { let boot: Int, frame: Int64, store: Int, at: Int64 }  // at: the phone's clock as it was read

  /// The board's state, kept for the app's page and sent to the server under the board's name. Its model and revision
  /// are read again on each connection (`fresh`): a board flashed over USB meanwhile came back on another one, and the
  /// page offered it the update it already ran.
  private func status(_ p: Conn, _ name: String, fresh: Bool = false) throws -> Status {
    let t = try read(p, Ble.store, Ble.time)
    guard t.count >= 16 else { throw Fail(description: "状态只有 \(t.count) 字节") }
    let s = Status(boot: Int(Store.le(t, 0, 2)), frame: Ble.u32(t, 2), store: Ble.store(t), at: Recorder.now())
    noCard = s.store < 0 && Ble.u32(t, 12) == 0
    let left = syncTotalKb - syncDoneKb, rate = caughtSince.map { Double(caught) / max(1, -$0.timeIntervalSinceNow) } ?? 0
    let dbm = try rssi(p)
    var h: [String: Any] = ["name": name, "store": s.store, "free_kb": Ble.u32(t, 8), "total_kb": Ble.u32(t, 12),
                            // a backlog read in the background: what is left, and when it is done at this pace
                            "backlog_kb": left, "backlog_eta_s": rate > 0 && left > 0 ? Int(Double(left * 1024) / rate) : NSNull(),
                            // optional: macOS cannot publish 180F, so the Mac's fake recorder (app/ios/e2e) has none
                            "battery": (try? read(p, Ble.bas, Ble.battery))?.first.map { Int($0) } ?? NSNull(),
                            "live_gaps": liveGaps,  // holes in LIVE the card could not fill this session (follow())
                            "rssi": dbm ?? NSNull(),
                            "charge": Ble.charge(t) ?? NSNull()]
    if t.count >= 47 {
      let left = Int64(Int32(truncatingIfNeeded: Store.le(t, 43, 4)))
      pausedUntil = left > 0 ? Recorder.now() + left * 1000 : 0
    }
    for (k, c) in [("model", Ble.model), ("fw", Ble.fwRev)] {
      h[k] = (fresh ? nil : hw[k] as? String) ?? (try? read(p, Ble.dis, c)).map { String(decoding: $0, as: UTF8.self) } ?? NSNull()
    }
    hw = h
    if let a = usbAsk {
      usbNote = Ble.usbNote(on: a.on, err: a.err, store: s.store)
      if a.err != nil || (s.store == -16) == a.on { usbAsk = nil }
    }
    prefs.set(h["model"] as? String, forKey: "bleModel")  // for the page while not connected
    prefs.set(h["fw"] as? String, forKey: "bleFw")
    DispatchQueue.main.async { Dfu.back(h["fw"] as? String) }  // the first status after an update, for its log
    report(Ble.tag(name), h.filter { $0.key != "name" })
    return s
  }

  /// The link's signal in dBm (a timeout drop from distance, the case's effect); nil: iOS had none.
  private func rssi(_ c: Conn) throws -> Int? {
    do { return Int(Int8(bitPattern: try op(c, "rssi") { c.p.readRSSI() }.first ?? 0)) } catch is Gone { throw Gone() } catch { return nil }
  }

  /// Pause the recording for `s` seconds (0: go on now); the recorder keeps the time itself.
  func pause(_ s: Int) throws {
    guard peripheral?.state == .connected else { throw Fail(description: "录音设备没连上") }
    jobs.put { [self] p in
      try write(p, Ble.ctrl, [0x20] + Store.le(UInt32(s)))  // PAUSE
      pausedUntil = s > 0 ? Recorder.now() + Int64(s) * 1000 : 0
      DeviceLog.app(tag ?? "", s > 0 ? "pause" : "resume", s > 0 ? "暂停录音 \(s % 60 == 0 ? "\(s / 60) 分钟" : "\(s) 秒")" : "恢复录音")
      Ble.log("pause \(s) s")
    }
  }

  /// Listening on this phone, or not. On, the session puts the phone's clock into the stream now: the lines go by the
  /// stream's 'T' records, which come only on connect and every 10 minutes otherwise.
  func listen(_ on: Bool) throws {
    if !on {
      let l = listening
      listening = nil
      l?.stop()
      return
    }
    if listening != nil { return }
    listening = try Listen()
    jobs.put { [self] p in _ = try sync(p) }
  }

  /// Unbinding: the recorder forgets every phone, this one too (FORGET all), and the link goes. False: not connected.
  /// This iPhone's own keys stay (no API): the person forgets the recorder in Settings › Bluetooth.
  func forgetAll() -> Bool {
    guard peripheral?.state == .connected else { return false }
    let done = DispatchSemaphore(value: 0)
    var ok = false
    jobs.put { [self] p in
      defer { done.signal() }
      ok = (try? write(p, Ble.ctrl, [5, 1])) != nil
    }
    return done.wait(timeout: .now() + 20) == .success && ok
  }

  /// The board's SD card as a USB disk on the computer it is plugged into (CTRL 0x06), or back.
  func usbDisk(_ on: Bool) {
    guard peripheral?.state == .connected else { usbNote = "录音设备没连上"; return }
    usbNote = on ? "正在切换到 U 盘模式" : "正在切回录音"
    jobs.put { [self] p in
      let err = try reply(p, [6, on ? 1 : 0], 0x86).map { Int(Int8(bitPattern: $0[5])) }
      Ble.log("usb disk \(on): \(err.map { "\($0)" } ?? "no answer")")
      usbAsk = (on, err)
      _ = try status(p, prefs.string(forKey: "bleName") ?? p.p.identifier.uuidString)  // the note and the switch from what it reports
    }
  }

  /// A CTRL command and its reply (op), other replies passed over; nil: none in 15 s.
  private func reply(_ p: Conn, _ cmd: [UInt8], _ op: UInt8) throws -> [UInt8]? {
    ctrl.clear { $0.first == op }
    try write(p, Ble.ctrl, cmd)
    let until = Date(timeIntervalSinceNow: 15)
    while Date() < until && alive(p) {
      if let r = ctrl.poll(1), r.first == op { return r }
    }
    return nil
  }

  //
  // What the mirror lacks, READ alongside LIVE. At most one READ at a time; the board ends one at any other
  // command or TIME write, so the session ends it first (endRead) and the next READ goes on from what the mirror has.
  // Order: the file LIVE goes on in (and the one before it, after a new file) while LIVE waits; then the rest, oldest
  // first: what the board recorded while the phone was away, files of earlier boots, a lost notify
  //
  private struct Reading { let f: Int64, from: Int64; var last: Date }
  private var reading: Reading?
  private var open: Int64 = -1      // the file the stream goes on in
  private var liveFloor: Int64 = -1     // LIVE waits for the files from here on (liveRead); older ones are the backlog
  private var ahead: Int64 = -1     // a backlog under way: pieces of the files from here on go up ahead of it
  private var liveRead = true       // the live file (and the one before it) first: LIVE goes on from where its READ ends
  private var needList = true
  private var listFull = false      // the last LIST was cut at Mirror.listMax: more files once some are deleted
  private var stalls: [Int64: Int] = [:]
  private var holeAt: Int64 = -1    // LIVE went on at this offset past a hole in the live file: its READ has to reach it
  private var refollow = false      // that READ did not: no card to fill it from (none in the board, or it failed)
  private var abandoned: Int64 = -1  // the live file given up for a new one (follow()): what LIVE still sends of it is dropped
  private var liveGaps = 0
  // no card (store < 0, none mounted this boot): nothing to LIST or READ; each READ kept the link on its fast interval
  // (2420, 10-07: 15 ms for an hour). A hole in LIVE is a new file at once (follow())
  private var noCard = false
  private var settleUntil = Date.distantPast  // after a subscribe: LIVE still sends the file before the new one, from mid-way
  private var caught: Int64 = 0     // a catch-up under way: bytes taken since
  private var caughtSince: Date?

  /// LIST: the card's files with their sizes into the mirror, and the file the stream goes on in.
  private func list(_ p: Conn, _ m: Mirror) throws {
    var r: [Int64: Int64] = [:]
    ctrl.clear { $0.first == 0x81 || $0.first == 0x80 }
    try write(p, Ble.ctrl, [1])  // LIST
    var o: Int64 = -1
    for waited in 0... {
      guard alive(p) else { throw Gone() }
      guard let x = ctrl.poll(1) else { if waited >= 10 { throw Fail(description: "卡上文件列表超时") }; continue }
      if x.first == 0x81 && x.count >= 9 { r[Ble.u32(x, 1)] = Ble.u32(x, 5) }
      if x.first == 0x80 {
        if x.count > 1 && x[1] != 0 { Ble.log("LIST error \(Int8(bitPattern: x[1]))") }
        if x.count >= 6 { o = Ble.u32(x, 2) }
        break
      }
    }
    if o >= 0 { open = o }
    if liveFloor < 0 { liveFloor = open }
    m.listed(r, open: open)
    needList = false
    listFull = r.count >= Mirror.listMax
    Ble.log("LIST: \(r.count) files, open \(String(open, radix: 16)), missing \(m.missing().map { String($0.0, radix: 16) })")
  }

  /// Bytes on the card the mirror lacks: the live file's while LIVE waits for it, and the backlog (older files). A
  /// backlog makes the pieces of what is said now go up ahead of it (pieces(), the hold); the progress of both is the
  /// app's and the server's (status()).
  private func behind(_ m: Mirror) -> Int64 {
    var backlog: Int64 = 0, live: Int64 = 0
    for (f, from) in m.missing() {
      let n = max(0, m.files[f]!.size - from)
      if f < liveFloor { backlog += n } else if liveRead { live += n }
    }
    if backlog > 0 && ahead < 0 { ahead = liveFloor }
    if backlog + live > 0 && caughtSince == nil { caughtSince = Date() }
    syncDoneKb = caught / 1024
    syncTotalKb = backlog + live > 0 ? (caught + backlog + live) / 1024 : 0
    syncLiveKb = live / 1024
    return backlog + live
  }

  /// The next READ, or LIST first; none: caught up.
  private func nextRead(_ p: Conn, _ m: Mirror) throws {
    if noCard {  // what a LIST would say, without asking: no files, the stream goes on in the live one
      if needList { m.listed([:], open: open); needList = false }
      liveRead = false
      return
    }
    if needList { try list(p, m) }
    let missing = m.missing()
    if liveRead && open < 0 { liveRead = false }  // a LIST without the open file: nothing to wait for
    if liveRead {
      // the file before the live one, its end (LIVE may have missed it), then the live one: once, up to the stream's
      // end as the READ starts; LIVE (subscribed first) has every record from there. Reading on never ended while
      // recording: each READ found what the last one took time for
      if let (f, _) = missing.first(where: { $0.0 >= liveFloor && $0.0 != open }) { return try startRead(p, m, f) }
      return try startRead(p, m, open)
    }
    if let (f, _) = missing.first(where: { $0.0 != open }) { return try startRead(p, m, f) }
    if let t0 = caughtSince, behind(m) == 0 {  // caught up
      Ble.log("caught up: \(caught) B in \(Int(-t0.timeIntervalSinceNow)) s")
      syncNote = "\(Ble.hhmm()) 补传录音设备的录音 \(caught / 1024) KB"
      if caught > 0 { DeviceLog.app(tag ?? "", "sync", "补传 \(caught / 1024) KB，用时 \(Int(-t0.timeIntervalSinceNow)) s") }
      caught = 0; caughtSince = nil; ahead = -1
      try unhold(m)
    }
  }

  private func startRead(_ p: Conn, _ m: Mirror, _ f: Int64) throws {
    data.clear()
    ctrl.clear { $0.first == 0x82 }
    let from = m.have(f)
    try write(p, Ble.ctrl, [2] + Store.le(UInt32(f)) + Store.le(UInt32(from)))  // READ
    reading = Reading(f: f, from: from, last: Date())
  }

  /// The READ's DATA so far into the mirror; true when there was any. It ends at its 0x82 (the DATA came before it),
  /// at a lost notify (a hole: STOP, and the rest of it is not taken), or after 10 s without DATA.
  private func takeData(_ p: Conn, _ m: Mirror) throws -> Bool {
    guard var r = reading else { return false }
    var took = false
    while let d = data.poll(0) {
      took = true
      r.last = Date()
      guard d.count > 4 else { continue }
      let had = m.have(r.f)
      if let hole = try autoreleasepool(invoking: { try m.put(r.f, Ble.u32(d, 0), Data(d[4...])) }) {
        Ble.log("DATA hole in \(String(r.f, radix: 16)) at \(hole) (got \(Ble.u32(d, 0))): STOP")
        try endRead(p, m, keep: false)
        return true
      }
      caught += m.have(r.f) - had
      if m.have(r.f) % 32768 < d.count { _ = behind(m) }
    }
    reading = r
    if ctrl.contains(where: { $0.first == 0x82 }) && data.isEmpty || -r.last.timeIntervalSinceNow > 10 { finishRead(m, stopped: false) }
    return took
  }

  /// The READ in flight ended before anything else goes on CTRL or TIME; what it sent so far kept (keep: false after
  /// a hole: what comes after it is not the mirror's yet).
  private func endRead(_ p: Conn, _ m: Mirror, keep: Bool = true) throws {
    guard let r = reading else { return }
    try write(p, Ble.ctrl, [3])  // STOP
    let until = Date(timeIntervalSinceNow: 3)
    while !ctrl.contains(where: { $0.first == 0x82 }) && Date() < until && alive(p) { Thread.sleep(forTimeInterval: 0.05) }
    // no end: its DATA may still come and be taken for what goes next on DATA (LOG): the link again instead
    guard ctrl.contains(where: { $0.first == 0x82 }) else { throw Fail(description: "READ 停不下来") }
    while keep, let d = data.poll(0), d.count > 4 {
      let had = m.have(r.f)
      if try m.put(r.f, Ble.u32(d, 0), Data(d[4...])) != nil { break }
      caught += m.have(r.f) - had
    }
    data.clear()
    finishRead(m, stopped: true)
  }

  private func finishRead(_ m: Mirror, stopped: Bool) {
    guard let r = reading else { return }
    reading = nil
    ctrl.clear { $0.first == 0x82 }
    let got = m.have(r.f) - r.from
    if r.f == open && !stopped {
      // a hole the card did not fill: without a card nothing ever will, and every LIVE packet after it found the hole
      // again and was dropped (2420 with no card, 10-07: an hour of LIVE, no piece)
      if holeAt > m.have(r.f) { refollow = true }
      holeAt = -1
      liveRead = false  // LIVE goes on from where this READ ended
    } else if got > 0 || stopped {
      stalls[r.f] = nil
    } else if (stalls[r.f] ?? 0) >= 3 {
      m.noMore(r.f)  // the card has no more of it
    } else {
      stalls[r.f, default: 0] += 1
    }
    _ = behind(m)
  }

  /// One LIVE packet into the mirror; true when LIVE has to wait for a READ: a new file (LIST says how the last one
  /// ended), a hole in the live file (a lost notify).
  private func takeLive(_ p: Conn, _ m: Mirror, _ pk: [UInt8]) throws -> Bool {
    guard pk.count > 8 else { return false }
    let f = Ble.u32(pk, 0)
    if f <= abandoned { return false }
    if f > open {
      liveFloor = open; open = f; needList = true; liveRead = true
      try endRead(p, m)
    }
    if let hole = try m.put(f, Ble.u32(pk, 4), Data(pk[8...])), f == open {
      if noCard {
        if Date() > settleUntil { refollow = true }
        return refollow
      }
      Ble.log("LIVE hole in \(String(f, radix: 16)) at \(hole): READ")
      liveFloor = open; liveRead = true; holeAt = Ble.u32(pk, 4)
      try endRead(p, m)
    }
    return liveRead
  }

  /// LIVE from a new file: the board starts one for a phone that subscribes, so LIVE has it from offset 0 again.
  /// What the old one lacks is lost (the board had no card for it); the caller dates the new one (boot 0 dates each
  /// file by its own 'T').
  private func follow(_ p: Conn) throws {
    abandoned = open
    liveGaps += 1
    settleUntil = Date(timeIntervalSinceNow: 3)
    Ble.log("LIVE hole in \(String(open, radix: 16)) the card cannot fill (card \(hw["store"] ?? "-")): a new file")
    if liveGaps == 1, let tag { DeviceLog.app(tag, "live_gap", "实时音频断了一小段，录音设备上没有卡可以补，从新文件接着收", level: "warn") }
    let ch = try chr(p, Ble.store, Ble.live)
    _ = try op(p, "unsubscribe live") { p.p.setNotifyValue(false, for: ch) }
    try subscribe(p, Ble.live)
  }

  /// Where the server's finalization waits while a backlog goes up after what is said now (`hold:<tag>`, sent with
  /// every upload): a minute before the first piece that goes ahead of it (its start, as pieces() will put it,
  /// may still move by the clock's drift). Set before that piece exists, so no upload has it without the hold.
  private func holdAhead(_ m: Mirror) {
    guard ahead >= 0, let tag, Recorder.hold(tag) == nil, let at = m.nextStart(from: ahead) else { return }
    prefs.set(at - Self.holdMarginMs, forKey: "hold:\(tag)")
    Ble.log("hold \(tag) at \(at - Self.holdMarginMs): the backlog goes up after what is said now")
  }

  private static let holdMarginMs: Int64 = 60_000  // the hold before where LIVE began: the clock's drift

  /// No backlog left, and none of its pieces waiting to go up: the server may finalize past the hold. The last READ's
  /// bytes are cut first, and the backlog runs up to where LIVE began, the hold plus its margin (Mirror.up).
  private func unhold(_ m: Mirror) throws {
    guard let tag, let h = Recorder.hold(tag), behind(m) == 0 else { return }
    try pieces(m)
    guard m.up(chunks, before: h + Self.holdMarginMs) else { return }
    prefs.removeObject(forKey: "hold:\(tag)")
    Ble.log("hold \(tag) let go")
  }

  private var boot = 0  // the session's

  /// The recorder's event log since the last read (DeviceLog), with nothing else in flight on the link. A log that
  /// started over (erased: next below where we were) is read from 0.
  private func readLog(_ p: Conn) {
    guard let tag else { return }
    do {
      var from = DeviceLog.from(tag)
      for _ in 0..<2 {
        data.clear()
        guard let r = try reply(p, [7] + Store.le(UInt32(from)), 0x87), r.count >= 5 else { throw Fail(description: "LOG 没有回应") }  // LOG
        let next = Ble.u32(r, 1)
        if next < from { Ble.log("devlog \(tag): log restarted at \(next) (had \(from))"); from = 0; continue }
        var packets: [[UInt8]] = []
        while let d = data.poll() { packets.append(d) }  // its DATA came before the reply
        DeviceLog.took(tag, packets, next: next)
        Ble.log("devlog \(tag): \(packets.count) packets from \(from), next \(next)")
        return
      }
    } catch { Ble.log("devlog \(tag) read: \(error)") }
  }

  /// The mirror into dated pieces (Mirror.pieces): the live file in pieces as long as the app's slice.
  private func pieces(_ m: Mirror) throws {
    holdAhead(m)
    files += try m.pieces(chunks, undated, tag: tag ?? "recorder", liveFrames: sliceS() * 100, boot: boot)
  }

  /// Files mirrored whole and uploaded: off the card (DELETE) and out of the mirror.
  private func clean(_ p: Conn, _ m: Mirror) throws {
    if noCard { return }
    let done = m.done(chunks)
    if !done.isEmpty && listFull { needList = true }  // room in the next LIST for files it left out
    for f in done {
      guard let r = try reply(p, [4] + Store.le(UInt32(f)), 0x84), r.count >= 6 else { return }  // DELETE
      let err = Int8(bitPattern: r[5])
      Ble.log("DELETE \(String(f, radix: 16)): \(err)")
      if err == 0 || err == -2 { m.drop(f) }  // gone (-2: not on this card)
    }
  }
}
