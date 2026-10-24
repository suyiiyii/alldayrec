// iOS counterpart of RecorderService.kt + Slicer: the microphone stays open under the `audio` background mode,
// the stream is cut into Ogg Opus pieces (a piece cut short by a kill still decodes) and uploaded in order.
// iOS has no START_STICKY: after a kill, the next launch uploads what was left and the owner restarts by hand.
// With the BLE recorder as the source (Ble.swift) the microphone stays off: the link makes the pieces, and it comes back
// by itself at launch, also when iOS relaunches the app in the background for the recorder.
import AVFoundation
import CoreBluetooth
import Flutter
import MetricKit
import UIKit

enum Recorder {
  static var slicer: Slicer?
  static var link: BleLink?  // the BLE recorder, when it is the source instead of the microphone
  static var nearby: Nearby?  // the add-recorder page's scan, while it is open
  static var probe: CBCentralManager?  // whether Bluetooth is on, for that page
  static var uploader: Uploader?
  static var interruptedAt: Int64?
  static var foregroundApp = true
  static let defaults = UserDefaults(suiteName: "recorder")!
  static let dir: URL = {
    let d = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("chunks")
    try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
    return d
  }()

  static func register(_ messenger: FlutterBinaryMessenger) {
    FlutterMethodChannel(name: "live_record/recorder", binaryMessenger: messenger).setMethodCallHandler { call, result in
      let a = call.arguments as? [String: Any] ?? [:]
      switch call.method {
      case "start": result(start(a).map { FlutterError(code: "start", message: $0, details: nil) })
      case "stop": stop(); result(nil)
      case "foreground":
        let on = a["on"] as? Bool ?? true
        if on != foregroundApp { DeviceLog.app(DeviceLog.mine(), on ? "foreground" : "background", on ? "App 回到前台" : "App 进了后台") }
        foregroundApp = on
        slicer?.foreground = foregroundApp
        if foregroundApp { slicer?.resume() }  // a recording the system stopped and did not give back (from the background iOS may refuse)
        result(nil)
      case "slices":  // new lengths from the server: the running slicer cuts its next piece with them, the link reads them
        slicer?.setSlices(fg: a["foreground"] as? Int ?? 1, bg: a["background"] as? Int ?? 20)
        for k in ["foreground", "background"] { defaults.set(a[k] as? Int ?? 20, forKey: k) }
        result(nil)
      case "status": result(status())
      case "version": result(DeviceLog.version)  // 我的 › 关于
      case "error":  // an error the Flutter side did not catch: its type and first frames, no message
        DeviceLog.app(DeviceLog.mine(), "app_error", "界面出错：\(a["type"] as? String ?? "?") \(a["stack"] as? String ?? "")", level: "error")
        result(nil)
      case "deviceName": result(UIDevice.current.name)  // "iPhone" since iOS 16 without the user-assigned-device-name entitlement
      case "upload": if Uploader.pending(dir) > 0 { upload() }; result(nil)
      case "recorder":  // the bound BLE recorder, what it last said, and how a firmware update goes
        result(["name": defaults.string(forKey: "bleName") ?? NSNull(), "model": defaults.string(forKey: "bleModel") ?? NSNull(),
                "fw": defaults.string(forKey: "bleFw") ?? NSNull(), "dfu": Dfu.state ?? NSNull(), "kicked": defaults.bool(forKey: "bleKicked")] as [String: Any])
      case "permissions":  // what adding a recorder asks for: on iOS only Bluetooth (no notification, no battery setting)
        let ok = CBManager.authorization == .allowedAlways
        if ok && probe == nil { probe = CBCentralManager(delegate: nil, queue: nil, options: [CBCentralManagerOptionShowPowerAlertKey: false]) }
        result(["bluetooth": ok, "notifications": true, "battery": true, "bluetoothOn": probe.map { $0.state != .poweredOff } ?? true])
      case "askPermission":
        if a["which"] as? String == "bluetooth" {
          if CBManager.authorization == .notDetermined { probe = CBCentralManager(delegate: nil, queue: nil) }  // iOS asks now
          else if let u = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(u) }  // refused once: only Settings
        }
        result(nil)
      case "scan":  // the add-recorder page opens (true; again every 30 s: one scan goes on, keeping the names it has) or closes
        if a["on"] as? Bool != true { nearby?.stop(); nearby = nil }
        else if nearby == nil && CBManager.authorization == .allowedAlways { nearby = Nearby() }
        result(nil)
      case "nearby": result(nearby?.list() ?? [])
      case "bind":  // the recorder the person chose (address: its identifier): the link pairs with it next
        if let old = defaults.string(forKey: "bleName"), old != a["name"] as? String {
          defaults.removeObject(forKey: "hold:\(Ble.tag(old))")  // its backlog no longer comes from here (Dart: releaseHold)
        }
        defaults.set(a["name"] as? String, forKey: "bleName")
        defaults.set(a["address"] as? String, forKey: "bleId")
        defaults.set(true, forKey: "bindPending")
        defaults.set(a["force"] as? Bool ?? false, forKey: "bindForce")  // the recorder then forgets its other phones
        for k in ["bleKicked", "bleModel", "bleFw"] { defaults.removeObject(forKey: k) }
        result(nil)
      case "usbDisk":  // the connected board's card to the computer's USB, or back
        guard let l = link else { return result(FlutterError(code: "none", message: "先开始录音，连上录音设备", details: nil)) }
        l.usbDisk(a["on"] as? Bool ?? false)
        result(nil)
      case "unbind":  // the recorder forgets every phone (when connected); this iPhone's keys stay (no API: Settings)
        Thread.detachNewThread {
          let name = defaults.string(forKey: "bleName")
          // first: the link must not reconnect when the recorder drops it
          for k in ["bleName", "bleId", "bleModel", "bleFw", "bindPending", "bindForce", "bleKicked"] { defaults.removeObject(forKey: k) }
          if let name { defaults.removeObject(forKey: "hold:\(Ble.tag(name))") }  // its backlog no longer comes from here
          let forgot = link?.forgetAll() ?? false
          if let name { DeviceLog.app(Ble.tag(name), "unbind", forgot ? "解除绑定：录音设备忘掉了所有手机" : "解除绑定：录音设备没连上，只有这台手机忘掉了它") }
          DispatchQueue.main.async { result(forgot) }
        }
      case "listen":  // the recorder's sound played on this phone as it comes (Listen.swift), or not; off when the app leaves the front
        let on = a["on"] as? Bool ?? false
        guard let l = link else { return result(on ? FlutterError(code: "none", message: "先开始录音，连上录音设备", details: nil) : nil) }
        do { try l.listen(on); result(nil) } catch { result(FlutterError(code: "listen", message: "没法播放：\(error.localizedDescription)", details: nil)) }
      case "pause":  // the connected recorder keeps nothing for s seconds (0: records again)
        do {
          guard let l = link else { throw CocoaError(.featureUnsupported) }
          try l.pause(a["s"] as? Int ?? 0)
          result(nil)
        } catch { result(FlutterError(code: "none", message: "录音设备没连上", details: nil)) }
      case "dfu":  // a package from the server for `model`: only onto a recorder that said it is that model (DfuRun.swift)
        let zip = (a["zip"] as? FlutterStandardTypedData)?.data ?? Data(), id = defaults.string(forKey: "bleId")
        if let no = Dfu.refusal(want: a["model"] as? String, model: defaults.string(forKey: "bleModel"), id: id,
                                linked: slicer != nil || link != nil, sha256: Dfu.sha256(zip), expected: a["sha256"] as? String,
                                version: a["version"] as? String, running: defaults.string(forKey: "bleFw")) {
          return result(FlutterError(code: no.0, message: no.1, details: nil))
        }
        Dfu.log = DfuLog(device: Ble.tag(defaults.string(forKey: "bleName") ?? id!), from: defaults.string(forKey: "bleFw"),
                         to: a["version"] as? String ?? "?", bytes: zip.count, sha256: a["sha256"] as? String ?? "", battery: a["battery"] as? Int)
        do { try Dfu.start(UUID(uuidString: id!)!, zip); result(nil) }  // refusal() checked the identifier
        catch {
          Dfu.log?.fail(nil, "固件包打不开：\(error)")
          result(FlutterError(code: "zip", message: "固件包打不开：\(error)", details: nil))
        }
      default: result(FlutterMethodNotImplemented)
      }
    }
  }

  static func status() -> [String: Any] {
    let q = Uploader.files(dir)
    let bytes = q.reduce(0) { $0 + ((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
    let l = link
    return ["running": slicer != nil || l != nil, "uploading": uploader != nil && !q.isEmpty, "files": slicer?.files ?? l?.files ?? NSNull(),
            "slice": slicer?.sliceS() ?? (l != nil ? sliceS() : nil) ?? NSNull(), "error": slicer?.error ?? NSNull(), "sent": uploader?.sent ?? NSNull(),
            "pending": q.count, "pendingBytes": bytes, "oldest": q.first.flatMap { Uploader.piece($0)?.start } ?? NSNull(),
            "note": uploader?.note ?? NSNull(), "interruptedAt": interruptedAt ?? NSNull(),
            "source": (slicer != nil ? "mic" : l != nil ? "ble" : nil) ?? NSNull(), "link": l?.state ?? NSNull(), "tag": l?.tag ?? NSNull(),
            "hw": l?.hw ?? NSNull(), "usbNote": l?.usbNote ?? NSNull(), "lastSync": l?.syncNote ?? NSNull(),
            "syncDone": l?.syncDoneKb ?? NSNull(), "syncTotal": l?.syncTotalKb ?? NSNull(), "syncLive": l?.syncLiveKb ?? NSNull(), "pausedUntil": l?.pausedUntil ?? NSNull(),
            "dfu": Dfu.state ?? NSNull(), "dfuBusy": Dfu.busy, "listen": l?.listening?.lines ?? NSNull()]
  }

  /// A firmware update ended (DfuRun.swift): the BLE link back at once, set up as the last start left it. The home
  /// page's ticker would start it, but iOS does not run it in the background, and nothing else wakes the app with the
  /// link down: after an update in the background, 18 min without LIVE until iOS relaunched the app. Started
  /// here, the link's connect request waits for the recorder's reboot and wakes the app for it.
  static func relink() {
    guard slicer == nil, link == nil, defaults.string(forKey: "source") == "ble" else { return }
    _ = start(Dictionary(uniqueKeysWithValues: ["server", "token", "device", "source", "archive", "foreground", "background"]
      .compactMap { k in defaults.object(forKey: k).map { (k, $0) } }))
  }

  /// The piece length the BLE link cuts the live file into (the microphone's Slicer has its own).
  static func sliceS() -> Int { max(1, defaults.integer(forKey: foregroundApp ? "foreground" : "background")) }

  private static func start(_ a: [String: Any]) -> String? {
    if slicer != nil || link != nil { return nil }
    for k in ["server", "token", "device", "source"] { defaults.set(a[k] as? String ?? "", forKey: k) }
    defaults.set(a["archive"] as? Bool ?? true, forKey: "archive")
    defaults.set(a["foreground"] as? Int ?? 1, forKey: "foreground")
    defaults.set(a["background"] as? Int ?? 20, forKey: "background")
    if defaults.string(forKey: "source") == "ble" {
      startLink()
    } else {
      let s = Slicer(dir: dir, fgS: a["foreground"] as? Int ?? 1, bgS: a["background"] as? Int ?? 20)
      s.foreground = foregroundApp
      do { try s.start() } catch { return "\(error)" }
      slicer = s
      UIApplication.shared.isIdleTimerDisabled = true  // the page is meant to be watched
    }
    interruptedAt = nil
    defaults.set(true, forKey: "active")
    defaults.removeObject(forKey: "endAt")  // a new recording carries on: no end for the last stop
    defaults.removeObject(forKey: "endDevice")
    upload()
    return nil
  }

  private static func startLink() {
    let fm = FileManager.default
    let support = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
    let docs = fm.urls(for: .documentDirectory, in: .userDomainMask)[0]  // undated audio: in the Files app, as on Android
    link = BleLink(chunks: dir, undated: docs.appendingPathComponent("undated"), mirrors: support.appendingPathComponent("recorder"),
                   sliceS: sliceS, report: { name, hw in uploader?.report(name, hw) })
    link!.start()
  }

  private static func stop() {
    defaults.set(false, forKey: "active")
    if defaults.object(forKey: "endAt") == nil {  // set already: the recording stopped earlier, at a gap (gap(from:))
      defaults.set(now(), forKey: "endAt")  // sent after the last piece, also by a later launch if this one is killed first
    }
    UIApplication.shared.isIdleTimerDisabled = false
    slicer?.stop(); slicer = nil
    if let l = link { defaults.set(l.tag, forKey: "endDevice"); l.stop(); link = nil }  // the end goes under the recorder's name
    // iOS gives about 30 s after the app leaves the screen; what is not sent by then goes on the next launch
    var task = UIBackgroundTaskIdentifier.invalid
    let release = {
      DispatchQueue.main.async {
        if task != .invalid { UIApplication.shared.endBackgroundTask(task); task = .invalid }
      }
    }
    task = UIApplication.shared.beginBackgroundTask(withName: "stop", expirationHandler: release)  // not ended on expiry = killed
    endAfterQueue(release)
  }

  /// Once every piece is up (however long that takes, across suspensions), the saved end, unless recording started
  /// or resumed meanwhile (which removes it).
  private static func endAfterQueue(_ done: @escaping () -> Void = {}) {
    Thread.detachNewThread {
      while autoreleasepool(invoking: { Uploader.pending(dir) > 0 && defaults.object(forKey: "endAt") != nil }) {  // see Uploader.main
        Thread.sleep(forTimeInterval: 0.5)
      }
      if let at = defaults.object(forKey: "endAt") as? Int64 {  // the server may close the conversation now
        uploader?.end(at, device: defaults.string(forKey: "endDevice"))
        defaults.removeObject(forKey: "endAt")
        defaults.removeObject(forKey: "endDevice")
      }
      done()
    }
  }

  /// The system stopped the recording at `at` and it could not resume: the server gets `at` as the recording's end
  /// once the pieces before it are up, so its conversation closes instead of waiting for audio that is not coming.
  static func gap(from at: Int64) {
    guard defaults.object(forKey: "endAt") == nil else { return }
    defaults.set(at, forKey: "endAt")
    endAfterQueue()
  }

  static func resumed(after at: Int64) {
    interruptedAt = at
    defaults.removeObject(forKey: "endAt")
  }

  /// At launch (AppDelegate, before any UI: iOS may be relaunching the app in the background for the BLE recorder): a
  /// piece being written when the process died still decodes up to its last page; send what is left over. The BLE link
  /// comes back by itself: the recorder records anyway, and the link is always on (Android's sticky service).
  static func launch() {
    // app/ios/e2e/run.sh: settings in the launch environment (only devicectl or a debugger sets one); null removes a key
    if let j = ProcessInfo.processInfo.environment["LR_E2E"], let d = try? JSONSerialization.jsonObject(with: Data(j.utf8)) as? [String: Any] {
      for (k, v) in d {
        let u = k.hasPrefix("flutter.") ? UserDefaults.standard : defaults  // flutter.*: the Dart side's shared_preferences
        if v is NSNull { u.removeObject(forKey: k) } else { u.set(v, forKey: k) }
      }
    }
    // app/ios/e2e/uitest.sh kill: the process ends itself after LR_E2E_KILL seconds, as when iOS ends it (not the person
    // swiping it away), to see iOS relaunch it in the background for the recorder (state restoration)
    if let s = ProcessInfo.processInfo.environment["LR_E2E_KILL"].flatMap(Double.init) {
      DispatchQueue.main.asyncAfter(deadline: .now() + s) { kill(getpid(), SIGKILL) }
    }
    DeviceLog.start(dir: dir.deletingLastPathComponent(), prefs: defaults)
    foregroundApp = UIApplication.shared.applicationState != .background
    // what the app says of itself: this launch (in the background: iOS brought it back for the
    // recorder, state restoration: BleLink's restore event says what it kept), how its processes ended and crashed
    // (MetricKit, a day later), what it may do
    DeviceLog.app(DeviceLog.mine(), "launch", foregroundApp ? "App 启动" : "App 启动（在后台被系统拉起）")
    MXMetricManager.shared.add(Metrics.shared)
    permissions(UIApplication.shared.backgroundRefreshStatus)
    let fm = FileManager.default
    var last: Int64 = 0
    for f in (try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.contentModificationDateKey])) ?? [] {
      let m = (try? f.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate).map { Int64($0.timeIntervalSince1970 * 1000) } ?? 0
      last = max(last, m)
      if f.lastPathComponent.contains(".part.") {  // no sample count: <start>.ogg (or .aac from the app before)
        try? fm.moveItem(at: f, to: dir.appendingPathComponent(f.lastPathComponent.replacingOccurrences(of: ".part.", with: ".")))
      }
    }
    if defaults.bool(forKey: "active") && defaults.string(forKey: "source") == "ble" {
      startLink()
      upload()
      return
    }
    guard defaults.bool(forKey: "active") || Uploader.pending(dir) > 0 || defaults.object(forKey: "endAt") != nil else { return }
    let killed = defaults.bool(forKey: "active")
    defaults.set(false, forKey: "active")
    upload()
    if killed {
      uploader?.note = "上次录音被系统终止，未传的 \(Uploader.pending(dir)) 块正在补传，需要手动重新开始"
      DeviceLog.app(DeviceLog.mine(), "killed", "上次用手机麦克风录音时被系统终止，没有自动恢复", level: "warn")
      if last > 0 { defaults.set(last, forKey: "endAt") }  // close the conversation the kill left open
    }
    endAfterQueue()  // unless a new recording takes over first
  }

  /// One uploader for the queue directory; a new start only refreshes its settings, so no piece goes twice.
  private static func upload() {
    let archive = defaults.bool(forKey: "archive")
      ? FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0].appendingPathComponent("recordings") : nil
    let u = uploader ?? Uploader(dir: dir)
    u.server = defaults.string(forKey: "server") ?? ""
    u.token = defaults.string(forKey: "token") ?? ""
    u.device = defaults.string(forKey: "device") ?? "phone"
    u.archive = archive
    if uploader == nil { uploader = u; u.start() }
  }

  static func now() -> Int64 { Int64(Date().timeIntervalSince1970 * 1000) }

  /// Where the server's finalization waits for a recorder's backlog (BleLink.holdAhead), nil: it does not.
  static func hold(_ tag: String) -> Int64? { (defaults.object(forKey: "hold:\(tag)") as? NSNumber)?.int64Value }

  /// The phone, for the status sent with its recorder's: this build, the system, the model identifier
  /// (iPhone18,1; never the name the person gave the phone), its battery and whether it saves power or runs hot (iOS
  /// slows or ends apps then), and this app's memory. Off the main thread (UIDevice is read on it).
  static func phone() -> [String: Any] {
    var u = utsname()
    uname(&u)
    let machine = withUnsafeBytes(of: &u.machine) { String(decoding: $0.prefix { $0 != 0 }, as: UTF8.self) }
    let (level, battery) = DispatchQueue.main.sync {
      UIDevice.current.isBatteryMonitoringEnabled = true
      permissions(UIApplication.shared.backgroundRefreshStatus)  // with each status: a change while the app runs
      return (UIDevice.current.batteryLevel, UIDevice.current.batteryState)
    }
    let p = ProcessInfo.processInfo, v = p.operatingSystemVersion
    return ["app": DeviceLog.version, "os": "iOS \(v.majorVersion).\(v.minorVersion).\(v.patchVersion)", "phone": machine,
            "phone_battery": level >= 0 ? Int((level * 100).rounded()) : NSNull(),
            "phone_charging": battery == .unknown ? NSNull() : battery != .unplugged, "low_power": p.isLowPowerModeEnabled,
            "thermal": ["nominal", "fair", "serious", "critical"][min(p.thermalState.rawValue, 3)], "app_mb": footprintMB()]
  }

  /// What the app may do, logged when it changes and the first time: Bluetooth, without which nothing connects;
  /// background app refresh (also off in Low Power Mode). On the main thread.
  static func permissions(_ refresh: UIBackgroundRefreshStatus) {
    let ble = CBManager.authorization
    let line = [ble == .allowedAlways ? "蓝牙权限 有" : ble == .notDetermined ? "蓝牙权限 还没问" : "蓝牙权限 无",
                refresh == .available ? "后台刷新 开" : "后台刷新 关"].joined(separator: " · ")
    guard line != defaults.string(forKey: "perms") else { return }
    defaults.set(line, forKey: "perms")
    DeviceLog.app(DeviceLog.mine(), "permissions", "权限：\(line)", level: ble == .allowedAlways ? nil : "warn")
  }

  /// This process's memory as iOS counts it against the app's limit (phys_footprint), in MB.
  static func footprintMB() -> Int {
    var info = task_vm_info_data_t()
    var n = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
    let kr = withUnsafeMutablePointer(to: &info) {
      $0.withMemoryRebound(to: integer_t.self, capacity: Int(n)) { task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &n) }
    }
    return kr == KERN_SUCCESS ? Int(info.phys_footprint >> 20) : -1
  }

  static func clock(_ ms: Int64) -> String {
    let f = DateFormatter()
    f.dateFormat = "HH:mm:ss"
    return f.string(from: Date(timeIntervalSince1970: Double(ms) / 1000))
  }
}

/// The input node's stream, resampled to 16 kHz mono and encoded as Ogg Opus pieces (OggOpus.swift) whose length
/// follows the app state, each named by the wall time of its first sample and its sample count. A run is one
/// encoder over continuous audio; an interruption (a call), a route change (AirPods) or a media services reset ends
/// it, and the next run re-anchors the wall time. Everything that starts or stops the engine runs on the main
/// thread; the run and the files belong to `q`.
final class Slicer {
  var foreground = true
  private(set) var files = 0
  private(set) var error: String?
  private let dir: URL
  private var fgS: Int, bgS: Int  // set on q
  private var engine = AVAudioEngine()  // new after a media services reset
  private let out = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: OpusPieces.rate, channels: 1, interleaved: false)!
  private let q = DispatchQueue(label: "slicer")  // owns run, anchorMs, fileSlice
  private var run: OpusPieces?
  private var anchorMs: Int64 = 0, fileSlice = 0
  private var gapFrom: Int64?  // main: the system stopped the recording at this time and it has not resumed yet
  private var observers: [NSObjectProtocol] = []

  init(dir: URL, fgS: Int, bgS: Int) { self.dir = dir; self.fgS = fgS; self.bgS = bgS }

  func sliceS() -> Int { foreground ? fgS : bgS }

  func setSlices(fg: Int, bg: Int) { q.async { self.fgS = fg; self.bgS = bg } }

  func start() throws {
    try activate()
    try restart()
    let nc = NotificationCenter.default, session = AVAudioSession.sharedInstance()
    observers = [
      nc.addObserver(forName: AVAudioSession.interruptionNotification, object: session, queue: .main) { [weak self] n in
        guard let self, let raw = n.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt else { return }
        // ended: resume whether or not it says shouldResume. It may never come; then the app coming to the front resumes
        if AVAudioSession.InterruptionType(rawValue: raw) == .began { self.pause() } else { self.resume() }
      },
      nc.addObserver(forName: AVAudioSession.mediaServicesWereResetNotification, object: session, queue: .main) { [weak self] _ in
        guard let self else { return }
        self.pause()
        self.engine = AVAudioEngine()  // every audio object of before is dead; the session is set up again in resume
        self.resume()
      },
      nc.addObserver(forName: .AVAudioEngineConfigurationChange, object: nil, queue: .main) { [weak self] n in
        guard let self, n.object as AnyObject === self.engine, self.gapFrom == nil else { return }
        do { try self.restart() } catch { self.pause(); self.failed("切换输入后恢复失败", error) }
      },
    ]
  }

  func stop() {
    observers.forEach(NotificationCenter.default.removeObserver)
    engine.inputNode.removeTap(onBus: 0)
    engine.stop()
    q.sync { finishRun() }
    try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
  }

  /// Mixable, so playing a video or a podcast neither silences it nor interrupts the recording (.record did both);
  /// recording is passive and leaves the output where the user has it: .allowBluetoothA2DP keeps AirPods available as
  /// an output (playAndRecord hides them otherwise; A2DP is output only). No .allowBluetooth: the input would move to
  /// the AirPods' call microphone. No .defaultToSpeaker: it would force the sound off headphones. .measurement: no
  /// voice processing.
  private func activate() throws {
    let session = AVAudioSession.sharedInstance()
    try session.setCategory(.playAndRecord, mode: .measurement, options: [.mixWithOthers, .allowBluetoothA2DP])
    if #available(iOS 14.5, *) { try session.setPrefersNoInterruptionsFromSystemAlerts(true) }  // a call as a banner: only answering it interrupts
    try session.setActive(true)
  }

  /// The system took the microphone: the piece being written ends here and the gap starts.
  private func pause() {
    if gapFrom == nil { gapFrom = Recorder.now() }
    error = "\(Recorder.clock(gapFrom!)) 起被系统中断，等待恢复"
    q.sync { finishRun() }
  }

  /// Records again after a pause (interruption over, media services back, the app back in front). iOS may refuse
  /// to start recording from the background: then the gap stays open, the page says since when and the server
  /// gets its start as the end of the recording, and the next try is when the app comes to the front.
  func resume() {
    guard let from = gapFrom else { return }
    do {
      try activate()
      try restart()
      gapFrom = nil
      error = nil
      Recorder.resumed(after: from)
    } catch { failed("中断后恢复失败", error) }
  }

  private func failed(_ what: String, _ e: Error) {
    error = "\(Recorder.clock(gapFrom ?? Recorder.now())) 起没有录音：\(what)（\(e.localizedDescription)），回到前台时重试"
    Recorder.gap(from: gapFrom ?? Recorder.now())
  }

  /// (Re)attach the tap with the input's current format and start a new run at the current wall time. Each tap
  /// has its own resampler and run, so a buffer of the old tap still on its way cannot reach the new ones.
  private func restart() throws {
    let input = engine.inputNode
    input.removeTap(onBus: 0)
    engine.stop()
    let format = input.outputFormat(forBus: 0)
    guard let resampler = AVAudioConverter(from: format, to: out) else { throw NSError(domain: "Slicer", code: 1, userInfo: [NSLocalizedDescriptionKey: "no input (\(format))"]) }
    let run = try q.sync { () throws -> OpusPieces in
      finishRun()
      anchorMs = Recorder.now()
      let r = try OpusPieces(open: { [unowned self] first in
        fileSlice = sliceS()
        return dir.appendingPathComponent("\(anchorMs + first * 1000 / Int64(OpusPieces.rate)).part.ogg")
      }, full: { [unowned self] n in  // the slice length is up, or the state changed to a shorter slice that is already over
        let sr = Int64(OpusPieces.rate), state = sliceS()
        return n >= Int64(fileSlice) * sr || (state < fileSlice && n >= Int64(state) * sr)
      }, closed: { [unowned self] url, _, n in  // <start>.part.ogg becomes <start>_<n>.ogg
        try? FileManager.default.moveItem(at: url, to: url.deletingLastPathComponent().appendingPathComponent(url.lastPathComponent.replacingOccurrences(of: ".part.ogg", with: "_\(n).ogg")))
        files += 1
      })
      self.run = r
      return r
    }
    input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buf, _ in self?.convert(buf, resampler, run) }
    engine.prepare()
    try engine.start()
  }

  private func convert(_ buf: AVAudioPCMBuffer, _ resampler: AVAudioConverter, _ run: OpusPieces) {
    guard let o = AVAudioPCMBuffer(pcmFormat: out, frameCapacity: AVAudioFrameCount(Double(buf.frameLength) * out.sampleRate / buf.format.sampleRate) + 16)
    else { return }
    var fed = false, err: NSError?
    resampler.convert(to: o, error: &err) { _, status in
      if fed { status.pointee = .noDataNow; return nil }
      fed = true; status.pointee = .haveData; return buf
    }
    if let err { error = "\(err)"; return }
    if o.frameLength > 0 { q.async { do { try run.write(o) } catch { self.error = "\(error)" } } }
  }

  private func finishRun() {  // on q
    do { try run?.finish() } catch { self.error = "\(error)" }
    run = nil
  }
}

/// Uploads the .ogg files (.aac from the app before) in start order, retrying forever; a file is archived or deleted once
/// the server has it, so the directory is the offline queue. The BLE recorder's back-to-back pieces waiting together go
/// as one request, as on Android (each is a whole Ogg stream, so their bytes in a row decode as one): with 1 s pieces
/// one request per piece cannot keep up when a request takes over a second.
final class Uploader: Thread {
  var server = "", token = "", device = "phone", archive: URL?
  private(set) var sent = 0
  var note: String?
  private let dir: URL
  @Shared private var fails = 0      // in a row, and since when (unix ms)
  private var failedAt: Int64 = 0
  @Shared private var okAt: Int64 = 0  // the last upload the server took (unix ms; 0: none since the launch)

  init(dir: URL) { self.dir = dir }

  /// A queued file's start (unix ms) and sample count: <start>_<n>.ogg, or <start>.ogg for a piece a kill cut short;
  /// the BLE recorder's carry its name instead: <start>@LR-P1-BDAE.ogg, uploaded as that device.
  static func piece(_ f: URL) -> (start: Int64, n: Int64?, device: String?)? {
    let at = f.deletingPathExtension().lastPathComponent.split(separator: "@", maxSplits: 1)
    let p = at[0].split(separator: "_").map { Int64($0) }
    guard ["ogg", "aac"].contains(f.pathExtension), (1...2).contains(p.count), let start = p[0] else { return nil }
    return (start, p.count == 2 ? p[1] : nil, at.count == 2 ? String(at[1]) : nil)
  }

  /// The first pieces of the queue of one BLE recorder that follow each other (a stop or a kill leaves a gap), at most
  /// 30: one request, one stretch from its first piece's start. Following means later: with what is said now first,
  /// the backlog after it is older and goes on its own. The microphone's go one at a time, each with its n.
  private func batch(_ q: [URL]) -> [URL] {
    guard let d = Uploader.piece(q[0])?.device else { return [q[0]] }
    var out = [q[0]]
    for f in q.dropFirst() {
      guard out.count < 30, let p = Uploader.piece(f), p.device == d, (1...1500).contains(p.start - Uploader.piece(out.last!)!.start) else { break }
      out.append(f)
    }
    return out
  }

  static func files(_ dir: URL) -> [URL] {
    ((try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)) ?? [])
      .compactMap { f in piece(f).map { (f, $0.start) } }
      .sorted { $0.1 < $1.1 }.map { $0.0 }
  }

  static func pending(_ dir: URL) -> Int { files(dir).count }

  /// A pool per pass: this thread never ends, and what Foundation autoreleases on it (the directory listed every second,
  /// each batch read) would otherwise stay for good.
  override func main() { while true { autoreleasepool(invoking: pass) } }

  private func pass() {
    // a recorder's pieces from its hold on go first (what is said now, ahead of a backlog read off its card)
    let all = Uploader.files(dir), ahead = { (f: URL) in
      Uploader.piece(f).map { p in p.device.flatMap(Recorder.hold).map { p.start >= $0 } ?? false } ?? false
    }
    let q = all.filter(ahead) + all.filter { !ahead($0) }
    guard let f = q.first, let p = Uploader.piece(f) else { Thread.sleep(forTimeInterval: 1); return }
    let b = batch(q)
    // the hold with every upload while there is one: the server lets go when an upload comes without it
    let hold = p.device.flatMap(Recorder.hold).map { "&hold=\($0)" } ?? ""
    do {
      let code = try post("\(server)/api/chunks?device=\(enc(p.device ?? device))&start=\(p.start)" + (p.n.map { "&n=\($0)" } ?? "") + hold,
                          try b.reduce(Data()) { $0 + (try Data(contentsOf: $1)) }, f.pathExtension == "ogg" ? "audio/ogg" : "audio/aac")
      switch code {
      case 200:
        b.forEach(keep); sent += b.count; note = nil
        okAt = Recorder.now()
        if fails > 0 {
          let s = (okAt - failedAt) / 1000
          DeviceLog.app(p.device ?? device, "upload_ok", "上传恢复（之前连续失败 \(fails) 次，\(s < 120 ? "\(s) 秒" : "\(s / 60) 分钟")）",
                        fields: ["fails": fails, "ms": okAt - failedAt])
        }
        fails = 0
      case 400:  // the server keeps the bytes (recordings/rejected/); this phone says it lost them
        let bytes = b.reduce(0) { $0 + ((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
        DeviceLog.app(p.device ?? device, "upload_rejected", "服务端无法解码 \(b.count) 块（起于 \(Recorder.clock(p.start))，\(bytes / 1024) KB），已丢弃",
                      level: "error", fields: ["start": p.start, "pieces": b.count, "bytes": bytes])
        b.forEach { try? FileManager.default.removeItem(at: $0) }; note = "服务端无法解码一块，已丢弃"
      default: throw URLError(.badServerResponse, userInfo: [NSLocalizedDescriptionKey: "HTTP \(code)"])
      }
    } catch {
      fails += 1
      if fails == 1 {
        failedAt = Recorder.now()
        DeviceLog.app(p.device ?? device, "upload_fail", "上传失败：\(DeviceLog.why(error, server: server))", level: "error")
      }
      note = "上传失败 \(error.localizedDescription)"
      Thread.sleep(forTimeInterval: 5 * Double(min(fails, 6)))
    }
  }

  func end(_ atMs: Int64, device ble: String? = nil) {
    do { _ = try post("\(server)/api/chunks/end?device=\(enc(ble ?? device))&t=\(atMs)", Data(), nil) } catch { note = "结束信号失败 \(error.localizedDescription)" }
  }

  /// What the phone last read from its BLE recorder, for the server's progress page, with the phone's own state and this
  /// recorder's upload queue; best effort, off this thread.
  func report(_ device: String, _ hw: [String: Any]) {
    Thread.detachNewThread { [self] in
      let body = hw.merging(Recorder.phone()) { a, _ in a }.merging(queued(device)) { a, _ in a }
      _ = try? post("\(server)/api/device-status?device=\(enc(device))", (try? JSONSerialization.data(withJSONObject: body)) ?? Data(), "application/json")
    }
  }

  /// `device`'s pieces waiting (how many, KB, the oldest's age in s) and how uploads go: failures in a row, s since the
  /// last one the server took (nil: none since the launch).
  func queued(_ device: String) -> [String: Any] {
    let q = Uploader.files(dir).filter { Uploader.piece($0)?.device == device }, now = Recorder.now()
    let bytes = q.reduce(0) { $0 + ((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
    return ["queue_n": q.count, "queue_kb": bytes / 1024, "queue_age_s": q.first.flatMap { Uploader.piece($0).map { (now - $0.start) / 1000 } } ?? NSNull(),
            "upload_fails": fails, "upload_ok_s": okAt > 0 ? (now - okAt) / 1000 : NSNull()]
  }

  private func keep(_ f: URL) {
    let fm = FileManager.default
    guard let archive else { try? fm.removeItem(at: f); return }
    let fmt = DateFormatter()
    fmt.locale = Locale(identifier: "en_US_POSIX"); fmt.dateFormat = "yyyy-MM-dd"
    let day = archive.appendingPathComponent(fmt.string(from: Date(timeIntervalSince1970: Double(Uploader.piece(f)!.start) / 1000)))
    do {
      try fm.createDirectory(at: day, withIntermediateDirectories: true)
      try? fm.removeItem(at: day.appendingPathComponent(f.lastPathComponent))
      try fm.moveItem(at: f, to: day.appendingPathComponent(f.lastPathComponent))
    } catch { note = "归档失败 \(error.localizedDescription)"; try? fm.removeItem(at: f) }
  }

  private func post(_ url: String, _ body: Data, _ type: String?) throws -> Int {
    guard let u = URL(string: url) else { throw URLError(.badURL) }
    var r = URLRequest(url: u, timeoutInterval: 60)
    r.httpMethod = "POST"
    r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    if let type { r.setValue(type, forHTTPHeaderField: "Content-Type") }
    // the request finishes even if iOS would suspend the app meanwhile (the BLE link keeps it awake only while the
    // recorder sends): up to the ~30 s iOS gives
    var task = UIBackgroundTaskIdentifier.invalid
    let release = { DispatchQueue.main.async { if task != .invalid { UIApplication.shared.endBackgroundTask(task); task = .invalid } } }
    task = UIApplication.shared.beginBackgroundTask(withName: "upload", expirationHandler: release)  // not ended on expiry = killed
    defer { release() }
    var code = 0, err: Error?
    let done = DispatchSemaphore(value: 0)
    URLSession.shared.uploadTask(with: r, from: body) { _, resp, e in
      code = (resp as? HTTPURLResponse)?.statusCode ?? 0; err = e; done.signal()
    }.resume()
    done.wait()
    if let err { throw err }
    return code
  }

  private func enc(_ s: String) -> String { s.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? s }
}

/// What iOS reports of the app afterwards (MetricKit): every day how its processes ended in front and in
/// the background (the memory limit, memory pressure, the watchdog, a crash...), and on the next launch each crash (its
/// kind, the reason iOS gave, the first frames as binary+offset), hang, CPU or disk-write overrun. No data.
final class Metrics: NSObject, MXMetricManagerSubscriber {
  static let shared = Metrics()

  func didReceive(_ payloads: [MXMetricPayload]) {
    for p in payloads {
      guard let x = p.applicationExitMetrics else { continue }
      let f = x.foregroundExitData, b = x.backgroundExitData
      let fg = ["正常": f.cumulativeNormalAppExitCount, "内存超限": f.cumulativeMemoryResourceLimitExitCount, "看门狗": f.cumulativeAppWatchdogExitCount,
                "非法访问": f.cumulativeBadAccessExitCount, "非法指令": f.cumulativeIllegalInstructionExitCount, "异常": f.cumulativeAbnormalExitCount]
      let bg = ["正常": b.cumulativeNormalAppExitCount, "内存超限": b.cumulativeMemoryResourceLimitExitCount, "内存紧张": b.cumulativeMemoryPressureExitCount,
                "CPU 超限": b.cumulativeCPUResourceLimitExitCount, "看门狗": b.cumulativeAppWatchdogExitCount, "非法访问": b.cumulativeBadAccessExitCount,
                "非法指令": b.cumulativeIllegalInstructionExitCount, "异常": b.cumulativeAbnormalExitCount,
                "挂起时占着文件": b.cumulativeSuspendedWithLockedFileExitCount, "后台任务超时": b.cumulativeBackgroundTaskAssertionTimeoutExitCount]
      let said = { (c: [String: Int]) in c.filter { $0.value > 0 }.sorted { $0.key < $1.key }.map { "\($0.key) \($0.value)" }.joined(separator: "、") }
      guard !(fg.values.allSatisfy { $0 == 0 } && bg.values.allSatisfy { $0 == 0 }) else { continue }
      let bad = (fg.merging(bg) { $0 + $1 }).contains { $0.key != "正常" && $0.value > 0 }
      DeviceLog.app(DeviceLog.mine(), "exits", "App 进程结束（过去一天）：前台 \(said(fg).isEmpty ? "无" : said(fg))；后台 \(said(bg).isEmpty ? "无" : said(bg))",
                    level: bad ? "warn" : nil, fields: ["fg": fg.filter { $0.value > 0 }, "bg": bg.filter { $0.value > 0 }, "build": p.metaData?.applicationBuildVersion ?? NSNull()],
                    at: Metrics.ms(p.timeStampEnd))
    }
  }

  func didReceive(_ payloads: [MXDiagnosticPayload]) {
    for p in payloads {
      let at = Metrics.ms(p.timeStampEnd)
      for c in p.crashDiagnostics ?? [] {
        let frames = DeviceLog.frames(c.callStackTree.jsonRepresentation())
        DeviceLog.app(DeviceLog.mine(), "crash", "崩溃：exception \(c.exceptionType?.intValue ?? -1) code \(c.exceptionCode?.intValue ?? -1) signal \(c.signal?.intValue ?? -1)" +
                      (c.terminationReason.map { "，\($0.prefix(120))" } ?? "") + "；\(frames.joined(separator: " < "))", level: "error",
                      fields: ["build": c.metaData.applicationBuildVersion, "frames": frames], at: at)
      }
      let hangs = p.hangDiagnostics ?? [], cpu = p.cpuExceptionDiagnostics ?? [], disk = p.diskWriteExceptionDiagnostics ?? []
      guard !hangs.isEmpty || !cpu.isEmpty || !disk.isEmpty else { continue }
      let longest = hangs.map { $0.hangDuration.converted(to: .seconds).value }.max() ?? 0
      let wrote = disk.map { $0.totalWritesCaused.converted(to: .megabytes).value }.reduce(0, +)
      DeviceLog.app(DeviceLog.mine(), "diagnostics", [hangs.isEmpty ? nil : "卡顿 \(hangs.count) 次（最长 \(String(format: "%.1f", longest)) 秒）",
                                                      cpu.isEmpty ? nil : "CPU 超限 \(cpu.count) 次",
                                                      disk.isEmpty ? nil : "写盘超限 \(disk.count) 次（\(Int(wrote)) MB）"].compactMap { $0 }.joined(separator: "，"),
                    level: "warn", fields: ["hangs": hangs.count, "cpu": cpu.count, "disk": disk.count], at: at)
    }
  }

  static func ms(_ d: Date) -> Int64 { Int64(d.timeIntervalSince1970 * 1000) }
}
