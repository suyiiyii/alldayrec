// Presentation of native recorder hardware and pause status, and of what the native side says as codes.
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'l10n/app_localizations.dart';

const recordingRed = Color(0xffc62828);

/// What a BLE recorder runs on (its status' `charge`; none from an older firmware).
String? chargeText(AppLocalizations l, Object? charge) =>
    switch (charge) { 'battery' => l.onBattery, 'charging' => l.charging, 'full' => l.charged, _ => null };

/// A BLE recorder's card when it does not record to it: its status' store is an errno. An error with nothing mounted
/// this boot (total_kb 0) is no card or one not seated (-19; a mount that timed out, -116: LR-P3-2420 without one,
/// 10-07); -28 full; -16 lent as a USB disk; any other error a card that failed once mounted. tile: under 存储卡.
({String tile, String line, String sub})? cardState(AppLocalizations l, Map hw) {
  final store = hw['store'] as int?, total = (hw['total_kb'] as num?) ?? 0;
  if (store == null || store == 0) return null;
  return switch (store) {
    -28 => (tile: l.cardFullTile, line: l.cardFull, sub: l.cardFullSub),
    -16 => (tile: l.cardUsbTile, line: l.cardUsb, sub: l.cardUsbSub),
    _ when total == 0 => (tile: l.cardNoneTile, line: l.cardNone, sub: l.cardNoneSub),
    _ => (tile: l.cardErrorTile, line: l.cardError, sub: l.cardErrorSub),
  };
}

/// What a recorder's scan response says of its power and card, for the add-recorder list (after the
/// state byte, whose bits 3-4 are the charge, the battery mV / 20 and the card's errno); '' from an older firmware.
String advText(AppLocalizations l, Map d) {
  final state = d['state'] as int, mv = d['mv'] as int? ?? -1, card = d['card'] as int? ?? -1;
  if (state < 0 || mv < 0) return '';
  final charge = ['battery', 'charging', 'full'].elementAtOrNull(state >> 3 & 3);
  return [
    if (charge == 'battery' && mv > 0) l.advBattery((mv / 1000).toStringAsFixed(2)) else ?chargeText(l, charge),
    switch (card) { 0 => l.advCardOk, 28 => l.advCardFull, 16 => l.cardUsbTile, 19 || 116 => l.cardNoneTile, _ => l.advCardError },
  ].join(' · ');
}

/// A BLE recorder's battery and card in a few words.
String hwLine(AppLocalizations l, Map? hw) {
  if (hw == null || hw.isEmpty) return '';
  final free = hw['free_kb'], total = hw['total_kb'];
  final card = cardState(l, hw)?.line ?? (total is num && total > 0 ? l.cardFreeGb((free / 1048576).toStringAsFixed(1)) : '');
  return [if (hw['battery'] != null) l.batteryPercent(hw['battery']), ?chargeText(l, hw['charge']), if (card.isNotEmpty) card].join(' · ');
}

/// So many KB as KB, MB or GB.
String sizeText(num kb) => kb >= 1048576 ? '${(kb / 1048576).toStringAsFixed(2)} GB' : kb >= 1024 ? '${(kb / 1024).toStringAsFixed(1)} MB' : '$kb KB';

/// A battery glyph with as many bars as the level (unknown: a question mark).
IconData batteryIcon(int? p) {
  if (p == null) return Icons.battery_unknown_rounded;
  const icons = [Icons.battery_0_bar_rounded, Icons.battery_1_bar_rounded, Icons.battery_2_bar_rounded, Icons.battery_3_bar_rounded,
    Icons.battery_4_bar_rounded, Icons.battery_5_bar_rounded, Icons.battery_6_bar_rounded, Icons.battery_full_rounded];
  return icons[(p * 7 / 100).round().clamp(0, 7)];
}

/// A battery level's colour on the native pages: red at 20 % or less, orange to 50 %, green above.
Color batteryColor(ColorScheme cs, int? p) =>
    p == null ? cs.outline : p <= 20 ? cs.error : p <= 50 ? Colors.orange.shade700 : Colors.green.shade600;

/// How close a recorder is by its signal (dBm): 2 right here, 1 nearby, 0 farther away.
int signalLevel(int rssi) => rssi > -60 ? 2 : rssi > -80 ? 1 : 0;

/// Seconds as minutes, or hours and minutes.
String durationText(AppLocalizations l, num s) {
  final m = (s / 60).round();
  return m >= 60 ? l.hoursMinutes(m ~/ 60, m % 60) : l.minutes(m);
}

/// What the top bar's chip shows and the status panel says, from the native status (`ble`: the BLE recorder is the
/// source; `stopping`: the microphone is being stopped). The chip answers "is my day being recorded, and for how
/// long still": an icon for what goes on, a number for how much of it (the recorder's battery, the catch-up's share
/// done, the pause's minutes left), and `line`, what goes on in a few words (the chip's tooltip, the panel's header).
/// The BLE link keeps the last read hw after a disconnect: its battery shows greyed then.
({IconData icon, Color color, IconData? battery, String? number, Color numberColor, bool spin, String line}) chipSpec(
    BuildContext context, Map s, {required bool ble, bool stopping = false}) {
  final l = AppLocalizations.of(context), cs = Theme.of(context).colorScheme, amber = Colors.amber.shade700;
  final hw = (s['hw'] as Map?) ?? {}, b = hw['battery'] as int?, pct = b == null ? null : '$b%';
  final connected = s['connected'] == true, syncDone = (s['syncDone'] as num?) ?? 0, syncTotal = (s['syncTotal'] as num?) ?? 0;
  final link = s['link'] as Map?, pausedMin = pauseLeftMin(s);
  IconData? glyph() => b == null ? null : hw['charge'] == 'charging' ? Icons.battery_charging_full_rounded : batteryIcon(b);
  final low = b != null && b <= 20 ? cs.error : cs.onSurfaceVariant;  // as batteryColor
  if (!ble) {
    return stopping
        ? (icon: Icons.mic_rounded, color: amber, battery: null, number: null, numberColor: low, spin: false,
            line: '${l.stopping} · ${l.waitingLastPieces(s['pending'] ?? 0)}')
        : s['running'] == true
            ? (icon: Icons.mic_rounded, color: recordingRed, battery: null, number: null, numberColor: low, spin: false, line: l.recordingNow)
            : (icon: Icons.mic_off_rounded, color: cs.outline, battery: null, number: null, numberColor: low, spin: false, line: l.notRecording);
  }
  if (pausedMin > 0 && connected) {  // a paused recorder that went away shows as away (the pause's line says both)
    return (icon: Icons.pause_rounded, color: cs.outline, battery: null, number: l.minutes(pausedMin), numberColor: cs.onSurfaceVariant,
        spin: false, line: l.statePaused(pausedMin));
  }
  if (syncTotal > 0) {  // the card's backlog being read; before LIVE is up only the file being recorded: no share yet
    final eta = hw['backlog_eta_s'] as num?;
    return (icon: Icons.sync_rounded, color: cs.primary, battery: null,
        number: connected ? '${(syncDone * 100 / syncTotal).floor()}%' : null, numberColor: cs.onSurfaceVariant, spin: true,
        line: eta == null || eta <= 0 ? l.catchingUp : l.stateCatchingUp(durationText(l, eta)));
  }
  if (!connected) {
    final lastSeen = link?['lastSeen'] as int?;
    return (icon: Icons.bluetooth_searching_rounded, color: amber, battery: glyph(), number: pct, numberColor: cs.outline, spin: false,
        line: link == null ? (s['running'] == true ? l.starting : l.notConnected)
            : link['code'] != 'away' ? nativeText(context, link)
            : lastSeen == null ? (pausedMin > 0 ? l.statePausedAway : l.stateAway)
            : (pausedMin > 0 ? l.statePausedAwaySince : l.stateAwaySince)(
                TimeOfDay.fromDateTime(DateTime.fromMillisecondsSinceEpoch(lastSeen)).format(context)));
  }
  return (icon: Icons.mic_rounded, color: recordingRed, battery: glyph(), number: pct, numberColor: low, spin: false, line: l.recordingNow);
}

/// The top bar's chip (chipSpec): an icon, the battery's glyph when there is one, a number; an amber dot on the icon
/// when something wants a look (`warn`: an upload's note, an error), the number stays.
class StatusChip extends StatelessWidget {
  final Map status;
  final bool ble, stopping, warn;
  final VoidCallback onPressed;
  const StatusChip({super.key, required this.status, required this.ble, required this.onPressed, this.stopping = false, this.warn = false});

  @override
  Widget build(BuildContext context) {
    final c = chipSpec(context, status, ble: ble, stopping: stopping), l = AppLocalizations.of(context);
    Widget icon = Icon(c.icon, size: 20, color: c.color);
    if (c.spin && !MediaQuery.disableAnimationsOf(context)) icon = _Spin(child: icon);
    return ActionChip(
      tooltip: c.battery == null ? c.line : '${c.line} · ${l.battery} ${c.number}',
      label: Row(mainAxisSize: MainAxisSize.min, children: [
        Badge(isLabelVisible: warn, smallSize: 8, backgroundColor: Colors.amber.shade700, child: icon),
        if (c.battery != null) Padding(padding: const EdgeInsets.only(left: 6), child: Icon(c.battery, size: 18, color: c.numberColor)),
        if (c.number != null)
          Padding(
            padding: EdgeInsets.only(left: c.battery == null ? 6 : 1),
            child: Text(c.number!, style: TextStyle(fontSize: 14, fontWeight: FontWeight.w500, color: c.numberColor,
                fontFeatures: const [FontFeature.tabularFigures()])),
          ),
      ]),
      labelPadding: const EdgeInsets.symmetric(horizontal: 2),  // standard density: a 48 px target
      side: BorderSide(color: Theme.of(context).colorScheme.outlineVariant),
      onPressed: onPressed,
    );
  }
}

class _Spin extends StatefulWidget {
  final Widget child;
  const _Spin({required this.child});
  @override
  State<_Spin> createState() => _SpinState();
}

class _SpinState extends State<_Spin> with SingleTickerProviderStateMixin {
  late final turns = AnimationController(vsync: this, duration: const Duration(seconds: 2))..repeat();
  @override
  void dispose() {
    turns.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => RotationTransition(turns: turns, child: widget.child);
}

/// Minutes the recorder's pause has left (0: recording), from the link's status.
int pauseLeftMin(Map status) {
  final until = (status['pausedUntil'] as num?) ?? 0;
  final left = until - DateTime.now().millisecondsSinceEpoch;
  return left > 0 ? (left / 60000).ceil() : 0;
}

/// What the native side says as a code and its values (a map with `code`: the link's state, the upload's note, the USB
/// disk switch, the last catch-up, a firmware update's step, a method-channel error; Ble.kt and Ble.swift,
/// RecorderService.kt and Recorder.swift, Dfu.kt and DfuRun.swift), in words in the app's language. Anything else is a
/// system's or a library's own message, shown as it is.
String nativeText(BuildContext context, Object? v) {
  if (v is! Map) return '${v ?? ''}';
  final l = AppLocalizations.of(context), detail = '${v['detail'] ?? ''}';
  String time(String key) => TimeOfDay.fromDateTime(DateTime.fromMillisecondsSinceEpoch(v[key] as int)).format(context);
  return switch (v['code']) {
    'scanning' => v['name'] == null ? l.linkScanning : l.linkScanningName(v['name']),
    'bluetoothOff' => l.linkBluetoothOff,
    'noPermission' => l.linkNoPermission,
    'unbound' => l.linkUnbound,
    'kicked' => l.linkKicked,
    'elsewhere' => l.linkElsewhere(v['name']),
    'bluetoothNotOn' => l.linkBluetoothNotOn(v['s']),
    'scanFailed' => l.linkScanFailed(v['err'], v['s']),
    'connectFailed' => l.linkConnectFailed(v['s']),
    'away' => v['lastSeen'] == null ? l.linkAway : l.linkAwaySince(time('lastSeen')),
    'connecting' => l.linkConnecting,
    'stalePairing' => l.linkStalePairing(v['name'] ?? l.recorder),
    'mtu' => l.linkMtu,
    'pairing' => l.linkPairing,
    'pairingDialog' => l.linkPairingDialog,
    'encrypting' => l.linkEncrypting,
    'pairingFailed' => l.linkPairingFailed,
    'services' => l.linkServices,
    'clock' => l.linkClock,
    'status' => l.linkStatus,
    'forgetOthers' => l.linkForgetOthers,
    'joining' => l.linkJoining,
    'connected' => l.linkConnected,
    'linkError' => l.linkError(detail),
    'synced' => l.lastSynced(time('at'), v['kb']),
    'usbSwitchingOn' => l.usbSwitchingOn,
    'usbSwitchingOff' => l.usbSwitchingOff,
    'usbNoComputer' => l.usbNoComputer,
    'usbFailed' => l.usbFailed(v['err']),
    'usbNotYetOn' => l.usbNotYetOn,
    'usbStillOn' => l.usbStillOn,
    'usbOn' => l.usbOn,
    'rejected' => l.noteRejected,
    'uploadFailed' => l.noteUploadFailed(detail),
    'endFailed' => l.noteEndFailed(detail),
    'archiveFailed' => l.noteArchiveFailed(detail),
    'killed' => l.noteKilled(v['count']),
    'interrupted' => l.micInterrupted(time('at')),
    'routeFailed' => l.micRouteFailed(time('at'), detail),
    'resumeFailed' => l.micResumeFailed(time('at'), detail),
    'dfuPreparing' => l.dfuPreparing,
    'dfuRebooting' => l.dfuRebooting,
    'dfuStarting' => l.dfuStarting,
    'dfuProgress' => l.dfuProgress(v['pct'], (v['kbps'] as num).toStringAsFixed(1)),
    'dfuDone' => l.dfuDone,
    'dfuAborted' => l.dfuAborted,
    'dfuFailed' => l.dfuFailed(v['why'] == null ? detail : nativeText(context, {'code': v['why'], 'detail': v['detail']})),
    'dfuNoBluetooth' => l.dfuNoBluetooth,
    'dfuUnknown' => l.dfuUnknown,
    'dfuNoConnect' => l.dfuNoConnect,
    'dfuNoService' => l.dfuNoService,
    'dfuNoReboot' => l.dfuNoReboot,
    'dfuRefused' => l.dfuRefused(detail),
    'dfuDropped' => l.dfuDropped,
    'dfuNoBootloader' => l.dfuNoBootloader,
    'permission' => l.errPermission,
    'notStarted' => l.errNotStarted,
    'notConnected' => l.errNotConnected,
    'listen' => l.errListen(detail),
    'updating' => l.errUpdating,
    'linked' => l.errLinked,
    'noModel' => l.errNoModel,
    'model' => l.errModel(v['want'], v['model']),
    'same' => l.errSame(v['version']),
    'sha256' => l.errSha256,
    'zip' => l.errZip(detail),
    final code => detail.isNotEmpty ? detail : '$code',  // iOS's start: the system's message
  };
}

/// A native call's error in words: its code and values (details), or the system's message.
String platformText(BuildContext context, PlatformException e) =>
    nativeText(context, {...?(e.details as Map?), 'code': e.code, 'detail': e.message});
