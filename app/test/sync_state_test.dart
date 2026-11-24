// 11-23: the top bar's chip said 补传中 (a bean's recording read from behind) while the device page said 已连接 with its
// idle sync line; Android's notification looked at neither. Each reader now asks syncSpec() (native BeanSync.ledger(),
// the status's `sync`): for each status in fixtures/sync-ledger.json the chip, the status panel's card and the device
// page say the same phase and the same amount (Android's notification, from the same table: NoticeTest.kt).
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:live_record/home.dart';
import 'package:live_record/l10n/app_localizations.dart';
import 'package:live_record/recorder_page.dart';
import 'package:live_record/recorder_status.dart';

Future<void> _noop(String _) async {}

void main() {
  final readers = (jsonDecode(File('../fixtures/sync-ledger.json').readAsStringSync())['readers'] as List).cast<Map>();

  for (final c in readers) {
    final status = c['status'] as Map, hw = status['hw'] as Map;
    testWidgets('${c['name']}: the chip, the card and the device page say the same', (tester) async {
      expect(syncSpec(status).phase, c['phase']);
      tester.view.physicalSize = const Size(1170, 2532);  // a phone, 390 wide
      tester.view.devicePixelRatio = 3;
      addTearDown(tester.view.reset);
      SharedPreferences.setMockInitialValues({'server': 'http://127.0.0.1:1', 'source': 'ble'});
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(const MethodChannel('live_record/recorder'), (call) async {
        if (call.method == 'recorder') return {'name': hw['name'], 'model': hw['model']};
        if (call.method == 'status') return status;
        if (call.method == 'progress') {
          return {'wifi': status['wifiStep'] != null, 'wifiStep': status['wifiStep'], 'syncDone': status['syncDone'],
            'syncTotal': status['syncTotal'], 'sync': status['sync']};
        }
        if (call.method == 'deviceName') return 'Test phone';
        if (call.method == 'version') return '1.0.0+1';
        if (call.method == 'nearby') return <Object>[];
        if (call.method == 'permissions') return <String, Object>{};
        return null;
      });

      // the top bar's chip and, behind it, the status panel's card (home.dart)
      Widget zh(Widget home) => MaterialApp(locale: const Locale('zh'), localizationsDelegates: AppLocalizations.localizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales, home: home);
      await tester.pumpWidget(zh(const Home()));
      for (var i = 0; i < 4; i++) {
        await tester.pump(const Duration(seconds: 1));  // the first status poll
      }
      final chip = tester.widget<ActionChip>(find.byType(ActionChip)).tooltip!;
      expect(chip.split(' · 电量').first, c['chip']);
      final bytes = c['phase'] == 'wifi' && ((status['sync'] as Map)['total_kb'] as num) > 0;  // the owner, 11-23: no percentage
      final read = (c['page'] as String).split(' / ').first;  // the chip: the bytes read so far, of the device page's x / y
      if (bytes) expect(find.descendant(of: find.byType(ActionChip), matching: find.text(read)), findsOneWidget);
      await tester.tap(find.byType(ActionChip));
      for (var i = 0; i < 5; i++) {
        await tester.pump(const Duration(milliseconds: 200));  // a spinning icon never settles
      }
      Finder panel(String text) => find.descendant(of: find.byType(BottomSheet), matching: find.text(text));
      expect(panel(c['card'] as String), findsOneWidget, reason: 'the card');
      if ((c['card_sub'] as String).isNotEmpty) expect(panel(c['card_sub'] as String), findsOneWidget, reason: 'the card');
      if (bytes) {  // the card's bar as far as the chip's bytes, not spinning
        final bar = find.descendant(of: find.byType(BottomSheet), matching: find.byType(LinearProgressIndicator));
        expect(tester.widget<LinearProgressIndicator>(bar).value, isNotNull, reason: 'the card');
      }
      await tester.pumpWidget(const SizedBox());

      // the device page
      await tester.pumpWidget(zh(const Scaffold(body: RecorderPage(onSource: _noop))));
      await tester.pump();
      await tester.pump();
      expect(find.text(c['page_chip'] as String), findsOneWidget, reason: 'the device page');
      expect(find.text(c['page'] as String), findsOneWidget, reason: 'the device page');
      await tester.pumpWidget(const SizedBox());
      await tester.pump(const Duration(seconds: 3));  // timers gone with their pages
    });
  }
}
