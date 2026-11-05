// What happened on the capture side, for the server's device page (/device/<name>, POST /api/device-log), port of the
// Android app's DeviceLog.kt: the recorder's own event log (firmware lr_log.h), read over BLE from where the last read
// ended (BleLink.readLog), and the app's key events (connect with each step's time, disconnect, offline sync, pause,
// unbind, uploads failing and coming back). An event is first appended to a local file; a thread of its own sends the
// file every 30 s and keeps it while that fails, so a phone offline loses nothing, and neither reading nor sending ever
// waits on the audio's upload or holds it up. Foundation only: app/ios/StoreCheck runs the parsing on a Mac and in CI.
import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
#if canImport(UIKit)
import UIKit
#endif

enum DeviceLog {
  /// A record of the recorder's log: boot:u16 t:u32 code:u8 x:u8 a:u32 b:u32 (lr_log.h), and its seq.
  struct Rec: Equatable { let seq: Int64, boot: Int, t: Int64, code: Int, x: Int, a: Int64, b: Int64 }
  static let time = 14      // a | b << 32: the epoch ms the phone said frame t was at
  static let uptime = 0x80  // before the boot's first frame: t is ms since power-up

  private static var file: URL?  // the queue: one JSON event per line, the device it belongs to inside
  private static var prefs = UserDefaults.standard
  private static let lock = NSLock()
  private static var src = ""    // this install's app events: "app:<id made at the first start>"
  private static var lastSeq: Int64 = 0  // an app event's seq: its time in ms, kept increasing (also across restarts)
  private static var steps: [String] = []  // the connection's steps so far, for its connect event
  /// This build, 1.4.6+21: in every app event and status.
  static let version = "\(Bundle.main.infoDictionary?["CFBundleShortVersionString"] ?? "?")+\(Bundle.main.infoDictionary?["CFBundleVersion"] ?? "?")"

  static func start(dir: URL, prefs p: UserDefaults) {
    lock.lock()
    defer { lock.unlock() }
    if file != nil { return }
    file = dir.appendingPathComponent("devlog.jsonl")
    prefs = p
    // src + seq is the server's key: another phone, or this one reinstalled, never writes over these events
    src = p.string(forKey: "logSrc") ?? "app:\(UUID().uuidString.lowercased())"
    p.set(src, forKey: "logSrc")
    lastSeq = Int64(p.integer(forKey: "logSeq"))
    Thread.detachNewThread {
      while true { autoreleasepool(invoking: send); Thread.sleep(forTimeInterval: 30) }  // a pool per round
    }
  }

  //
  // The recorder's log
  //

  /// DATA notifications of a LOG (seq:u32 + 16 B records), in any order, maybe overlapping -> records by seq.
  static func records(_ packets: [[UInt8]]) -> [Rec] {
    var out: [Int64: Rec] = [:]
    for p in packets where p.count >= 4 {
      let seq = Int64(Store.le(p, 0, 4))
      for i in 0..<((p.count - 4) / 16) {
        let o = 4 + i * 16
        out[seq + Int64(i)] = Rec(seq: seq + Int64(i), boot: Int(Store.le(p, o, 2)), t: Int64(Store.le(p, o + 2, 4)), code: Int(p[o + 6]),
                                  x: Int(p[o + 7]), a: Int64(Store.le(p, o + 8, 4)), b: Int64(Store.le(p, o + 12, 4)))
      }
    }
    return out.keys.sorted().map { out[$0]! }
  }

  /// Each record's wall time (epoch ms) or nil, as lr_sync.py dates them: a frame by the boot's TIME record nearest to
  /// it, among these and `known` (boot -> (frame, epoch) of earlier reads), at the boot's rate (Store.at). A record
  /// from before the boot's first frame goes at that frame: a few seconds late at most (the log has no record of the
  /// first frame's uptime). A boot that ended before any phone set its clock stays undated.
  static func dates(_ recs: [Rec], known: [Int: (frame: Int64, epoch: Int64)]) -> [Int64?] {
    var sync: [Int: [(frame: Int64, epochMs: Int64)]] = known.mapValues { [($0.frame, $0.epoch)] }
    for r in recs where r.code == time { sync[r.boot, default: []].append((r.t, r.b << 32 | r.a)) }
    let rates = sync.mapValues { Store.bootRate($0) }
    return recs.map { r in
      let frame = r.code & uptime != 0 ? 0 : r.t
      return sync[r.boot].map { Store.at($0, frame, rate: rates[r.boot]) }
    }
  }

  /// Where the next read starts: after the last record of an unbroken run from the first one received (a lost notify
  /// is read again), or `next` when nothing came at all.
  static func cursor(_ recs: [Rec], next: Int64) -> Int64 {
    guard var c = recs.first?.seq else { return next }
    for r in recs { if r.seq == c { c += 1 } else { break } }
    return c == recs.last!.seq + 1 ? next : c
  }

  /// A LOG read of the recorder `tag`: `from` is where to ask from (a log that started over is asked again from 0 by
  /// the caller), `next` its reply; the records go into the queue, dated, and the cursor and the last TIME are kept.
  static func took(_ tag: String, _ packets: [[UInt8]], next: Int64) {
    let recs = records(packets)
    let k = prefs.string(forKey: "logTime:\(tag)")?.split(separator: ",").compactMap { Int64($0) }
    let at = dates(recs, known: k?.count == 3 ? [Int(k![0]): (k![1], k![2])] : [:])
    add(recs.enumerated().map { i, x in
      event(tag, "rec", x.seq, at[i], ["boot": x.boot, "t": x.t, "code": x.code, "x": x.x, "a": x.a, "b": x.b])
    })
    prefs.set(cursor(recs, next: next), forKey: "logNext:\(tag)")
    if let t = recs.last(where: { $0.code == time }) { prefs.set("\(t.boot),\(t.t),\(t.b << 32 | t.a)", forKey: "logTime:\(tag)") }
  }

  static func from(_ tag: String) -> Int64 { Int64(prefs.integer(forKey: "logNext:\(tag)")) }

  /// Whose page the app's own events go on: the bound recorder's (its name as Ble.tag cleans it), else the phone's (its
  /// microphone's audio name).
  static func mine() -> String {
    if let n = prefs.string(forKey: "bleName") { return n.replacingOccurrences(of: "[^A-Za-z0-9._-]", with: "_", options: .regularExpression) }
    return prefs.string(forKey: "device").flatMap { $0.isEmpty ? nil : $0 } ?? "phone"
  }

  //
  // The app's events
  //

  /// One of the app's events, filed under the device it is about (a recorder's name, or the phone's for its upload);
  /// `fields` go into its data beside kind and text, for the server to read.
  static func app(_ device: String, _ kind: String, _ text: String, level: String? = nil, fields: [String: Any] = [:], at: Int64? = nil) {
    lock.lock()
    guard file != nil else { lock.unlock(); return }
    let now = Int64(Date().timeIntervalSince1970 * 1000)
    lastSeq = max(now, lastSeq + 1)
    let seq = lastSeq
    prefs.set(seq, forKey: "logSeq")
    lock.unlock()
    var data = fields.merging(["kind": kind, "text": text, "app": version]) { $1 }
    if let level { data["level"] = level }
    add([event(device, src, seq, at ?? now, data)])
  }

  /// The crashed thread's first frames in a MetricKit call stack tree (MXCallStackTree.jsonRepresentation()), as
  /// binary+offset: a build's dSYM turns them into lines; no data.
  static func frames(_ json: Data, max n: Int = 5) -> [String] {
    guard let tree = try? JSONSerialization.jsonObject(with: json) as? [String: Any],
          let stacks = tree["callStacks"] as? [[String: Any]] else { return [] }
    var frame = (stacks.first { $0["threadAttributed"] as? Bool == true } ?? stacks.first)?["callStackRootFrames"] as? [[String: Any]]
    var out: [String] = []
    while let f = frame?.first, out.count < n {
      out.append("\(f["binaryName"] as? String ?? "?")+\(f["offsetIntoBinaryTextSegment"] as? Int ?? 0)")
      frame = f["subFrames"] as? [[String: Any]]
    }
    return out
  }

  /// Why a request to `server` failed, for an upload_fail event: the system's words, the host (the address as typed
  /// when it is no URL), the error's code, the one under it, the socket's errno and the network path iOS saw (a VPN
  /// or proxy shows there); never the token or the query.
  static func why(_ error: Error, server: String) -> String {
    let e = error as NSError, under = e.userInfo[NSUnderlyingErrorKey] as? NSError
    var s = "\(e.localizedDescription)（\(URL(string: server)?.host ?? server.debugDescription) \(e.domain) \(e.code)"
    if let under { s += "，底层 \(under.domain) \(under.code)" }
    if let c = e.userInfo["_kCFStreamErrorCodeKey"] { s += "，流 \(e.userInfo["_kCFStreamErrorDomainKey"] ?? "?")/\(c)" }
    if let p = under?.userInfo["_NSURLErrorNWPathKey"] ?? e.userInfo["_NSURLErrorNWPathKey"] { s += "，网络 \(p)" }
    return s + "）"
  }

  /// A step of connecting (BleLink.step), kept for the connect event.
  static func step(_ what: String, _ ms: Int) { lock.lock(); steps.append("\(what) \(ms) ms"); lock.unlock() }

  /// A connection's session starts: its steps from here. Not at the disconnect: the session it ends adds the step it
  /// was in after that, and it showed up first in the next connect event.
  static func connecting() { lock.lock(); steps = []; lock.unlock() }

  /// The link is up and the audio flows: the steps it took, and how long it was down, as one event.
  static func connected(_ device: String) {
    lock.lock(); let s = steps.joined(separator: "，"); steps = []; lock.unlock()
    let down = (prefs.object(forKey: "downAt") as? Int64).map { Int(Int64(Date().timeIntervalSince1970 * 1000) - $0) }
    prefs.removeObject(forKey: "downAt")
    app(device, "connect", "\(down.map { "断开 \(DfuLog.span($0)) 后连上" } ?? "连上")：\(s)", fields: down.map { ["down_ms": $0] } ?? [:])
  }

  /// The link went down: why, as CoreBluetooth says it (nil: this phone ended it). The first of an outage dates it, in
  /// prefs: iOS may end the app while it waits and relaunch it for the connection (10-15: 58 min).
  static func disconnected(_ device: String, _ why: String?) {
    if prefs.object(forKey: "downAt") == nil { prefs.set(Int64(Date().timeIntervalSince1970 * 1000), forKey: "downAt") }
    app(device, "disconnect", "断开（\(why ?? "手机断开")）", level: "warn")
  }

  //
  // The queue
  //

  private static func event(_ device: String, _ src: String, _ seq: Int64, _ at: Int64?, _ data: [String: Any]) -> [String: Any] {
    ["device": device, "src": src, "seq": seq, "at": at ?? NSNull(), "data": data]
  }

  private static func add(_ events: [[String: Any]]) {
    guard !events.isEmpty else { return }
    var lines = Data()
    for e in events { if let j = try? JSONSerialization.data(withJSONObject: e) { lines.append(j); lines.append(0x0a) } }
    lock.lock()
    defer { lock.unlock() }
    guard let f = file else { return }
    if !FileManager.default.fileExists(atPath: f.path) { _ = FileManager.default.createFile(atPath: f.path, contents: nil) }
    if let h = FileHandle(forWritingAtPath: f.path) { _ = try? h.seekToEnd(); try? h.write(contentsOf: lines); try? h.close() }
  }

  /// The queue moved aside (new events start a new file) and sent per device; kept for the next round while a request
  /// fails (the server keeps a record sent again once). A batch the server cannot take (400, 422) is dropped.
  private static func send() {
    guard let f = file, let server = prefs.string(forKey: "server"), !server.isEmpty else { return }
    let fm = FileManager.default, dir = f.deletingLastPathComponent()
    lock.lock()
    try? fm.moveItem(at: f, to: dir.appendingPathComponent("devlog-\(Int64(Date().timeIntervalSince1970 * 1000)).send"))
    lock.unlock()
    let queued = { ((try? fm.contentsOfDirectory(atPath: dir.path)) ?? []).filter { $0.hasSuffix(".send") }.sorted().map { dir.appendingPathComponent($0) } }
    let gone = trim(queued(), cap: cap)
    if gone > 0 { app(mine(), "log_dropped", "离线太久，待发的日志超过 \(cap >> 20) MB，丢掉最早的 \(gone / 1024) KB", level: "warn") }
    for s in queued() {
      let events = ((try? String(contentsOf: s, encoding: .utf8)) ?? "").split(separator: "\n")
        .compactMap { try? JSONSerialization.jsonObject(with: Data($0.utf8)) as? [String: Any] }
      var kept = false
      for (device, list) in Dictionary(grouping: events, by: { $0["device"] as? String ?? "" }) {
        let body = (try? JSONSerialization.data(withJSONObject: list.map { $0.filter { $0.key != "device" } })) ?? Data()
        let device = device.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? device
        let code = post("\(server)/api/device-log?device=\(device)", body)
        if code == 200 { continue }
        NSLog("live-record devlog send %@: HTTP %d", device, code)
        if code != 400 && code != 422 { kept = true }
      }
      if kept { return }
      try? fm.removeItem(at: s)
    }
  }

  static let cap = 10 << 20  // the queue kept while sends fail: a recorder's log offline for weeks

  /// The oldest files of the queue past `cap` bytes deleted, the newest always kept; how many bytes went.
  static func trim(_ files: [URL], cap: Int) -> Int {
    let size = { (f: URL) in (try? f.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0 }
    var total = files.reduce(0) { $0 + size($1) }, gone = 0
    for f in files.dropLast() where total > cap {
      total -= size(f); gone += size(f)
      try? FileManager.default.removeItem(at: f)
    }
    return gone
  }

  private static func post(_ url: String, _ body: Data) -> Int {
    guard let u = URL(string: url) else { return -1 }
    #if canImport(UIKit)
    // the request finishes even if iOS would suspend the app meanwhile, as an upload's
    var task = UIBackgroundTaskIdentifier.invalid
    let release = { DispatchQueue.main.async { if task != .invalid { UIApplication.shared.endBackgroundTask(task); task = .invalid } } }
    task = UIApplication.shared.beginBackgroundTask(withName: "devlog", expirationHandler: release)
    defer { release() }
    #endif
    var r = URLRequest(url: u, timeoutInterval: 30)
    r.httpMethod = "POST"
    r.setValue("Bearer \(prefs.string(forKey: "token") ?? "")", forHTTPHeaderField: "Authorization")
    r.setValue("application/json", forHTTPHeaderField: "Content-Type")
    final class Reply { var code = -1 }
    let reply = Reply(), done = DispatchSemaphore(value: 0)
    URLSession.shared.uploadTask(with: r, from: body) { _, resp, _ in reply.code = (resp as? HTTPURLResponse)?.statusCode ?? -1; done.signal() }.resume()
    done.wait()
    return reply.code
  }
}
