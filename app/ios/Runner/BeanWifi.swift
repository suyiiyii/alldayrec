// The Feishu recorder bean's Wi-Fi fast transfer (#431): its hotspot joined (NEHotspotConfiguration: iOS asks the
// person each time, the name being new each time), then a WebSocket over TLS to the bean, the one certificate the bean
// has pinned (expired and not for its address: only for this endpoint, nothing else trusts it). Requests go as hex text,
// answers come as binary 09 FF frames (ws/soundcore wifi.py, 0BSD; Eliqi797/open-feishu-recording-bean
// PinnedRecorderLink.swift, MIT, for the endpoint, the pin, the keep-alive and the request headers; see
// THIRD_PARTY_NOTICES.md). BeanSync (Bean.swift) does the rest, as over BLE; BeanLink carries its bytes here meanwhile.
import CryptoKit
import Foundation
import Network
import NetworkExtension

final class BeanWifi {
  static let host = "192.168.43.1", port: UInt16 = 443  // the bean's, whatever else it answers
  /// SHA-256 of the bean's certificate (DER), as Eliqi797 found it
  static let pin = "3f2667135cafff135614944392d6b3bc0e991b2ca55dbe9e469caca44d5a0125"

  private let device: String, ssid: String, password: String
  private let q = DispatchQueue(label: "bean.wifi")
  @Shared private var ws: NWConnection?  // set once it is up: what goes after that ends receive()
  private var udp: NWConnection?, keepAlive: DispatchSourceTimer?
  private let inbox = Inbox<[UInt8]?>()  // nil: the socket went
  @Shared private var seen: String?  // the certificate's pin, as it came
  @Shared private(set) var bytes: Int64 = 0
  @Shared private var trail = ""  // a try's states, for its log
  @Shared private var lastVia = "?"  // a try's interfaces
  @Shared private var waitingWhy = ""  // why the socket's path is not there (localNetworkDenied: the permission refused)
  private var monitor: NWPathMonitor?

  init(device: String, ssid: String, password: String) { self.device = device; self.ssid = ssid; self.password = password }

  private func log(_ kind: String, _ text: String, _ level: String? = nil, _ fields: [String: Any] = [:]) {
    Ble.log("bean \(kind): \(text)")
    DeviceLog.app(device, kind, text, level: level, fields: fields)
  }

  private static func mono() -> Int64 { Int64(ProcessInfo.processInfo.systemUptime * 1000) }

  /// The hotspot (checked: iOS can say joined and not be), then the socket on it: up to 90 s while its address comes up and the person answers iOS's local
  /// network question (the first time). `endpoint`: the 1A/05 answer, only ever the bean's fixed one.
  func open(_ endpoint: [UInt8]) throws {
    let at = Bean.endpoint(endpoint) ?? "?"
    guard at == "\(BeanWifi.host):\(BeanWifi.port)" else {
      log("bean_wifi_fail", "热点地址 \(at) 不是豆子固定的那个，不连", "warn", ["step": "endpoint", "endpoint": at])
      throw Bean.Fail("hotspot at \(at), not the bean's", note: "beanWifiNoHotspot")
    }
    watch()
    let t0 = BeanWifi.mono()
    // iOS joins once the person taps 加入 on its question; joined before about 4.5 s after the bean's answer it often
    // says joined and is not (its own 「无法加入网络」 then, the phone still on the Wi-Fi before; 11-08 to 11-10: 4 of
    // 6 such, none of 4 later), the hotspot not up yet; a second try, asking nothing, joined each time: wait for it
    Thread.sleep(forTimeInterval: 5)
    var joinedOnce = false
    for (n, wait) in [0.0, 2, 4, 8].enumerated() {
      if wait > 0 { Thread.sleep(forTimeInterval: wait) }
      // iOS joins nothing for an app not in front (14 applicationIsNotInForeground: 11-11 14:00, left 8 s before and
      // back 32 s later): up to a minute for it to come back, the hotspot waiting meanwhile
      for _ in 0..<120 where !BeanWifi.front() { Thread.sleep(forTimeInterval: 0.5) }
      let t1 = BeanWifi.mono()
      let c = NEHotspotConfiguration(ssid: ssid, passphrase: password, isWEP: false)
      c.joinOnce = true  // iOS leaves it when the app leaves the front
      let done = DispatchSemaphore(value: 0)
      var failure: NSError?
      NEHotspotConfigurationManager.shared.apply(c) { failure = $0 as NSError?; done.signal() }
      guard done.wait(timeout: .now() + 120) == .success else {
        log("bean_wifi_fail", "加入热点 120 s 没有结果", "warn", ["step": "join", "ms": BeanWifi.mono() - t1, "try": n + 1].merging(BeanWifi.context()) { $1 })
        throw Bean.Fail("joining the hotspot: no answer in 120 s", note: "beanWifiJoinTimeout")
      }
      let applied = BeanWifi.mono() - t1  // the person's answer and iOS's join: the address is waited for from here
      // alreadyAssociated (13): iOS on it already, as joined; onHotspot() below checks it is the bean's
      let ours = failure == nil || failure!.domain == NEHotspotConfigurationErrorDomain && failure!.code == NEHotspotConfigurationError.alreadyAssociated.rawValue
      if let e = failure, !ours {
        let code = NEHotspotConfigurationError(rawValue: e.code).map(BeanWifi.name) ?? "\(e.code)"
        // again would not help; 16/17 are for accessory networks (the SDK's words), not expected here: beanWifiFailed
        let last = ["userDenied", "applicationIsNotInForeground", "userUnauthorized", "systemDenied"].contains(code)
        log(last ? "bean_wifi_fail" : "bean_wifi_retry", "没加入豆子的热点（第 \(n + 1) 次）：\(code == "userDenied" ? "主人点了取消" : e.localizedDescription)（\(e.domain) \(e.code) \(code)，\(applied) ms）",
            "warn", ["step": "join", "domain": e.domain, "code": e.code, "name": code, "ms": applied, "try": n + 1].merging(BeanWifi.context()) { $1 })
        if last { throw Bean.Fail("join: \(code)", note: ["userDenied": "beanWifiDenied", "applicationIsNotInForeground": "beanWifiBackground"][code]) }
        NEHotspotConfigurationManager.shared.removeConfiguration(forSSID: ssid)
        continue
      }
      if let mine = onHotspot() {
        log("bean_wifi_join", "加入了豆子的热点（第 \(n + 1) 次，iOS 答复 \(applied) ms\(failure != nil ? "，本来就连着" : "")，再 \(BeanWifi.mono() - t1 - applied) ms 手机拿到了热点的地址 \(mine)）", nil,
            ["ms": BeanWifi.mono() - t1, "apply_ms": applied, "try": n + 1, "ip": mine].merging(BeanWifi.context()) { $1 })
        joinedOnce = true
        break
      } else {
        let other = BeanWifi.wifiAddress() != nil
        log("bean_wifi_retry", "iOS 说加入了（\(applied) ms），之后 10 s 手机没拿到热点的地址，当前 Wi-Fi \(other ? "是别的网络" : "没有")（第 \(n + 1) 次）", "warn",
            ["step": "join_unverified", "ms": BeanWifi.mono() - t1, "apply_ms": applied, "try": n + 1, "wifi": other].merging(BeanWifi.context()) { $1 })
        NEHotspotConfigurationManager.shared.removeConfiguration(forSSID: ssid)
      }
    }
    guard joinedOnce else {
      log("bean_wifi_fail", "试了 4 次都没加入豆子的热点", "warn", ["step": "join", "tries": 4].merging(BeanWifi.context()) { $1 })
      throw Bean.Fail("join: not on the hotspot after 4 tries", note: "beanWifiNoJoin")
    }
    let joined = BeanWifi.mono() - t0
    var tries = 0, why = ""
    // up to 90 s: the hotspot's address coming up, and the first time iOS's local network question (21:25 11-08: three
    // tries of 15 s went while it was unanswered, most likely)
    while BeanWifi.mono() - t0 < 90_000 + joined {
      tries += 1
      do { try connect(); break } catch {
        why = "\(error)"
        log("bean_wifi_socket", "连豆子的 WebSocket 第 \(tries) 次没成：\(why)；状态 \(trail)；走 \(lastVia)", "warn",
            ["tries": tries, "error": why, "path": waitingWhy, "states": trail, "via": lastVia].merging(BeanWifi.context()) { $1 })
        if let s = seen, s != BeanWifi.pin {
          log("bean_wifi_fail", "豆子的证书不是认得的那张（\(s)）", "warn", ["step": "pin", "pin": s])
          throw Bean.Fail("certificate \(s)", note: "beanWifiNoSocket")
        }
        Thread.sleep(forTimeInterval: 0.7)
      }
    }
    guard ws != nil else {
      let vpn = BeanWifi.vpn()
      log("bean_wifi_fail", "连不上豆子的 WebSocket（\(tries) 次，\(why)）" + (vpn ? "；检测到 VPN，快传可能失败，可临时关闭" : ""), "warn",
          ["step": "socket", "tries": tries].merging(BeanWifi.context()) { $1 })
      throw Bean.Fail("socket: \(why)", note: "beanWifiNoSocket")
    }
    let ms = BeanWifi.mono() - t0 - joined
    let via = ws?.currentPath?.availableInterfaces.map { "\($0.name)(\($0.type))" }.joined(separator: ",") ?? "?"
    log("bean_wifi_socket", "连上豆子的 WebSocket（第 \(tries) 次，\(ms) ms，走 \(via)），证书指纹对上 \(seen ?? "")", nil,
        ["ms": ms, "tries": tries, "pin": seen ?? "", "via": via].merging(BeanWifi.context()) { $1 })
  }

  private func connect() throws {
    let tls = NWProtocolTLS.Options()
    sec_protocol_options_set_verify_block(tls.securityProtocolOptions, { [weak self] _, trust, finish in
      let t = sec_trust_copy_ref(trust).takeRetainedValue()
      let leaf = (SecTrustCopyCertificateChain(t) as? [SecCertificate])?.first
      let pin = leaf.map { SHA256.hash(data: SecCertificateCopyData($0) as Data).map { String(format: "%02x", $0) }.joined() }
      self?.seen = pin
      finish(pin == BeanWifi.pin)
    }, q)
    let p = NWParameters(tls: tls)
    p.requiredInterfaceType = .wifi  // the hotspot, never the cellular route
    p.prohibitedInterfaceTypes = [.other]  // nor a VPN's tunnel (Shadowrocket on the owner's iPhone)
    let w = NWProtocolWebSocket.Options()
    w.autoReplyPing = true
    w.maximumMessageSize = 4 << 20
    w.setAdditionalHeaders([("User-Agent", "okhttp/3.12.13.18")])  // the SDK's, as the bean knows it
    p.defaultProtocolStack.applicationProtocols.insert(w, at: 0)
    let c = NWConnection(to: .url(URL(string: "wss://\(BeanWifi.host):\(BeanWifi.port)/")!), using: p)
    let ready = DispatchSemaphore(value: 0)
    var failure: Error?
    trail = ""
    let t0 = BeanWifi.mono()
    c.stateUpdateHandler = { [weak self, weak c] s in
      if self?.ws == nil {  // each try's way, and the interfaces it was on
        self?.trail += (self?.trail.isEmpty ?? true ? "" : " → ") + "\(s) @\(BeanWifi.mono() - t0) ms"
        self?.lastVia = c?.currentPath?.availableInterfaces.map { "\($0.name)(\($0.type))" }.joined(separator: ",") ?? "?"
      }
      switch s {
      case .ready: ready.signal()
      case .failed(let e): failure = e; ready.signal(); if self?.ws != nil { self?.inbox.put(nil) }
      case .waiting(let e):  // no route yet, or the local network question unanswered or refused: wait on
        failure = e
        self?.waitingWhy = BeanWifi.why(c?.currentPath)
      case .cancelled: if self?.ws != nil { self?.inbox.put(nil) }
      default: break
      }
    }
    c.start(queue: q)
    guard ready.wait(timeout: .now() + 15) == .success, failure == nil || c.state == .ready else {
      c.stateUpdateHandler = nil
      c.cancel()
      throw failure ?? Bean.Fail("no answer in 15 s")
    }
    ws = c
    pump(c)
    let u = NWConnection(host: .init(BeanWifi.host), port: 32003, using: {
      let p = NWParameters.udp
      p.requiredInterfaceType = .wifi
      return p
    }())
    u.start(queue: q)
    udp = u
    let timer = DispatchSource.makeTimerSource(queue: q)
    timer.schedule(deadline: .now(), repeating: 0.1)  // ws/soundcore's 100 ms (1 s till 45)
    timer.setEventHandler { [weak u] in u?.send(content: Data("soundcore-keep-alive-unicast".utf8), completion: .contentProcessed { _ in }) }
    timer.resume()
    keepAlive = timer
  }

  /// The phone's address on the hotspot (its DHCP gives 192.168.43.x), within 10 s of iOS's answer; nil: iOS said joined
  /// and is not. No entitlement needed, unlike NEHotspotNetwork.fetchCurrent. Waiting longer does not help: 11-10 the
  /// phone stayed on the Wi-Fi before for all of 20 s, as for 8 s twice on 11-08/09; the second try asks nothing
  private func onHotspot() -> String? {
    for _ in 0..<20 {
      if let a = BeanWifi.wifiAddress(), a.hasPrefix("192.168.43.") { return a }
      Thread.sleep(forTimeInterval: 0.5)
    }
    return nil
  }

  /// The IPv4 address of a Wi-Fi interface (en*), if any.
  static func wifiAddress() -> String? {
    var list: UnsafeMutablePointer<ifaddrs>?
    guard getifaddrs(&list) == 0, let first = list else { return nil }
    defer { freeifaddrs(list) }
    var found: [String] = []
    for p in sequence(first: first, next: { $0.pointee.ifa_next }) {
      guard let sa = p.pointee.ifa_addr, sa.pointee.sa_family == UInt8(AF_INET), String(cString: p.pointee.ifa_name).hasPrefix("en") else { continue }
      var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
      if getnameinfo(sa, socklen_t(sa.pointee.sa_len), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST) == 0 { found.append(String(cString: host)) }
    }
    return found.first { $0.hasPrefix("192.168.43.") } ?? found.first
  }

  /// A VPN up (its tunnel among the system's scoped proxies: utun/ipsec/ppp/tap/tun), and the app in front or not.
  static func vpn() -> Bool {
    let scoped = (CFNetworkCopySystemProxySettings()?.takeRetainedValue() as? [String: Any])?["__SCOPED__"] as? [String: Any] ?? [:]
    return scoped.keys.contains { k in ["utun", "ipsec", "ppp", "tap", "tun"].contains { k.hasPrefix($0) } }
  }

  static func front() -> Bool { Thread.isMainThread ? Recorder.foregroundApp : DispatchQueue.main.sync { Recorder.foregroundApp } }

  static func context() -> [String: Any] { ["vpn": vpn(), "front": front()] }

  static func name(_ e: NEHotspotConfigurationError) -> String {
    switch e {
    case .invalid: "invalid"
    case .invalidSSID: "invalidSSID"
    case .invalidWPAPassphrase: "invalidWPAPassphrase"
    case .invalidWEPPassphrase: "invalidWEPPassphrase"
    case .invalidEAPSettings: "invalidEAPSettings"
    case .invalidHS20Settings: "invalidHS20Settings"
    case .invalidHS20DomainName: "invalidHS20DomainName"
    case .userDenied: "userDenied"
    case .internal: "internal"
    case .pending: "pending"
    case .systemConfiguration: "systemConfiguration"
    case .unknown: "unknown"
    case .joinOnceNotSupported: "joinOnceNotSupported"
    case .alreadyAssociated: "alreadyAssociated"
    case .applicationIsNotInForeground: "applicationIsNotInForeground"
    case .invalidSSIDPrefix: "invalidSSIDPrefix"
    case .userUnauthorized: "userUnauthorized"
    case .systemDenied: "systemDenied"
    @unknown default: "\(e.rawValue)"
    }
  }

  static func why(_ p: Network.NWPath?) -> String {
    guard let p else { return "none" }
    let reason: String
    switch p.unsatisfiedReason {
    case .notAvailable: reason = ""
    case .cellularDenied: reason = "cellularDenied"
    case .wifiDenied: reason = "wifiDenied"
    case .localNetworkDenied: reason = "localNetworkDenied"
    @unknown default: reason = "other"
    }
    return "\(p.status)" + (reason.isEmpty ? "" : " \(reason)")
  }

  /// The phone's default route while on the hotspot (which has no internet); uploads wait meanwhile (UploadPath)
  private func watch() {
    let m = NWPathMonitor()
    m.pathUpdateHandler = { [weak self] p in
      let kinds = [NWInterface.InterfaceType.wifi, .cellular, .wiredEthernet, .other].filter(p.usesInterfaceType).map { "\($0)" }
      let all = p.availableInterfaces.map { "\($0.type)" }
      self?.log("bean_wifi_path", "手机网络：\(p.status)，默认走 \(kinds.joined(separator: "+"))，可用 \(all.joined(separator: ","))" +
                (p.isExpensive ? "（计流量）" : ""), nil, ["status": "\(p.status)", "uses": kinds, "available": all, "expensive": p.isExpensive])
    }
    m.start(queue: q)
    monitor = m
    UploadPath.device = device
  }

  /// A new socket on the hotspot, the one before closed: the bean stops sending its file then, as when BLE goes (redial()
  /// in BeanSync.wifiLoop: what the phone has already is not sent again).
  func redial() throws {
    keepAlive?.cancel(); keepAlive = nil
    udp?.cancel(); udp = nil
    let old = ws
    ws = nil
    old?.stateUpdateHandler = nil
    old?.cancel()
    inbox.clear()
    let t0 = BeanWifi.mono()
    do { try connect() } catch {
      log("bean_wifi_socket", "重连豆子的 WebSocket 没成：\(error)；状态 \(trail)", "warn", ["redial": true, "error": "\(error)", "states": trail])
      throw error
    }
    log("bean_wifi_socket", "重连了豆子的 WebSocket（\(BeanWifi.mono() - t0) ms）：让豆子停下这个文件", nil, ["redial": true, "ms": BeanWifi.mono() - t0])
  }

  /// Each message as it comes: binary frames, or frames as hex text (OK, ACK and the like are not).
  private func pump(_ c: NWConnection) {
    c.receiveMessage { [weak self] data, context, _, error in
      guard let self, self.ws === c else { return }  // a socket redial() closed: nothing of it any more
      if error != nil { self.inbox.put(nil); return }
      let meta = context?.protocolMetadata(definition: NWProtocolWebSocket.definition) as? NWProtocolWebSocket.Metadata
      if let data {
        self.bytes += Int64(data.count)
        switch meta?.opcode {
        case .binary: self.inbox.put([UInt8](data))
        case .text: if let b = BeanWifi.unhex(String(decoding: data, as: UTF8.self)) { self.inbox.put(b) }
        case .close: self.inbox.put(nil); return
        default: break
        }
      }
      self.pump(c)
    }
  }

  static func unhex(_ s: String) -> [UInt8]? {
    let c = Array(s.trimmingCharacters(in: .whitespacesAndNewlines).lowercased().utf8)
    guard c.count >= 20, c.count % 2 == 0, c.starts(with: Array("09ff".utf8)) else { return nil }
    var out: [UInt8] = []
    out.reserveCapacity(c.count / 2)
    for i in stride(from: 0, to: c.count, by: 2) {
      guard let b = UInt8(String(decoding: c[i..<(i + 2)], as: UTF8.self), radix: 16) else { return nil }
      out.append(b)
    }
    return out
  }

  /// A frame BeanSync made, as hex text (the bean ignores binary requests).
  func send(_ frame: [UInt8]) throws { try text(Bean.hex(frame)) }

  private func text(_ s: String) throws {
    guard let c = ws else { throw BeanSync.Gone("Wi-Fi socket closed") }
    let ctx = NWConnection.ContentContext(identifier: "text", metadata: [NWProtocolWebSocket.Metadata(opcode: .text)])
    let sent = DispatchSemaphore(value: 0)
    var failure: Error?
    c.send(content: Data(s.utf8), contentContext: ctx, isComplete: true, completion: .contentProcessed { failure = $0; sent.signal() })
    guard sent.wait(timeout: .now() + 10) == .success else { throw Bean.Fail("Wi-Fi send: no answer in 10 s") }
    if let failure { throw BeanSync.Gone("Wi-Fi send: \(failure)") }
  }

  func receive(_ ms: Int64) throws -> [UInt8]? {
    guard let m = inbox.poll(Double(ms) / 1000) else { return nil }
    guard let b = m else { inbox.put(nil); throw BeanSync.Gone("Wi-Fi socket closed") }
    return b
  }

  /// FINISH (the SDK's goodbye), the socket closed and the hotspot forgotten: iOS goes back to the Wi-Fi it had.
  func close() {
    try? text("FINISH")
    keepAlive?.cancel(); keepAlive = nil
    udp?.cancel(); udp = nil
    ws?.cancel(); ws = nil
    NEHotspotConfigurationManager.shared.removeConfiguration(forSSID: ssid)
    monitor?.cancel(); monitor = nil
    UploadPath.device = nil
    let listed = DispatchSemaphore(value: 0)
    var still: Bool?
    NEHotspotConfigurationManager.shared.getConfiguredSSIDs { still = $0.contains(self.ssid); listed.signal() }
    _ = listed.wait(timeout: .now() + 5)
    log("bean_wifi_leave", "离开豆子的热点（收到 \(bytes / 1024) KB）：配置" + (still == nil ? "查不到" : still! ? "还在" : "已移除"),
        still == true ? "warn" : nil, ["bytes": bytes, "configured": still ?? NSNull()])
  }
}

/// While a fast transfer is on (`device` set) uploads wait (Uploader): the hotspot has no internet, and 11-09 with a VPN
/// on the phone's route went back and forth between Wi-Fi and cellular and uploads failed (-1005); the queue goes after.
enum UploadPath {
  @Shared static var device: String?
}
