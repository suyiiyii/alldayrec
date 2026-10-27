// The Feishu recorder bean the person added (prefs bleName, D3200-XXXX) over CoreBluetooth: port of the Android app's
// BeanLink.kt. Found by scanning for its service and its name from the MAC in its manufacturer data (its address
// changes; when iOS gives no manufacturer data, by the peripheral identifier it last had), connected without pairing
// (the bean asks for none), its notifications into BeanSync (Bean.swift), which does the rest. One thread: scan,
// connect, session, then scan again as soon as the link goes. Where iOS differs from Android:
//  - no scan modes (Android's fast discovery after a rollover, #185): a scan for the service, which iOS also runs in the
//    background and relaunches the app for (state restoration, its own identifier); the same timing logs (bean_scan)
//  - no MTU request: iOS negotiates it; the connect line says what it got
//  - no debug capture of sessions (Android's bean-capture over adb)
import CoreBluetooth
import Foundation

final class BeanLink: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate, BeanIo {
  static let restoreId = "live-record.bean"
  static let service = CBUUID(string: Bean.service), write = CBUUID(string: Bean.write), notify = CBUUID(string: Bean.notify)

  let name: String
  @Shared var files = 0  // pieces made
  @Shared private var linkState: [String: Any]
  @Shared private var inSession = false
  private let prefs = Recorder.defaults
  private let sliceS: () -> Int
  private let onReport: (String, [String: Any]) -> Void
  private let q = DispatchQueue(label: "bean")  // CoreBluetooth's: its callbacks, and every call into it
  private var central: CBCentralManager?
  private var restored: [CBPeripheral] = []  // on q: what iOS kept for the app when it relaunched it
  private var advFlags = -2, advConnectable: Bool?  // on q: what the bean advertised last, logged when it changes
  @Shared private var on = true
  @Shared private var radio: CBManagerState = .unknown  // as the central last said
  private var poweredOn: Bool { radio == .poweredOn }
  @Shared private var peripheral: CBPeripheral?
  @Shared private var cmd: CBCharacteristic?
  @Shared private var mtu = 23
  @Shared private var rssi: Int?
  @Shared private var lastSeen: Int64?
  @Shared private var downWhy: String?
  @Shared private var established = false  // the link came up this round: a drop is not a failure to back off from
  @Shared private var natural = false  // the last session ended at the bean's natural file rollover (the scan log's reason)
  @Shared private var phase: String?   // the scan that found the bean, for the connect steps' log
  private let notes = Inbox<[UInt8]?>()  // nil: the link is gone
  private let found = Inbox<CBPeripheral>()
  private var sync: BeanSync!

  /// What the link does, as a code the app words (recorder_status.dart): the session's while one runs.
  var state: [String: Any] { inSession ? sync.state : linkState }
  /// The session is up (its key exchange and first reads done): recording or not.
  var connected: Bool { inSession && ["beanIdle", "joining", "connected"].contains(sync.state["code"] as? String ?? "") }
  var hw: [String: Any] { sync.hw.merging(["name": name]) { $1 } }
  var syncDoneKb: Int64 { sync.syncDoneKb }
  var syncTotalKb: Int64 { sync.syncTotalKb }

  init(chunks: URL, state: URL, sliceS: @escaping () -> Int, report: @escaping (String, [String: Any]) -> Void) {
    name = Recorder.defaults.string(forKey: "bleName") ?? ""
    linkState = ["code": "scanning", "name": name]
    self.sliceS = sliceS; onReport = report
    super.init()
    sync = BeanSync(tag: name, stateFile: state.appendingPathComponent("\(name).json"), chunks: chunks, io: self)
  }

  func start() {
    q.sync { central = CBCentralManager(delegate: self, queue: q, options: [CBCentralManagerOptionRestoreIdentifierKey: BeanLink.restoreId]) }
    Thread.detachNewThread { self.loop() }
  }

  func stop() {
    on = false
    notes.put(nil)
    q.sync {
      abort()
      if central?.state == .poweredOn {
        central?.stopScan()
        if let p = peripheral { central?.cancelPeripheralConnection(p) }
      }
      central?.delegate = nil
      central = nil
    }
  }

  //
  // The thread: scan, connect, session, again
  //

  private func loop() {
    while on {
      guard Bean.isBean(name), prefs.string(forKey: "bleName") == name else { linkState = ["code": "unbound"]; return }
      switch radio {
      case .poweredOn: break
      case .unknown, .resetting: Thread.sleep(forTimeInterval: 0.2); continue  // the central's first word still to come
      case .unauthorized: linkState = ["code": "noPermission"]; Thread.sleep(forTimeInterval: 5); continue
      default:
        natural = false  // a rollover allowance is used up even while Bluetooth is off (as on Android)
        linkState = ["code": "bluetoothOff"]
        Thread.sleep(forTimeInterval: 5)
        continue
      }
      established = false
      do {
        if let p = find() { try connect(p) }
      } catch let e as BeanSync.Again {  // straight back
        natural = e.naturalRollover
        scanTiming(["event": "session_again", "reason": natural ? "natural_rollover" : "other_again"])
      } catch is BeanSync.Gone {
        if !established && on { Thread.sleep(forTimeInterval: 5) }  // it never came up: results wait out 5 s
      } catch {
        Ble.log("bean: \(error)")
        if on { linkState = ["code": "linkError", "detail": "\(error)"]; Thread.sleep(forTimeInterval: 5) }
      }
    }
  }

  private func mono() -> Int64 { Int64(ProcessInfo.processInfo.systemUptime * 1000) }

  /// The bean, advertising and connectable: a scan for its service (iOS runs it in the background too) for up to 10
  /// minutes, then a new one. nil: none (time up, Bluetooth off, stopped).
  private func find() -> CBPeripheral? {
    let reason = natural ? "natural_rollover" : "ordinary"
    natural = false
    let id = UUID().uuidString, t0 = mono(), budget: Int64 = 600_000
    linkState = lastSeen.map { ["code": "away", "lastSeen": $0] } ?? ["code": "scanning", "name": name]
    found.clear()
    q.sync { central?.scanForPeripherals(withServices: [BeanLink.service], options: [CBCentralManagerScanOptionAllowDuplicatesKey: true]) }
    scanTiming(["phase": id, "event": "registration_returned", "reason": reason])
    var p: CBPeripheral?
    while p == nil && on && poweredOn && mono() - t0 < budget { p = found.poll(1) }
    q.sync { if central?.state == .poweredOn { central?.stopScan() } }
    let outcome = p != nil ? "found" : !on ? "cancelled" : !poweredOn ? "unavailable" : "timeout"
    if p != nil { phase = id }
    scanTiming(["phase": id, "reason": reason, "budgetMs": budget, "elapsedMs": mono() - t0, "outcome": outcome])
    return on && poweredOn ? p : nil
  }

  private func scanTiming(_ fields: [String: Any]) {
    let timed = fields.merging(["atElapsedMs": mono()]) { $1 }
    Ble.log("bean scan: \(timed)")
    DeviceLog.app(name, "bean_scan", "扫描阶段计时", fields: timed)
  }

  private func connect(_ p: CBPeripheral) throws {
    var steps = ""
    func step<T>(_ code: String, _ what: String, _ block: () throws -> T) rethrows -> T {
      linkState = ["code": code]
      let t0 = mono()
      defer {
        let elapsed = mono() - t0
        steps += (steps.isEmpty ? "" : "，") + "\(what) \(elapsed) ms"
        DeviceLog.app(name, "bean_connect_step", what, fields: ["step": what, "elapsedMs": elapsed, "phase": phase ?? NSNull()])
      }
      return try block()
    }
    notes.clear(); downWhy = nil
    peripheral = p
    defer {
      inSession = false
      q.sync { if central?.state == .poweredOn { central?.cancelPeripheralConnection(p) } }
      peripheral = nil; cmd = nil
      if established { lastSeen = Recorder.now(); DeviceLog.disconnected(name, downWhy) }
    }
    DeviceLog.app(name, "connect", "扫描发现豆子，开始连接", fields: ["path": "scan"])
    _ = try step("connecting", "连接") { try op("connect", 30) { p.delegate = self; self.central?.connect(p) } }
    established = true
    prefs.set(p.identifier.uuidString, forKey: "bleId")  // when a scan has no manufacturer data, this finds it
    let s = try step("services", "读取设备服务") { () throws -> CBService in
      _ = try op("services") { p.discoverServices([BeanLink.service]) }
      guard let s = q.sync(execute: { p.services?.first { $0.uuid == BeanLink.service } }) else { throw Bean.Fail("no bean service") }
      _ = try op("characteristics") { p.discoverCharacteristics([BeanLink.write, BeanLink.notify], for: s) }
      return s
    }
    let (w, n) = q.sync { (s.characteristics?.first { $0.uuid == BeanLink.write }, s.characteristics?.first { $0.uuid == BeanLink.notify }) }
    guard let w else { throw Bean.Fail("no write characteristic") }
    guard let n else { throw Bean.Fail("no notify characteristic") }
    cmd = w
    mtu = q.sync { p.maximumWriteValueLength(for: .withoutResponse) } + 3
    if mtu < 78 { throw Bean.Fail("MTU \(mtu): too small for the key exchange") }
    try step("services", "订阅通知") { if !q.sync(execute: { n.isNotifying }) { _ = try op("subscribe") { p.setNotifyValue(true, for: n) } } }
    if prefs.bool(forKey: "bindPending") || prefs.object(forKey: "beanSince:\(name)") == nil {  // added now: its files from here on are this phone's
      prefs.removeObject(forKey: "bindPending"); prefs.removeObject(forKey: "bindForce")
      prefs.set(Recorder.now(), forKey: "beanSince:\(name)")
    }
    inSession = true
    try sync.session(since: (prefs.object(forKey: "beanSince:\(name)") as? NSNumber)?.int64Value ?? 0, steps: "\(steps)（MTU \(mtu)）")
  }

  //
  // One GATT operation at a time (as BleLink): the session thread starts one on q and waits for its callback
  //

  private final class Op { let sem = DispatchSemaphore(value: 0); var result: Result<Data, Error> = .failure(BeanSync.Gone()) }
  @Shared private var pending: Op?

  private func op(_ what: String, _ timeout: TimeInterval = 15, _ start: @escaping () -> Void) throws -> Data {
    guard on else { throw BeanSync.Gone() }
    let o = Op()
    pending = o
    q.async(execute: start)
    guard o.sem.wait(timeout: .now() + timeout) == .success else {
      q.sync { if pending === o { pending = nil } }  // a late callback must not complete the next operation
      throw Bean.Fail("\(what): no answer in \(Int(timeout)) s")
    }
    return try o.result.get()
  }

  private func done(_ error: Error?, _ value: Data = Data()) {
    guard let o = pending else { return }
    pending = nil
    o.result = error.map { .failure($0) } ?? .success(value)
    o.sem.signal()
  }

  private func abort() { if let o = pending { pending = nil; o.result = .failure(BeanSync.Gone()); o.sem.signal() } }

  //
  // BeanSync's bytes
  //

  /// To the bean's command characteristic only (never the OTA service), and only what BeanSync lets through.
  func send(_ frame: [UInt8]) throws {
    guard Bean.allowed(frame, sync.deletable), frame.count <= mtu - 3 else { throw Bean.Fail("not sent: \(frame.count) B") }
    guard let p = peripheral, let ch = cmd else { throw BeanSync.Gone() }
    _ = try op("write") { p.writeValue(Data(frame), for: ch, type: .withResponse) }
  }

  func receive(_ ms: Int64) throws -> [UInt8]? {
    // Bluetooth off: no disconnect callback comes; unbound: this phone no longer connects to it
    guard on, poweredOn, prefs.string(forKey: "bleName") == name else { throw BeanSync.Gone() }
    guard let note = notes.poll(Double(ms) / 1000) else { return nil }
    guard let b = note else { notes.put(nil); throw BeanSync.Gone() }
    return b
  }

  func now() -> Int64 { Recorder.now() }
  func monotonicNow() -> Int64 { mono() }
  func liveUnits() -> Int { sliceS() * 50 }

  func log(_ kind: String, _ text: String, _ level: String?, _ fields: [String: Any], _ at: Int64?) {
    Ble.log("bean \(kind): \(text)")
    DeviceLog.app(name, kind, text, level: level, fields: kind == "bean_connect_step" ? fields.merging(["phase": phase ?? NSNull()]) { $1 } : fields, at: at)
  }

  func report(_ hw: [String: Any]) { onReport(name, hw.merging(["name": name, "rssi": rssi ?? NSNull()]) { $1 }) }
  func hold(_ at: Int64?) { if let at { prefs.set(at, forKey: "hold:\(name)") } else { prefs.removeObject(forKey: "hold:\(name)") } }
  func held() -> Int64? { Recorder.hold(name) }
  func end(_ at: Int64) { prefs.set(at, forKey: "end:\(name)") }  // Uploader.ends() sends it
  func piece() { files += 1 }
  func refused() -> Set<Int64> { Set((prefs.stringArray(forKey: "refused:\(name)") ?? []).compactMap { Int64($0) }) }

  //
  // CoreBluetooth, on q
  //

  func centralManagerDidUpdateState(_ c: CBCentralManager) {
    Ble.log("bean central state \(c.state.rawValue)")
    radio = c.state
    switch c.state {
    case .poweredOn:
      for p in restored where p.state != .disconnected { c.cancelPeripheralConnection(p) }  // a session starts afresh
      restored = []
    case .poweredOff, .resetting:  // the link goes without a callback: the session ends at its next receive
      abort()
      notes.put(nil)
    default: break
    }
  }

  /// The app relaunched in the background for the bean (a scan result or a connection iOS kept): what it kept.
  func centralManager(_ c: CBCentralManager, willRestoreState dict: [String: Any]) {
    restored = dict[CBCentralManagerRestoredStatePeripheralsKey] as? [CBPeripheral] ?? []
    let scanning = dict[CBCentralManagerRestoredStateScanServicesKey] != nil
    DeviceLog.app(name, "restore", "iOS 恢复蓝牙：" + (scanning ? "还在扫描录音豆" : "没有扫描") + "，\(restored.count) 个连接",
                  fields: ["scanning": scanning, "peripherals": restored.map { $0.state.rawValue }])
  }

  func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral, advertisementData a: [String: Any], rssi dbm: NSNumber) {
    guard on, prefs.string(forKey: "bleName") == name else { return }
    let mfr = (a[CBAdvertisementDataManufacturerDataKey] as? Data).map { [UInt8]($0) }
    let n = mfr.flatMap(Bean.name)
    guard n == name || n == nil && prefs.string(forKey: "bleId") == p.identifier.uuidString else { return }
    let connectable = a[CBAdvertisementDataIsConnectable] as? Bool ?? true
    if dbm.intValue != 127 { rssi = dbm.intValue }
    let flags = mfr.map(Bean.flags) ?? -1
    if flags != advFlags || connectable != advConnectable {
      advFlags = flags; advConnectable = connectable
      DeviceLog.app(name, "bean_adv", String(format: "豆子在广播：标志 0x%02X（%@），%@，信号 %d dBm", max(flags, 0),
                                             flags >= 0 && flags & 0x80 != 0 ? "已绑定飞书" : "未绑定飞书", connectable ? "可连接" : "不可连接", dbm.intValue),
                    fields: ["flags": flags, "connectable": connectable, "rssi": dbm.intValue])
    }
    if connectable { found.put(p) }
  }

  func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) { if p === peripheral { done(nil) } }

  func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
    if p === peripheral { done(error ?? Bean.Fail("connect failed")) }
  }

  func centralManager(_ c: CBCentralManager, didDisconnectPeripheral p: CBPeripheral, error: Error?) {
    Ble.log("bean disconnected: \(error.map { "\($0)" } ?? "-")")
    guard p === peripheral else { return }
    downWhy = error?.localizedDescription
    abort()
    notes.put(nil)
  }

  func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) { done(error) }
  func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor s: CBService, error: Error?) { done(error) }
  func peripheral(_ p: CBPeripheral, didWriteValueFor c: CBCharacteristic, error: Error?) { done(error) }
  func peripheral(_ p: CBPeripheral, didUpdateNotificationStateFor c: CBCharacteristic, error: Error?) { done(error) }
  func peripheral(_ p: CBPeripheral, didUpdateValueFor c: CBCharacteristic, error: Error?) {
    if c.uuid == BeanLink.notify, let v = c.value { notes.put([UInt8](v)) }
  }
}
