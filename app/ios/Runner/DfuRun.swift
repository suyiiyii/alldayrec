// The update with Nordic's DFU library (NordicDFU, a Swift package), as Dfu.kt does it on Android: the recorder reboots
// into its Adafruit bootloader (legacy DFU) and the package goes in 20-byte packets, a receipt every 8 (lr_dfu.py).
// Where iOS differs:
//  - the app sends the recorder into the bootloader itself, with the control point's 0x06 as lr_dfu.py does. The
//    library's way (Start DFU, 0x01 0x04) gets a one-byte notification back from our firmware (transport.c), which the
//    iOS library takes for a broken response and stops on; Android's ignores it
//  - no Bluetooth address: the bootloader (AdaDFU on the recorder's address + 1, unbonded) is the first device that
//    advertises the legacy DFU service, as the library's own selector picks it (Android looks for address + 1)
//  - the library connects with a CBCentralManager of its own; BleLink's is gone meanwhile (Recorder.withoutLink)
import CoreBluetooth
import CryptoKit
import NordicDFU

extension Dfu {
  private static var run: DfuRun?  // the library holds its delegates weakly

  static func sha256(_ d: Data) -> String { SHA256.hash(data: d).map { String(format: "%02x", $0) }.joined() }

  /// Updates the recorder with peripheral identifier `id`; throws when `zip` is no DFU package.
  static func start(_ id: UUID, _ zip: Data) throws {
    let fw = try DFUFirmware(zipFile: zip)
    state = ["code": "dfuPreparing"]
    busy = true
    NSLog("live-record dfu %d B to %@", zip.count, id.uuidString)
    run = DfuRun(id, fw)
  }

  fileprivate static func end(_ s: [String: Any]) {
    state = s
    busy = false
    run = nil
    Recorder.relink()
  }

  /// `why`: the app's own reason (a code the page words, DfuRun.said its device log text), nil: the library's `text`.
  fileprivate static func failed(_ why: String?, _ text: String, detail: String? = nil, code: Int? = nil) {
    log?.fail(code, text)
    end(["code": "dfuFailed", "why": why ?? NSNull(), "detail": detail ?? NSNull()])
  }
}

/// One update, on the main thread: connect to the recorder (iOS encrypts with the bond: the control point takes only a
/// bonded phone), write 0x06, and once it has dropped the link, find the bootloader and hand it to the library.
private final class DfuRun: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate, DFUServiceDelegate, DFUProgressDelegate, LoggerDelegate {
  static let service = CBUUID(string: "00001530-1212-EFDE-1523-785FEABCD123"), cp = CBUUID(string: "00001531-1212-EFDE-1523-785FEABCD123")
  private let id: UUID, fw: DFUFirmware
  private var central: CBCentralManager?
  private var recorder: CBPeripheral?
  private var wrote = false
  private var deadline: DispatchWorkItem?
  private var controller: DFUServiceController?

  init(_ id: UUID, _ fw: DFUFirmware) {
    self.id = id
    self.fw = fw
    super.init()
    central = CBCentralManager(delegate: self, queue: nil)
  }

  /// The device log's words for the app's own reasons (DfuLog); the page words the codes.
  static let said = ["dfuNoBluetooth": "蓝牙不可用", "dfuUnknown": "这台 iPhone 不认识录音设备", "dfuNoConnect": "连不上录音设备",
                     "dfuNoService": "录音设备上没有升级服务", "dfuNoReboot": "录音设备没有重启", "dfuRefused": "录音设备拒绝进入升级模式",
                     "dfuDropped": "录音设备断开了", "dfuNoBootloader": "录音设备没有进入升级模式"]

  /// Fails with `why` unless the next step comes within `s` seconds (a connect request on iOS never times out).
  private func within(_ s: Double, _ why: String) {
    deadline?.cancel()
    let w = DispatchWorkItem { [weak self] in self?.fail(why) }
    deadline = w
    DispatchQueue.main.asyncAfter(deadline: .now() + s, execute: w)
  }

  private func fail(_ why: String, _ detail: String? = nil) {
    guard controller == nil else { return }  // the library's now: it reports its own end
    NSLog("live-record dfu: %@ %@", why, detail ?? "")
    deadline?.cancel()
    if let c = central {
      c.delegate = nil
      if c.state == .poweredOn { c.stopScan(); if let p = recorder { c.cancelPeripheralConnection(p) } }
    }
    Dfu.failed(why, DfuRun.said[why]! + (detail.map { "（\($0)）" } ?? ""), detail: detail)
  }

  func centralManagerDidUpdateState(_ c: CBCentralManager) {
    guard c.state == .poweredOn else { if c.state != .unknown { fail("dfuNoBluetooth") }; return }
    guard let p = c.retrievePeripherals(withIdentifiers: [id]).first else { return fail("dfuUnknown") }
    recorder = p
    p.delegate = self
    Dfu.state = ["code": "connecting"]
    c.connect(p)
    within(20, "dfuNoConnect")
  }

  func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) { fail("dfuNoConnect") }

  func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) {
    Dfu.state = ["code": "dfuRebooting"]
    p.discoverServices([DfuRun.service])
  }

  func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
    guard let s = p.services?.first(where: { $0.uuid == DfuRun.service }) else { return fail("dfuNoService") }
    p.discoverCharacteristics([DfuRun.cp], for: s)
  }

  func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor s: CBService, error: Error?) {
    guard let ch = s.characteristics?.first(where: { $0.uuid == DfuRun.cp }) else { return fail("dfuNoService") }
    wrote = true
    p.writeValue(Data([0x06]), for: ch, type: .withResponse)  // it resets before it answers
    within(15, "dfuNoReboot")  // iOS sees the link gone at its supervision timeout (up to 6 s)
  }

  func peripheral(_ p: CBPeripheral, didWriteValueFor ch: CBCharacteristic, error: Error?) {
    NSLog("live-record dfu: 0x06 written: %@", error.map { "\($0)" } ?? "ok")
    if let e = error as? CBATTError { fail("dfuRefused", e.localizedDescription) }  // it answered: no reset
  }

  func centralManager(_ c: CBCentralManager, didDisconnectPeripheral p: CBPeripheral, error: Error?) {
    guard wrote else { return fail("dfuDropped") }
    c.scanForPeripherals(withServices: [DfuRun.service])
    within(30, "dfuNoBootloader")
  }

  func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral, advertisementData a: [String: Any], rssi: NSNumber) {
    let name = a[CBAdvertisementDataLocalNameKey] as? String ?? p.name ?? ""
    if name.hasPrefix("LR-") { return }  // a recorder on its firmware (lr_dfu.py)
    NSLog("live-record dfu: bootloader %@ %@ rssi %@", name, p.identifier.uuidString, rssi)
    deadline?.cancel()
    c.stopScan()
    c.delegate = nil  // the library connects with its own central
    let i = DFUServiceInitiator(queue: DispatchQueue(label: "dfu"))  // Bluetooth on its own queue, callbacks on main
    i.delegate = self
    i.progressDelegate = self
    i.logger = self
    i.packetReceiptNotificationParameter = 8  // as Dfu.kt and lr_dfu.py: on 16 the XIAO's bootloader stalled mid-image
    controller = i.with(firmware: fw).start(target: p)
  }

  func dfuStateDidChange(to s: DFUState) {
    switch s {
    case .starting: Dfu.state = ["code": "dfuStarting"]; Dfu.log?.reached("boot")
    case .uploading: Dfu.log?.reached("send")
    case .completed:  // no .aborted: nothing here aborts (Android's notification can)
      Dfu.log?.done()
      if let to = Dfu.log?.to { Recorder.defaults.set(to, forKey: "bleFw") }  // what the page shows until the link reads it
      Dfu.end(["code": "dfuDone"])
    default: break
    }
  }

  func dfuError(_ e: DFUError, didOccurWithMessage m: String) {
    NSLog("live-record dfu error %d: %@", e.rawValue, m)
    Dfu.failed(nil, m, detail: m, code: e.rawValue)
  }

  func dfuProgressDidChange(for part: Int, outOf totalParts: Int, to progress: Int, currentSpeedBytesPerSecond: Double, avgSpeedBytesPerSecond: Double) {
    Dfu.state = ["code": "dfuProgress", "pct": progress, "kbps": (avgSpeedBytesPerSecond / 100).rounded() / 10]  // KB/s, Android's B/ms
    Dfu.log?.progress(progress, kbps: avgSpeedBytesPerSecond / 1000)
  }

  func logWith(_ level: LogLevel, message: String) {
    if level.rawValue >= LogLevel.application.rawValue { NSLog("live-record dfu %@ %@", level.name(), message) }
  }
}
