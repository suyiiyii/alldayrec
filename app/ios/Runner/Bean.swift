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
  /// recording (18/82 01, START: start()), never its stop (00) or pause (02); its Wi-Fi hotspot on (1A/05) and off (1A/02)
  /// for the fast transfer (BeanSync.wifi()). Nothing that binds or unbinds (0B/xx), resets, updates firmware (FF/xx, and
  /// never the OTA service) or reads device logs.
  static let sends: Set<Int> = [0x0101, 0x01A6, 0x2E01, 0x1A0E, 0x1A07, 0x1A05, 0x1A02]
  static let deleteCmd = 0x1A10
  static let startCmd = 0x1882

  /// `note`: the device page's code for it when it ends a fast transfer (BeanSync.wifiNote)
  struct Fail: Error, CustomStringConvertible {
    let description: String, note: String?
    init(_ s: String, note: String? = nil) { description = s; self.note = note }
  }

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
  /// Over the hotspot's WebSocket (ws/soundcore wifi.rs): a file's audio from `offset` bytes on, no realtime flag.
  static func wifiTransfer(_ file: Int64, _ offset: Int64) -> [UInt8] { build(0x1A, 0x07, le32(offset) + le32(file)) }
  /// The hotspot on, by a name it has not had (a repeated one gets an empty answer and no hotspot): its answer is its
  /// address, IPv4 reversed and the port LE; off.
  static func wifiOn(_ ssid: String, _ password: String) -> [UInt8] {
    let s = [UInt8](ssid.utf8), p = [UInt8](password.utf8)
    return build(0x1A, 0x05, [UInt8(s.count)] + s + [UInt8(p.count)] + p)
  }
  static func wifiOff() -> [UInt8] { build(0x1A, 0x02, []) }
  /// A 1A/05 answer's address, a.b.c.d:port; nil: no hotspot.
  static func endpoint(_ p: [UInt8]) -> String? { p.count < 6 ? nil : "\(p[3]).\(p[2]).\(p[1]).\(p[0]):\(u16(p, 4))" }
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
  /// The live page is open: the latest upload's answer said so (slice.watched) and is under a minute old.
  func watched() -> Bool
  func log(_ kind: String, _ text: String, _ level: String?, _ fields: [String: Any], _ at: Int64?)
  func report(_ hw: [String: Any])
  /// prefs hold:<tag>, sent with every upload while set (Uploader); nil: let go.
  func hold(_ at: Int64?)
  func held() -> Int64?
  /// The bean's last recording ended at `at` (wall ms) and all of it is up: the server is told (prefs end:<tag>, the
  /// Uploader sends it until the server takes it).
  func end(_ at: Int64)
  func piece()
  /// Starts of this bean's pieces the server could not decode (UploadAnswer.badAudio): their files stay on the bean.
  func refused() -> Set<Int64>
  /// The phone's side of a slow link, for bean_lag and too_slow (BeanSync.diag()): its audio route, a call, power, front.
  func phoneState() -> [String: Any]
  /// The fast transfer was asked for (once: reading it clears it).
  func wifiAsked() -> Bool
  /// Joins the bean's hotspot and opens its WebSocket (`endpoint`: the 1A/05 answer); send and receive go over it then,
  /// until wifiLeave() (FINISH, the hotspot forgotten).
  func wifiJoin(_ ssid: String, _ password: String, _ endpoint: [UInt8]) throws
  func wifiLeave()
}

extension BeanIo {  // a link without Wi-Fi (the checks' fakes unless they say otherwise)
  func wifiAsked() -> Bool { false }
  func wifiJoin(_ ssid: String, _ password: String, _ endpoint: [UInt8]) throws { throw Bean.Fail("no Wi-Fi") }
  func wifiLeave() {}
}
extension BeanIo { func phoneState() -> [String: Any] { [:] } }

/// One bean, session after session (BeanLink connects, then calls session()), as BeanSync in Bean.kt: the key exchange,
/// its info and clock, its files, its start when idle (start(), once a connection), then one transfer at a time, as
/// the bean allows no more and cannot be stopped mid-file: the file being recorded, from where the phone lacks it on to
/// live (plan()), else the oldest file this phone lacks units of (the backlog, files begun after the bean was added here: `since`). Units
/// go into dated Ogg pieces in `chunks` (the Uploader's queue). Per file (`state`, JSON, kept after every piece): when
/// its unit 0 was on the phone's clock (its anchor), the anchors it was dated by anew while live (reanchor()) and which
/// units are in pieces already. A file the server has whole
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
  static let watchedGapUnits: Int64 = 3000  // the owner, 10-27: a hole of up to a minute is read first even while /live watches
  static let clockUnits: Int64 = 1500  // half a minute of live units: the fastest of each dates the file (reanchor())
  static let stepUnits = 250  // while a live file is off its target, pieces of 5 s: a step each (step(), ≤ 1800 ppm)

  final class F {
    var anchor: Int64, have: [(Int64, Int64)], total: Int64, tries: Int
    /// (seq, anchor): from that unit on the file is dated by that anchor instead (step()), in order
    var anchors: [(Int64, Int64)]
    var wifiTries = 0  // over Wi-Fi in this fast transfer (wifi()), not saved: BLE keeps all of `tries` (tried())
    init(_ anchor: Int64, have: [(Int64, Int64)] = [], total: Int64 = -1, tries: Int = 0, anchors: [(Int64, Int64)] = []) {
      self.anchor = anchor; self.have = have; self.total = total; self.tries = tries; self.anchors = anchors
    }
    /// A unit's time on the phone's clock: by the anchor in force at its seq.
    func at(_ seq: Int64) -> Int64 { (anchors.last { $0.0 <= seq }?.1 ?? anchor) + seq * Bean.frameMs }
    var latest: Int64 { anchors.last?.1 ?? anchor }
  }

  /// live: the file being recorded (pieces of the app's slice, the stall guard); realtime: the wire's flag (plan());
  /// watched: begun for the live page (tail_short, live_now): ended when the page closes or opens (loop()).
  private final class Xfer {
    let file: Int64, live: Bool, from: Int64, realtime: Bool, watched: Bool
    var until: Int64 = -1  // the phone has the rest of the file from here: the link goes once this unit is in (rest())
    var cut = false
    var key: [UInt8]?
    var nonce = [UInt8](repeating: 0, count: 16)
    var first: Int64 = -1, last: Int64 = -1
    var units = 0, dupes = 0
    var lost: Int64 = 0, at: Int64 = 0
    var asked = 1  // over Wi-Fi: how often its 1A/07 went (the bean may ignore the first on a fresh socket, ws/soundcore)
    var seen: Int64 = 0  // when its last unit came
    var firstAt: Int64 = 0  // when its first unit came (pace)
    var half: Int64 = -1, fastest = Int64.max, before: Int64?  // reanchor(): this half minute's first seq and fastest, the last's
    var target: Int64?  // the anchor the units' timing says, live (reanchor()); pieces go there a step at a time (step())
    var clockSaid: Int64 = 0  // when bean_clock was last logged
    var resent = false  // live, the bean went back in the file: what comes after is late by the resend, no clock
    var lag: Int64?  // live, once within 10 s: how long after its end its last unit came (report(), bean_lag)
    var gainAt: Int64 = 0, gainBehind: Int64 = 0, gainLast: Int64 = 0  // slow(): this minute's start, behind and last unit
    init(_ file: Int64, _ live: Bool, _ from: Int64, realtime: Bool = false, watched: Bool = false) {
      self.file = file; self.live = live; self.from = from; self.realtime = realtime; self.watched = watched
    }
  }

  // Read by the app's status (main thread) while the session thread writes them
  @Shared var state: [String: Any] = ["code": "encrypting"]
  @Shared var hw: [String: Any] = [:]  // free_kb stays at the last 01/01 reading while a transfer is live
  @Shared var syncDoneKb: Int64 = 0
  @Shared var syncTotalKb: Int64 = 0
  /// What of the bean's recording this phone lacks, for the status and its reports (owe())
  @Shared var owed: [String: Any] = [:]
  /// Why the last fast transfer got nothing, for the device page (recorder_status.dart): its end only, never a try of
  /// the join that a later one got past; nil once one starts
  @Shared var wifiNote: [String: Any]?
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
  private var lagging = false  // live audio over a minute behind: one warning until it is back within 10 s
  private var xfer: Xfer?
  private var since: Int64 = 0
  private var pendingEnd: Int64 = 0  // kept in the state file: a kill before the pieces are up does not lose it
  private var caught: Int64 = 0      // backlog units taken since the backlog began
  private var caughtSince: Int64 = 0
  private var pace = 2.0  // a realtime=0 read's speed, times real time: the last one measured (10-27 probe: about 2)
  private var marked = false
  private var unknown: Set<Int> = []
  private var run: [Data] = []
  private var fills: [Int64: Int] = [:]  // realtime=0 reads of the open file that came to nothing (missed())
  private var runFile: Int64 = -1, runFrom: Int64 = 0
  private var writeFailed = false  // said once until a piece is written again
  private var overWifi = false  // wifi(): the transfers go over the hotspot's WebSocket
  private var wifiNext = false  // asked for: before the next transfer
  private var wifiOpen = false  // a hotspot was asked for: the next session closes it (1A/02)
  private var wifiOffs = 0  // 1A/02s BLE went before the answer of, in a row: given up at BeanSync.tries (wifiOff())
  private var wifiUnits = 0
  private var wifiEnded: Int64 = 0, wifiBattery: Any = NSNull()  // for the log of the way back (wifiOff())
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
                      total: (f["t"] as? NSNumber)?.int64Value ?? -1, tries: (f["r"] as? NSNumber)?.intValue ?? 0,
                      anchors: (f["p"] as? [[NSNumber]] ?? []).map { ($0[0].int64Value, $0[1].int64Value) })
      }
    } catch { log("bean_state", "手机上的录音豆文件记录读不了（\(error)），从头记", "warn") }
  }

  private func log(_ kind: String, _ text: String, _ level: String? = nil, _ fields: [String: Any] = [:], at: Int64? = nil) {
    io.log(kind, text, level, fields, at)
  }

  private func log(_ kind: String, _ text: String, fields: [String: Any], at: Int64? = nil) { log(kind, text, nil, fields, at: at) }

  private func save() {
    var o: [String: Any] = [:]
    for (id, f) in files {
      o["\(id)"] = ["a": f.anchor, "t": f.total, "r": f.tries, "h": f.have.map { [$0.0, $0.1] }, "p": f.anchors.map { [$0.0, $0.1] }]
    }
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
    defer { flush(); xfer = nil; syncDoneKb = 0; syncTotalKb = 0; owed = [:] }
    do {
      let mine = Bean.keys()
      key = try step("encrypting", "密钥交换") { try Bean.session(mine, try ask(Bean.hello(Bean.pub(mine)), 0x2E01).payload) }
      let p = try step("status", "读设备信息") { try ask(Bean.info(), 0x0101).payload }
      hw = try Bean.info(p)
      try step("clock", "对时") { try clock() }
      if wifiOpen { try wifiOff() }
      listing = try step("status", "读文件列表") { try list() }
      log("connect", "连上：\(t)；固件 \(hw["fw"] ?? "")，序列号 \(Bean.serial(p))，电量 \(hw["battery"] ?? "")%，" +
          (recording ? "在录音（文件 \(listing.open)）" : "没在录音") + "，豆子上 \(listing.files.count) 个文件",
          fields: hw.merging(["files": listing.files.count, "open": listing.open]) { $1 })
      owe()
      io.report(hw.merging(owed) { $1 })
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
        if io.wifiAsked() {  // a transfer cannot be stopped but by the link going: Wi-Fi first in the next session
          if recording { wifiRecording() } else {
            wifiNext = true
            if xfer != nil { flush(); xfer = nil; throw Again(description: "Wi-Fi asked for: the transfer stops") }
          }
        }
        if xfer == nil && wifiNext { wifiNext = false; try wifi() }
        if xfer == nil && io.now() >= quietUntil {
          if needList || recording && listing.open == 0 && io.now() - listedAt > 10_000 { listing = try list() }
          let open = listing.open
          let x: Xfer? = recording && open != 0 && io.now() >= liveAgain ? plan(open) : recording ? nil : backlog()
          if let x { try begin(x) } else { state = ["code": "beanIdle"] }
        }
        let quiet = quietUntil - io.now()
        if let f = try next(xfer == nil && quiet > 0 ? quiet : 1000) { try take(f) }
        if let x = xfer, x.cut {
          complete(x, 0)
          if let g = files[x.file].flatMap(gap), g <= x.from { missed(x) }
          throw Again(description: "file \(x.file): the rest is here already")
        }
        if let x = xfer, x.live && !x.realtime && !x.watched && io.watched() {  // the live page opened
          let at = x.last >= 0 ? x.last + 1 : x.from
          let behind = edge(x.file) - at
          if behind > BeanSync.watchedGapUnits {
            flush()
            log("bean_xfer", "实时页开了：实时文件 \(x.file) 补到单元 \(at)，离实时还差 \(behind * Bean.frameMs / 1000) s，" +
                "超过 1 分钟：断开重连再定，没取的留在豆子上", fields: ["file": x.file, "realtime": false, "reason": "watched_abort",
                "from": at, "hole_units": behind, "hole_ms": behind * Bean.frameMs, "watched": true])
            throw Again(description: "file \(x.file): the live page opened \(behind) units behind")
          }
        }
        if let x = xfer, x.live && x.realtime && x.watched && x.first >= 0 && !io.watched(),  // the page closed
           let f = files[x.file], let g = gap(f), g < x.first {  // a hole live_now left: the next start fills it
          let hole = (f.have.first { $0.0 > g }?.0 ?? x.first) - g
          flush()
          log("bean_xfer", "实时页关了：实时文件 \(x.file) 单元 \(g) 起缺 \(hole * Bean.frameMs / 1000) s，断开重连去补",
              fields: ["file": x.file, "realtime": true, "reason": "watched_end", "from": g, "hole_units": hole,
                       "hole_ms": hole * Bean.frameMs, "watched": false])
          throw Again(description: "file \(x.file): the live page closed, a hole from \(g)")
        }
        if let x = xfer, case let (behind, pace)? = slow(x) {
          flush()
          fills[x.file] = BeanSync.tries
          log("bean_xfer", "实时文件 \(x.file) 补读 1 分钟没追近：读到单元 \(x.last)，离实时还差 \(behind * Bean.frameMs / 1000) s，" +
              "读速 \(String(format: "%.2f", pace)) 倍：这个文件改实时读，缺的停录后补", "warn",
              ["file": x.file, "realtime": false, "reason": "too_slow", "from": x.last + 1, "hole_units": behind,
               "hole_ms": behind * Bean.frameMs, "pace": (pace * 100).rounded() / 100, "watched": x.watched]
                .merging(diag(pace * 1000 / Double(Bean.frameMs))) { $1 })
          throw Again(description: "file \(x.file): read at \(pace) of real time, \(behind) units behind")
        }
        let now = io.now()
        if paused { liveSeen = now }
        if (recording || xfer?.live == true) && !paused && liveFile != 0 && now - liveSeen > 30_000 {
          flush()
          if !liveStalled { log("bean_stall", "实时文件 \(liveFile) 30 s 没收到单元，断开重连", "warn", ["file": liveFile]) }
          liveStalled = true
          if let x = xfer, x.units == 0 { missed(x) }
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
        if now - settled >= 5000 { settled = now; settle(); owe(); if xfer == nil && !recording { try prune() } }
        if now - reported >= 60_000 {
          reported = now
          io.report(hw.merging(owed) { $1 }.merging(xfer?.lag.map { ["live_lag_s": $0 / 1000] } ?? [:]) { $1 })
        }
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

  /// Wi-Fi fast transfer (#431; by hand: the device page's button). The bean opens a hotspot of a new
  /// name (1A/05; its BLE goes, ws/soundcore), the phone joins it (io.wifiJoin) and reads what it lacks over its
  /// WebSocket as over BLE, into the same pieces (the same units, dated the same): the backlog, only while the bean is
  /// not recording (wifiRecording()). Then back to BLE (Again), whose next session turns the hotspot off (1A/02) in case
  /// it is on still.
  private func wifi() throws {
    if recording { return wifiRecording() }
    let battery = hw["battery"] as? Int
    log("bean_wifi_start", "开始 Wi-Fi 快传（手动）：电量 \(battery.map { "\($0)%" } ?? "?")，" +
        "没在录音，积压 \(owed["backlog_s"] ?? 0) s",
        fields: ["reason": "manual", "battery": battery ?? NSNull(), "recording": recording, "open": listing.open,
                 "backlog_s": owed["backlog_s"] ?? 0, "files": listing.files.count])
    wifiNote = nil
    if needList { listing = try list() }  // a file just closed: its length
    owe()
    if owed.isEmpty {  // nothing lacking (a minute or less behind live is none: owe()): 11-08 a second tap did one for nothing
      log("bean_wifi_skip", "没有积压，不开热点", fields: ["battery": battery ?? NSNull(), "recording": recording])
      return
    }
    if let battery, battery < 30 {  // the bean opens no hotspot below 30% (Feishu's manual), and it costs power
      log("bean_wifi_fail", "电量 \(battery)% 低于 30%，不开热点，接着用蓝牙", "warn", ["step": "battery", "battery": battery])
      wifiNote = ["code": "beanWifiLowBattery", "battery": battery]
      return
    }
    wifiBattery = battery ?? NSNull()
    var r = [UInt8](repeating: 0, count: 12)
    for i in r.indices { r[i] = UInt8.random(in: 0...255) }
    let ssid = "LR-" + Bean.hex(Array(r[0..<4])), password = Bean.hex(Array(r[4...]))
    let t0 = io.monotonicNow()
    let a: Bean.Frame
    do { a = try ask(Bean.wifiOn(ssid, password), 0x1A05, 45_000) }
    catch let e as Gone {  // the bean dropped BLE for its hotspot before the answer came: not a link lost (a stop by hand holds)
      wifiOpen = true
      log("bean_wifi_fail", "问热点时蓝牙先断了（\(e)），没拿到地址，回蓝牙", "warn", ["step": "hotspot"])
      wifiNote = ["code": "beanWifiNoHotspot"]
      throw Again(description: "BLE went while asking for the hotspot")
    } catch let e as Again { throw e }
    catch { log("bean_wifi_fail", "豆子没开热点（\(error)），接着用蓝牙", "warn", ["step": "hotspot"]); wifiNote = ["code": "beanWifiNoHotspot"]; return }
    wifiOpen = true
    let opened = io.monotonicNow() - t0
    guard let at = Bean.endpoint(a.payload) else {
      log("bean_wifi_fail", "豆子没开热点（回了 \(Bean.hex(a.payload))，状态 \(a.status)），接着用蓝牙", "warn",
          ["step": "hotspot", "ms": opened, "reply": Bean.hex(a.payload), "status": a.status])
      wifiNote = ["code": "beanWifiNoHotspot"]
      return
    }
    log("bean_wifi_hotspot", "豆子开了热点 \(ssid)（\(opened) ms）：回复 \(Bean.hex(a.payload))，地址 \(at)",
        fields: ["ms": opened, "reply": Bean.hex(a.payload), "endpoint": at, "ssid": ssid])
    overWifi = true; wifiUnits = 0
    files.values.forEach { $0.wifiTries = 0 }
    state = ["code": "beanWifi"]
    var t1 = io.monotonicNow(), why = "", rollover = false, note: String?
    do {
      try io.wifiJoin(ssid, password, a.payload)
      t1 = io.monotonicNow()
      reader = Bean.Reader(); inbox = []
      why = try wifiLoop()
    } catch let e as Again { why = "豆子换了文件"; rollover = e.naturalRollover } catch { why = "\(error)"; note = (error as? Bean.Fail)?.note ?? "beanWifiFailed" }
    if wifiUnits == 0, let code = note ?? (why == "豆子没回音" ? "beanWifiNoAnswer" : nil) { wifiNote = ["code": code, "detail": why] }
    if let x = xfer { complete(x, -1, "；Wi-Fi 快传结束") }
    overWifi = false
    io.wifiLeave()
    wifiEnded = io.monotonicNow()
    let ms = max(1, wifiEnded - t1), kb = Int64(wifiUnits) * Int64(Bean.unit) / 1024
    let audio = Int64(wifiUnits) * Bean.frameMs, x = Double(audio) / Double(ms)
    let q = queued().count  // what is still to go up: the bean gives faster than the phone uploads (11-08: 200× vs 40×)
    log("bean_wifi_done", "Wi-Fi 快传结束（\(why)）：\(wifiUnits) 个单元（\(audio / 1000) s 音频），\(kb) KB，用时 \(ms / 1000) s，" +
        String(format: "%.0f KB/s，%.1f 倍实时", Double(kb) * 1000 / Double(ms), x) + "；上传队列还有 \(q) 块", wifiUnits == 0 ? "warn" : nil,
        ["why": why, "units": wifiUnits, "kb": kb, "ms": ms, "kbps": Double(kb) * 1000 / Double(ms), "x": x, "queued": q])
    throw Again(description: "Wi-Fi transfer over: \(why)", naturalRollover: rollover)
  }

  /// wifi()'s transfers, one at a time as over BLE, until the backlog is in or the bean records. The reason they ended.
  private func wifiLoop() throws -> String {
    var at = io.monotonicNow(), was = 0
    while true {
      let t = io.monotonicNow()
      if io.wifiAsked() { log("bean_wifi_ask", "已在 Wi-Fi 快传，这次请求忽略") }  // not kept for after it (11-08)
      if t - at >= 10_000 {  // progress: what came in the last 10 s, and the upload queue (does it drain meanwhile?)
        let n = wifiUnits - was, kbps = Double(n * Bean.unit) / 1.024 / Double(t - at), x = Double(Int64(n) * Bean.frameMs) / Double(t - at)
        log("bean_wifi_progress", String(format: "Wi-Fi 快传：共 %d 个单元，近 10 s %.0f KB/s，%.1f 倍实时，", wifiUnits, kbps, x) +
            "文件 \(xfer?.file ?? 0) 到单元 \(xfer?.last ?? -1)，上传队列 \(queued().count) 块",
            fields: ["units": wifiUnits, "kb": Int64(wifiUnits) * Int64(Bean.unit) / 1024, "kbps": kbps, "x": x, "file": xfer?.file ?? 0,
                     "seq": xfer?.last ?? -1, "lag_s": xfer?.lag.map { $0 / 1000 } ?? NSNull(), "queued": queued().count])
        at = t; was = wifiUnits
      }
      let why: String? = try autoreleasepool {
        if recording { return "豆子开始录音，回蓝牙" }  // its button: it answers nothing over Wi-Fi then (wifiRecording())
        if xfer == nil {
          if io.now() < quietUntil { if let f = try next(quietUntil - io.now()) { try take(f) }; return nil }
          if busy > 20 { return "豆子一直忙（code 4）" }
          guard let x = backlog() else { return "都取到了" }
          try begin(x)
        }
        if let f = try next(1000) { try take(f) }
        guard let x = xfer else { return nil }
        let now = io.now()
        if x.key == nil && now - x.at > 8000 {  // ws/soundcore: 3 asks 8 s apart
          if x.asked < 3 {
            x.asked += 1; x.at = now
            log("bean_xfer", "补传文件 \(x.file) Wi-Fi 上 8 s 没回音，第 \(x.asked) 次再要", "warn",
                ["file": x.file, "from": x.from, "asked": x.asked, "recording": recording])
            try send(Bean.wifiTransfer(x.file, x.from * Int64(Bean.unit)))
            return nil
          }
          failed(x, "Wi-Fi 上要了 3 次都没回音")
          return "豆子没回音"
        }
        // units came, then none: over Wi-Fi the bean sends far faster than real time, and 11-09 it stopped 16 units
        // short of the end, sending no 1A/0A either; BLE fills what is left (no 30 s wait for nothing)
        let idle: Int64 = x.last >= 0 ? 10_000 : 30_000
        if now - x.seen > idle { complete(x, -1, "；\(idle / 1000) s 没收到单元"); return "\(idle / 1000) s 没收到单元" }
        return nil
      }
      if let why { return why }
    }
  }

  /// No fast transfer while the bean records: over Wi-Fi it answers no 1A/07 then, for the file it records or a closed one
  /// (11-08 22:36, 23:41, 11-09 10:12: three asks each, nothing; stopped, 11-08 21:26, the first answered), and Anker's
  /// SDK demo refuses it (「正在录音中，无法传输文件」). BLE goes on live.
  private func wifiRecording() {
    log("bean_wifi_skip", "豆子在录音，Wi-Fi 快传要停录后用；接着用蓝牙", fields: ["recording": true, "backlog_s": owed["backlog_s"] ?? 0])
    wifiNote = ["code": "beanWifiRecording"]
  }

  /// After a fast transfer: the hotspot off, in case it is on still.
  private func wifiOff() throws {
    let back = wifiEnded > 0 ? io.monotonicNow() - wifiEnded : -1
    log("bean_wifi_back", "快传后蓝牙连回来了：离开热点后 \(back / 1000) s；电量 \(wifiBattery) → \(hw["battery"] ?? "?")%",
        fields: ["ms": back, "battery_before": wifiBattery, "battery": hw["battery"] ?? NSNull(), "recording": recording])
    do {
      let f = try ask(Bean.wifiOff(), 0x1A02, 5000)
      log("bean_wifi_off", "关了热点（1A/02，状态 \(f.status)，" + (f.payload.isEmpty ? "回复不带数据）" : "回复 \(Bean.hex(f.payload))）"),
          fields: ["reply": Bean.hex(f.payload), "status": f.status])
    } catch is Gone {  // the next session sends it again, up to `tries` in all: a bean that never answers blocks no BLE sync
      wifiOffs += 1
      if wifiOffs >= BeanSync.tries {
        log("bean_wifi_off", "关热点 \(wifiOffs) 次都没等到回复（蓝牙先断了），不再发 1A/02", "warn", ["tries": wifiOffs])
        wifiOpen = false; wifiOffs = 0
      }
      // not a link lost (Gone would start the bean on the next connection): a stop by hand holds, as at 1A/05
      throw Again(description: "BLE went before 1A/02's answer")
    } catch { log("bean_wifi_off", "关热点没有回音（\(error)）", "warn", ["error": "\(error)"]) }
    wifiOpen = false; wifiOffs = 0  // only now: BLE gone before the answer, the next session sends 1A/02 again
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
      (f.have.isEmpty || gap(f) == nil && !q.contains { $0 >= f.at(0) && $0 < f.at(f.total) }) {
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

  /// Units of these files the phone lacks.
  private func lacking(_ p: [(id: Int64, ms: Int64)]) -> Int64 {
    p.reduce(Int64(0)) { acc, e in
      acc + (files[e.id].map { f in max(0, (f.total >= 0 ? f.total : e.ms / Bean.frameMs) - covered(f)) } ?? e.ms / Bean.frameMs)
    }
  }

  private func backlog() -> Xfer? {
    let p = pending()
    if p.isEmpty {
      if caught > 0 { log("sync", "补传 \(caught * Int64(Bean.unit) / 1024) KB，用时 \((io.now() - caughtSince) / 1000) s") }
      caught = 0; syncDoneKb = 0; syncTotalKb = 0
      return nil
    }
    if caught == 0 { caughtSince = io.now() }
    let left = lacking(p)
    syncTotalKb = (caught + left) * Int64(Bean.unit) / 1024; syncDoneKb = caught * Int64(Bean.unit) / 1024
    let id = p[0].id
    return Xfer(id, false, files[id].flatMap(gap) ?? 0)
  }

  /// What of the bean's recording this phone lacks (the owner, 11-07: the app said 已同步 with 42 min on the bean, held
  /// back by the live page), in the status and its reports: `backlog_s`, how long until it is all here at the measured
  /// pace (`backlog_eta_s`; the file being recorded grows meanwhile) and why it waits (`backlog_held`: `live`, the live
  /// page is open, plan()'s live_now; `recording`, it goes after the bean stops: files closed meanwhile, a file read
  /// live only); none: being read. A minute or less behind is no backlog (watchedGapUnits: the live page's own line).
  private func owe() {
    let open = recording ? listing.open : 0
    var now: Int64 = 0  // units of the file being recorded, begun since the bean was added here
    if open != 0 && open * 1000 >= since { now = max(0, edge(open) - (files[open].map(covered) ?? 0)) }
    if now <= BeanSync.watchedGapUnits { now = 0 }
    let closed = lacking(pending())
    owed = Self.owedFields(now: now, closed: recording ? closed : 0, idle: recording ? 0 : closed, watched: io.watched(),
                           live: fills[open, default: 0] >= BeanSync.tries, pace: pace)
  }

  /// owe()'s fields from units lacking: `now` of the file being recorded, `closed` of files that wait for the bean to
  /// stop, `idle` of files read now (the bean stopped); `live`: the recorded file is read live only (live_fallback).
  static func owedFields(now: Int64, closed: Int64, idle: Int64, watched: Bool, live: Bool, pace: Double) -> [String: Any] {
    let s = (now + closed + idle) * Bean.frameMs / 1000
    guard s > 0 else { return [:] }
    // closed files wait for the stop whatever the page does (11-08: 3.5 h of one said 补传中)
    let held: String? = now > 0 && live || closed > 0 ? "recording" : now > 0 && watched ? "live" : nil
    // the recorded file grows by real time while it is read: it comes nearer at pace - 1
    let eta: Double? = held == "recording" ? nil : now > 0 ? (pace > 1.05 ? Double(now * Bean.frameMs) / 1000 / (pace - 1) : nil)
      : Double(idle * Bean.frameMs) / 1000 / pace
    return ["backlog_s": s, "backlog_eta_s": eta.map { Int64($0) } ?? NSNull(), "backlog_held": held ?? NSNull()]
  }

  /// The file being recorded, from where the phone lacks it (the owner, 10-27; Bean.kt's plan()). realtime=0 reads it
  /// from the offset at about twice real time, then goes on live with no end (10-27 probe); realtime=1 starts where the
  /// bean is, whatever the offset. While the live page is open a tail hole of up to a minute is read first, a longer
  /// one is left until the page closes (watched_end) and live starts now; else the holes go oldest first, each cut
  /// where a long stretch the phone has begins (rest(); the link goes, the next hole from its own offset), then the
  /// tail, which carries on live. A new file has no stretch here yet: read from unit 0.
  private func plan(_ open: Int64) -> Xfer {
    let f = files[open]
    let tail = f?.have.last?.1 ?? 0  // the first unit after the phone's latest stretch
    let lack = f.flatMap(gap) ?? tail  // all of it here (its length known): nothing older to fill, on from the tail
    let watched = io.watched()
    let now = edge(open)
    let mine = open * 1000 >= since  // a file begun before the bean was added here: live only, its start is not this phone's
    let fallback = fills[open, default: 0] >= BeanSync.tries  // realtime=0 came to nothing `tries` times: live, the holes after stop
    let x: Xfer
    if !mine || fallback { x = Xfer(open, true, tail, realtime: true) }  // not for the page: the page closing never ends it
    else if watched { x = Xfer(open, true, tail, realtime: now - tail > BeanSync.watchedGapUnits, watched: true) }
    else if let f, lack < tail { x = Xfer(open, true, lack); x.until = rest(f, lack) }
    else { x = Xfer(open, true, tail) }
    let older = x.from < tail
    let hole = older ? f!.have.first { $0.0 > x.from }!.0 - x.from : max(0, now - tail)
    let reason = mine && fallback ? "live_fallback" : x.realtime ? "live_now" : watched ? "tail_short" : older ? "fill_hole" : "fill_tail"
    let s = hole * Bean.frameMs / 1000
    let how: String
    switch reason {
    case "live_now": how = "直接实时（realtime=1）：" + (mine ? "实时页开着，尾部缺 \(s) s，超过 1 分钟，留到以后补" : "文件在豆子加到这台手机前就开始了")
    case "live_fallback": how = "直接实时（realtime=1）：realtime=0 取不到或追不上实时，这个文件改回实时，缺的停录后补"
    case "tail_short": how = "补尾部 \(s) s（realtime=0）：实时页开着，不超过 1 分钟，补上接着实时"
    case "fill_hole": how = "补前面的洞 \(s) s（realtime=0）" + (x.until > 0 ? "，单元 \(x.until) 起手机已有，到那里断开" : "，补完接着实时")
    default: how = "补尾部 \(s) s（realtime=0），补上接着实时"
    }
    log("bean_xfer", "实时文件 \(open) 从单元 \(x.from) 起" + how, fields: ["file": open, "realtime": x.realtime, "reason": reason,
        "from": x.from, "hole_units": hole, "hole_ms": hole * Bean.frameMs, "watched": watched])
    return x
  }

  /// A realtime=0 read of the file being recorded that does not gain on it never gets to live: 11-07/08 the bean sent it
  /// at 0.67 of real time for hours (one unit a notification, 33 a second; the same bean's closed files 5 times faster;
  /// nothing in 1A/07 sets a rate, iOS sets no connection interval for a central), the server's audio ever later. Each
  /// minute of such a read: (units behind, its pace) when still over a minute behind and no nearer than a minute before
  /// (then live_fallback: realtime=1, the holes after the stop), else nil.
  private func slow(_ x: Xfer) -> (Int64, Double)? {
    let now = io.now()
    guard x.live && !x.realtime && x.last >= 0 && !paused else { x.gainAt = 0; return nil }
    let behind = edge(x.file) - x.last - 1
    if x.gainAt == 0 { x.gainAt = now; x.gainBehind = behind; x.gainLast = x.last; return nil }
    if now - x.gainAt < 60_000 { return nil }
    let pace = Double((x.last - x.gainLast) * Bean.frameMs) / Double(now - x.gainAt)
    let stuck = behind > BeanSync.watchedGapUnits && behind >= x.gainBehind
    x.gainAt = now; x.gainBehind = behind; x.gainLast = x.last
    return stuck ? (behind, pace) : nil
  }

  /// What may slow a live read, logged with bean_lag and too_slow (11-08: 0.67 for hours, cause unknown; the owner's
  /// AirPods in call mode that day?): the phone's side (BeanIo.phoneState), the bean's battery and case, units a second.
  private func diag(_ unitsS: Double) -> [String: Any] {
    var d = io.phoneState()
    for k in ["battery", "charge", "case_battery", "case_charging"] { d["bean_\(k)"] = hw[k] ?? NSNull() }
    d["units_s"] = (unitsS * 10).rounded() / 10
    return d
  }

  /// The unit the bean is at in the file it records, by the phone's clock (a pause would put it early).
  private func edge(_ file: Int64) -> Int64 { max(0, (io.now() - (files[file]?.latest ?? file * 1000)) / Bean.frameMs) }

  private func begin(_ x: Xfer) throws {
    xfer = x; x.at = io.now(); x.seen = x.at; marked = false
    if x.live && liveFile != x.file { liveFile = x.file; liveSeen = x.at }
    if !overWifi { state = ["code": x.live ? "joining" : "beanIdle"] }
    if x.live { syncDoneKb = 0; syncTotalKb = 0 }  // a backlog waits while the bean records
    try send(overWifi ? Bean.wifiTransfer(x.file, x.from * Int64(Bean.unit)) : Bean.transfer(x.file, x.from * Int64(Bean.unit), x.realtime))
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
      return id * 1000 >= since && id != listing.open && gap(f) == nil && !q.contains { $0 >= f.at(0) && $0 < f.at(f.total) }
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
      if let x, !x.live {
        // Recording began mid catch-up: the bean sends no more of it. Live at once on a new connection, not a try.
        flush()
        log("bean_xfer", "豆子开始录音，补传文件 \(x.file) 停在单元 \(max(x.last + 1, x.from))，停录后接着补",
            fields: ["file": x.file, "open": open, "units": x.units, "last": x.last])
        xfer = nil
        liveAgain = 0
        save()
        throw Again(description: "file \(x.file): the bean began recording \(open)")
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
        if !x.live && !overWifi { x.until = rest(f, x.from) }  // over Wi-Fi a cut would cost more than the rest
      }
    case 3 where x.live: failed(x, "code 3")  // the file being recorded goes on: never its end
    case 3:  // nothing after `from`: all of it is here
      let f = file(x.file, x.file * 1000)
      if f.total < 0 { f.total = x.from }
      xfer = nil; save()
    case 1: file(x.file, x.file * 1000).tries = BeanSync.tries + 1; failed(x, "no such file，不再取")
    case 4:  // still at the transfer before (10-25: asked 2 ms after its end): again in a moment, not a try
      if busy == 0 { log("bean_xfer", "\(x.live ? "实时" : "补传")文件 \(x.file) 豆子还忙（code 4），稍后再要", fields: ["file": x.file]) }
      busy += 1
      quietUntil = io.now() + min(5000, BeanSync.paceMs * busy)
      xfer = nil
    default: failed(x, "code \(h.code)")
    }
  }

  private func failed(_ x: Xfer, _ why: String) {
    log("bean_xfer", "\(x.live ? "实时" : "补传")文件 \(x.file) 没开始：\(why)", "warn", ["file": x.file, "from": x.from, "realtime": x.realtime])
    if x.live { liveAgain = io.now() + 30_000; missed(x) } else { tried(x.file, file(x.file, x.file * 1000)) }
    xfer = nil
    save()
  }

  /// A read of a closed file that left it short: a try. Over Wi-Fi one of its own for this fast transfer, so Wi-Fi
  /// going wrong leaves BLE all of its tries (#262 review: four taps the bean did not answer over Wi-Fi left a file
  /// unread, and unsaid). Giving it up is logged.
  private func tried(_ id: Int64, _ f: F) {
    if overWifi { f.wifiTries += 1 } else { f.tries += 1 }
    let n = overWifi ? f.wifiTries : f.tries
    guard n == BeanSync.tries + 1 else { return }
    log("bean_xfer", "文件 \(id) 从单元 \(gap(f) ?? -1) 起\(overWifi ? "在 Wi-Fi 上" : "")取了 \(n) 次仍不全，" +
        (overWifi ? "这次快传不再取它，留给蓝牙" : "不再取"), overWifi ? "warn" : "error", ["file": id, "gap": gap(f) ?? -1, "wifi": overWifi])
  }

  /// A realtime=0 read of the open file came to nothing: refused, no answer, no unit in 30 s, or cut with its hole where
  /// it was (the bean did not honour the offset). After `tries` in a row (a read from its offset clears them: first())
  /// that file is read realtime=1, as before (plan()).
  private func missed(_ x: Xfer) { if x.live && !x.realtime { fills[x.file, default: 0] += 1 } }

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
    // the bean paused mid-file (the app never asks it to) or the link stalled; a unit sent again (seq ≤ the highest seen)
    // is not late, else a resend logs one warn per unit (11-08 16:42 would have been ~20,000)
    if x.live && x.last >= 0 && s.seq > x.last && late > 5000 {
      log("bean_gap_time", "文件 \(x.file) 实时单元 \(x.last)→\(s.seq) 之间过了 \((now - x.seen) / 1000) s：豆子若暂停过，" +
          "之后的单元按序号排会早 \(late / 1000) s", "warn", ["file": x.file, "seq": s.seq, "late_ms": late])
    }
    if x.live && x.last >= 0 && s.seq <= x.last && !x.resent {  // 11-08 16:42: 20704→0, then 41 s behind all along
      x.resent = true
      log("bean_xfer", "实时文件 \(x.file) 豆子从单元 \(x.last) 退回到 \(s.seq) 重发：这次传输之后的单元不再用来对钟", "warn",
          ["file": x.file, "from": x.last, "to": s.seq])
    }
    x.units += 1; x.last = max(x.last, s.seq); x.seen = now
    if overWifi { wifiUnits += 1 }
    // a read behind (not caught up to live yet): how fast the bean sends, for owe()'s estimate
    if !x.realtime && x.lag == nil && s.seq - x.first >= BeanSync.restUnits && now > x.firstAt {
      pace = Double((s.seq - x.first) * Bean.frameMs) / Double(now - x.firstAt)
    }
    if x.live {
      liveSeen = now; liveStalled = false; busy = 0
      // the link can slow to a fraction of real time and stay up (iOS in the background, 10-28): how far behind, once
      // the transfer has caught up (a read from a unit behind starts behind by the hole it fills)
      if !x.resent { reanchor(x, f, s.seq, now - (s.seq + 1) * Bean.frameMs) }
      let lag = now - f.at(s.seq + 1)
      if x.lag != nil || lag <= 10_000 {
        x.lag = lag
        if lagging ? lag <= 10_000 : lag > 60_000 {
          lagging.toggle()
          let fields: [String: Any] = ["file": x.file, "seq": s.seq, "lag_s": lag / 1000]
          log("bean_lag", "实时文件 \(x.file) " + (lagging ? "落后 \(lag / 1000) s" : "追上了，落后 \(lag / 1000) s"), lagging ? "warn" : nil,
              lagging ? fields.merging(diag(Double(s.seq - x.first + 1) * 1000 / Double(max(1, now - x.firstAt)))) { $1 } : fields)
        }
      }
    }
    if s.flags & 4 != 0 { f.total = s.seq + 1 }
    let dupe = has(f, s.seq) || runFile == x.file && s.seq >= runFrom && s.seq < runFrom + Int64(run.count)
    let mark = s.flags & 1 != 0
    if mark && !marked && !dupe { log("mark", "双击标记", fields: ["file": x.file, "seq": s.seq], at: f.at(s.seq)) }
    marked = mark  // also on units had already: a mark that goes on past them is not a new one
    if x.until >= 1 && x.until <= s.seq + 1 { x.cut = true }
    if dupe { x.dupes += 1; return }
    let n = Int64(run.count)  // a piece is dated by one anchor
    if !run.isEmpty && (runFile != x.file || s.seq != runFrom + n || f.at(s.seq) != f.at(runFrom) + n * Bean.frameMs) { flush() }
    if run.isEmpty { runFile = x.file; runFrom = s.seq; if x.live { step(x, f, s.seq) } }
    run.append(Data(audio))
    if !x.live { caught += 1; syncDoneKb = caught * Int64(Bean.unit) / 1024 }
    // off the target (step()): a live piece ends at 5 s, the next one a step nearer, whatever the slice (iOS in the
    // background 20 s, Android with the screen off 60 s: a step a piece could not keep up with 750 ppm)
    let owed = x.target.map { abs($0 - f.latest) >= 5 } ?? false
    if run.count >= (!x.live ? Bean.pieceUnits : owed ? min(io.liveUnits(), BeanSync.stepUnits) : io.liveUnits()) { flush() }
  }

  /// The bean's units do not keep the phone's time (#233: up to 750 ppm slow for half an hour, then on time, 2–3 s over
  /// six hours): while live the file's anchor is measured each half minute. A unit never comes before its end, so the
  /// fastest one of a half minute (`est`: now − (seq + 1) × 20 ms) says it; when two halves in a row agree within 50 ms
  /// the units come as recorded (not a fill catching up, not a link slowed to a fraction) and that is the target.
  private func reanchor(_ x: Xfer, _ f: F, _ seq: Int64, _ est: Int64) {
    if x.half < 0 || seq < x.half { x.half = seq; x.fastest = est; x.before = nil; return }
    x.fastest = min(x.fastest, est)
    if seq - x.half + 1 < BeanSync.clockUnits { return }
    if let b = x.before, abs(x.fastest - b) <= 50 {
      let d = x.fastest - f.latest
      let now = io.now()
      if abs(d) >= 200 || abs(d) >= 20 && now - x.clockSaid >= 600_000 {  // a jump, else at most once in 10 min
        x.clockSaid = now
        log("bean_clock", "文件 \(x.file) 到单元 \(seq) 按序号排比手机的钟\(d >= 0 ? "早" : "晚") \(abs(d)) ms，之后的块逐段对齐",
            fields: ["file": x.file, "seq": seq, "ms": d])
      }
      x.target = x.fastest
    }
    x.before = x.fastest; x.half = seq + 1; x.fastest = .max
  }

  /// A live piece begins at `seq`: from it on the file is dated a step nearer the target, at most 9 ms so the server's
  /// decoder goes on from the piece before (audio.before(): a join within 10 ms, else a click), at once when 0.2 s or
  /// more off. Units before keep their dates, a hole among them filled later too.
  /// ponytail: what is read on return from an absence (the tail before live) goes by the anchor from before it, off by
  /// what the bean drifted meanwhile (≤ 750 ppm × the absence); extrapolate by the last rate if that matters.
  private func step(_ x: Xfer, _ f: F, _ seq: Int64) {
    guard let t = x.target, seq > f.anchors.last?.0 ?? 0 else { return }
    let d = t - f.latest
    if abs(d) >= 5 { f.anchors.append((seq, f.latest + (abs(d) >= 200 ? d : max(-9, min(9, d))))) }
  }

  /// A transfer's first unit: establish the file's anchor. Live never holds finals: backlog is read only after stop.
  private func first(_ x: Xfer, _ s: Bean.Slice, _ audio: [UInt8]) {
    x.first = s.seq; x.firstAt = io.now()
    if x.live && !x.realtime && s.seq == x.from { fills[x.file] = nil }  // the bean honoured the offset: misses start again
    let now = io.now(), byId = x.file * 1000, est = now - s.seq * Bean.frameMs
    if files[x.file] == nil {
      let phone = x.realtime && (-2000...3000).contains(est - byId)  // realtime=0 starts behind: its first unit is not now
      _ = file(x.file, phone ? est : byId)
      if x.realtime {
        log("bean_clock", "文件 \(x.file) 的起点按\(phone ? "手机" : "豆子")的钟：豆子的钟比手机\(est >= byId ? "慢" : "快") \(abs(est - byId)) ms",
            fields: ["file": x.file, "ms": est - byId])
      }
    }
    let toc = Int(audio.first ?? 0)
    log("bean_xfer", "\(x.live ? "实时" : "补传")文件 \(x.file) 开始：首单元 \(s.seq)（请求从 \(x.from) 起），Opus 配置 \(toc >> 3)",
        fields: ["file": x.file, "seq": s.seq, "from": x.from, "realtime": x.realtime, "toc": toc])
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
      if x.last >= 0 { pendingEnd = max(pendingEnd, f.at(x.last + 1)) }
    } else if gap(f) == nil {
      pendingEnd = max(pendingEnd, f.at(f.total))
    } else if !(x.cut && gap(f)! > x.from) {  // cut with the hole filled: not a try
      tried(x.file, f)
    }
    save()
  }

  /// The units taken so far into a piece, dated by its file's anchor.
  private func flush() {
    if run.isEmpty { return }
    guard let f = files[runFile] else { run = []; return }
    let start = f.at(runFrom)
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

  private func done(_ f: F) -> Bool { gap(f) == nil || f.tries > BeanSync.tries || overWifi && f.wifiTries > BeanSync.tries }

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
