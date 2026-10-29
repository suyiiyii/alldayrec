// The BLE recorder's record stream (firmware src/lr_store.h, reference client firmware/devkit/lr_sync.py), port of the
// Android app's Store.kt: files of 'A' records (consecutive 10 ms Opus frames from `first`) and 'T' records (frame
// `frame` was at the phone's `epochMs`), named boot << 16 | segment. A frame's wall time is its boot's nearest 'T' +
// 10 ms per frame. Foundation only: app/ios/StoreCheck runs it on a Mac and in CI (Linux).
import Foundation

#if !canImport(ObjectiveC)
/// Linux (StoreCheck in CI) has no autorelease pools: the body as is.
func autoreleasepool<Result>(invoking body: () throws -> Result) rethrows -> Result { try body() }
#endif

enum Store {
  static let frameMs: Int64 = 10
  static let oldPieceFrames = 6000  // a file the board has closed goes up in 60 s pieces

  enum Rec {
    case audio(first: Int64, frames: [Data])
    case sync(frame: Int64, epochMs: Int64)
  }

  /// Little-endian unsigned integer of `n` bytes at `at`.
  static func le(_ b: [UInt8], _ at: Int, _ n: Int) -> UInt64 { (0..<n).reduce(0) { $0 | UInt64(b[at + $1]) << (8 * $1) } }

  static func le<T: FixedWidthInteger>(_ v: T) -> [UInt8] { withUnsafeBytes(of: v.littleEndian) { Array($0) } }

  /// The board takes ACK (status byte 48 bit 0; firmware phone mode: it keeps what the phone acked off its card). Older
  /// firmware has no such byte, and would take an ACK as a command that ends its READ: none is sent to it.
  static func acks(_ status: [UInt8]) -> Bool { status.count > 48 && status[48] & 1 != 0 }

  /// CTRL 0x08 ACK: the mirror has `f` up to `have`, all of it (of other files it says nothing).
  static func ack(_ f: Int64, _ have: Int64) -> [UInt8] {
    [8] + le(UInt32(truncatingIfNeeded: f)) + le(UInt32(truncatingIfNeeded: have))
  }
  static let ackEvery: TimeInterval = 5  // the board writes its RAM to the card after 8 s without one

  /// The whole records of `d` and the offset each ends at (relative to `base`); a record cut short ends the list.
  static func scan(_ d: Data, base: Int64 = 0) -> [(rec: Rec, end: Int64)] {
    let b = [UInt8](d)
    var out: [(rec: Rec, end: Int64)] = []
    var i = 0
    while i < b.count {
      if b[i] == 0x41 && i + 6 <= b.count {  // 'A'
        let n = Int(b[i + 1]), first = Int64(le(b, i + 2, 4))
        var j = i + 6, frames: [Data] = []
        for _ in 0..<n {
          guard j < b.count, j + 1 + Int(b[j]) <= b.count else { return out }
          frames.append(Data(b[(j + 1)..<(j + 1 + Int(b[j]))]))
          j += 1 + Int(b[j])
        }
        i = j
        out.append((.audio(first: first, frames: frames), base + Int64(i)))
      } else if b[i] == 0x54 && i + 13 <= b.count {  // 'T'
        out.append((.sync(frame: Int64(le(b, i + 1, 4)), epochMs: Int64(bitPattern: le(b, i + 5, 8))), base + Int64(i + 13)))
        i += 13
      } else {
        return out
      }
    }
    return out
  }

  /// 16 kHz Opus packets of `ms` each into one Ogg Opus file (RFC 7845), as Store.kt and web/src/lib/ogg.ts do:
  /// pre-skip 0 (the board's encoder runs on across pieces), granule counting every frame from the piece's start. The
  /// bean's (Bean.swift): 20 ms frames, stereo.
  static func oggOpus(_ frames: [Data], frameMs ms: Int64 = frameMs, channels: UInt8 = 1) -> Data {
    let serial = UInt32.random(in: 0...UInt32.max)
    var head = Data("OpusHead".utf8)
    for part in [[1, channels], le(UInt16(0)), le(UInt32(16000)), [0, 0, 0]] { head.append(contentsOf: part) }  // version, channels, pre-skip, rate, gain, mapping
    var out = Ogg.page([head], flags: 2, granule: 0, serial: serial, seq: 0)
    out += Ogg.page([Data("OpusTags".utf8) + Data(count: 8)], flags: 0, granule: 0, serial: serial, seq: 1)
    var seq: UInt32 = 2
    for i in stride(from: 0, to: frames.count, by: 50) {
      let end = min(i + 50, frames.count)
      out += Ogg.page(Array(frames[i..<end]), flags: end == frames.count ? 4 : 0, granule: Int64(end) * ms * 48, serial: serial, seq: seq)
      seq += 1
    }
    return out
  }
}

/// A value several threads read and one writes (a link's state for the app's page).
@propertyWrapper final class Shared<T> {
  private var v: T
  private let l = NSLock()
  init(wrappedValue: T) { v = wrappedValue }
  var wrappedValue: T {
    get { l.lock(); defer { l.unlock() }; return v }
    set { l.lock(); v = newValue; l.unlock() }
  }
}

/// One Ogg page (RFC 3533), for the microphone's pieces (OggOpus.swift) and the recorder's (Store.oggOpus).
enum Ogg {
  static func page(_ packets: [Data], flags: UInt8, granule: Int64, serial: UInt32, seq: UInt32) -> Data {
    var lacing: [UInt8] = []
    for p in packets {
      var n = p.count
      while true { lacing.append(UInt8(min(n, 255))); if n < 255 { break }; n -= 255 }
    }
    var out = Data("OggS".utf8)
    for part in [[0, flags], Store.le(UInt64(granule)), Store.le(serial), Store.le(seq), Store.le(UInt32(0)), [UInt8(lacing.count)], lacing] {
      out.append(contentsOf: part)
    }
    for p in packets { out.append(p) }
    var c: UInt32 = 0
    for b in out { c = (c << 8) ^ crc[Int(((c >> 24) ^ UInt32(b)) & 0xff)] }
    out.replaceSubrange(22..<26, with: Store.le(c))
    return out
  }

  private static let crc: [UInt32] = (0..<256).map { i in
    var r = UInt32(i) << 24
    for _ in 0..<8 { r = r & 0x8000_0000 != 0 ? (r << 1) ^ 0x04c1_1db7 : r << 1 }
    return r
  }
}

/// The recorder's files mirrored byte for byte in `dir` (<file>.BIN, as on its card), and how far each has become Ogg
/// pieces for the upload queue. One thread uses it (the BLE session's). State in `dir`/state.txt, as Store.kt keeps it:
///   F file closed size cut tscan finished piece,piece...   per file: closed (the stream goes on in another), its
///                                 size on the card (-1: unknown), bytes turned into pieces, bytes scanned for 'T',
///                                 all of it in pieces, the pieces written
///   T clock frame epochMs                        the 'T' records seen; clock: the boot, or the file for boot 0 (no
///                                                card to count boots: each such boot has its own files)
///   C file cut [piece]                           a piece made since (or frames passed over): appended, see cut()
final class Mirror {
  static let listMax = 64

  final class F {
    var closed = false, size: Int64 = -1, cut: Int64 = 0, tscan: Int64 = 0, pieces: [String] = [], finished = false
  }

  private(set) var files: [Int64: F] = [:]
  private var syncs: [Int64: [(frame: Int64, epochMs: Int64)]] = [:]
  private let dir: URL, state: URL
  private let fm = FileManager.default

  init(_ dir: URL) {
    self.dir = dir
    state = dir.appendingPathComponent("state.txt")
    try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
    for l in ((try? String(contentsOf: state, encoding: .utf8)) ?? "").split(separator: "\n") {
      let p = l.split(separator: " ", omittingEmptySubsequences: false).map(String.init)
      if p[0] == "F", p.count >= 7, let f = Int64(p[1], radix: 16) {
        let s = F()
        s.closed = p[2] == "1"; s.size = Int64(p[3]) ?? -1; s.cut = Int64(p[4]) ?? 0; s.tscan = Int64(p[5]) ?? 0
        s.finished = p[6] == "1"; s.pieces = p.count > 7 ? p[7].split(separator: ",").map(String.init) : []
        files[f] = s
      } else if p[0] == "T", p.count >= 4, let c = Int64(p[1], radix: 16), let fr = Int64(p[2]), let e = Int64(p[3]) {
        syncs[c, default: []].append((fr, e))
      } else if p[0] == "C", p.count >= 3, let f = Int64(p[1], radix: 16), let c = Int64(p[2]) {
        let s = file(f)
        s.cut = c
        if p.count > 3 { s.pieces.append(p[3]) }
      }
    }
  }

  private static func hex(_ f: Int64) -> String {
    let s = String(f, radix: 16, uppercase: true)
    return String(repeating: "0", count: max(0, 8 - s.count)) + s
  }

  private func save() {
    var t = ""
    for f in files.keys.sorted() {
      let s = files[f]!
      t += "F \(Mirror.hex(f)) \(s.closed ? 1 : 0) \(s.size) \(s.cut) \(s.tscan) \(s.finished ? 1 : 0) \(s.pieces.joined(separator: ","))\n"
    }
    for (c, ts) in syncs { for x in ts { t += "T \(Mirror.hex(c)) \(x.frame) \(x.epochMs)\n" } }
    try? Data(t.utf8).write(to: state, options: .atomic)
  }

  /// A piece made (`name`) or frames passed over: one line appended, not the whole state again. save() per piece wrote
  /// every piece name of the open file each time: at 1 s pieces ~150 MB an hour, more the longer the file. The
  /// next save() folds these lines in; tscan is not in them: a restart scans again from the saved one, finding the
  /// same 'T' records.
  private func cut(_ f: Int64, _ name: String?) {
    guard let h = FileHandle(forWritingAtPath: state.path) else { return save() }  // no state yet
    defer { try? h.close() }
    _ = try? h.seekToEnd()
    try? h.write(contentsOf: Data("C \(Mirror.hex(f)) \(files[f]!.cut)\(name.map { " \($0)" } ?? "")\n".utf8))
  }

  private func bin(_ f: Int64) -> URL { dir.appendingPathComponent("\(Mirror.hex(f)).BIN") }

  func have(_ f: Int64) -> Int64 {
    guard let h = FileHandle(forReadingAtPath: bin(f).path) else { return 0 }
    defer { try? h.close() }
    return Int64((try? h.seekToEnd()) ?? 0)
  }

  private func clock(_ f: Int64) -> Int64 { f >> 16 == 0 ? f : f >> 16 }

  private func file(_ f: Int64) -> F {
    if let s = files[f] { return s }
    let s = F()
    files[f] = s
    return s
  }

  /// Bytes of file `f` at `off` (LIVE, or READ's DATA). Returns where a hole starts when they come after one (the
  /// caller READs from there), nil when taken. Bytes the mirror has already are the same: skipped.
  func put(_ f: Int64, _ off: Int64, _ b: Data) throws -> Int64? {
    let s = file(f)
    let h = have(f)
    if off > h { return h }
    if off + Int64(b.count) > h {
      if !fm.fileExists(atPath: bin(f).path) { _ = fm.createFile(atPath: bin(f).path, contents: nil) }
      guard let w = FileHandle(forWritingAtPath: bin(f).path) else { throw CocoaError(.fileWriteUnknown) }
      defer { try? w.close() }
      try w.seekToEnd()
      try w.write(contentsOf: b.suffix(from: b.startIndex + Int(h - off)))
    }
    if s.finished && have(f) > h { s.finished = false }
    return nil
  }

  /// What LIST says: files on the card with their sizes, and the one the stream goes on in (kept, listed or not). A
  /// LIST gives at most listMax files (firmware list()), so only a shorter one says that a file it leaves out is no
  /// longer on the card (deleted elsewhere, another card): closed at what the mirror has.
  func listed(_ card: [Int64: Int64], open: Int64) {
    for (f, size) in card { let s = file(f); s.size = size; s.closed = f != open }
    if open >= 0 { file(open).closed = false }
    if card.count < Mirror.listMax {
      for (f, s) in files where card[f] == nil && f != open { s.closed = true; s.size = have(f) }
    }
    save()
  }

  /// Files with bytes on the card the mirror lacks: (file, from).
  func missing() -> [(Int64, Int64)] { files.keys.sorted().filter { files[$0]!.size > have($0) }.map { ($0, have($0)) } }

  /// A file whose card copy cannot give more (its READ ends where the mirror does): take what the mirror has.
  func noMore(_ f: Int64) {
    guard let s = files[f] else { return }
    s.size = min(s.size, have(f))
    save()
  }

  private func bytes(_ f: Int64, _ from: Int64, max: Int? = nil) -> Data {
    guard let h = FileHandle(forReadingAtPath: bin(f).path) else { return Data() }
    defer { try? h.close() }
    try? h.seek(toOffset: UInt64(from))
    return (try? max.map { try h.read(upToCount: $0) ?? Data() } ?? h.readToEnd()) ?? Data()
  }

  /// Every 'T' record the mirror has: a later file may date an earlier one.
  private func scanSyncs() {
    for f in files.keys.sorted() where have(f) > files[f]!.tscan {
      let s = files[f]!
      for (r, end) in Store.scan(bytes(f, s.tscan), base: s.tscan) {
        if case let .sync(frame, epochMs) = r, !(syncs[clock(f)] ?? []).contains(where: { $0.frame == frame }) {
          syncs[clock(f), default: []].append((frame, epochMs))
        }
        s.tscan = end
      }
    }
  }

  /// Where the next piece of the files from `f0` on will start (unix ms), as pieces() will name it; nil when none of
  /// them has a dated frame yet. A phone sending these ahead of older audio still on the card says so first.
  func nextStart(from f0: Int64) -> Int64? {
    scanSyncs()
    for f in files.keys.sorted() where f >= f0 {
      let s = files[f]!
      guard !s.finished, let ts = syncs[clock(f)], !ts.isEmpty else { continue }
      for (r, _) in Store.scan(bytes(f, s.cut, max: 8192), base: s.cut) {  // a record is at most 2.6 KB
        if case let .audio(first, _) = r {
          let near = ts.min { abs($0.frame - first) < abs($1.frame - first) }!
          return near.epochMs + (first - near.frame) * Store.frameMs
        }
      }
    }
    return nil
  }

  /// Turns what the mirror has into Ogg pieces in `out`, `start@tag.ogg`, `start` = the frame's boot's nearest 'T' +
  /// 10 ms per frame. Pieces end at a gap in the frames, at `liveFrames` (the open file) or 60 s (a closed one), and
  /// at the end of a closed file the mirror has whole; a file's last frames otherwise wait for more. Frames of a boot
  /// with no 'T' yet wait for one; a file of a boot that is over (`boot` is the live one) and never had one goes to
  /// `undated` once the mirror has it whole, as one Ogg of all its frames (its runs back to back: no time to put
  /// them at anyway).
  /// Returns the number of pieces written.
  func pieces(_ out: URL, _ undated: URL, tag: String, liveFrames: Int, boot: Int) throws -> Int {
    scanSyncs()
    var n = 0
    for f in files.keys.sorted() {
      let s = files[f]!
      let whole = s.closed && s.size >= 0 && s.size <= have(f)
      if s.finished || (have(f) <= s.cut && !whole) { continue }
      let max = s.closed ? Store.oldPieceFrames : liveFrames
      let ts = syncs[clock(f)] ?? []
      let dead = f >> 16 == 0 ? whole : Int(f >> 16) < boot
      if ts.isEmpty && !(dead && whole) { continue }  // waits for this boot's first 'T'
      var first: Int64 = -1
      var run: [Data] = []
      var lost: [Data] = []  // frames that will never have a time
      func emit(_ end: Int64) throws {
        var piece: String?
        if !run.isEmpty {
          if ts.isEmpty {
            lost += run
          } else {
            let near = ts.min { abs($0.frame - first) < abs($1.frame - first) }!
            let name = "\(near.epochMs + (first - near.frame) * Store.frameMs)@\(tag)"
            let part = out.appendingPathComponent("\(name).part"), ogg = out.appendingPathComponent("\(name).ogg")
            try Store.oggOpus(run).write(to: part)
            try? fm.removeItem(at: ogg)  // the same piece again (a kill before the state was saved)
            try fm.moveItem(at: part, to: ogg)
            s.pieces.append(name)
            piece = name
            n += 1
          }
        }
        s.cut = end
        run.removeAll()
        cut(f, piece)
      }
      var last = s.cut  // where the last whole record ends
      for (r, end) in Store.scan(bytes(f, s.cut), base: s.cut) {
        if case let .audio(rf, frames) = r {
          if !run.isEmpty && rf != first + Int64(run.count) { try emit(last) }  // a gap: frames lost on the way
          if run.isEmpty { first = rf }
          run += frames
        }
        last = end
        if run.isEmpty { s.cut = end } else if run.count >= max { try emit(end) }
      }
      if whole {
        try emit(last)
        if !lost.isEmpty {
          try fm.createDirectory(at: undated, withIntermediateDirectories: true)
          try Store.oggOpus(lost).write(to: undated.appendingPathComponent("\(tag)-\(Mirror.hex(f)).ogg"))
        }
        s.finished = true
        save()
      }
    }
    return n
  }

  /// Closed files the mirror has whole, all turned into pieces, the pieces uploaded (gone from `out`): the card may
  /// delete them.
  func done(_ out: URL) -> [Int64] {
    files.keys.sorted().filter { f in
      let s = files[f]!
      return s.closed && s.finished && !s.pieces.contains {
        fm.fileExists(atPath: out.appendingPathComponent("\($0).ogg").path) || fm.fileExists(atPath: out.appendingPathComponent("\($0).part").path)
      }
    }
  }

  /// None of the pieces it cut that start before `until` is still in `out` waiting to go up. With `until` where LIVE
  /// began and pieces() run just before, the backlog is all up and the server's hold may go. 10-08: the hold went
  /// 1-3 s early, before the last READ's bytes were cut, and while the backlog's last minute (between the hold and where
  /// LIVE began) was still queued; those pieces came in behind the server's queue.
  func up(_ out: URL, before until: Int64) -> Bool {
    !files.values.contains { s in
      s.pieces.contains { n in
        (Int64(n.prefix { $0 != "@" }) ?? .max) < until
          && ["ogg", "part"].contains { fm.fileExists(atPath: out.appendingPathComponent("\(n).\($0)").path) }
      }
    }
  }

  /// A file the card has deleted: gone from the mirror too (its 'T' records stay for the rest of the boot).
  func drop(_ f: Int64) {
    try? fm.removeItem(at: bin(f))
    files[f] = nil
    save()
  }

  /// How far one boot's 'T' records may disagree on when it began (epochMs - frame * 10 ms): the frame clock runs
  /// within ~30 ppm of the phone's (59998-60000 frames in 10 min), frames a stalled codec lost move it on.
  static let sameBootMs: Int64 = 60_000

  /// The board is on boot `b` at `frame` now (`at`: the phone's clock then). A boot number can come back: the card's
  /// count read as 0 and it counted from 1 again (10-13: the new audio dated by 10-10's 'T' records of boot 1,
  /// three days back; its files taken for 10-10's of the same name: not READ, deleted from the card). Firmware e4cefed
  /// counts past the chip's own count too, which an erased chip still loses. The mirror's files and 'T' records of
  /// boot `b` are an earlier boot's when a 'T' is at a later frame than now (a boot's frames only go on) or puts the
  /// boot's start elsewhere. Files of `b` with no 'T' at all are taken for this boot's: a link that dropped before the
  /// READ reached this boot's 'T' leaves just that (older firmware wrote it at the end of a file of hours).
  /// Those files are cut into pieces as far as the mirror has them, by their own 'T' records, then gone with the 'T'
  /// records: the card's files of that name are this boot's, READ from 0. True: there was such a boot.
  func reused(boot b: Int64, frame: Int64, at: Int64, out: URL, undated: URL, tag: String) throws -> Bool {
    if b == 0 { return false }  // no card at boot: each file is its own clock
    scanSyncs()
    let mine = files.keys.filter { $0 >> 16 == b }, ts = syncs[b] ?? [], began = at - frame * Store.frameMs
    if !ts.contains(where: { $0.frame > frame || abs($0.epochMs - $0.frame * Store.frameMs - began) > Mirror.sameBootMs }) { return false }
    for f in mine { files[f]!.closed = true; files[f]!.size = have(f) }
    _ = try pieces(out, undated, tag: tag, liveFrames: Store.oldPieceFrames, boot: Int(b) + 1)  // that boot is over
    for f in mine { try? fm.removeItem(at: bin(f)); files[f] = nil }
    syncs[b] = nil
    save()
    return true
  }
}
