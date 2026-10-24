// Presentation of native recorder hardware and pause status.
import 'l10n/app_localizations.dart';

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

/// Minutes the recorder's pause has left (0: recording), from the link's status.
int pauseLeftMin(Map status) {
  final until = (status['pausedUntil'] as num?) ?? 0;
  final left = until - DateTime.now().millisecondsSinceEpoch;
  return left > 0 ? (left / 60000).ceil() : 0;
}
