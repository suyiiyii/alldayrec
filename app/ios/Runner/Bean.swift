// The Feishu recorder bean (Anker soundcore D3200, firmware 4.92) on iOS: port of the Android app's Bean.kt, its BLE
// protocol as bytes in and bytes out (no CoreBluetooth here: BeanLink.swift carries the bytes, app/ios/BeanCheck runs
// BeanSync against a fake bean). Ported from ws/soundcore (0BSD, Will Smidlein) and Eliqi797/open-feishu-recording-bean
// (MIT, Copyright (c) 2026 Eliqi797); see THIRD_PARTY_NOTICES.md. Not affiliated with Anker or Feishu.
// Foundation, CryptoKit (P-256, HMAC) and CommonCrypto (AES-CTR): system frameworks, no dependency.
import CommonCrypto
import CryptoKit
import Foundation

enum Bean {
  static let service = "020cf5da-0000-1000-8000-00805f9b34fb"
  static let write = "00007777-0000-1000-8000-00805f9b34fb"
  static let notify = "00008888-0000-1000-8000-00805f9b34fb"
  static let unit = 166        // a transport unit: seq u32 LE, flags, 160 B of encrypted Opus, a pad byte
  static let frameMs: Int64 = 20  // the Opus packet in a unit: 20 ms of 16 kHz stereo, 160 B (64 kbps CBR)
  static let pieceUnits = 3000  // a backlog piece: 60 s
  static let maxSeq: Int64 = 429_496_729  // seq × 10 still fits the counter's 32 bits

  /// The commands a bean is sent: its info (01/01), its clock (01/A6), the key exchange (2E/01), its files (1A/0E), a
  /// file's audio (1A/07); a delete (1A/10, DELETE) only of a file the server has whole (delete()); and the start of a
  /// recording (18/82 01, START: start()), never its stop (00) or pause (02). Nothing that binds or unbinds (0B/xx),
  /// turns on Wi-Fi (1A/05), resets, updates firmware (FF/xx, and never the OTA service) or reads device logs.
  static let sends: Set<Int> = [0x0101, 0x01A6, 0x2E01, 0x1A0E, 0x1A07]
  static let deleteCmd = 0x1A10
  static let startCmd = 0x1882

  struct Fail: Error, CustomStringConvertible { let description: String; init(_ s: String) { description = s } }

  static func isBean(_ name: String?) -> Bool { name?.range(of: "^D3200-[0-9A-F]{4}$", options: .regularExpression) != nil }

  /// A bean's name from its advertisement's manufacturer data (iOS gives company id and payload as one buffer, as the
  /// SDK reads them; the bean's MAC first): the address it advertises from changes, its MAC does not.
  static func name(_ mfr: [UInt8]) -> String? { mfr.count < 6 ? nil : String(format: "D3200-%02X%02X", mfr[4], mfr[5]) }

  /// The advertisement's flags byte (bit 7: bound to a Feishu account), -1 without one.
  static func flags(_ mfr: [UInt8]) -> Int { mfr.count > 6 ? Int(mfr[6]) : -1 }

  //
  // Frames: phone to bean 08 EE 00 00 00 type id len16 payload sum8, bean to phone 09 FF 00 00 status type id len16
  // payload sum8; len counts the whole frame, sum8 every byte before it
  //

  struct Frame {
    let type: Int, id: Int, status: Int, payload: [UInt8]
    var cmd: Int { type << 8 | id }
    /// Answered as done: status 1 (device commands), else its low nibble 1.
    var ok: Bool { type == 0x01 ? status == 1 : status & 0x0F == 1 }
  }

  static func frame(_ type: Int, _ id: Int, _ payload: [UInt8] = []) throws -> [UInt8] {
    guard sends.contains(type << 8 | id) else { throw Fail(String(format: "%02X/%02X is never sent to a bean", type, id)) }
    return build(type, id, payload)
  }

  private static func build(_ type: Int, _ id: Int, _ payload: [UInt8]) -> [UInt8] {
    let n = payload.count + 10
    let head: [UInt8] = [0x08, 0xEE, 0, 0, 0, UInt8(truncatingIfNeeded: type), UInt8(truncatingIfNeeded: id),
                         UInt8(truncatingIfNeeded: n), UInt8(truncatingIfNeeded: n >> 8)]
    let b = head + payload
    return b + [sum(b, 0, b.count)]
  }

  /// A frame on its way to the radio is one of `sends`, the delete of a file in `confirmed`, or the start: checked again
  /// right before the write (BeanSync.send, BeanLink.write).
  static func allowed(_ f: [UInt8], _ confirmed: Set<Int64> = []) -> Bool {
    guard f.count >= 10, f[0] == 0x08, f[1] == 0xEE else { return false }
    let c = Int(f[5]) << 8 | Int(f[6])
    let ok = sends.contains(c) || c == deleteCmd && f.count == 14 && confirmed.contains(u32(f, 9)) || c == startCmd && f.count == 11 && f[9] == 1
    return ok && u16(f, 7) == f.count && f[f.count - 1] == sum(f, 0, f.count - 1)
  }

  private static func sum(_ b: [UInt8], _ from: Int, _ to: Int) -> UInt8 { b[from..<to].reduce(0, &+) }

  static func info() -> [UInt8] { build(0x01, 0x01, []) }
  /// The bean's clock: unix seconds and the time zone's hours; its file ids are its clock as each file began.
  static func clock(_ unixS: Int64, _ tzHours: Int) -> [UInt8] { build(0x01, 0xA6, le32(unixS) + [UInt8(truncatingIfNeeded: tzHours)]) }
  static func hello(_ publicKey: [UInt8]) -> [UInt8] { build(0x2E, 0x01, publicKey) }
  static func list(_ page: Int) -> [UInt8] { build(0x1A, 0x0E, u16le(page)) }
  /// A file's audio from `offset` bytes on (whole units); realtime: the file being recorded, as it is.
  static func transfer(_ file: Int64, _ offset: Int64, _ realtime: Bool) -> [UInt8] {
    build(0x1A, 0x07, le32(offset) + le32(file) + [realtime ? 1 : 0])
  }
  /// Deletes a file on the bean: only one in `confirmed`, the files the server has whole (BeanSync.deletable).
  static func delete(_ file: Int64, _ confirmed: Set<Int64>) throws -> [UInt8] {
    guard confirmed.contains(file) else { throw Fail("file \(file) is not whole on the server: never deleted") }
    return build(0x1A, 0x10, le32(file))
  }
  /// Starts recording, as a press of the button does when the bean is idle, but silently (10-25: no vibration).
  static func startFrame() -> [UInt8] { build(0x18, 0x82, [1]) }

  /// Frames out of notifications: one may span several, junk before one is skipped, as is a frame whose sum is off.
  final class Reader {
    private var buf: [UInt8] = []
    func push(_ b: [UInt8]) -> [Frame] {
      buf += b
      var out: [Frame] = []
      var i = 0
      while buf.count - i >= 10 {
        let n = u16(buf, i + 7)
        if buf[i] != 0x09 || buf[i + 1] != 0xFF || buf[i + 2] != 0 || buf[i + 3] != 0 || n < 10 { i += 1; continue }
        if buf.count - i < n { break }
        if sum(buf, i, i + n - 1) != buf[i + n - 1] { i += 1; continue }
        out.append(Frame(type: Int(buf[i + 5]), id: Int(buf[i + 6]), status: Int(buf[i + 4]), payload: Array(buf[(i + 9)..<(i + n - 1)])))
        i += n
      }
      buf = Array(buf[i...])
      return out
    }
  }

  //
  // Keys: P-256 ECDH, the session key by HKDF-SHA256 (salt and info 01 02 03), each file's key wrapped in it (AES-CTR)
  //

  static func keys() -> P256.KeyAgreement.PrivateKey { P256.KeyAgreement.PrivateKey() }

  /// An uncompressed P-256 point: 04, X, Y.
  static func pub(_ k: P256.KeyAgreement.PrivateKey) -> [UInt8] { [UInt8](k.publicKey.x963Representation) }

  static func shared(_ mine: P256.KeyAgreement.PrivateKey, _ theirs: [UInt8]) throws -> [UInt8] {
    guard theirs.count >= 65, theirs[0] == 4 else { throw Fail("not a P-256 point") }
    let p = try P256.KeyAgreement.PublicKey(x963Representation: theirs[0..<65])
    return try mine.sharedSecretFromKeyAgreement(with: p).withUnsafeBytes { [UInt8]($0) }
  }

  /// The session key from the bean's answer to hello: its public key (65 B) and the secret it got (32 B), ours too.
  static func session(_ mine: P256.KeyAgreement.PrivateKey, _ reply: [UInt8]) throws -> [UInt8] {
    guard reply.count >= 97 else { throw Fail("key exchange answer of \(reply.count) B") }
    let s = try shared(mine, reply)
    guard s == Array(reply[65..<97]) else { throw Fail("the bean's shared secret is not ours") }
    return hkdf(s)
  }

  static func hkdf(_ shared: [UInt8]) -> [UInt8] { hmac(hmac([1, 2, 3], shared), [1, 2, 3, 1]) }
  private static func hmac(_ key: [UInt8], _ v: [UInt8]) -> [UInt8] {
    [UInt8](HMAC<SHA256>.authenticationCode(for: v, using: SymmetricKey(data: key)))
  }

  /// AES-CTR, the counter the whole 16-byte block, big-endian (Java's AES/CTR/NoPadding).
  static func ctr(_ key: [UInt8], _ iv: [UInt8], _ b: [UInt8]) throws -> [UInt8] {
    var c: CCCryptorRef?
    guard CCCryptorCreateWithMode(CCOperation(kCCEncrypt), CCMode(kCCModeCTR), CCAlgorithm(kCCAlgorithmAES), CCPadding(ccNoPadding),
                                  iv, key, key.count, nil, 0, 0, CCModeOptions(kCCModeOptionCTR_BE), &c) == kCCSuccess, let c else {
      throw Fail("AES-CTR with a \(key.count) B key")
    }
    defer { CCCryptorRelease(c) }
    var out = [UInt8](repeating: 0, count: b.count + 16)
    var n = 0, m = 0
    guard CCCryptorUpdate(c, b, b.count, &out, out.count, &n) == kCCSuccess,
          CCCryptorFinal(c, &out[n], out.count - n, &m) == kCCSuccess else { throw Fail("AES-CTR failed") }
    return Array(out[0..<(n + m)])
  }

  private static let magic = [UInt8]("soundcored3200".utf8)

  /// A file's audio key: unwrapped with the session key, behind a fixed text.
  static func fileKey(_ session: [UInt8], _ sessionNonce: [UInt8], _ wrapped: [UInt8]) throws -> [UInt8] {
    let k = try ctr(session, sessionNonce, wrapped)
    guard k.count == magic.count + 32, Array(k[0..<magic.count]) == magic else { throw Fail("the file key does not unwrap") }
    return Array(k[magic.count...])
  }

  /// A unit's counter: the file's nonce (12 B), then seq × 10 (each unit is ten AES blocks).
  static func iv(_ nonce: [UInt8], _ seq: Int64) throws -> [UInt8] {
    guard (0...maxSeq).contains(seq) else { throw Fail("unit \(seq) out of range") }
    let c = UInt32(truncatingIfNeeded: seq * 10)
    let counter: [UInt8] = [UInt8(c >> 24), UInt8(c >> 16 & 255), UInt8(c >> 8 & 255), UInt8(c & 255)]
    return Array((nonce + [UInt8](repeating: 0, count: 12)).prefix(12)) + counter
  }

  //
  // What the bean says
  //

  /// A 1A/07 answer: the file, its bytes (0xFFFFFFFF: still recording), its nonce, its key (none when code is not 0:
  /// 1 no such file, 2 the bean could not read it, 3 nothing left from that offset).
  struct Header { let file: Int64, size: Int64, nonce: [UInt8], code: Int, key: [UInt8]? }

  static func header(_ session: [UInt8], _ p: [UInt8]) throws -> Header {
    guard p.count >= 87 else { throw Fail("file header of \(p.count) B") }
    let code = Int(p[86])
    let key = code == 0 || code == 255 ? try fileKey(session, Array(p[70..<86]), Array(p[24..<70])) : nil
    return Header(file: u32(p, 0), size: u32(p, 4), nonce: Array(p[8..<24]), code: code == 255 ? 0 : code, key: key)
  }

  /// flags: 0x01 a double-tap mark, 0x02 the file's first unit, 0x04 its last.
  struct Slice { let seq: Int64, flags: Int, data: [UInt8] }

  static func slices(_ p: [UInt8]) throws -> [Slice] {
    guard p.count % unit == 0 else { throw Fail("audio of \(p.count) B is not whole units") }
    return stride(from: 0, to: p.count, by: unit).map { Slice(seq: u32(p, $0), flags: Int(p[$0 + 4]), data: Array(p[($0 + 5)..<($0 + 165)])) }
  }

  static func opus(_ key: [UInt8], _ nonce: [UInt8], _ s: Slice) throws -> [UInt8] { try ctr(key, iv(nonce, s.seq), s.data) }

  /// 01/01: battery (a level 0-9, or a percent above), charging, firmware, card, the case, recording now.
  static func info(_ p: [UInt8]) throws -> [String: Any] {
    guard p.count >= 45 else { throw Fail("device info of \(p.count) B") }
    return ["model": "D3200", "battery": pct(p[1]), "charge": p[2] == 1 ? "charging" : "battery", "fw": ascii(p, 3, 8),
            "total_kb": u32(p, 24), "free_kb": u32(p, 28), "case_battery": pct(p[38]), "case_charging": p[32] == 1,
            "recording": p.count > 50 && p[50] == 1]
  }

  static func serial(_ p: [UInt8]) -> String { ascii(p, 8, 24).lowercased() }
  static func pct(_ b: UInt8) -> Int { b <= 9 ? (Int(b) + 1) * 10 : min(Int(b), 100) }
  private static func ascii(_ p: [UInt8], _ from: Int, _ to: Int) -> String {
    String(decoding: p[from..<to], as: UTF8.self).trimmingCharacters(in: CharacterSet(charactersIn: "\u{0} "))
  }

  /// 1A/0E, a page of the bean's files: (id, ms) each, and the file being recorded (0: none).
  struct Listing { var files: [(id: Int64, ms: Int64)], open: Int64 }

  static func listing(_ p: [UInt8]) throws -> Listing {
    if p.isEmpty { return Listing(files: [], open: 0) }
    guard p.count >= 2 else { throw Fail("file list of \(p.count) B") }
    let n = u16(p, 0)
    guard p.count >= 2 + n * 8 + 4 else { throw Fail("file list of \(p.count) B for \(n) files") }
    return Listing(files: (0..<n).map { (id: u32(p, 2 + $0 * 8), ms: u32(p, 6 + $0 * 8)) }, open: u32(p, 2 + n * 8))
  }

  static func u32(_ b: [UInt8], _ i: Int) -> Int64 { Int64(b[i]) | Int64(b[i + 1]) << 8 | Int64(b[i + 2]) << 16 | Int64(b[i + 3]) << 24 }
  static func u16(_ b: [UInt8], _ i: Int) -> Int { Int(b[i]) | Int(b[i + 1]) << 8 }
  static func le32(_ v: Int64) -> [UInt8] { (0..<4).map { UInt8(truncatingIfNeeded: v >> (8 * $0)) } }
  static func u16le(_ v: Int) -> [UInt8] { [UInt8(truncatingIfNeeded: v), UInt8(truncatingIfNeeded: v >> 8)] }
  static func hex(_ b: [UInt8]) -> String { b.map { String(format: "%02x", $0) }.joined() }
}

/// What carries a session's bytes (BeanLink.swift, or the checks' fake bean).
protocol BeanIo: AnyObject {
  func send(_ frame: [UInt8]) throws
  /// The next notification, nil after `ms` without one; throws BeanSync.Gone once the link is gone.
  func receive(_ ms: Int64) throws -> [UInt8]?
  func now() -> Int64
  func monotonicNow() -> Int64
  /// Units in a piece while recording live: the app's slice.
  func liveUnits() -> Int
  func log(_ kind: String, _ text: String, _ level: String?, _ fields: [String: Any], _ at: Int64?)
  func report(_ hw: [String: Any])
  /// prefs hold:<tag>, sent with every upload while set (Uploader); nil: let go.
  func hold(_ at: Int64?)
  func held() -> Int64?
  /// The bean's last recording ended at `at` (wall ms) and all of it is up: the server is told (prefs end:<tag>, the
  /// Uploader sends it until the server takes it).
  func end(_ at: Int64)
  func piece()
  /// Starts of this bean's pieces the server refused (Uploader: HTTP 400): their files stay on the bean.
  func refused() -> Set<Int64>
}

/// One bean, session after session (BeanLink connects, then calls session()), as BeanSync in Bean.kt: the key exchange,
/// its info and clock, its files, its start when idle (start(), once a connection), then one transfer at a time, as
/// the bean allows no more and cannot be stopped mid-file: the file being recorded, as it is recorded (realtime), else
/// the oldest file this phone lacks units of (the backlog, files begun after the bean was added here: `since`). Units
/// go into dated Ogg pieces in `chunks` (the Uploader's queue). Per file (`state`, JSON, kept after every piece): when
/// its unit 0 was on the phone's clock (its anchor) and which units are in pieces already. A file the server has whole
/// is then deleted on the bean (prune()); nothing else is. See Bean.kt for the whole story; the logic is the same.
final class BeanSync {
  /// The link went (an IOException on Android): the next connection starts the bean again.
  struct Gone: Error, CustomStringConvertible { let description: String; init(_ s: String = "link gone") { description = s } }
  /// The session ends on purpose: the link goes, which is how a transfer stops early; BeanLink connects again.
  struct Again: Error, CustomStringConvertible { let description: String; var naturalRollover = false }

  static let tries = 3
  static let paceMs: Int64 = 200  // between transfers (ws/soundcore: the SDK's 200 ms)
  static let restUnits: Int64 = 1500  // 30 s of audio, 5 s to send (about 300 units/s): about what a new connection costs
  static let holdMarginMs: Int64 = 60_000  // the hold a minute before what is said now: the clocks' drift

  final class F {
    var anchor: Int64, have: [(Int64, Int64)], total: Int64, tries: Int
    init(_ anchor: Int64, have: [(Int64, Int64)] = [], total: Int64 = -1, tries: Int = 0) {
      self.anchor = anchor; self.have = have; self.total = total; self.tries = tries
    }
  }

  private final class Xfer {
    let file: Int64, live: Bool, from: Int64
    var until: Int64 = -1  // the phone has the rest of the file from here: the link goes once this unit is in (rest())
    var cut = false
    var key: [UInt8]?
    var nonce = [UInt8](repeating: 0, count: 16)
    var first: Int64 = -1, last: Int64 = -1
    var units = 0, dupes = 0
    var lost: Int64 = 0, at: Int64 = 0
    var seen: Int64 = 0  // when its last unit came
    init(_ file: Int64, _ live: Bool, _ from: Int64) { self.file = file; self.live = live; self.from = from }
  }

  // Read by the app's status (main thread) while the session thread writes them
  @Shared var state: [String: Any] = ["code": "encrypting"]
  @Shared var hw: [String: Any] = [:]  // free_kb stays at the last 01/01 reading while a transfer is live
  @Shared var syncDoneKb: Int64 = 0
  @Shared var syncTotalKb: Int64 = 0
  var recording: Bool {
    get { hw["recording"] as? Bool == true }
    set { var h = hw; h["recording"] = newValue; hw = h }
  }
  /// The file being deleted, while it is: the one id a 1A/10 may carry (Bean.allowed).
  @Shared private(set) var deletable: Set<Int64> = []
  private var kept: Set<Int64> = []  // a delete the bean did not confirm: not asked again until the app restarts
  private(set) var files: [Int64: F] = [:]
  private let tag: String, stateFile: URL, chunks: URL
  private unowned let io: BeanIo  // BeanLink owns this
  private var key: [UInt8] = []
  private var reader = Bean.Reader()
  private var inbox: [Bean.Frame] = []
  private var listing = Bean.Listing(files: [], open: 0)
  private var needList = true
  private var listedAt: Int64 = 0, askedAt: Int64 = 0
  private var liveAgain: Int64 = 0   // when a refused realtime transfer is tried again
  private var quietUntil: Int64 = 0  // no 1A/07 before: the bean has just ended a transfer (code 4 when asked at once)
  private var busy: Int64 = 0        // code 4s in a row
  private var paused = false  // only the bean's button state can suspend the live silence guard
  private var liveFile: Int64 = 0
  private var liveSeen: Int64 = 0   // includes waiting for a header and gaps between code-4 retries
  private var liveStalled = false  // one warning until audio resumes, across reconnects
  private var xfer: Xfer?
  private var since: Int64 = 0
  private var pendingEnd: Int64 = 0  // kept in the state file: a kill before the pieces are up does not lose it
  private var caught: Int64 = 0      // backlog units taken since the backlog began
  private var caughtSince: Int64 = 0
  private var marked = false
  private var unknown: Set<Int> = []
  private var run: [Data] = []
  private var runFile: Int64 = -1, runFrom: Int64 = 0
  private var writeFailed = false  // said once until a piece is written again
  private var startDone = false  // this connection's start is settled (sent, the bean was recording, or its button was
                                 // pressed): a stop by hand holds until the link goes (Gone), not across the app's own
                                 // reconnects (a cut re-pull, a stalled transfer)

  init(tag: String, stateFile: URL, chunks: URL, io: BeanIo) {
    self.tag = tag; self.stateFile = stateFile; self.chunks = chunks; self.io = io
    try? FileManager.default.createDirectory(at: chunks, withIntermediateDirectories: true)
    guard FileManager.default.fileExists(atPath: stateFile.path) else { return }
    do {
      guard let o = try JSONSerialization.jsonObject(with: Data(contentsOf: stateFile)) as? [String: Any] else { throw Bean.Fail("not an object") }
      for (k, v) in o {
        if k == "end" { pendingEnd = (v as? NSNumber)?.int64Value ?? 0; continue }
        guard let id = Int64(k), let f = v as? [String: Any], let h = f["h"] as? [[NSNumber]] else { throw Bean.Fail("file \(k)") }
        files[id] = F((f["a"] as? NSNumber)?.int64Value ?? id * 1000, have: h.map { ($0[0].int64Value, $0[1].int64Value) },
                      total: (f["t"] as? NSNumber)?.int64Value ?? -1, tries: (f["r"] as? NSNumber)?.intValue ?? 0)
      }
    } catch { log("bean_state", "手机上的录音豆文件记录读不了（\(error)），从头记", "warn") }
  }

  private func log(_ kind: String, _ text: String, _ level: String? = nil, _ fields: [String: Any] = [:], at: Int64? = nil) {
    io.log(kind, text, level, fields, at)
  }

  private func log(_ kind: String, _ text: String, fields: [String: Any], at: Int64? = nil) { log(kind, text, nil, fields, at: at) }

  private func save() {
    var o: [String: Any] = [:]
    for (id, f) in files { o["\(id)"] = ["a": f.anchor, "t": f.total, "r": f.tries, "h": f.have.map { [$0.0, $0.1] }] }
    if pendingEnd > 0 { o["end"] = pendingEnd }
    try? FileManager.default.createDirectory(at: stateFile.deletingLastPathComponent(), withIntermediateDirectories: true)
    try? JSONSerialization.data(withJSONObject: o).write(to: stateFile, options: .atomic)
  }

  /// One connection, from the key exchange until the link goes (io.receive throws). `steps`: the link's own, for the log.
  func session(since: Int64, steps: String) throws {
    self.since = since
    reader = Bean.Reader(); inbox = []; xfer = nil; needList = true; askedAt = io.now(); liveFile = 0; paused = false
    var t = steps
    func step<T>(_ code: String, _ what: String, _ block: () throws -> T) rethrows -> T {
      state = ["code": code]
      let t0 = io.monotonicNow()
      defer {
        let elapsed = io.monotonicNow() - t0
        t += (t.isEmpty ? "" : "，") + "\(what) \(elapsed) ms"
        log("bean_connect_step", what, nil, ["step": what, "elapsedMs": elapsed])
      }
      return try block()
    }
    defer { flush(); xfer = nil; syncDoneKb = 0; syncTotalKb = 0 }
    do {
      let mine = Bean.keys()
      key = try step("encrypting", "密钥交换") { try Bean.session(mine, try ask(Bean.hello(Bean.pub(mine)), 0x2E01).payload) }
      let p = try step("status", "读设备信息") { try ask(Bean.info(), 0x0101).payload }
      hw = try Bean.info(p)
      try step("clock", "对时") { try clock() }
      listing = try step("status", "读文件列表") { try list() }
      log("connect", "连上：\(t)；固件 \(hw["fw"] ?? "")，序列号 \(Bean.serial(p))，电量 \(hw["battery"] ?? "")%，" +
          (recording ? "在录音（文件 \(listing.open)）" : "没在录音") + "，豆子上 \(listing.files.count) 个文件",
          fields: hw.merging(["files": listing.files.count, "open": listing.open]) { $1 })
      io.report(hw)
      if !startDone { startDone = true; if !recording { try start() } }
      try loop()
    } catch let e as Gone {
      startDone = false  // the link went: the next connection starts the bean again
      throw e
    }
  }

  private func loop() throws {
    var reported = io.now(), settled: Int64 = 0
    while true {
      // a pool per pass: this thread lives as long as the link, and what Foundation autoreleases here (the queue listed
      // every 5 s, each piece and the state written) would stay until it ends (Ble.swift's session loop, 10-06)
      try autoreleasepool {
        if xfer == nil && io.now() >= quietUntil {
          if needList || recording && listing.open == 0 && io.now() - listedAt > 10_000 { listing = try list() }
          let open = listing.open
          let x: Xfer? = recording && open != 0 && io.now() >= liveAgain ? Xfer(open, true, files[open].flatMap(gap) ?? 0)
            : recording ? nil : backlog()
          if let x { try begin(x) } else { state = ["code": "beanIdle"] }
        }
        let quiet = quietUntil - io.now()
        if let f = try next(xfer == nil && quiet > 0 ? quiet : 1000) { try take(f) }
        if let x = xfer, x.cut { complete(x, 0); throw Again(description: "file \(x.file): the rest is here already") }
        let now = io.now()
        if paused { liveSeen = now }
        if (recording || xfer?.live == true) && !paused && liveFile != 0 && now - liveSeen > 30_000 {
          flush()
          if !liveStalled { log("bean_stall", "实时文件 \(liveFile) 30 s 没收到单元，断开重连", "warn", ["file": liveFile]) }
          liveStalled = true
          throw Again(description: "file \(liveFile): no audio for 30 s")
        }
        if let x = xfer {  // the bean did not answer, or a transfer stopped: on with the next
          if x.key == nil && now - x.at > 15_000 { failed(x, "no answer") }
          // the bean cannot be stopped mid-file: a new connection starts it afresh, no stray units of this file then go
          // to the next one's
          else if !x.live && now - x.seen > 30_000 {
            complete(x, -1, "；30 s 没收到单元，断开重连")
            throw Again(description: "file \(x.file): no audio for 30 s")
          }
        }
        if xfer == nil && now - askedAt > 60_000 {  // not during a transfer: its pushes say battery and charging
          askedAt = now
          let info = try Bean.info(try ask(Bean.info(), 0x0101).payload)
          if info["recording"] as? Bool == true && !recording { needList = true }
          hw = info
        }
        if now - settled >= 5000 { settled = now; settle(); if xfer == nil && !recording { try prune() } }
        if now - reported >= 60_000 { reported = now; io.report(hw) }
      }
    }
  }

  /// The owner, 10-26: a bean connected records. Its answer only echoes the command (01 9c, 10-25); it has started once
  /// its new file comes (1A/06, 850 ms on, 10-25) or its button's state says so (take()). Not asked again in the
  /// connection; never stopped or paused by the app.
  private func start() throws {
    let why: String
    do {
      _ = try ask(Bean.startFrame(), Bean.startCmd, 3000)
      let until = io.now() + 3000
      while !recording, let f = try next(until - io.now()) { try take(f) }
      if recording { log("bean_start", "自动开始录音"); return }
      why = "答了，3 s 内没开始录新文件"
    } catch let e as Gone { throw e } catch let e as Again { throw e } catch { why = "\(error)" }
    log("bean_start", "自动开始录音没成功（\(why)），这次连接不再试", "warn")
  }

  private func clock() throws {
    let now = io.now()
    let tz = TimeZone.current.secondsFromGMT(for: Date(timeIntervalSince1970: Double(now) / 1000)) / 3600
    do { _ = try ask(Bean.clock(now / 1000, tz), 0x01A6, 3000) }
    catch let e as Gone { throw e }
    catch { log("bean_clock", "对时没有回音（\(error)），文件时间按豆子自己的钟", "warn") }
  }

  /// This bean's pieces still in the upload queue, by start (Uploader: <start>@<tag>.ogg).
  private func queued() -> [Int64] {
    let suffix = "@\(tag).ogg"
    return ((try? FileManager.default.contentsOfDirectory(atPath: chunks.path)) ?? [])
      .compactMap { $0.hasSuffix(suffix) ? Int64($0.dropLast(suffix.count)) : nil }
  }

  /// Every page of the bean's files: page by page until one adds none.
  private func list() throws -> Bean.Listing {
    var all: [Int64: Int64] = [:], order: [Int64] = []
    var open: Int64 = 0
    for page in 0..<64 {
      let l = try Bean.listing(try ask(Bean.list(page), 0x1A0E).payload)
      if page == 0 { open = l.open }
      let n = all.count
      for (id, ms) in l.files { if all[id] == nil { order.append(id) }; all[id] = ms }
      if all.count == n { break }
    }
    needList = false; listedAt = io.now()
    // A just-closed file can be absent briefly. Keep its ranges until the server has every piece.
    let q = queued() + io.refused()
    for (id, f) in files where all[id] == nil && id != open &&
      (f.have.isEmpty || gap(f) == nil && !q.contains { $0 >= f.anchor && $0 < f.anchor + f.total * Bean.frameMs }) {
      files[id] = nil
      if id * 1000 >= since {
        let missing = gap(f) != nil
        log("bean_gone", "文件 \(id) 已不在豆子上，" + (missing ? "手机还缺它的一部分" : "服务端都有了，清掉手机记录"), missing ? "warn" : nil, ["file": id])
      }
    }
    save()
    return Bean.Listing(files: order.map { (id: $0, ms: all[$0]!) }, open: open)
  }

  /// The oldest file begun since the bean was added here that this phone lacks units of.
  private func pending() -> [(id: Int64, ms: Int64)] {
    listing.files.filter { $0.id * 1000 >= since && $0.id != listing.open && (files[$0.id].map { !done($0) } ?? true) }.sorted { $0.id < $1.id }
  }

  private func backlog() -> Xfer? {
    let p = pending()
    if p.isEmpty {
      if caught > 0 { log("sync", "补传 \(caught * Int64(Bean.unit) / 1024) KB，用时 \((io.now() - caughtSince) / 1000) s") }
      caught = 0; syncDoneKb = 0; syncTotalKb = 0
      return nil
    }
    if caught == 0 { caughtSince = io.now() }
    let left = p.reduce(Int64(0)) { acc, e in
      acc + (files[e.id].map { f in max(0, (f.total >= 0 ? f.total : e.ms / Bean.frameMs) - covered(f)) } ?? e.ms / Bean.frameMs)
    }
    syncTotalKb = (caught + left) * Int64(Bean.unit) / 1024; syncDoneKb = caught * Int64(Bean.unit) / 1024
    let id = p[0].id
    return Xfer(id, false, files[id].flatMap(gap) ?? 0)
  }

  private func begin(_ x: Xfer) throws {
    xfer = x; x.at = io.now(); x.seen = x.at; marked = false
    if x.live && liveFile != x.file { liveFile = x.file; liveSeen = x.at }
    state = ["code": x.live ? "joining" : "beanIdle"]
    if x.live { syncDoneKb = 0; syncTotalKb = 0 }  // a backlog waits while the bean records
    try send(Bean.transfer(x.file, x.from * Int64(Bean.unit), x.live))
  }

  /// Release legacy holds while recording: even closed-file backlog cannot be fetched then. After a stop, finish
  /// reading it first. Already-cut older pieces must be uploaded before letting go in either case.
  private func settle() {
    if !recording && !pending().isEmpty { return }
    let q = queued()
    if let h = io.held(), !q.contains(where: { $0 < h + BeanSync.holdMarginMs }) { io.hold(nil); log("hold", "旧录音不再压住定稿") }
    if !recording && xfer == nil && pendingEnd > 0 && q.isEmpty {
      let f = DateFormatter()
      f.dateFormat = "HH:mm:ss"
      log("end", "录音到 \(f.string(from: Date(timeIntervalSince1970: Double(pendingEnd) / 1000))) 结束，都上传了：告诉服务端", fields: ["at": pendingEnd])
      io.end(pendingEnd); pendingEnd = 0; save()
    }
  }

  /// Files the server has whole: begun since the bean was added here, closed on the bean, every unit on the phone and
  /// every piece of them taken by the server (none queued still, none refused).
  private func confirmed() -> [Int64] {
    let q = queued() + io.refused()
    return listing.files.map(\.id).filter { id in
      guard let f = files[id] else { return false }
      return id * 1000 >= since && id != listing.open && gap(f) == nil && !q.contains { $0 >= f.anchor && $0 < f.anchor + f.total * Bean.frameMs }
    }
  }

  /// The files the server has whole go off the bean, one at a time, each once (the owner, 10-25: what stays on it can
  /// be read by anyone near while no phone is connected; Feishu deletes too). An unclear answer leaves the file.
  private func prune() throws {
    for id in confirmed() where !kept.contains(id) {
      let f = files[id]!
      deletable = [id]
      do { _ = try ask(Bean.delete(id, deletable), Bean.deleteCmd, 3000) }  // the SDK's 3 s
      catch let e as Gone { deletable = []; throw e }
      catch let e as Again { deletable = []; throw e }
      catch {
        deletable = []
        kept.insert(id)
        log("bean_delete", "豆子上的文件 \(id) 没删掉（\(error)），留在豆子上", "warn", ["file": id])
        continue
      }
      deletable = []
      files[id] = nil
      listing.files.removeAll { $0.id == id }
      save()
      log("bean_delete", "豆子上的文件 \(id) 删了：服务端都有了（\(f.total) 个单元，\(f.total * Bean.frameMs / 1000) 秒）",
          fields: ["file": id, "units": f.total, "s": f.total * Bean.frameMs / 1000])
    }
  }

  private func take(_ f: Bean.Frame) throws {
    let x = xfer, p = f.payload
    switch f.cmd {
    case 0x1A07: if let x, x.key == nil { try header(x, f) }
    case 0x1A08, 0x1A12: if let x, x.key != nil { for s in try Bean.slices(p) { try unit(x, s) } }
    case 0x1A0A: if let x, p.count < 9 || Bean.u32(p, 5) == 0 || Bean.u32(p, 5) == x.file { complete(x, p.first.map(Int.init) ?? 0) }
    case 0x1A06:  // a new file: recording began, or the bean went on in the next file
      guard p.count >= 4 else { break }
      let open = Bean.u32(p, 0)
      let naturalRollover = x != nil && liveFile == x!.file && recording && !paused
      paused = false
      if let x, x.live, x.file != open {
        // The bean may skip 1A/0A at rollover: flush as usual, fetch the old tail after stop.
        complete(x, 0, "；豆子开始录新文件 \(open)，缺的停录后补传")
        liveAgain = 0
        throw Again(description: "file \(x.file): rolled over to \(open)", naturalRollover: naturalRollover)
      }
      recording = true
      listing.open = open
      let pre = p.count > 8 ? Int(p[8]) : p.count > 4 ? Int(p[4]) : -1
      log("bean_file", "豆子开始录新文件 \(open)", fields: ["file": open, "pre": pre])
    case 0x1882:  // the bean's button: 0 stopped, 1 recording, 2 paused
      switch p.first {
      case 0: liveFile = 0; paused = false; recording = false; startDone = true; log("bean_stop", "豆子停止录音")
      case 1: if !recording { needList = true }; if paused { liveSeen = io.now() }; paused = false; recording = true; log("bean_record", "豆子开始录音")
      case 2: paused = true; startDone = true; log("bean_pause", "豆子暂停录音")
      default: break
      }
    case 0x0103: if p.count >= 2 { var h = hw; h["battery"] = Bean.pct(p[0]); h["case_battery"] = Bean.pct(p[1]); hw = h }
    case 0x0104:
      if !p.isEmpty {
        var h = hw
        h["charge"] = p[0] == 1 ? "charging" : "battery"
        if p.count > 1 { h["case_charging"] = p[1] == 1 }
        hw = h
      }
    default:
      if unknown.insert(f.cmd).inserted { log("bean_frame", String(format: "豆子发来 %02X/%02X（%d B），不认识，略过", f.type, f.id, p.count)) }
    }
  }

  private func header(_ x: Xfer, _ fr: Bean.Frame) throws {
    if !fr.ok { return failed(x, "status \(fr.status)") }
    let h = try Bean.header(key, fr.payload)
    if h.file != x.file { return failed(x, "it answered with file \(h.file)") }
    switch h.code {
    case 0:
      x.key = h.key; x.nonce = h.nonce; if !x.live { busy = 0 }
      if x.live { liveSeen = io.now() }
      if !x.live || h.size != 0xFFFF_FFFF {
        if h.size % Int64(Bean.unit) != 0 { return failed(x, "file of \(h.size) B") }
        let f = file(x.file, x.file * 1000)
        f.total = h.size / Int64(Bean.unit)
        if !x.live { x.until = rest(f, x.from) }
      }
    case 3:  // nothing after `from`: all of it is here
      let f = file(x.file, x.file * 1000)
      if f.total < 0 { f.total = x.from }
      xfer = nil; save()
    case 1: file(x.file, x.file * 1000).tries = BeanSync.tries + 1; failed(x, "no such file")
    case 4:  // still at the transfer before (10-25: asked 2 ms after its end): again in a moment, not a try
      if busy == 0 { log("bean_xfer", "\(x.live ? "实时" : "补传")文件 \(x.file) 豆子还忙（code 4），稍后再要", fields: ["file": x.file]) }
      busy += 1
      quietUntil = io.now() + min(5000, BeanSync.paceMs * busy)
      xfer = nil
    default: failed(x, "code \(h.code)")
    }
  }

  private func failed(_ x: Xfer, _ why: String) {
    log("bean_xfer", "\(x.live ? "实时" : "补传")文件 \(x.file) 没开始：\(why)", "warn", ["file": x.file, "from": x.from])
    if x.live { liveAgain = io.now() + 30_000 } else { file(x.file, x.file * 1000).tries += 1 }
    xfer = nil
    save()
  }

  private func file(_ id: Int64, _ anchor: Int64) -> F {
    if let f = files[id] { return f }
    let f = F(anchor)
    files[id] = f
    return f
  }

  private func unit(_ x: Xfer, _ s: Bean.Slice) throws {
    let audio = try Bean.opus(x.key!, x.nonce, s)
    if x.first < 0 { first(x, s, audio) }
    let f = files[x.file]!
    if x.last >= 0 && s.seq > x.last + 1 {  // units the bean did not send: the file has a hole, asked for again later
      x.lost += s.seq - x.last - 1
      log("bean_gap", "文件 \(x.file) 缺单元 \(x.last + 1)..\(s.seq - 1)（\((s.seq - x.last - 1) * Bean.frameMs) ms）", "warn",
          ["file": x.file, "from": x.last + 1, "to": s.seq])
    }
    let now = io.now()
    let late = now - x.seen - (s.seq - x.last) * Bean.frameMs
    if x.live && x.last >= 0 && late > 5000 {  // the bean paused mid-file (the app never asks it to) or the link stalled
      log("bean_gap_time", "文件 \(x.file) 实时单元 \(x.last)→\(s.seq) 之间过了 \((now - x.seen) / 1000) s：豆子若暂停过，" +
          "之后的单元按序号排会早 \(late / 1000) s", "warn", ["file": x.file, "seq": s.seq, "late_ms": late])
    }
    x.units += 1; x.last = s.seq; x.seen = now
    if x.live { liveSeen = now; liveStalled = false; busy = 0 }
    if s.flags & 4 != 0 { f.total = s.seq + 1 }
    let dupe = has(f, s.seq) || runFile == x.file && s.seq >= runFrom && s.seq < runFrom + Int64(run.count)
    let mark = s.flags & 1 != 0
    if mark && !marked && !dupe { log("mark", "双击标记", fields: ["file": x.file, "seq": s.seq], at: f.anchor + s.seq * Bean.frameMs) }
    marked = mark  // also on units had already: a mark that goes on past them is not a new one
    if x.until >= 1 && x.until <= s.seq + 1 { x.cut = true }
    if dupe { x.dupes += 1; return }
    if !run.isEmpty && (runFile != x.file || s.seq != runFrom + Int64(run.count)) { flush() }
    if run.isEmpty { runFile = x.file; runFrom = s.seq }
    run.append(Data(audio))
    if !x.live { caught += 1; syncDoneKb = caught * Int64(Bean.unit) / 1024 }
    if run.count >= (x.live ? io.liveUnits() : Bean.pieceUnits) { flush() }
  }

  /// A transfer's first unit: establish the file's anchor. Live never holds finals: backlog is read only after stop.
  private func first(_ x: Xfer, _ s: Bean.Slice, _ audio: [UInt8]) {
    x.first = s.seq
    let now = io.now(), byId = x.file * 1000, est = now - s.seq * Bean.frameMs
    if files[x.file] == nil {
      let phone = x.live && (-2000...3000).contains(est - byId)
      _ = file(x.file, phone ? est : byId)
      if x.live {
        log("bean_clock", "文件 \(x.file) 的起点按\(phone ? "手机" : "豆子")的钟：豆子的钟比手机\(est >= byId ? "慢" : "快") \(abs(est - byId)) ms",
            fields: ["file": x.file, "ms": est - byId])
      }
    }
    let toc = Int(audio.first ?? 0)
    log("bean_xfer", "\(x.live ? "实时" : "补传")文件 \(x.file) 开始：首单元 \(s.seq)（请求从 \(x.from) 起），Opus 配置 \(toc >> 3)",
        fields: ["file": x.file, "seq": s.seq, "from": x.from, "toc": toc])
    if x.live { state = ["code": "connected"] }
  }

  private func complete(_ x: Xfer, _ result: Int, _ why: String = "") {
    flush()
    xfer = nil
    quietUntil = io.now() + BeanSync.paceMs
    let f = files[x.file]
    log("bean_xfer", "\(x.live ? "实时" : "补传")文件 \(x.file) \(x.cut ? "缺的取到了" : "传完（结果 \(result)）")：\(x.units) 个单元，seq \(x.first)..\(x.last)，" +
        "重复 \(x.dupes)，缺 \(x.lost)，用时 \((io.now() - x.at) / 1000) s" +
        (x.cut ? "；单元 \(x.until) 起的一段手机已有，断开连接让豆子停下，缺的再从各自的位置要" : "") + why, result == 0 && x.lost == 0 ? nil : "warn",
        ["file": x.file, "units": x.units, "first": x.first, "last": x.last, "dupes": x.dupes, "lost": x.lost, "result": result, "cut": x.cut])
    if x.live {
      listing.open = 0; needList = true; askedAt = 0  // recording on? the info says
      if x.units == 0 { liveAgain = io.now() + 30_000 }
    }
    guard let f else { return }
    if x.live {
      if x.last >= 0 { pendingEnd = max(pendingEnd, f.anchor + (x.last + 1) * Bean.frameMs) }
    } else if gap(f) == nil {
      pendingEnd = max(pendingEnd, f.anchor + f.total * Bean.frameMs)
    } else if !(x.cut && gap(f)! > x.from) {  // cut with the hole filled: not a try
      f.tries += 1
      if f.tries > BeanSync.tries {
        log("bean_xfer", "文件 \(x.file) 从单元 \(gap(f)!) 起取了 \(BeanSync.tries) 次仍不全，不再取", "error", ["file": x.file, "gap": gap(f)!])
      }
    }
    save()
  }

  /// The units taken so far into a piece, dated by its file's anchor.
  private func flush() {
    if run.isEmpty { return }
    guard let f = files[runFile] else { run = []; return }
    let start = f.anchor + runFrom * Bean.frameMs
    // one atomic write: a piece is whole or not there (Uploader takes <start>@<tag>.ogg)
    do {
      try Store.oggOpus(run, frameMs: Bean.frameMs, channels: 2).write(to: chunks.appendingPathComponent("\(start)@\(tag).ogg"), options: .atomic)
      writeFailed = false
    } catch {  // its units are dropped, not marked had: asked for again as a gap (Android: the session ends)
      if !writeFailed { log("bean_piece", "录音豆的一段写不进手机（\(error)），这段之后再从豆子上取", "warn") }
      writeFailed = true
      run = []
      return
    }
    add(f, runFrom, runFrom + Int64(run.count))
    run = []
    save()
    io.piece()
  }

  private func has(_ f: F, _ seq: Int64) -> Bool { f.have.contains { seq >= $0.0 && seq < $0.1 } }
  private func covered(_ f: F) -> Int64 { f.have.reduce(0) { $0 + $1.1 - $1.0 } }

  private func add(_ f: F, _ from: Int64, _ to: Int64) {
    var merged: [(Int64, Int64)] = []
    for r in (f.have + [(from, to)]).sorted(by: { $0.0 < $1.0 }) {
      if let l = merged.last, r.0 <= l.1 { merged[merged.count - 1].1 = max(l.1, r.1) } else { merged.append(r) }
    }
    f.have = merged
  }

  /// The first unit this phone lacks; nil: it has the whole file (its length known).
  func gap(_ f: F) -> Int64? {
    var at: Int64 = 0
    for r in f.have { if r.0 > at { break } else { at = max(at, r.1) } }
    return f.total >= 0 && f.total <= at ? nil : at
  }

  private func done(_ f: F) -> Bool { gap(f) == nil || f.tries > BeanSync.tries }

  /// Where the first range the phone has after `from` that is long enough to skip begins: a new connection costs less
  /// than taking it again (the bean sends from the offset to the end and cannot be stopped mid-file). -1: take it all.
  private func rest(_ f: F, _ from: Int64) -> Int64 { f.have.first { $0.0 > from && $0.1 - $0.0 >= BeanSync.restUnits }?.0 ?? -1 }

  private func send(_ frame: [UInt8]) throws {
    guard Bean.allowed(frame, deletable) else { throw Bean.Fail("not sent: \(Bean.hex(frame))") }  // the last check before the radio
    try io.send(frame)
  }

  private func next(_ ms: Int64) throws -> Bean.Frame? {
    let until = io.now() + ms
    while inbox.isEmpty {
      let left = until - io.now()
      if left <= 0 { return nil }
      guard let b = try io.receive(left) else { return nil }
      inbox += reader.push(b)
    }
    return inbox.removeFirst()
  }

  /// A command and its answer; what else comes meanwhile (a push, audio) is taken as it comes.
  private func ask(_ frame: [UInt8], _ cmd: Int, _ ms: Int64 = 10_000) throws -> Bean.Frame {
    try send(frame)
    let until = io.now() + ms
    while true {
      guard let f = try next(until - io.now()) else { throw Bean.Fail(String(format: "no answer to %02X/%02X", cmd >> 8, cmd & 255)) }
      if f.cmd != cmd { try take(f); continue }
      if !f.ok { throw Bean.Fail(String(format: "%02X/%02X refused (status %d)", f.type, f.id, f.status)) }
      return f
    }
  }
}
