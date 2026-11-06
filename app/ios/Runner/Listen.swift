// Listening on the phone: the recorder's LIVE records decoded and played here as they come, a few tenths of a second
// behind its mic and no server on the way, to hear what a solder joint, a case or foam does while changing it. A port
// of firmware/devkit/lr_listen.py, with its line a second for the second just played, on the stream's wall clock ('T'):
// level (RMS), 50 Hz hum (the 50..250 Hz harmonics together), ms in runs of exact zeros of 10 ms or more (the mic sent
// nothing), full-scale samples, ms lost on the link, the delay from the mic to the output (and how much of it is the
// queue here). It only reads the packets the BLE session takes (Ble.swift), on its own queue: the mirror, the pieces
// and their upload go on as before. Listener and Jitter are Foundation only (StoreCheck runs them on Linux too); the
// decoder needs AudioToolbox, the output iOS.
import Foundation

/// LIVE notifications (file:u32 offset:u32 bytes) -> each frame's PCM to `play`, which says in how many s it is heard,
/// and a line a second to `say`. A record comes in MTU pieces. After a gap in the offsets (records left out of LIVE, a
/// new file, listening just turned on, a link encrypted again: the recorder sends before iOS hands its notifications
/// over) the bytes may begin inside a record: they are taken as a record's start only at offset 0 or where two 'A'
/// records parse in a row with their frame numbers going on, and what comes before is dropped. Bytes that stop parsing
/// (a record this app does not know) are dropped the same way once there are more than a record can hold.
final class Listener {
  static let rate = 16000
  static let zero = rate / 100  // a run of exact zeros this long (10 ms) is the mic sending nothing
  static let recordMax = 6 + 10 * 256  // 'A', 10 frames of up to 255 B

  private let decode: (Data) -> [Int16], play: ([Int16]) -> Double, say: ([String: Any]) -> Void
  private var at: (file: Int64, off: Int64)?, buf: [UInt8] = [], aligned = false
  private var sync: (frame: Int64, epochMs: Int64)?, next: Int64?, sec: Int64?  // the latest 'T'; the frame expected next; the second
  private var pcm: [Int16] = [], delay: [Double] = [], queued: [Double] = [], lost: Int64 = 0, z = 0
  // a new decoder's first 30 ms, decoded but not played: it starts inside the stream with no state, and the first frame
  // came out up to 4.5 times too loud from a recording (a click in the ears; a 0.5 sine clipped on the emulator)
  private var warm = 3

  init(decode: @escaping (Data) -> [Int16], play: @escaping ([Int16]) -> Double, say: @escaping ([String: Any]) -> Void) {
    self.decode = decode; self.play = play; self.say = say
  }

  /// One notification, `now` the wall ms it came.
  func feed(_ p: [UInt8], now: Int64) {
    guard p.count > 8 else { return }
    let f = Int64(Store.le(p, 0, 4)), off = Int64(Store.le(p, 4, 4))
    if at?.file != f || at?.off != off { buf = []; aligned = off == 0 }
    at = (f, off + Int64(p.count - 8))
    buf += p[8...]
    if !aligned {
      guard let s = Listener.start(buf) else {
        if buf.count > 2 * Listener.recordMax { buf.removeFirst(buf.count - Listener.recordMax) }
        return
      }
      buf.removeFirst(s)
      aligned = true
    }
    var used = 0
    for (r, end) in Store.scan(Data(buf)) {
      used = Int(end)
      switch r {
      case let .sync(frame, epochMs): sync = (frame, epochMs)
      case let .audio(first, frames): for (i, o) in frames.enumerated() { frame(first + Int64(i), o, now) }
      }
    }
    buf.removeFirst(used)
    if buf.count > Listener.recordMax { aligned = false }  // nothing parses any more: look for a start again
  }

  /// Where a record begins in `b`: the first 'A' from which two audio records parse with frame numbers going on.
  static func start(_ b: [UInt8]) -> Int? {
    b.indices.first { p in
      guard b[p] == 0x41 else { return false }
      let a = Store.scan(Data(b[p...])).compactMap { r -> (first: Int64, n: Int)? in
        if case let .audio(first, frames) = r.rec { return (first, frames.count) }
        return nil
      }
      return a.count >= 2 && a[0].first + Int64(a[0].n) == a[1].first
    }
  }

  private func frame(_ n: Int64, _ opus: Data, _ now: Int64) {
    let x = decode(opus)
    let gap = next.map { max(n - $0, 0) } ?? 0
    next = n + 1
    if warm > 0 { warm -= 1; return }
    let ahead = play(x)
    guard let s = sync else { return }  // not on the wall clock yet: heard, not counted
    let t = s.epochMs + (n - s.frame) * Store.frameMs  // when the mic took it
    if t / 1000 != sec {
      flush()
      sec = t / 1000
    }
    pcm += x
    lost += gap * Store.frameMs
    delay.append(Double(now - t) + ahead * 1000)
    queued.append(ahead * 1000)
  }

  /// The line for the second so far: t (its wall ms), rms and hum (dBFS), zero (ms), full (samples), lost, delay and
  /// queued (ms).
  func flush() {
    guard let s = sec else { return }
    let st = Listener.stats(pcm, z: z)
    z = st.z
    let mean = { (v: [Double]) in v.isEmpty ? 0 : Int((v.reduce(0, +) / Double(v.count)).rounded()) }
    say(["t": s * 1000, "rms": st.rms, "hum": st.hum, "zero": st.zero, "full": st.full, "lost": lost, "delay": mean(delay), "queued": mean(queued)])
    pcm = []; delay = []; queued = []; lost = 0
  }

  /// Samples (a second) -> RMS dBFS, hum dBFS (each harmonic by Goertzel), ms in zero runs of 10 ms or more, full-scale
  /// samples, and the zero run going on at the end (the next second's z).
  static func stats(_ pcm: [Int16], z: Int) -> (rms: Double, hum: Double, zero: Int, full: Int, z: Int) {
    let db = { (ms: Double) in 10 * log10(max(ms, 1e-3) / (32768.0 * 32768)) }  // mean square -> dBFS
    let n = Double(max(pcm.count, 1))
    var hum = 0.0, sq = 0.0, z = z, zero = 0, full = 0
    for f in stride(from: 50, through: 250, by: 50) {
      let w = 2 * cos(2 * Double.pi * Double(f) / Double(rate))
      var s1 = 0.0, s2 = 0.0
      for x in pcm { (s1, s2) = (Double(x) + w * s1 - s2, s1) }
      hum += 2 * (s1 * s1 + s2 * s2 - w * s1 * s2) / n / n
    }
    for x in pcm {
      z = x == 0 ? z + 1 : 0
      zero += z == Listener.zero ? Listener.zero : z > Listener.zero ? 1 : 0
      if abs(Int(x)) >= 32767 { full += 1 }
      sq += Double(x) * Double(x)
    }
    return (db(sq / n), db(hum), zero * 1000 / rate, full, z)
  }
}

/// PCM queued for the output: playing (re)starts once `prime` samples are in; past `max` the oldest go (a burst after a
/// stall; an output clock a little slower than the recorder's: the MacBook's ran 0.3% slow, 10-07).
final class Jitter {
  static let prime = Listener.rate * 15 / 100, max = Listener.rate / 4  // 0.15 s, 0.25 s
  private var q: [Int16] = [], on = false
  private let l = NSLock()

  /// Queues `pcm`; s until it is heard (what is queued before it).
  func put(_ pcm: [Int16]) -> Double {
    l.lock(); defer { l.unlock() }
    q += pcm
    if q.count > Jitter.max { q.removeFirst(q.count - Jitter.max) }
    return Double(q.count - pcm.count) / Double(Listener.rate)
  }

  /// `n` samples for the output (-1...1): silence until `prime` are in, and again after running dry (the link held back).
  func take(_ out: UnsafeMutablePointer<Float>, _ n: Int) {
    l.lock(); defer { l.unlock() }
    on = on || q.count >= Jitter.prime
    let k = on ? min(n, q.count) : 0
    for i in 0..<n { out[i] = i < k ? Float(q[i]) / 32768 : 0 }
    q.removeFirst(k)
    on = on && k == n
  }
}

/// The recorder as it is heard: `db` louder, as the server plays it (PLAY_GAIN_DB in server/alldayrec/audio.py, the same
/// 12 dB: it comes out 11-13 dB quieter than a bean or a phone; this app cannot read the server's setting). Lower only
/// where a frame's peaks or its neighbours' would pass -1 dBFS, ramped across each frame: no hard clip, no click; never
/// below the frame (squeeze.louder's limiter, a frame at a time). A frame waits for the next one's peaks: 10 ms later.
/// The line a second (Listener.stats) stays on the frames as they came: it measures the recorder.
final class Louder {
  static let db = 12.0, peak = 0.89 * 32768
  private let up: Double
  private var held: [Int16] = [], before = 0.0, last = 0.0, g = 1.0  // the frame held; the peaks of the one before it and its own; the gain at its start

  init(db: Double = Louder.db) { up = pow(10, db / 20) }

  /// A decoded frame in; the frame before it out, louder.
  func put(_ x: [Int16]) -> [Int16] {
    let p = Double(x.lazy.map { abs(Int($0)) }.max() ?? 0)
    let end = max(1, min(up, Louder.peak / max(before, last, p, 1)))  // the held frame's gain at its end
    let n = Double(held.count)
    let out = held.enumerated().map { i, v in Int16(clamping: Int((Double(v) * (g + (end - g) * Double(i + 1) / n)).rounded())) }
    held = x; before = last; last = p; g = end
    return out
  }
}

#if canImport(AVFoundation)
import AVFoundation

/// AudioToolbox's Opus decoder (kAudioFormatOpus) for the recorder's frames, one at a time: 16 kHz mono, 10 ms. A
/// frame's samples come out with it (the first 2.5 ms short: the decoder's own delay).
final class OpusDecoder {
  private let opus: AVAudioFormat, conv: AVAudioConverter
  private let pcm = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: Double(Listener.rate), channels: 1, interleaved: true)!

  init() throws {
    var d = AudioStreamBasicDescription(mSampleRate: Double(Listener.rate), mFormatID: kAudioFormatOpus, mFormatFlags: 0, mBytesPerPacket: 0,
                                        mFramesPerPacket: UInt32(Listener.rate / 100), mBytesPerFrame: 0, mChannelsPerFrame: 1, mBitsPerChannel: 0, mReserved: 0)
    guard let opus = AVAudioFormat(streamDescription: &d), let conv = AVAudioConverter(from: opus, to: pcm) else {
      throw NSError(domain: "OpusDecoder", code: 1, userInfo: [NSLocalizedDescriptionKey: "no Opus decoder"])
    }
    self.opus = opus; self.conv = conv
  }

  func decode(_ p: Data) -> [Int16] {
    let i = AVAudioCompressedBuffer(format: opus, packetCapacity: 1, maximumPacketSize: max(p.count, 1))
    p.withUnsafeBytes { if let b = $0.baseAddress { i.data.copyMemory(from: b, byteCount: p.count) } }
    i.byteLength = UInt32(p.count)
    i.packetCount = 1
    i.packetDescriptions![0] = AudioStreamPacketDescription(mStartOffset: 0, mVariableFramesInPacket: 0, mDataByteSize: UInt32(p.count))
    let o = AVAudioPCMBuffer(pcmFormat: pcm, frameCapacity: AVAudioFrameCount(Listener.rate * 120 / 1000))!  // the most one packet holds
    var given = false, err: NSError?
    _ = conv.convert(to: o, error: &err) { _, s in
      if given { s.pointee = .noDataNow; return nil }
      given = true
      s.pointee = .haveData
      return i
    }
    return err == nil ? Array(UnsafeBufferPointer(start: o.int16ChannelData![0], count: Int(o.frameLength))) : []
  }
}
#endif

#if os(iOS)
/// Listening switched on (the recorder page, the app in front): a session for playing on the output the person has
/// (mixable: other audio goes on), the engine pulling from a Jitter, a Listener on its own queue; off: all of it goes.
/// Only while the BLE link is the source: the iPhone's own microphone and its session (Slicer) are off then
/// (Recorder.start runs one or the other).
final class Listen {
  @Shared var lines: [[String: Any]] = []  // the last 20 s
  private let q = DispatchQueue(label: "listen")
  private let engine = AVAudioEngine()
  private let jitter = Jitter()
  private var listener: Listener!
  private var observer: NSObjectProtocol?

  init() throws {
    let dec = try OpusDecoder()
    let session = AVAudioSession.sharedInstance()
    try session.setCategory(.playback, options: [.mixWithOthers])
    try session.setActive(true)
    let fmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Double(Listener.rate), channels: 1, interleaved: false)!
    let jitter = jitter, louder = Louder()
    let src = AVAudioSourceNode(format: fmt) { _, _, n, abl in
      jitter.take(UnsafeMutableAudioBufferListPointer(abl)[0].mData!.assumingMemoryBound(to: Float.self), Int(n))
      return noErr
    }
    engine.attach(src)
    engine.connect(src, to: engine.mainMixerNode, format: fmt)
    try engine.start()
    // headphones in or out: the engine stops; on again with the new output
    observer = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main) { [weak self] _ in
      try? self?.engine.start()
    }
    listener = Listener(decode: dec.decode, play: {  // the frame goes in after the one Louder held: heard that much later
                          let y = louder.put($0)
                          return jitter.put(y) + Double(y.count) / Double(Listener.rate) + session.outputLatency + session.ioBufferDuration
                        },
                        say: { [weak self] l in
                          Ble.log("listen \(l)")
                          if let self { self.lines = Array((self.lines + [l]).suffix(20)) }
                        })
  }

  /// A LIVE notification as it came (on the BLE queue).
  func feed(_ p: [UInt8]) {
    let now = Recorder.now()
    q.async { self.listener.feed(p, now: now) }
  }

  func stop() {
    observer.map(NotificationCenter.default.removeObserver)
    engine.stop()
    q.sync {}
    try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
  }
}
#endif
