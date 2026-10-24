// The hands of app/ios/e2e/uitest.sh: each test is one thing a person would do on the iPhone, in the test app
// (com.suyiiyii.liverecord.uitest, built by uitest.sh: its own data and binding next to the real app, which these tests
// never open), in iOS's dialogs (SpringBoard: Bluetooth permission, pairing) and in Settings › Bluetooth. uitest.sh runs
// them one at a time and judges each step from the test server and LR-P1's adverts; a test passing only means the taps
// went through and the screen said what was expected. Lines "LRUI <what>" in the output are read by uitest.sh.
// Parameters come from uitest.sh's environment with TEST_RUNNER_ in front: LR_NAME (the recorder), LR_DAILY (the real
// app's recorder, never forgotten), LR_E2E (testLaunch: the app's settings, Recorder.launch()), LR_E2E_KILL (testLaunch:
// the app ends itself after that many seconds), LR_PAUSE_S (testPause), LR_WAIT_S (how long to wait for the link).
import XCTest

final class Flow: XCTestCase {
  let app = XCUIApplication(bundleIdentifier: "com.suyiiyii.liverecord.uitest")
  let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
  let settings = XCUIApplication(bundleIdentifier: "com.apple.Preferences")
  let env = ProcessInfo.processInfo.environment
  var recorder: String { env["LR_NAME"] ?? "LR-MAC-BEEF" }
  var waitS: Double { Double(env["LR_WAIT_S"] ?? "") ?? 180 }

  override func setUp() { continueAfterFailure = false }

  // the app's own pages

  /// The app started fresh with uitest.sh's settings (and, given LR_E2E_KILL, ending itself as the system ends an app).
  func testLaunch() {
    app.terminate()
    app.launchEnvironment = env.filter { $0.key == "LR_E2E" || $0.key == "LR_E2E_KILL" }
    app.launch()
    dialogs(5)
    XCTAssert(app.wait(for: .runningForeground, timeout: 20))
  }

  /// Adding the recorder: the add list, 强制绑定 (or 绑定), iOS's pairing dialog; when iOS still has keys the recorder
  /// forgot, the app's advice to forget it in Settings, done there; until the status sheet says it follows live.
  func testAdd() {
    page("录音设备")
    tap(button(["添加录音设备", "重新添加"]))
    let allow = app.buttons["允许"]  // the Bluetooth permission, the first time
    if allow.waitForExistence(timeout: 3) { allow.tap(); dialogs(10) }
    let row = text(recorder)
    XCTAssert(row.waitForExistence(timeout: 60), "\(recorder) not in the add list")
    sleep(2)  // its state from the scan response
    let force = button(["强制绑定"]), bind = button(["绑定"])
    if force.exists { force.tap(); tap(button(["强制绑定"])); say("bound force") } else { tap(bind); say("bound plain") }
    page("时间线")
    var advised = false
    XCTAssert(until(waitS) {
      self.dialogs(0)
      if !advised && self.text("忽略此设备").exists {  // iOS's stale keys: the app's advice, followed
        advised = true
        self.say("advice shown")
        self.forget()
        self.app.activate()
      }
      return self.following()
    }, "not following \(recorder) live within \(waitS) s")
    say("connected advice=\(advised)")
  }

  /// The recorder page's pause, for LR_PAUSE_S, and going on.
  func testPause() {
    page("录音设备")
    tap(button(["暂停 15 分钟"]))
    XCTAssert(text("已暂停").waitForExistence(timeout: 15), "the page does not say 已暂停")
    say("paused")
    sleep(UInt32(env["LR_PAUSE_S"] ?? "") ?? 60)
    tap(button(["继续"]))
    XCTAssert(text("正在录音").waitForExistence(timeout: 15), "the page does not say 正在录音")
    say("resumed")
  }

  /// The app as the person opens it again (from the background or after a quit): it follows live within LR_WAIT_S.
  func testOpen() {
    app.activate()
    dialogs(3)
    page("时间线")
    XCTAssert(until(waitS) { self.dialogs(0); return self.following() }, "not following live within \(waitS) s")
    say("connected")
  }

  /// Swiped away (as from the app switcher).
  func testQuit() { app.terminate() }

  func testHome() { XCUIDevice.shared.press(.home) }

  /// The lock button (XCUIDevice's private pressLockButton, as WebDriverAgent uses): the phone then needs unlocking.
  func testLock() { XCUIDevice.shared.perform(NSSelectorFromString("pressLockButton")) }

  /// Another phone took the recorder: the recorder page says so.
  func testKicked() {
    app.activate()
    page("录音设备")
    XCTAssert(text("已被其他手机绑定").waitForExistence(timeout: waitS), "the page does not say 已被其他手机绑定")
    say("kicked page")
  }

  /// 解除绑定, then forgetting it in Settings › Bluetooth as the app asks.
  func testUnbind() {
    page("录音设备")
    tap(reveal(text("解除绑定"), in: app))  // at the bottom of the page
    tap(button(["解除绑定"]))
    XCTAssert(text("未绑定").waitForExistence(timeout: 60), "the page does not say 未绑定")
    say("unbound")
    forget()
  }

  // Settings › Bluetooth

  /// Is the recorder listed (under My Devices: the 忽略此设备 advice needs it)?
  func testListed() {
    bluetooth()
    let listed = row(recorder).waitForExistence(timeout: 10)
    say("listed \(listed)")
    if !listed { print(settings.debugDescription) }
    app.activate()
    XCTAssert(listed, "\(recorder) not in Settings › Bluetooth")
  }

  func testForget() { forget() }

  /// What the screen holds, for fixing a test whose labels no longer match.
  func testDump() { print(app.debugDescription, springboard.debugDescription, settings.debugDescription) }

  /// Settings › Display & Brightness › Auto-Lock set to LR_AUTOLOCK (永不 for the run; the old one after it), saying
  /// what it was: steps leave the phone alone for minutes and must find it unlocked.
  func testAutoLock() {
    settings.terminate()
    settings.launch()
    tap(reveal(settings.descendants(matching: .any).matching(NSPredicate(
      format: "identifier BEGINSWITH 'com.apple.settings.display' OR label IN %@", ["显示与亮度", "Display & Brightness"])).firstMatch, in: settings))
    tap(settings.staticTexts.matching(NSPredicate(format: "label IN %@", ["自动锁定", "Auto-Lock"])).firstMatch)
    let was = settings.cells.matching(NSPredicate(format: "isSelected == true")).firstMatch
    XCTAssert(was.waitForExistence(timeout: 10), "no Auto-Lock option selected (Low Power Mode holds it at 30 s?)")
    say("autolock was \(was.label)")
    let want = env["LR_AUTOLOCK"] ?? "永不", wants = want == "永不" ? ["永不", "Never"] : [want]  // the phone's language
    tap(settings.descendants(matching: .any).matching(NSPredicate(format: "label IN %@", wants)).firstMatch)
    XCTAssert(until(5) { wants.contains(was.label) }, "Auto-Lock is \(was.label), not \(want)")
    say("autolock now \(was.label)")
    app.activate()
  }

  func testBluetoothOff() { bluetoothSwitch(false) }

  func testBluetoothOn() {
    bluetoothSwitch(true)
    app.activate()
  }

  // helpers

  func say(_ s: String) { print("LRUI \(s)") }

  /// Waits up to `s` seconds for `f`, every second.
  func until(_ s: Double, _ f: () -> Bool) -> Bool {
    let end = Date(timeIntervalSinceNow: s)
    while Date() < end { if f() { return true }; sleep(1) }
    return f()
  }

  /// An element whose label contains `s` (Flutter merges a tile's texts into one label).
  func text(_ s: String, in a: XCUIApplication? = nil) -> XCUIElement {
    (a ?? app).descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", s)).firstMatch
  }

  func button(_ labels: [String], in a: XCUIApplication? = nil) -> XCUIElement {
    (a ?? app).buttons.matching(NSPredicate(format: "label IN %@", labels)).firstMatch
  }

  /// `e`, the list it is in swiped up until it is on the screen (long lists build only what is near it).
  func reveal(_ e: XCUIElement, in a: XCUIApplication) -> XCUIElement {
    sleep(1)
    let list = a.collectionViews.firstMatch  // Settings' list (swiping the whole app did not scroll it, 10-06)
    for _ in 0..<6 where !(e.exists && e.isHittable) { (a == settings && list.exists ? list : a).swipeUp() }
    return e
  }

  func tap(_ e: XCUIElement, timeout: Double = 15) {
    if !e.waitForExistence(timeout: timeout) { testDump(); XCTFail("no \(e)") }
    e.tap()
  }

  /// The top bar's chip says the recorder is connected, and its status sheet that the app follows live (the sheet is
  /// closed again).
  func following() -> Bool {
    let chip = app.buttons.matching(NSPredicate(format: "label BEGINSWITH '已连接'")).firstMatch
    guard chip.exists else { return false }
    chip.tap()
    let live = text("已连接 · 实时上传").waitForExistence(timeout: 5)
    app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.2)).tap()  // the scrim above the sheet
    sleep(1)
    return live
  }

  /// One of the app's pages: a tab of its bottom bar, or one of 我的's entries (录音设备, 上传, 设置).
  func page(_ title: String) {
    app.activate()
    let back = button(["Back", "返回"])  // one of 我的's pages is open: back to the tabs
    for _ in 0..<3 where back.exists { back.tap(); sleep(1) }
    let tabs = ["时间线", "实时", "人物", "我的"]
    // "我的\nTab 4 of 4"
    tap(app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@ AND label CONTAINS 'Tab '",
                                                             tabs.contains(title) ? title : "我的")).firstMatch)
    if !tabs.contains(title) { tap(app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", title)).firstMatch) }
    sleep(1)
  }

  /// iOS's dialogs over whatever runs, for up to `s` seconds: Bluetooth permission and pairing are said yes to.
  @discardableResult
  func dialogs(_ s: Double) -> Bool {
    let yes = button(["配对", "Pair", "允许", "Allow", "好", "OK"], in: springboard)
    var tapped = false
    _ = until(s) {
      if yes.exists { print("LRUI dialog \(springboard.alerts.firstMatch.label): \(yes.label)"); yes.tap(); tapped = true; sleep(1) }
      return tapped && !yes.exists
    }
    return tapped
  }

  /// The recorder's row in Settings › Bluetooth.
  func row(_ n: String) -> XCUIElement { settings.cells.containing(NSPredicate(format: "label BEGINSWITH %@", n)).firstMatch }

  /// Settings › Bluetooth, from the top.
  func bluetooth() {
    settings.terminate()
    settings.launch()
    tap(reveal(settings.staticTexts.matching(NSPredicate(format: "label IN %@", ["蓝牙", "Bluetooth"])).firstMatch, in: settings))
    XCTAssert(settings.switches.matching(NSPredicate(format: "label IN %@", ["蓝牙", "Bluetooth"])).firstMatch.waitForExistence(timeout: 10))
  }

  func bluetoothSwitch(_ on: Bool) {
    bluetooth()
    let sw = settings.switches.matching(NSPredicate(format: "label IN %@", ["蓝牙", "Bluetooth"])).firstMatch
    if (sw.value as? String == "1") != on { sw.tap() }
    XCTAssert(until(10) { (sw.value as? String == "1") == on }, "Bluetooth did not turn \(on ? "on" : "off")")
    say("bluetooth \(on ? "on" : "off")")
  }

  /// Settings › Bluetooth › the recorder ⓘ › 忽略此设备 (nothing to do when it is not listed).
  func forget() {
    // only the Mac's fake, unless uitest.sh runs with a real board (LR_REAL=1); never the real app's (LR_DAILY)
    XCTAssert(recorder.hasPrefix("LR-MAC-") || (env["LR_REAL"] == "1" && recorder != env["LR_DAILY"]), "not forgetting \(recorder)")
    bluetooth()
    let cell = row(recorder)
    guard cell.waitForExistence(timeout: 10) else { return say("forget: \(recorder) not listed") }
    let info = cell.buttons.matching(NSPredicate(format: "label IN %@", ["更多信息", "More Info"])).firstMatch
    tap(info.exists ? info : cell.buttons.element(boundBy: max(0, cell.buttons.count - 1)))  // ⓘ
    tap(settings.descendants(matching: .any).matching(NSPredicate(format: "label IN %@", ["忽略此设备", "Forget This Device"])).firstMatch)
    tap(button(["忽略设备", "Forget Device"], in: settings))
    XCTAssert(until(15) { !self.row(self.recorder).exists }, "\(recorder) still listed")
    say("forgot \(recorder)")
  }
}
