// The Feishu recorder bean on the iPhone (Runner/Bean.swift), the cases of the Android app's BeanTest.kt: the protocol
// against ws/soundcore's byte vectors (fixtures/bean-crypto.json, 0BSD), and BeanSync against a fake bean (FakeBean
// below: synthetic keys, files and audio, a made-up serial and MAC; nothing of a real bean). CryptoKit and CommonCrypto,
// so macOS only (CI: the app workflow's bean-check job):
//   cd app/ios && swiftc -o /tmp/beancheck Runner/Bean.swift Runner/Store.swift "$PWD/BeanCheck/main.swift" && /tmp/beancheck
import CryptoKit
import Foundation

func check<T: Equatable>(_ got: T, _ want: T, _ what: String = "", line: Int = #line) {
  if got != want { print("FAIL line \(line) \(what): got \(got), want \(want)"); exit(1) }
}
func throwsError(_ what: String = "", line: Int = #line, _ block: () throws -> Void) {
  do { try block(); print("FAIL line \(line) \(what): no error"); exit(1) } catch {}
}
func error<E: Error>(_ type: E.Type, line: Int = #line, _ block: () throws -> Void) -> E {
  do { try block() } catch let e as E { return e } catch { print("FAIL line \(line): \(error)"); exit(1) }
  print("FAIL line \(line): no error"); exit(1)
}

let fixtureURL = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
  .deletingLastPathComponent().deletingLastPathComponent().appendingPathComponent("fixtures/bean-crypto.json")
let v = try JSONSerialization.jsonObject(with: Data(contentsOf: fixtureURL)) as! [String: Any]
func hex(_ s: String) -> [UInt8] {
  let c = Array(s)
  return stride(from: 0, to: c.count, by: 2).map { UInt8(String(c[$0...($0 + 1)]), radix: 16)! }
}
func field(_ o: String, _ k: String) -> [UInt8] { hex((v[o] as! [String: Any])[k] as! String) }
func tmp() -> URL {
  let d = FileManager.default.temporaryDirectory.appendingPathComponent("beancheck-\(UUID().uuidString)")
  try! FileManager.default.createDirectory(at: d, withIntermediateDirectories: true)
  return d
}
func sum(_ b: [UInt8]) -> UInt8 { b.reduce(0, &+) }

// commands: ws/soundcore's vectors
check(Bean.hex(Bean.info()), "08ee0000000101" + "0a0002")
check(Bean.hex(Bean.list(0)), "08ee0000001a0e0c0000002a")
check(Bean.hex(Array(Bean.clock(0x0102_0304, -5).dropFirst(9).dropLast())), "04030201fb")
check(Bean.hex(Array(Bean.transfer(0x0102_0304, 166, true).dropFirst(9).dropLast())), "a60000000403020101")
for f in [Bean.info(), Bean.list(3), Bean.clock(1, 8), Bean.transfer(1, 0, false), Bean.hello([UInt8](repeating: 0, count: 65))] { check(Bean.allowed(f), true) }

// only five commands go out, and the Wi-Fi hotspot's on and off (#431)
check(Bean.sends, [0x0101, 0x01A6, 0x2E01, 0x1A0E, 0x1A07, 0x1A05, 0x1A02])
check(Bean.hex(Bean.wifiOn("LR-1a2b3c4d", "0123456789abcdef")), "08ee0000001a052700" + "0b" + Bean.hex(Array("LR-1a2b3c4d".utf8)) + "10" +
      Bean.hex(Array("0123456789abcdef".utf8)) + "d8")
check(Bean.hex(Bean.wifiOff()), "08ee0000001a020a001c")
check(Bean.hex(Bean.wifiTransfer(1_794_126_868, 0)), "08ee0000001a07120000000000" + "1434f06a" + "cb")  // Eliqi797's WebSocket request
for f in [Bean.wifiOn("x", "password1"), Bean.wifiOff(), Bean.wifiTransfer(1, 166)] { check(Bean.allowed(f), true) }
// bind, auth, unbind, the record button, delete, completion ack, SPP reads, factory reset and OTA, a log read
for cmd in [0x0B87, 0x0B8B, 0x0B01, 0x01B8, 0x1882, 0x1A10, 0x1A0A, 0x1A0F, 0x1A11, 0xFF20, 0xFF01, 0x1B0E] {
  throwsError("\(cmd)") { _ = try Bean.frame(cmd >> 8, cmd & 255) }
}
func made(_ type: UInt8, _ id: UInt8, _ p: [UInt8] = []) -> [UInt8] {
  let head: [UInt8] = [0x08, 0xEE, 0, 0, 0, type, id, UInt8(10 + p.count), 0]
  let b = head + p
  return b + [sum(b)]
}
check(Bean.allowed(made(0x0B, 0x87)), false)  // a frame made some other way: well formed, and still not sent
// the record button: its start alone (the frame sent to the owner's bean, 10-25), never a stop (00) or pause (02)
check(Bean.hex(Bean.startFrame()), "08ee00000018820b00019c")
check(Bean.allowed(Bean.startFrame()), true)
check(Bean.startFrame(), made(0x18, 0x82, [1]))
for p: UInt8 in [0, 2] { check(Bean.allowed(made(0x18, 0x82, [p])), false) }
check(Bean.allowed(made(0x18, 0x82)), false)
check(Bean.allowed(made(0x18, 0x82, [1, 0])), false)
var badSum = Bean.info(); badSum[9] = 0
check(Bean.allowed(badSum), false)
// a delete: only of a file the server has whole, framed and sent for that file alone (ws/soundcore's vector)
throwsError { _ = try Bean.delete(0x0102_0304, []) }
throwsError { _ = try Bean.delete(0x0102_0304, [7]) }
let del = try Bean.delete(0x0102_0304, [0x0102_0304])
check(String(Bean.hex(del).dropLast(2)), "08ee0000001a100e00" + "04030201")
check(Bean.allowed(del, [0x0102_0304]), true)
check(Bean.allowed(del), false)
check(Bean.allowed(del, [7]), false)

// reader: ws/soundcore's framing vectors, one frame, and one split over notifications after junk
let r = Bean.Reader()
let f1 = r.push(hex("09ff00000110a20a00c5"))
check(f1.count, 1)
check([f1[0].type, f1[0].id, f1[0].status, f1[0].payload.count], [0x10, 0xA2, 1, 0])
check(r.push(hex("de09")).isEmpty, true)
check(r.push(hex("ff00000110a2")).isEmpty, true)
check(r.push(hex("0a00c5")).map(\.cmd), [0x10A2])
check(r.push(hex("09ff00000110a20a00c4") + hex("09ff00000110a20a00c5")).map(\.cmd), [0x10A2])  // a bad sum skipped
check(Bean.Frame(type: 0x01, id: 0x01, status: 0x11, payload: []).ok, false)  // device commands: status 1 only
check(Bean.Frame(type: 0x1A, id: 0x07, status: 0x11, payload: []).ok, true)

// crypto
check(try Bean.ctr(field("aes_256_ctr", "key_hex"), field("aes_256_ctr", "iv_hex"), field("aes_256_ctr", "plaintext_hex")),
      field("aes_256_ctr", "ciphertext_hex"))
check(Bean.hkdf(field("hkdf_sha256", "shared_secret_hex")), field("hkdf_sha256", "derived_key_hex"))
// ECDH: the app's private value 3, the device's public key (its private value 4)
let mine = try P256.KeyAgreement.PrivateKey(rawRepresentation: [UInt8](repeating: 0, count: 31) + [3])
check(Bean.pub(mine), field("ecdh_p256", "app_public_key_hex"))
let device = field("ecdh_p256", "device_public_key_hex"), shared = field("ecdh_p256", "shared_secret_hex")
check(try Bean.shared(mine, device), shared)
check(try Bean.session(mine, device + shared), Bean.hkdf(shared))
throwsError("not the secret we got") { _ = try Bean.session(mine, device + [UInt8](repeating: 0, count: 32)) }
check(try Bean.fileKey(field("file_key", "session_key_hex"), field("file_key", "session_nonce_hex"), field("file_key", "encrypted_file_key_hex")),
      field("file_key", "file_key_hex"))
let nonce = field("audio_counter", "nonce_hex")
check(try Bean.iv(nonce, 3), field("audio_counter", "counter_hex"))
check(try Bean.iv(nonce, Bean.maxSeq), field("audio_counter", "max_counter_hex"))
throwsError { _ = try Bean.iv(nonce, Bean.maxSeq + 1) }
check(try Bean.opus(field("file_key", "file_key_hex"), nonce, Bean.Slice(seq: 3, flags: 0, data: field("audio_slice", "ciphertext_hex"))),
      field("audio_slice", "plaintext_hex"))

/// A bean as the protocol has it, scripted, for BeanSync (FakeBean in BeanTest.kt): it answers the five commands and
/// the start (`startable`: it records in a new file, else it only echoes it), wraps file keys in the session key of
/// each connection and encrypts each unit as a bean does; `onTransfer` says what a 1A/07 starts and `script` what
/// happens when the phone waits a second or more and nothing is on its way (out of script: the link goes).
final class FakeBean: BeanIo {
  var t: Int64
  private let keys = Bean.keys()
  private var session: [UInt8] = []
  private var rnd = SystemRandomNumberGenerator()
  private var out: [([UInt8], Int64)] = []  // notifications, and the ms before each
  private var fileKeys: [Int64: ([UInt8], [UInt8])] = [:]
  var recording = false
  var open: Int64 = 0
  var listed: [(Int64, Int64)] = []  // in order, as the bean lists them
  var sent: [[UInt8]] = []
  var transfers: [[Int64]] = []  // file, from unit, live
  var onTransfer: (Int64, Int64, Bool) -> Void = { _, _, _ in }
  var script: [() throws -> Void] = []
  var logs: [(String, String)] = []
  var logFields: [[String: Any]] = []  // each log's fields, as `logs`
  var pageOpen = false  // the live page is open (BeanIo.watched)
  var heldAt: Int64?
  var holds: [Int64?] = []
  var ends: [Int64] = []
  var refusedStarts: Set<Int64> = []
  var deleted: [Int64] = []
  var refuseDelete: Set<Int64> = []
  var confirmed: () -> Set<Int64> = { [] }
  var startable = false
  var starts = 0
  var reports: [[String: Any]] = []

  init(_ now: Int64) { t = now }

  func list(_ id: Int64, _ ms: Int64) { if let i = listed.firstIndex(where: { $0.0 == id }) { listed[i].1 = ms } else { listed.append((id, ms)) } }
  func sentOf(_ cmd: Int) -> [UInt8] { Array(sent.last { Int($0[5]) << 8 | Int($0[6]) == cmd }!.dropFirst(9).dropLast()) }

  func send(_ frame: [UInt8]) throws {
    check(Bean.allowed(frame, confirmed()), true, "only the five commands, a delete of a confirmed file and the start reach the bean")
    sent.append(frame)
    let p = Array(frame[9..<(frame.count - 1)])
    switch Int(frame[5]) << 8 | Int(frame[6]) {
    case 0x2E01:  // a new connection: nothing of the one before is on its way
      out = []
      let s = try Bean.shared(keys, p)
      session = Bean.hkdf(s)
      push(0x2E01, Bean.pub(keys) + s)
    case 0x0101: push(0x0101, info())
    case 0x1A05:  // 192.168.43.1:443 (ws/soundcore's capture); dropOnWifi: BLE goes before the answer
      if dropOnWifi { script.insert({ throw BeanSync.Gone("dropped for the hotspot") }, at: 0) } else { push(0x1A05, [1, 0x2B, 0xA8, 0xC0, 0xBB, 1]) }
    case 0x1A02: push(0x1A02, [])
    case 0x01A6: push(0x01A6, [])
    case 0x1A0E:
      let page: [UInt8] = Bean.u16(p, 0) > 0 ? Bean.u16le(0) : listed.reduce(Bean.u16le(listed.count)) { $0 + Bean.le32($1.0) + Bean.le32($1.1) }
      push(0x1A0E, [page, Bean.le32(open), [0, 0, 0, 0]].flatMap { $0 })
    case 0x1A10:
      let id = Bean.u32(p, 0)
      deleted.append(id)
      if refuseDelete.contains(id) { push(0x1A10, [], status: 2) } else { listed.removeAll { $0.0 == id }; push(0x1A10, []) }
    case 0x1882:  // as the owner's bean (10-25): the command echoed (01 9c) whatever it does, the new file 850 ms on
      starts += 1
      push(0x1882, [p[0], frame.last!])
      if startable { recording = true; open = t / 1000; rotation(open, wait: 850) }
    case 0x1A07:
      check(p.count == 8, onWifi, "1A/07 over Wi-Fi has no realtime flag, over BLE it has")
      let t = [Bean.u32(p, 4), Bean.u32(p, 0) / Int64(Bean.unit), p.count > 8 && p[8] == 1 ? 1 : 0]
      transfers.append(t)
      onTransfer(t[0], t[1], t[2] == 1)
    default: break
    }
  }

  func receive(_ ms: Int64) throws -> [UInt8]? {
    if out.isEmpty && ms < 1000 { t += ms; return nil }  // the pause between transfers: not a script step
    if out.isEmpty {
      guard !script.isEmpty else { throw BeanSync.Gone("out of script") }
      try script.removeFirst()()
    }
    guard !out.isEmpty else { t += ms; return nil }
    let (b, wait) = out.removeFirst()
    t += wait
    return b
  }

  func now() -> Int64 { t }
  func monotonicNow() -> Int64 { t }
  var slice = 50  // live units a piece (the app's slice)
  func liveUnits() -> Int { slice }
  func watched() -> Bool { pageOpen }
  func log(_ kind: String, _ text: String, _ level: String?, _ fields: [String: Any], _ at: Int64?) { logs.append((kind, text)); logFields.append(fields) }
  func report(_ hw: [String: Any]) { reports.append(hw) }
  func hold(_ at: Int64?) { holds.append(at); heldAt = at }
  func held() -> Int64? { heldAt }
  func end(_ at: Int64) { ends.append(at) }
  func piece() {}
  func refused() -> Set<Int64> { refusedStarts }
  var wifiAsk = false, onWifi = false, dropOnWifi = false
  var joins: [(String, String, [UInt8])] = []
  func wifiAsked() -> Bool { defer { wifiAsk = false }; return wifiAsk }
  func wifiJoin(_ ssid: String, _ password: String, _ endpoint: [UInt8]) throws { joins.append((ssid, password, endpoint)); onWifi = true }
  func wifiLeave() { onWifi = false }

  func info() -> [UInt8] {
    var b = [UInt8](repeating: 0, count: 57)
    b[0] = 1; b[1] = 7; b[38] = 5
    b.replaceSubrange(3..<8, with: Array("01.00".utf8)); b.replaceSubrange(8..<24, with: Array("fake000000000001".utf8))
    b.replaceSubrange(24..<28, with: Bean.le32(30_000_000)); b.replaceSubrange(28..<32, with: Bean.le32(20_000_000))
    b.replaceSubrange(39..<45, with: [2, 0, 0, 0, 0x0A, 0x0B])
    b[50] = recording ? 1 : 0
    return b
  }

  /// A frame from the bean (status 1: done), in notifications of up to 200 B.
  func push(_ cmd: Int, _ payload: [UInt8], wait: Int64 = 0, status: UInt8 = 1) {
    let n = payload.count + 10
    let head: [UInt8] = [0x09, 0xFF, 0, 0, status, UInt8(cmd >> 8), UInt8(cmd & 255), UInt8(n & 255), UInt8(n >> 8)]
    let f = head + payload
    let whole = f + [sum(f)]
    for (i, start) in stride(from: 0, to: whole.count, by: 200).enumerated() {
      out.append((Array(whole[start..<min(start + 200, whole.count)]), i == 0 ? wait : 0))
    }
  }

  func random(_ n: Int) -> [UInt8] { (0..<n).map { _ in UInt8.random(in: 0...255, using: &rnd) } }

  func header(_ file: Int64, _ size: Int64, code: UInt8 = 0) {
    if fileKeys[file] == nil { fileKeys[file] = (random(32), random(16)) }
    let (k, n) = fileKeys[file]!
    let sn = random(16)
    let wrapped: [UInt8] = try! Bean.ctr(session, sn, Array("soundcored3200".utf8) + k)
    push(0x1A07, [Bean.le32(file), Bean.le32(size), n, wrapped, sn, [code]].flatMap { $0 })
  }

  /// Units three to a frame, `ms` each (20: real time); the audio says its file and seq.
  func units(_ file: Int64, _ seqs: [Int64], last: Int64 = -1, marks: Set<Int64> = [], wait: Int64 = 0, ms: Int64 = 20) {
    let (k, n) = fileKeys[file]!
    for (i, c) in stride(from: 0, to: seqs.count, by: 3).map({ Array(seqs[$0..<min($0 + 3, seqs.count)]) }).enumerated() {
      push(0x1A08, c.reduce([UInt8]()) { acc, s in
        let flags = UInt8((s == last ? 4 : s == 0 ? 2 : 0) | (marks.contains(s) ? 1 : 0))
        let enc: [UInt8] = try! Bean.ctr(k, Bean.iv(n, s), audio(file, s))
        return acc + [Bean.le32(s), [flags], enc, [0]].flatMap { $0 }
      }, wait: ms * Int64(c.count) + (i == 0 ? wait : 0))
    }
  }

  /// The file being recorded as the bean answers a 1A/07 for it (the 10-27 probe): realtime=1 from the unit it is at,
  /// whatever the offset; realtime=0 from the offset, at 2.5× real time while behind, then on as it records; never an
  /// end. A closed file asked for meanwhile: the open file from 0 (10-26). `openAt`: its unit 0.
  var openAt: Int64 = 0
  private var nextSeq: Int64 = 0  // its next unit on the way
  private var nextAt: Int64 = 0   // when the last went
  func answer(_ file: Int64, _ from: Int64, _ realtime: Bool) {
    header(open, 0xFFFF_FFFF)
    nextSeq = file != open ? 0 : realtime ? (t - openAt) * 1000 / unitUs : from
    nextAt = t
  }
  var unitUs: Int64 = 20_000  // how long a unit takes the bean to record by the phone's clock (#233: up to 20.015 ms)
  /// `n` more of those units, three to a frame.
  func more(_ n: Int) {
    nextAt = max(nextAt, t)
    for c in stride(from: nextSeq, to: nextSeq + Int64(n), by: 3).map({ Array($0..<min($0 + 3, nextSeq + Int64(n))) }) {
      let at = max(nextAt + 8 * Int64(c.count), openAt + (c.last! + 1) * unitUs / 1000)
      units(open, c, wait: at - nextAt, ms: 0)
      nextAt = at
    }
    nextSeq += Int64(n)
  }

  func complete(_ file: Int64) { push(0x1A0A, [[0], Bean.le32(0), Bean.le32(file)].flatMap { $0 }) }
  func rotation(_ file: Int64, wait: Int64 = 0) { push(0x1A06, Bean.le32(file) + [0], wait: wait) }
  private func audio(_ file: Int64, _ seq: Int64) -> [UInt8] {
    let s = UInt32(seq)
    let head: [UInt8] = [0xBC, UInt8(s >> 24), UInt8(s >> 16 & 255), UInt8(s >> 8 & 255), UInt8(s & 255), UInt8(file % 251)]
    return head + [UInt8](repeating: 0, count: 154)
  }
}

/// An Ogg file's packets (OpusHead, OpusTags, then the audio).
func packets(_ f: URL) -> [[UInt8]] {
  let b = [UInt8](try! Data(contentsOf: f))
  var out: [[UInt8]] = [], cur: [UInt8] = [], p = 0
  while p < b.count {
    check(Array(b[p..<(p + 4)]), Array("OggS".utf8))
    let n = Int(b[p + 26])
    var body = p + 27 + n
    for i in 0..<n {
      let l = Int(b[p + 27 + i])
      cur += b[body..<(body + l)]; body += l
      if l < 255 { out.append(cur); cur = [] }
    }
    p = body
  }
  return out
}
func queue(_ chunks: URL) -> [URL] {
  ((try? FileManager.default.contentsOfDirectory(at: chunks, includingPropertiesForKeys: nil)) ?? [])
    .filter { $0.pathExtension == "ogg" }.sorted { startMs($0) < startMs($1) }
}
func startMs(_ f: URL) -> Int64 { Int64(f.deletingPathExtension().lastPathComponent.split(separator: "@")[0])! }
func seqOf(_ p: [UInt8]) -> Int64 { Int64(p[1]) << 24 | Int64(p[2]) << 16 | Int64(p[3]) << 8 | Int64(p[4]) }

// parsing
let unit: [UInt8] = [Bean.le32(7), [5], (0..<160).map { UInt8($0) }, [0]].flatMap { $0 }
let s0 = try Bean.slices(unit + unit)[0]
check([s0.seq, Int64(s0.flags)], [7, 5])
check(s0.data, (0..<160).map { UInt8($0) })
throwsError { _ = try Bean.slices(Array(unit.prefix(165))) }
let hw0 = try Bean.info(FakeBean(0).info())
check(hw0["battery"] as? Int, 80); check(hw0["charge"] as? String, "battery"); check(hw0["fw"] as? String, "01.00")
check(hw0["free_kb"] as? Int64, 20_000_000); check(hw0["case_battery"] as? Int, 60); check(hw0["recording"] as? Bool, false)
check(Bean.serial(FakeBean(0).info()), "fake000000000001")
check([Bean.pct(0), Bean.pct(9), Bean.pct(100)], [10, 100, 100])
let l = try Bean.listing([Bean.u16le(2), Bean.le32(11), Bean.le32(2000), Bean.le32(12), Bean.le32(4000), Bean.le32(13), [0, 0, 0, 0]].flatMap { $0 })
check(l.files.map { [$0.id, $0.ms] }, [[11, 2000], [12, 4000]])
check(l.open, 13)
// the name is from the MAC in the manufacturer data (company id first, as iOS gives it), whatever address it comes from
let adv: [UInt8] = [0x02, 0x00] + [0, 0, 0x0A, 0x0B, 0x91, 0, 0]
check(Bean.name(adv), "D3200-0A0B")
check(Bean.flags(adv), 0x91)
check(Bean.name([0x02, 0x00, 1, 2, 3]), nil)
check(Bean.flags([0x02, 0x00, 1, 2, 3]), -1)
check(Bean.isBean("D3200-0A0B"), true); check(Bean.isBean("LR-P3-0A0B"), false); check(Bean.isBean("xD3200-0A0B"), false)

let t0: Int64 = 1_793_000_000_000

/// What a fake bean records goes up as pieces, each unit once and dated by its seq: the file being recorded from its
/// start (begun 30 s before) on to live, on in the next file it rotates to, a drop mid-file resumed by its byte offset,
/// a hole asked for again once the recording stops, then the end after everything uploads. A file from before the bean
/// was added is never asked for.
do {
  let dir = tmp(), chunks = dir.appendingPathComponent("chunks"), state = dir.appendingPathComponent("state.json")
  let since = t0 - 3_600_000
  let bean = FakeBean(t0)
  let a = (t0 - 30_000) / 1000, old = since / 1000 - 100
  var b: Int64 = 0
  bean.recording = true; bean.open = a; bean.list(old, 60_000)
  bean.onTransfer = { file, from, live in
    switch (file == bean.open, file) {
    case (true, a):  // from the offset asked (realtime=0), on to live
      bean.header(file, 0xFFFF_FFFF)
      bean.units(file, Array(from...1749), last: 1749)
      bean.complete(file)
      b = (bean.t + 5000) / 1000 + 1
      bean.rotation(b); bean.open = b; bean.list(a, 35_000)
    case (true, b) where from == 0:
      bean.header(file, 0xFFFF_FFFF)
      bean.units(file, Array(0...199))
      bean.script.append { throw BeanSync.Gone("link dropped") }
    case (true, b):  // again after the drop, from its offset; ten units lost on the way
      bean.header(file, 0xFFFF_FFFF)
      bean.units(file, Array(200...249) + Array(260...299), last: 299)
      bean.complete(file)
      bean.push(0x1882, [0])
      bean.recording = false; bean.open = 0; bean.list(b, 6000)
    case (false, a): bean.header(file, 1750 * Int64(Bean.unit)); bean.units(file, Array(from...1749), last: 1749); bean.complete(file)
    case (false, b): bean.header(file, 300 * Int64(Bean.unit)); bean.units(file, Array(from...299), last: 299); bean.complete(file)
    default: check("asked for \(file) from \(from) live \(live)", "")
    }
  }
  let one = BeanSync(tag: "D3200-0A0B", stateFile: state, chunks: chunks, io: bean)
  check(error(BeanSync.Gone.self) { try one.session(since: since, steps: "") }.description, "link dropped")
  let anchors = one.files.mapValues(\.anchor)  // the files go once deleted on the bean
  check(anchors[a]!, a * 1000, "the bean's clock: a read from 0 starts behind, its first unit is not now")
  check(bean.heldAt, nil, "closed A is pending while B records: no hold")
  check(bean.holds.isEmpty, true, "live never sets a hold")
  check((t0 / 1000 - 1...t0 / 1000 + 1).contains(Bean.u32(bean.sentOf(0x01A6), 0)), true)

  // a new session (what the phone knows of the files read back): on in B from its offset, then the backlog
  var pieces: [(Int64, [[UInt8]])] = []
  bean.script.append {  // the uploader takes every piece, then time goes on
    for f in queue(chunks) { pieces.append((startMs(f), packets(f))); try FileManager.default.removeItem(at: f) }
    bean.t += 1000
  }
  bean.script.append { bean.t += 6000 }
  let two = BeanSync(tag: "D3200-0A0B", stateFile: state, chunks: chunks, io: bean)
  bean.confirmed = { two.deletable }
  _ = error(BeanSync.Gone.self) { try two.session(since: since, steps: "") }
  check(bean.transfers, [[a, 0, 0], [b, 0, 0], [b, 200, 0], [b, 250, 0]])
  check(bean.logs.contains { $0.0 == "bean_gap" && $0.1.contains("250..259") }, true)
  var seen: [Int64: [Int64]] = [:]
  for (start, ps) in pieces {
    check(ps[0][9], 2, "stereo")
    for (i, p) in ps.dropFirst(2).enumerated() {
      let file = p[5] == UInt8(a % 251) ? a : b
      check(p[0], 0xBC, "decrypted")
      check(anchors[file]! + seqOf(p) * 20, start + Int64(i) * 20, "dated by its seq")
      seen[file, default: []].append(seqOf(p))
    }
  }
  check(seen[a]!.sorted(), Array(0...1749), "each unit once")
  check(seen[b]!.sorted(), Array(0...299))
  check(bean.heldAt, nil, "all up: no hold")
  check(bean.ends, [anchors[b]! + 300 * 20])
  check(bean.transfers.contains { $0[0] == old }, false)
  check(bean.deleted, [a, b], "whole on the server: off the bean, once")
}

/// The natural 6-hour file rollover: the old partial piece is flushed, the session ends (natural rollover), the next
/// connection goes live in the new file and the old tail is fetched once the recording stops; each unit once, dated by
/// its own file.
func rollover(late: Bool, manual: Bool = false, paused: Bool = false) {
  let dir = tmp(), chunks = dir.appendingPathComponent("chunks")
  let bean = FakeBean(t0)
  let a = t0 / 1000, b = a + 1
  bean.recording = true; bean.open = a; bean.list(a, 0)
  let sync = BeanSync(tag: "D3200-0A0B", stateFile: dir.appendingPathComponent("state.json"), chunks: chunks, io: bean)
  bean.onTransfer = { file, from, live in
    switch (file == bean.open, file) {
    case (true, a):
      bean.header(file, 0xFFFF_FFFF)
      bean.units(file, Array(0...24))
      if manual { bean.push(0x1882, [0]); bean.push(0x1882, [1]) }
      if paused { bean.push(0x1882, [2]) }
      bean.rotation(b, wait: 500)
      bean.open = b; bean.list(a, 600); bean.list(b, 0)
    case (true, b):
      check(packets(queue(chunks)[0]).count - 2, 25, "rollover flushed the old partial piece")
      check(sync.gap(sync.files[a]!), 25, "old tail remains pending")
      check(sync.files[a]!.total, -1)
      bean.header(file, 0xFFFF_FFFF)
      bean.units(file, Array(0...11))
      if late { bean.complete(a) }
      bean.units(file, Array(12...24), last: 24)
      bean.script.append {
        check(bean.logs.filter { $0.0 == "bean_xfer" && $0.1.contains("文件 \(a) 传完") }.count, 1, "old completion cannot end the new transfer")
        bean.push(0x1882, [0]); bean.complete(b)
        bean.recording = false; bean.open = 0; bean.list(b, 500)
      }
    case (false, a):
      check(from, 25)
      bean.header(file, 30 * Int64(Bean.unit))
      bean.units(file, Array(from...29), last: 29); bean.complete(file)
    default: check("asked for \(file) from \(from) live \(live)", "")
    }
  }
  let again = error(BeanSync.Again.self) { try sync.session(since: t0 - 3_600_000, steps: "") }
  check(again.description, "file \(a): rolled over to \(b)")
  check(again.naturalRollover, !manual && !paused)
  check(bean.transfers, [[a, 0, 0]], "no rejoin on the old connection")
  check(packets(queue(chunks)[0]).count - 2, 25)
  _ = error(BeanSync.Gone.self) { try sync.session(since: t0 - 3_600_000, steps: "") }
  check(bean.transfers, [[a, 0, 0], [b, 0, 0], [a, 25, 0]])
  var seen: [Int64: [Int64]] = [:]
  for piece in queue(chunks) {
    for (i, p) in packets(piece).dropFirst(2).enumerated() {
      let file = p[5] == UInt8(a % 251) ? a : b
      check(p[0], 0xBC)
      check((0...3000).contains(sync.files[file]!.anchor - file * 1000), true, "anchor near the file's start")
      check(sync.files[file]!.anchor + seqOf(p) * Bean.frameMs, startMs(piece) + Int64(i) * Bean.frameMs, "dated by its own file and seq")
      seen[file, default: []].append(seqOf(p))
    }
  }
  check(seen[a]!.sorted(), Array(0...29), "old units and recovered tail, each once")
  check(seen[b]!.sorted(), Array(0...24), "new units, including after the late completion")
  check(bean.logs.filter { $0.0 == "bean_xfer" && $0.1.contains("文件 \(a) 传完") }.count, 2)  // live, then its missing tail
  check(bean.logs.filter { $0.0 == "bean_xfer" && $0.1.contains("文件 \(b) 传完") }.count, 1)
  check(sync.gap(sync.files[a]!), nil)
}
rollover(late: false)
rollover(late: true)
rollover(late: false, manual: true)
rollover(late: false, paused: true)

/// The owner, 10-26: a bean connected records. An idle bean is started once a connection (18/82 01, nothing else of the
/// button), goes live from its new file; stopped by hand, it is not started again in the connection, nor in the
/// reconnect that cuts the head's re-pull; the next connection starts it again (here the bean only echoes: logged).
do {
  let dir = tmp()
  let since = t0 - 3_600_000
  let bean = FakeBean(t0)
  bean.startable = true
  bean.onTransfer = { file, from, _ in
    if file == bean.open {  // units 0..35 lost on the way, until the button stops it
      bean.header(file, 0xFFFF_FFFF)
      bean.units(file, Array(36...1999))
      bean.push(0x1882, [0]); bean.complete(file)
      bean.recording = false; bean.open = 0; bean.list(file, 2000 * 20)
    } else { bean.header(file, 2000 * Int64(Bean.unit)); bean.units(file, Array(from...1999), last: 1999); bean.complete(file) }
  }
  let sync = BeanSync(tag: "D3200-0A0B", stateFile: dir.appendingPathComponent("state.json"), chunks: dir.appendingPathComponent("chunks"), io: bean)
  _ = error(BeanSync.Again.self) { try sync.session(since: since, steps: "") }
  let c = t0 / 1000  // the file the start began
  check(bean.transfers, [[c, 0, 0], [c, 0, 0]])
  check(bean.starts, 1)
  check(bean.logs.filter { $0.0 == "bean_start" }.map(\.1), ["自动开始录音"])
  _ = error(BeanSync.Gone.self) { try sync.session(since: since, steps: "") }  // the app's own reconnect: the stop holds
  check(bean.starts, 1)
  check(sync.gap(sync.files[c]!), nil, "all of it on the phone")
  bean.startable = false
  bean.script.append {}  // the wait for its new file
  _ = error(BeanSync.Gone.self) { try sync.session(since: since, steps: "") }  // a new connection
  check(bean.starts, 2)
  check(bean.logs.last { $0.0 == "bean_start" }!.1.hasPrefix("自动开始录音没成功"), true)
  check(bean.sent.filter { $0[5] == 0x18 }.allSatisfy { $0 == Bean.startFrame() }, true)
}

/// Off the bean go only files the server has whole: not one with a hole, one with a piece the server refused or still
/// queued, the file being recorded, or one from before the bean was added here; a whole one, once.
do {
  let dir = tmp(), chunks = dir.appendingPathComponent("chunks"), state = dir.appendingPathComponent("state.json")
  try FileManager.default.createDirectory(at: chunks, withIntermediateDirectories: true)
  let since = t0 - 3_600_000
  let old = since / 1000 - 100, hole = t0 / 1000 - 900, refused = hole + 100, queued = hole + 200
  let whole = hole + 300, open = hole + 400, nay = hole + 500
  func f(_ id: Int64, _ h: String, tries: Int = 0) -> String { "\"\(id)\":{\"a\":\(id * 1000),\"t\":10,\"r\":\(tries),\"h\":\(h)}" }
  try ("{" + [f(old, "[[0,10]]"), f(hole, "[[0,4],[6,10]]", tries: 4), f(refused, "[[0,10]]"), f(queued, "[[0,10]]"),
              f(whole, "[[0,10]]"), f(open, "[[0,10]]"), f(nay, "[[0,10]]")].joined(separator: ",") + "}").write(to: state, atomically: true, encoding: .utf8)
  let q = chunks.appendingPathComponent("\(queued * 1000)@D3200-0A0B.ogg")
  try Data(count: 8).write(to: q)
  let bean = FakeBean(t0)
  for id in [old, hole, refused, queued, whole, nay] { bean.list(id, 200) }
  bean.refuseDelete = [nay]  // the bean says no: it stays, and is not asked again
  bean.open = open
  bean.refusedStarts = [refused * 1000 + 60]
  let sync = BeanSync(tag: "D3200-0A0B", stateFile: state, chunks: chunks, io: bean)
  bean.confirmed = { sync.deletable }
  for _ in 0..<6 { bean.script.append {} }
  bean.script.append { try FileManager.default.removeItem(at: q) }  // its last piece up
  for _ in 0..<12 { bean.script.append {} }
  _ = error(BeanSync.Gone.self) { try sync.session(since: since, steps: "") }
  check(bean.deleted, [whole, nay, queued])
  check(Set(bean.listed.map(\.0)), [old, hole, refused, nay])
  check(sync.files[nay] != nil, true)
  check(bean.logs.contains { $0.0 == "bean_delete" && $0.1.contains("\(whole)") && $0.1.contains("10 个单元") }, true)
  check(sync.files[whole] == nil, true)
  check(sync.deletable.isEmpty, true)
}

/// A piece that cannot be written (no room on the phone) loses its units, not the file: they are not marked had, so no
/// later piece spans the hole they leave (here 60..89, lost on the way), the hole is asked for again, and the file stays
/// on the bean until it is whole (PR #186 review).
do {
  let dir = tmp(), chunks = dir.appendingPathComponent("chunks"), fm = FileManager.default
  let bean = FakeBean(t0)
  let a = t0 / 1000 - 3600
  bean.list(a, 120 * 20)
  var pieces: [(Int64, [[UInt8]])] = []
  let upload: () throws -> Void = { for f in queue(chunks) { pieces.append((startMs(f), packets(f))); try fm.removeItem(at: f) } }
  bean.onTransfer = { file, from, live in
    check(live, false)
    bean.header(file, 120 * Int64(Bean.unit))
    if bean.transfers.count == 1 {
      try! fm.setAttributes([.posixPermissions: 0o555], ofItemAtPath: chunks.path)  // no room: no piece is written
      let lossy: [Int64] = Array(0..<60) + Array(90..<120)
      bean.units(file, lossy, last: 119)
      bean.script.append { try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: chunks.path); bean.complete(file) }
    } else {
      check(bean.deleted, [], "nothing deleted before the file is whole")
      bean.units(file, Array(from..<120), last: 119)
      bean.complete(file)
      bean.script.append(upload)
      for _ in 0..<8 { bean.script.append {} }
    }
  }
  let sync = BeanSync(tag: "D3200-0A0B", stateFile: dir.appendingPathComponent("state.json"), chunks: chunks, io: bean)
  bean.confirmed = { sync.deletable }
  bean.script.append {}  // the wait after the start, which this bean only echoes
  _ = error(BeanSync.Gone.self) { try sync.session(since: a * 1000 - 1000, steps: "") }
  check(bean.logs.filter { $0.0 == "bean_piece" }.count, 1, "said once")
  check(bean.transfers.count, 2)
  check(bean.transfers[1][1] <= 60, true, "60..89 asked for again")
  var seen: [Int64] = []
  for (start, ps) in pieces {
    let seqs = ps.dropFirst(2).map(seqOf)
    check(seqs, Array(seqs[0]...seqs[seqs.count - 1]), "no piece spans a hole")
    check(start, a * 1000 + seqs[0] * Bean.frameMs, "dated by its first unit")
    seen += seqs
  }
  check(seen.sorted(), Array(0..<120), "each unit once")
  check(bean.deleted, [a], "whole on the server: off the bean")
}

/// A link that stays up but slows (the iPhone in the background, 10-28): the status says how far live audio is behind
/// (live_lag_s), and bean_lag is logged once past a minute and once back within 10 s, not for every unit.
do {
  let dir = tmp()
  let bean = FakeBean(t0)
  let a = t0 / 1000
  bean.recording = true; bean.open = a; bean.list(a, 0)
  bean.onTransfer = { file, _, _ in
    bean.header(file, 0xFFFF_FFFF)
    bean.units(file, Array(0..<1800), ms: 60)  // a third of real time: 36 s of audio in 108 s, 72 s behind
    bean.units(file, Array(1800..<6000), ms: 5)  // then four times: 9 s behind at the end
  }
  let sync = BeanSync(tag: "D3200-0A0B", stateFile: dir.appendingPathComponent("state.json"), chunks: dir.appendingPathComponent("chunks"), io: bean)
  _ = error(BeanSync.Gone.self) { try sync.session(since: t0 - 3_600_000, steps: "") }
  check(bean.reports.first!["live_lag_s"] == nil, true, "not live yet when connected")
  let lags = bean.reports.compactMap { $0["live_lag_s"] as? Int64 }
  check(lags.count, 2, "a status a minute")
  check((38...42).contains(lags[0]), true, "two thirds of the first minute behind: \(lags[0])")
  check((33...39).contains(lags[1]), true, "catching up, 12 s in: \(lags[1])")
  check(bean.logs.filter { $0.0 == "bean_lag" }.map { $0.1.contains("追上了") }, [false, true])
  let lag = zip(bean.logs, bean.logFields).first { $0.0.0 == "bean_lag" }!.1
  check((15.0...18.0).contains(lag["units_s"] as! Double), true, "units a second so far: \(lag["units_s"]!)")
  check(lag.keys.contains("bean_case_charging"), true, "the bean's side with it")
  check(bean.logs.contains { $0.0 == "bean_stall" }, false)
}

/// A live transfer from a unit behind (recording for 2 min, nothing of it here yet) starts behind by what it fills:
/// no live_lag_s nor bean_lag until it has caught up, then as above; units that keep coming as recorded 2.5 s after
/// their seq says date the file anew (reanchor()), so the lag is gone a minute on.
do {
  let dir = tmp()
  let bean = FakeBean(t0 + 120_000)
  let a = t0 / 1000
  bean.recording = true; bean.open = a; bean.list(a, 0)
  bean.onTransfer = { file, _, _ in
    bean.header(file, 0xFFFF_FFFF)
    bean.units(file, Array(0..<450), ms: 60)  // slow at first: 147 s behind after 27 s (no slower for a minute: slow())
    bean.units(file, Array(450..<14000), ms: 10)  // twice real time: caught up (2.5 s) 135.5 s on
    bean.units(file, Array(14000..<18000))  // then live for 80 s
  }
  let sync = BeanSync(tag: "D3200-0A0B", stateFile: dir.appendingPathComponent("state.json"), chunks: dir.appendingPathComponent("chunks"), io: bean)
  _ = error(BeanSync.Gone.self) { try sync.session(since: t0 - 3_600_000, steps: "") }
  check(bean.transfers, [[a, 0, 0]])
  check(bean.reports.dropFirst().map { $0["live_lag_s"] as? Int64 }, [nil, nil, 2, 0], "filling the tail at the first two, caught up, then dated anew")
  check(bean.logs.contains { $0.0 == "bean_lag" }, false)
}

/// Wi-Fi fast transfer (#431): asked for while the bean records a file begun 10 min ago, none of it here. Over BLE the
/// hotspot of a new name (1A/05), its answer to wifiJoin; over the WebSocket the file from unit 0 (1A/07, no realtime
/// flag) into pieces as over BLE, until within 10 s of live; then back to BLE (Again), whose next session turns the
/// hotspot off (1A/02) and reads on from the tail, realtime=0 over BLE.
do {
  let dir = tmp(), chunks = dir.appendingPathComponent("chunks")
  let bean = FakeBean(t0)
  let a = (t0 - 600_000) / 1000
  bean.recording = true; bean.open = a; bean.list(a, 0); bean.wifiAsk = true
  bean.onTransfer = { file, from, _ in
    bean.header(file, 0xFFFF_FFFF)
    if from == 0 { bean.units(file, Array(0..<31_200), ms: 1) }  // 20× real time: within 10 s of live at about unit 31053
    if bean.onWifi { bean.wifiAsk = true }  // tapped again meanwhile (11-08): not another one after it
  }
  let sync = BeanSync(tag: "D3200-0A0B", stateFile: dir.appendingPathComponent("state.json"), chunks: chunks, io: bean)
  let again = error(BeanSync.Again.self) { try sync.session(since: t0 - 3_600_000, steps: "") }
  check(again.description.contains("追上实时"), true, again.description)
  check(bean.joins.count, 1)
  let on = bean.sentOf(0x1A05), ssid = Array(on[1..<(1 + Int(on[0]))]), pw = Array(on[(2 + Int(on[0]))...])
  check(String(decoding: ssid, as: UTF8.self), bean.joins[0].0); check(String(decoding: pw, as: UTF8.self), bean.joins[0].1)
  check(bean.joins[0].2, [1, 0x2B, 0xA8, 0xC0, 0xBB, 1])
  check(bean.onWifi, false, "the hotspot left")
  check(bean.transfers, [[a, 0, 0]])
  let got = queue(chunks).flatMap { packets($0).dropFirst(2) }.map(seqOf)
  check(got.first, 0); check(got == Array(0..<Int64(got.count)), true, "each unit once, in order")
  check((31_053...31_060).contains(got.count), true, "up to live: \(got.count)")
  check(bean.logs.contains { $0.0 == "bean_wifi_done" }, true)
  check(bean.sent.contains { $0[5] == 0x1A && $0[6] == 0x02 }, false, "off in the next session")
  let n = bean.sent.count
  _ = error(BeanSync.Gone.self) { try sync.session(since: t0 - 3_600_000, steps: "") }
  check(bean.sent[n...].map { Int($0[5]) << 8 | Int($0[6]) }.prefix(4), [0x2E01, 0x0101, 0x01A6, 0x1A02])
  check(bean.sent.filter { $0[5] == 0x1A && $0[6] == 0x05 }.count, 1, "the tap during it dropped")
  check(bean.logs.contains { $0.0 == "bean_wifi_ask" }, true)
  check(bean.transfers.count, 2); check(bean.transfers[1][0], a); check(bean.transfers[1][1], Int64(got.count)); check(bean.transfers[1][2], 0)
}

/// Nothing lacking (a file begun just now): no hotspot.
do {
  let dir = tmp()
  let bean = FakeBean(t0)
  bean.recording = true; bean.open = t0 / 1000; bean.list(bean.open, 0); bean.wifiAsk = true
  bean.onTransfer = { file, _, _ in bean.header(file, 0xFFFF_FFFF) }
  let sync = BeanSync(tag: "D3200-0A0B", stateFile: dir.appendingPathComponent("state.json"), chunks: dir.appendingPathComponent("chunks"), io: bean)
  for _ in 0..<2 { do { try sync.session(since: t0 - 3_600_000, steps: "") } catch {} }
  check(bean.sent.contains { $0[5] == 0x1A && $0[6] == 0x05 }, false, "no hotspot for nothing")
  check(bean.logs.contains { $0.0 == "bean_wifi_skip" }, true)
}

/// A stop by hand holds across the fast transfer's reconnect (#324: a new connection starts the bean, the app's own
/// reconnects do not): the bean stopped by its button, then the transfer reads the closed file over Wi-Fi; the next
/// session sends no 18/82, also when BLE goes before the 1A/05 answer.
for dropFirst in [false, true] {
  let dir = tmp()
  let bean = FakeBean(t0)
  let a = (t0 - 60_000) / 1000
  bean.recording = true; bean.open = a; bean.list(a, 0); bean.startable = true
  bean.onTransfer = { file, from, _ in
    // live over BLE: nothing until the stop; then busy (code 4), the file left for Wi-Fi
    if !bean.onWifi { bean.header(file, 0xFFFF_FFFF, code: bean.recording ? 0 : 4); return }
    bean.header(file, 3000 * Int64(Bean.unit)); bean.units(file, Array(from..<3000), last: 2999, ms: 1); bean.complete(file)
  }
  bean.script = [
    { bean.push(0x1882, [0]); bean.complete(a); bean.recording = false; bean.open = 0; bean.list(a, 60_000) },
    { bean.wifiAsk = true },
  ]
  let sync = BeanSync(tag: "D3200-0A0B", stateFile: dir.appendingPathComponent("state.json"), chunks: dir.appendingPathComponent("chunks"), io: bean)
  bean.dropOnWifi = dropFirst
  let again = error(BeanSync.Again.self) { try sync.session(since: t0 - 3_600_000, steps: "") }
  check(again.description.contains(dropFirst ? "BLE went" : "都取到了"), true, again.description)
  check(bean.starts, 0, "it was recording: no start")
  _ = error(BeanSync.Gone.self) { try sync.session(since: t0 - 3_600_000, steps: "") }
  check(bean.starts, 0, "stopped by hand: the reconnect after Wi-Fi starts nothing")
  check(bean.sent.contains { $0[5] == 0x1A && $0[6] == 0x02 }, true)
  _ = error(BeanSync.Gone.self) { try sync.session(since: t0 - 3_600_000, steps: "") }
  check(bean.starts, 1, "a link lost (Gone): the next connection starts it again")
}

/// The session's thread lives as long as the link: what Foundation autoreleases in its loop (the queue listed every 5 s,
/// each piece and the state written) is drained every pass, so its memory stays flat. 1.5.9 had no pool there: +0.87
/// MB a minute on the iPhone, 10-28.
do {
  func footprintMB() -> Double {
    var info = task_vm_info_data_t()
    var n = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
    _ = withUnsafeMutablePointer(to: &info) { $0.withMemoryRebound(to: integer_t.self, capacity: Int(n)) { task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &n) } }
    return Double(info.phys_footprint) / 1_048_576
  }
  let dir = tmp(), chunks = dir.appendingPathComponent("chunks")
  let bean = FakeBean(t0)
  let a = t0 / 1000
  bean.recording = true; bean.open = a; bean.list(a, 0)
  let minutes = 60
  bean.onTransfer = { file, _, _ in
    bean.header(file, 0xFFFF_FFFF)
    for s in 0..<(minutes * 60) {  // a second of audio a step; the uploader takes what is queued now and then
      bean.script.append {
        bean.units(file, Array(Int64(s) * 50..<Int64(s + 1) * 50))
        if s % 20 == 0 { for f in queue(chunks) { try FileManager.default.removeItem(at: f) } }
      }
    }
  }
  let sync = BeanSync(tag: "D3200-0A0B", stateFile: dir.appendingPathComponent("state.json"), chunks: chunks, io: bean)
  var mb = 0.0
  let done = DispatchSemaphore(value: 0)
  Thread.detachNewThread {  // as BeanLink's: no pool of its own
    let before = footprintMB()
    _ = error(BeanSync.Gone.self) { try sync.session(since: t0 - 3_600_000, steps: "") }
    mb = footprintMB() - before
    done.signal()
  }
  done.wait()
  print(String(format: "bean session, %d min of live audio: %.1f MB kept", minutes, mb))
  check(bean.t - t0 >= Int64(minutes) * 60_000, true, "the hour went through")
  check(mb < 5, true, "a pool per pass keeps the session's memory flat")
}

// The file being recorded, read from where the phone lacks it on to live (plan(), Bean.kt; the owner, 10-27)

/// The file being recorded, begun `s` seconds ago, of which the phone has `have`; the bean as the 10-27 probe found it
/// (FakeBean.answer): `units` of it a 1A/07, then the link goes.
final class Rec {
  let bean: FakeBean, sync: BeanSync, id: Int64, chunks: URL
  init(_ bean: FakeBean, _ sync: BeanSync, _ id: Int64, _ chunks: URL) { self.bean = bean; self.sync = sync; self.id = id; self.chunks = chunks }
  var f: BeanSync.F { sync.files[id]! }
  var have: [[Int64]] { f.have.map { [$0.0, $0.1] } }
  var reasons: [String] { bean.logFields.compactMap { $0["reason"] as? String } }
}
func rec(_ s: Int64, _ have: [(Int64, Int64)], watched: Bool, units: Int = 600, total: Int64 = -1, anchors: [(Int64, Int64)] = []) -> Rec {
  let dir = tmp(), state = dir.appendingPathComponent("state.json")
  let bean = FakeBean(t0)
  bean.openAt = t0 - s * 1000; bean.open = bean.openAt / 1000; bean.recording = true; bean.list(bean.open, 0)
  bean.pageOpen = watched
  if !have.isEmpty {
    func pairs(_ l: [(Int64, Int64)]) -> String { "[" + l.map { "[\($0.0),\($0.1)]" }.joined(separator: ",") + "]" }
    try! "{\"\(bean.open)\":{\"a\":\(bean.openAt),\"t\":\(total),\"r\":0,\"h\":\(pairs(have)),\"p\":\(pairs(anchors))}}"
      .write(to: state, atomically: true, encoding: .utf8)
  }
  bean.onTransfer = { file, from, realtime in bean.answer(file, from, realtime); bean.more(units) }
  let chunks = dir.appendingPathComponent("chunks")
  return Rec(bean, BeanSync(tag: "D3200-0A0B", stateFile: state, chunks: chunks, io: bean), bean.open, chunks)
}
/// Only the five reads went out: no start (recording), no delete (the file is open).
func onlyReads(_ bean: FakeBean, line: Int = #line) {
  check(bean.sent.allSatisfy { Bean.sends.contains(Int($0[5]) << 8 | Int($0[6])) }, true, "only reads", line: line)
  check(bean.deleted.isEmpty, true, "nothing deleted", line: line)
}
/// Sessions one after another (BeanLink connects again after each) until one ends with `end`.
func until(_ r: Rec, _ end: String = "done", since: Int64 = 0, line: Int = #line) {
  for _ in 0..<20 {
    do { try r.sync.session(since: since, steps: "") } catch { if "\(error)" == end { return } }
  }
  check("never \(end)", "", line: line)
}
func session<E: Error>(_ r: Rec, _ type: E.Type, since: Int64 = 0, line: Int = #line) -> E {
  error(type, line: line) { try r.sync.session(since: since, steps: "") }
}

/// The live page open, the tail hole 50 s: read with realtime=0 from its start, caught up faster than real time, then
/// on live in the same transfer; no hole left.
do {
  let r = rec(190, [(0, 7000)], watched: true, units: 6000)  // the bean at unit 9500
  _ = session(r, BeanSync.Gone.self)
  check(r.bean.transfers, [[r.id, 7000, 0]])
  check(r.reasons, ["tail_short"])
  check(r.bean.logFields.first { $0["reason"] != nil }!["hole_units"] as? Int64, 2500)
  check(r.bean.logs.contains { $0.1.contains("首单元 7000（请求从 7000 起）") }, true)
  check(r.have, [[0, 13000]])
  check((13000 * 20...13000 * 20 + 100).contains(r.bean.t - r.bean.openAt), true, "caught up: the last units came as recorded")
  onlyReads(r.bean)
}

/// The live page open, the tail hole 100 s: live now (realtime=1, from where the bean is), the hole kept.
do {
  let r = rec(240, [(0, 7000)], watched: true, units: 300)  // the bean at unit 12000
  _ = session(r, BeanSync.Gone.self)
  check(r.bean.transfers, [[r.id, 7000, 1]])
  check(r.reasons, ["live_now"])
  check(r.have, [[0, 7000], [12000, 12300]])
  onlyReads(r.bean)
}

/// Nobody watching: the older hole first (realtime=0), cut where the long stretch the phone has begins, then the tail
/// from its own offset, on into live.
do {
  let r = rec(80, [(0, 100), (400, 2500)], watched: false, units: 3000)  // the bean at unit 4000
  _ = session(r, BeanSync.Again.self)
  check(r.have, [[0, 2500]])
  _ = session(r, BeanSync.Gone.self)
  check(r.bean.transfers, [[r.id, 100, 0], [r.id, 2500, 0]])
  check(r.reasons, ["fill_hole", "fill_tail"])
  check(r.have, [[0, 5500]])
  onlyReads(r.bean)
}

/// The live page opens while a fill is over a minute behind: it ends (the link goes), the next start is live now and
/// what was not read stays on the bean.
do {
  let r = rec(202, [(0, 100)], watched: false)  // the bean at unit 10100
  r.bean.script.append { r.bean.pageOpen = true; r.bean.more(300) }
  check(session(r, BeanSync.Again.self).description.hasPrefix("file \(r.id): the live page opened"), true)
  check(r.f.have.count, 1)
  let read = r.f.have[0].1
  check((700...1000).contains(read), true, "read \(read)")
  _ = session(r, BeanSync.Gone.self)
  check(r.bean.transfers, [[r.id, 100, 0], [r.id, read, 1]])
  check(r.reasons, ["fill_tail", "watched_abort", "live_now"])
  check(r.f.have.count, 2, "the hole stays")
  onlyReads(r.bean)
}

/// A new file (the bean rolled over, the phone back 1.5 s later) is read from unit 0, watched or not, and dated by the
/// bean's clock (its first unit came late, not as recorded). Before (iOS 1.6.0) it was read with realtime=1 and its
/// first seconds waited for the stop.
for watched in [false, true] {
  let r = rec(10, [], watched: watched)
  let a = r.id, b = a + 11
  r.bean.script.append { r.bean.t = b * 1000; r.bean.open = b; r.bean.openAt = b * 1000; r.bean.list(b, 0); r.bean.rotation(b) }
  check(session(r, BeanSync.Again.self).naturalRollover, true)
  r.bean.t += 1500
  _ = session(r, BeanSync.Gone.self)
  check(r.bean.transfers, [[a, 0, 0], [b, 0, 0]])
  check(r.reasons, Array(repeating: watched ? "tail_short" : "fill_tail", count: 2))
  check(r.bean.logs.contains { $0.1.contains("文件 \(b) 开始：首单元 0（请求从 0 起）") }, true)
  check(r.sync.files[b]!.have.first!.0, 0)
  check(r.sync.files[b]!.anchor, b * 1000)
  onlyReads(r.bean)
}

/// A read the bean honoured (its first unit the offset asked) clears the file's misses: only misses in a row switch it
/// to realtime=1.
do {
  let r = rec(100, [(0, 1000)], watched: false)
  var n = 0
  r.bean.onTransfer = { file, from, realtime in
    n += 1
    switch n {
    case 3: r.bean.answer(file, from, realtime); r.bean.more(30); r.bean.script.insert({ throw BeanSync.Gone("drop") }, at: 0)
    case 6: r.bean.answer(file, from, realtime); r.bean.more(30); r.bean.script = [{ throw BeanSync.Gone("done") }]
    default: r.bean.header(file, 0xFFFF_FFFF, code: 2)
    }
  }
  for _ in 0..<300 { r.bean.script.append {} }
  until(r)
  check(r.bean.transfers.map { $0[2] }, Array(repeating: 0, count: 6))
  check(r.reasons, Array(repeating: "fill_tail", count: 6))
  check(r.have, [[0, 1060]])
}

/// The live page closes during a live read it began with a hole left behind (live_now): the read ends, the hole is
/// filled with a cut, then the tail, on live.
do {
  let r = rec(240, [(0, 7000)], watched: true, units: 6000)  // the bean at unit 12000
  r.bean.script.append { r.bean.pageOpen = false; r.bean.more(300) }
  check(session(r, BeanSync.Again.self).description.hasPrefix("file \(r.id): the live page closed"), true)
  _ = session(r, BeanSync.Again.self)  // the hole, cut where the live stretch begins
  check(r.have, [[0, 18003]])
  _ = session(r, BeanSync.Gone.self)
  check(r.bean.transfers, [[r.id, 7000, 1], [r.id, 7000, 0], [r.id, 18003, 0]])
  check(r.reasons, ["live_now", "watched_end", "fill_hole", "fill_tail"])
  check(r.bean.logFields.first { $0["reason"] as? String == "watched_end" }!["hole_units"] as? Int64, 5000)
  check(r.have, [[0, 24003]])
  onlyReads(r.bean)
}

/// A live read not begun for the page (live_fallback, a file begun before the bean was added here) goes on when the
/// page closes, even with a hole behind it.
do {
  let r = rec(100, [(0, 1000)], watched: false)
  var refused = 0
  r.bean.onTransfer = { file, from, realtime in
    if realtime {
      r.bean.pageOpen = false; r.bean.answer(file, from, true); r.bean.more(30)
      r.bean.script = [{ throw BeanSync.Gone("done") }]
    } else { r.bean.header(file, 0xFFFF_FFFF, code: 2); refused += 1; if refused == 3 { r.bean.pageOpen = true } }
  }
  for _ in 0..<300 { r.bean.script.append {} }
  until(r)
  check(r.reasons, ["fill_tail", "fill_tail", "fill_tail", "live_fallback"])
  let p = rec(100, [], watched: true, units: 30)
  p.bean.script.append { p.bean.pageOpen = false; p.bean.more(30) }
  _ = session(p, BeanSync.Gone.self, since: (p.id + 50) * 1000)
  check(p.reasons, ["live_now"])
  check(p.have, [[5000, 5060]])
}

/// A bean that ignored the offset of the open file (starting where it is) cuts each fill of an older hole at once:
/// three tries, then realtime=1 for that file.
do {
  let r = rec(80, [(0, 100), (400, 2500)], watched: false, units: 30)
  r.bean.onTransfer = { file, _, _ in r.bean.answer(file, 0, true); r.bean.more(30) }
  for _ in 0..<3 { _ = session(r, BeanSync.Again.self) }
  _ = session(r, BeanSync.Gone.self)
  check(r.bean.transfers.prefix(3).map { $0[1] }, [100, 100, 100])
  check(r.reasons, ["fill_hole", "fill_hole", "fill_hole", "live_fallback"])
  check(r.bean.transfers[3][2], 1)
  onlyReads(r.bean)
}

/// A bean that refuses realtime=0 for the file it records (code 2, code 3, no answer, or a header and no unit in 30 s):
/// three tries, then live as before (realtime=1); the holes stay for after the stop.
for code: UInt8? in [2, 3, nil, 0] {
  let r = rec(100, [(0, 1000)], watched: false)
  r.bean.onTransfer = { file, from, realtime in
    if realtime {
      r.bean.answer(file, from, true); r.bean.more(30)
      r.bean.script = [{ throw BeanSync.Gone("done") }]
    } else if let code { r.bean.header(file, 0xFFFF_FFFF, code: code) }
  }
  for _ in 0..<300 { r.bean.script.append {} }
  until(r)  // the live guard may end a session meanwhile (Again): BeanLink connects again
  check(r.bean.transfers.map { $0[2] }, [0, 0, 0, 1], "code \(String(describing: code))")
  check(r.reasons, ["fill_tail", "fill_tail", "fill_tail", "live_fallback"])
  check(r.f.have.first!.1, 1000)
  check(r.f.have.count, 2)
}

/// A realtime=0 read of the open file slower than real time (11-08: 0.67, one unit every 30 ms) never gets to live: a
/// minute on, over a minute behind and no nearer, that file goes realtime=1 (live_fallback), its hole after the stop.
do {
  let r = rec(100, [(0, 1000)], watched: false)  // the bean at unit 5000
  r.bean.onTransfer = { file, from, realtime in
    if realtime {
      r.bean.answer(file, from, true); r.bean.more(30)
      r.bean.script = [{ throw BeanSync.Gone("done") }]
    } else { r.bean.header(file, 0xFFFF_FFFF); r.bean.units(file, Array(from..<(from + 6000)), ms: 30) }
  }
  until(r)
  check(r.reasons, ["fill_tail", "too_slow", "live_fallback"])
  check(r.bean.transfers.map { $0[2] }, [0, 1])
  let slow = r.bean.logFields.first { $0["reason"] as? String == "too_slow" }!
  check(slow["pace"] as? Double, 0.67)
  check(slow["units_s"] as? Double, 33.3)
  check(slow.keys.contains("bean_battery"), true, "the bean's side with it")
  check((5000...5100).contains(slow["hole_units"] as! Int64), true, "\(slow["hole_units"]!) units behind")
  check(r.f.have.first!.0, 0)
  check(r.f.have.count, 2, "the hole stays for after the stop")
}

/// Closed files wait for the stop even while the open file is read or the page is open (11-08: 3.5 h of a closed file
/// said 补传中); the open file's hole alone says why it waits.
check(BeanSync.owedFields(now: 6000, closed: 600_000, idle: 0, watched: false, live: false, pace: 2)["backlog_held"] as? String, "recording")
check(BeanSync.owedFields(now: 6000, closed: 600_000, idle: 0, watched: true, live: false, pace: 2)["backlog_held"] as? String, "recording")
check(BeanSync.owedFields(now: 6000, closed: 0, idle: 0, watched: true, live: false, pace: 2)["backlog_held"] as? String, "live")
check(BeanSync.owedFields(now: 6000, closed: 0, idle: 0, watched: false, live: false, pace: 2)["backlog_held"] is NSNull, true)

/// The open file with its length known and all of it on the phone (a live header that carried a size, or a unit with the
/// end flag, kept in the state file): nothing older to fill, on from the tail; before, plan() crashed the app here
/// (PR #230 review) at every connection.
do {
  let r = rec(150, [(0, 7000)], watched: false, total: 7000)  // the bean at unit 7500
  _ = session(r, BeanSync.Gone.self)
  check(r.bean.transfers, [[r.id, 7000, 0]])
  check(r.reasons, ["fill_tail"])
}

/// A file the bean began before it was added here: live only (realtime=1), its start is not this phone's (10-14).
do {
  let r = rec(100, [], watched: false, units: 30)  // the bean at unit 5000
  _ = session(r, BeanSync.Gone.self, since: (r.id + 50) * 1000)
  check(r.bean.transfers, [[r.id, 0, 1]])
  check(r.reasons, ["live_now"])
  check(r.have, [[5000, 5030]])
}

/// Code 3 (nothing from that offset) for the file being recorded is not its end: no length, the rest still due.
do {
  let r = rec(10, [(0, 400)], watched: false)
  r.bean.onTransfer = { file, _, _ in r.bean.header(file, 0xFFFF_FFFF, code: 3) }
  _ = session(r, BeanSync.Gone.self)
  check(r.f.total, -1)
  check(r.sync.gap(r.f), 400)
}

/// 10-25 T2: live, the link drops (the phone's Bluetooth off 30 s), back: the file from where the phone lacks it on to
/// live, the bean stops; the tail on the same connection; then the app is killed before the pieces are up. The
/// recording's end still goes to the server, once, after all of it is up, and only then is the file deleted.
do {
  let dir = tmp(), chunks = dir.appendingPathComponent("chunks"), state = dir.appendingPathComponent("state.json")
  let bean = FakeBean(t0)
  let c = t0 / 1000
  bean.recording = true; bean.open = c; bean.openAt = t0; bean.list(c, 0)
  var live = 0
  bean.onTransfer = { file, from, realtime in
    if file == bean.open && live == 0 {
      live += 1
      bean.answer(file, from, realtime); bean.more(1092)
      bean.script.append { throw BeanSync.Gone("link gone") }
    } else if file == bean.open {
      bean.answer(file, from, realtime); bean.more(5688 - Int(from))
      bean.push(0x1882, [0]); bean.complete(file)
      bean.recording = false; bean.open = 0; bean.list(c, 5699 * 20)
    } else {
      bean.header(file, 5699 * Int64(Bean.unit)); bean.units(file, Array(from...5698), last: 5698); bean.complete(file)
      if from > 0 { bean.script.append { throw BeanSync.Gone("app killed") } }
    }
  }
  let since = t0 - 3_600_000
  let sync = BeanSync(tag: "D3200-0A0B", stateFile: state, chunks: chunks, io: bean)
  bean.confirmed = { sync.deletable }
  check(error(BeanSync.Gone.self) { try sync.session(since: since, steps: "") }.description, "link gone")
  bean.t += 30_000
  check(error(BeanSync.Gone.self) { try sync.session(since: since, steps: "") }.description, "app killed")
  check(bean.transfers, [[c, 0, 0], [c, 1092, 0], [c, 5688, 0]])
  let anchor = sync.files[c]!.anchor
  check(bean.ends, [], "not yet: its pieces are not up")
  check(bean.deleted, [], "nothing off the bean before the server has it")
  let again = BeanSync(tag: "D3200-0A0B", stateFile: state, chunks: chunks, io: bean)  // the app back
  bean.confirmed = { again.deletable }
  bean.script.append { for f in queue(chunks) { try FileManager.default.removeItem(at: f) }; bean.t += 1000 }  // all of it goes up
  for _ in 0..<12 { bean.script.append {} }
  _ = error(BeanSync.Gone.self) { try again.session(since: since, steps: "") }
  check(bean.ends, [anchor + 5699 * 20], "the end, once")
  check(bean.transfers.count, 3)
  check(bean.deleted, [c])
}

/// The pieces in the queue: start, first seq, units.
func piecesIn(_ chunks: URL) -> [(start: Int64, seq: Int64, n: Int64)] {
  queue(chunks).map { f in let a = packets(f).dropFirst(2); return (startMs(f), seqOf(a.first!), Int64(a.count)) }
}

/// #233: a bean whose units take 20.015 ms by the phone's clock (750 ppm slow, as one was for half an hour on 11-05),
/// live 30 min, the link gone 2 min, then back (the app restarted: the file's anchors from the state file), with each
/// slice the app uses (1 s in front, 20 s iOS in the background, 60 s Android with the screen off): each live piece
/// within 60 ms of when its first unit was recorded, those read on return (the 2 min and the catch-up) by the anchor
/// from before, off by what the bean drifted meanwhile (by seq × 20 ms alone the last were 1.5 s early); each piece
/// within 10 ms of where the one before ends, so the server's decoder goes on from it (audio.before()); bean_clock
/// at most once in 10 min.
for slice in [50, 1000, 3000] {
  let dir = tmp(), chunks = dir.appendingPathComponent("chunks"), state = dir.appendingPathComponent("state.json")
  let bean = FakeBean(t0)
  bean.slice = slice
  bean.unitUs = 20_015; bean.openAt = t0 - 1000; bean.open = bean.openAt / 1000; bean.recording = true; bean.list(bean.open, 0)
  var n = 90_000
  bean.onTransfer = { file, from, realtime in bean.answer(file, from, realtime); bean.more(n) }
  let one = BeanSync(tag: "D3200-0A0B", stateFile: state, chunks: chunks, io: bean)
  _ = error(BeanSync.Gone.self) { try one.session(since: 0, steps: "") }
  let moves = one.files[bean.open]!.anchors.count
  check((100...300).contains(moves), true, "1.35 s in steps of 5–9 ms: \(moves)")
  check((1...3).contains(bean.logs.filter { $0.0 == "bean_clock" }.count), true, "bean_clock \(bean.logs.filter { $0.0 == "bean_clock" }.count)")
  bean.t += 120_000; n = 12_000
  let again = BeanSync(tag: "D3200-0A0B", stateFile: state, chunks: chunks, io: bean)
  check(again.files[bean.open]!.anchors.map { [$0.0, $0.1] }, one.files[bean.open]!.anchors.map { [$0.0, $0.1] }, "kept in the state file")
  _ = error(BeanSync.Gone.self) { try again.session(since: 0, steps: "") }
  check(bean.transfers, [[bean.open, 0, 0], [bean.open, 90_000, 0]])
  let ps = piecesIn(chunks)
  check(ps.reduce(Int64(0)) { $0 + $1.n }, 102_000, "each unit once")
  var worst: Int64 = 0, back: Int64 = 0, end: Int64 = 0
  for p in ps {
    let off = abs(p.start - (bean.openAt + p.seq * bean.unitUs / 1000))
    if (90_000..<102_000).contains(p.seq) { back = max(back, off) } else { worst = max(worst, off) }
    check(end == 0 || abs(p.start - end) < 10, true, "slice \(slice): piece at \(p.seq) \(p.start - end) ms after the one before")
    end = p.start + p.n * 20
  }
  check(worst <= 60, true, "slice \(slice): live pieces within 60 ms (a minute of 750 ppm) of their time: \(worst) ms")
  check(back <= 250, true, "slice \(slice): read on return within 0.25 s: \(back) ms")
  print("drift check, slice \(slice / 50) s: live \(worst) ms, on return \(back) ms, \(ps.count) pieces")
}

/// A hole of the file being recorded filled later (#230, #187) is dated by the anchors in force at its units, not the
/// latest: each piece by one of them.
do {
  let a = t0 - 80_000
  let r = rec(80, [(0, 1000), (2000, 4000)], watched: false, units: 1500, anchors: [(1525, a + 300), (2500, a + 900)])
  _ = session(r, BeanSync.Again.self)
  check(r.have, [[0, 4000]])
  let ps = piecesIn(r.chunks)
  check(ps.reduce(Int64(0)) { $0 + $1.n }, 1000)
  for p in ps {
    check(p.start, (p.seq < 1525 ? a : a + 300) + p.seq * 20, "unit \(p.seq)")  // 1525: inside a 50-unit slice
    check(p.seq < 1525 && p.seq + p.n > 1525, false, "a piece across 1525")
  }
  check(r.f.at(2600), a + 900 + 2600 * 20)
}

print("bean check: all passed")
