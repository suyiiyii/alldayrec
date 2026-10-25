// The top bar's chip: an icon and a number for each state of the recording (home.dart's canned statuses).
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:live_record/home.dart';
import 'package:live_record/l10n/app_localizations.dart';
import 'package:live_record/recorder_status.dart';

void main() {
  Future<void> pump(WidgetTester tester, Map status, {bool ble = true, bool warn = false, Locale locale = const Locale('en')}) =>
      tester.pumpWidget(MaterialApp(
        locale: locale,
        localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales,
        home: Scaffold(appBar: AppBar(actions: [StatusChip(status: status, ble: ble, warn: warn, onPressed: () {})])),
      ));

  Color colorOf(WidgetTester tester, IconData icon) => tester.widget<Icon>(find.byIcon(icon)).color!;
  final chip = find.byType(StatusChip);

  testWidgets('each state: its icon, its number, its words on a long press', (tester) async {
    final cases = <String, (IconData, IconData?, String?, String)>{
      'recording': (Icons.mic_rounded, Icons.battery_6_bar_rounded, '87%', 'Recording · Battery 87%'),
      'low': (Icons.mic_rounded, Icons.battery_1_bar_rounded, '14%', 'Recording · Battery 14%'),
      'charging': (Icons.mic_rounded, Icons.battery_charging_full_rounded, '62%', 'Recording · Battery 62%'),
      'syncing': (Icons.sync_rounded, null, '63%', 'Catching up · about 20 min left'),
      'paused': (Icons.pause_rounded, null, '12 min', 'Paused · resumes in 12 min'),
      'disconnected': (Icons.bluetooth_searching_rounded, Icons.battery_6_bar_rounded, '87%', 'Recorder out of reach · last seen'),
      'pausedAway': (Icons.bluetooth_searching_rounded, Icons.battery_6_bar_rounded, '87%', 'Paused · out of reach (last seen'),
    };
    for (final MapEntry(key: name, value: (icon, battery, number, words)) in cases.entries) {
      await pump(tester, cannedStatus(name));
      expect(find.descendant(of: chip, matching: find.byIcon(icon)), findsOneWidget, reason: name);
      expect(find.descendant(of: chip, matching: find.byType(Icon)), findsNWidgets(battery == null ? 1 : 2), reason: name);
      if (battery != null) expect(find.byIcon(battery), findsOneWidget, reason: name);
      expect(find.descendant(of: chip, matching: find.byType(Text)), number == null ? findsNothing : findsOneWidget, reason: name);
      if (number != null) expect(find.text(number), findsOneWidget, reason: name);
      expect(tester.widget<ActionChip>(find.byType(ActionChip)).tooltip, startsWith(words), reason: name);
      expect(tester.getSize(find.byType(ActionChip)).height, greaterThanOrEqualTo(44), reason: name);  // a thumb's target
    }
  });

  testWidgets('colours: red recording, red low battery, grey last battery while away', (tester) async {
    final cs = ThemeData().colorScheme;
    await pump(tester, cannedStatus('recording'));
    expect(colorOf(tester, Icons.mic_rounded), recordingRed);
    expect(colorOf(tester, Icons.battery_6_bar_rounded), cs.onSurfaceVariant);
    await pump(tester, cannedStatus('low'));
    expect(colorOf(tester, Icons.battery_1_bar_rounded), cs.error);
    await pump(tester, cannedStatus('disconnected'));
    expect(colorOf(tester, Icons.battery_6_bar_rounded), cs.outline);
  });

  testWidgets('catching up before LIVE is up: the icon alone', (tester) async {
    await pump(tester, {...cannedStatus('syncing'), 'connected': false, 'link': {'code': 'connecting'}});
    expect(find.byIcon(Icons.sync_rounded), findsOneWidget);
    expect(find.descendant(of: chip, matching: find.byType(Text)), findsNothing);
  });

  testWidgets('the microphone: red running, crossed out grey stopped, no number', (tester) async {
    await pump(tester, cannedStatus('mic'), ble: false);
    expect(colorOf(tester, Icons.mic_rounded), recordingRed);
    expect(find.descendant(of: chip, matching: find.byType(Text)), findsNothing);
    await pump(tester, {'running': false, 'pending': 0}, ble: false);
    expect(find.byIcon(Icons.mic_off_rounded), findsOneWidget);
    expect(tester.widget<ActionChip>(find.byType(ActionChip)).tooltip, 'Not recording');
  });

  testWidgets('a warning: an amber dot on the icon, the number stays', (tester) async {
    await pump(tester, cannedStatus('warning'), warn: true);
    expect(tester.widget<Badge>(find.byType(Badge)).isLabelVisible, isTrue);
    expect(find.text('87%'), findsOneWidget);
    await pump(tester, cannedStatus('recording'));
    expect(tester.widget<Badge>(find.byType(Badge)).isLabelVisible, isFalse);
  });

  testWidgets('in Chinese', (tester) async {
    await pump(tester, cannedStatus('paused'), locale: const Locale('zh'));
    expect(find.text('12 分钟'), findsOneWidget);
    expect(tester.widget<ActionChip>(find.byType(ActionChip)).tooltip, '已暂停 · 12 分钟后继续');
    await pump(tester, cannedStatus('pausedAway'), locale: const Locale('zh'));
    expect(tester.widget<ActionChip>(find.byType(ActionChip)).tooltip, matches(r'^已暂停 · 不在附近（上次 .+ 在线） · 电量 87%$'));
    await pump(tester, cannedStatus('recording'), locale: const Locale('zh'));
    expect(tester.widget<ActionChip>(find.byType(ActionChip)).tooltip, '正在录音 · 电量 87%');
  });
}
