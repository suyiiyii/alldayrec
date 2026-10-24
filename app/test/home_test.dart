import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:live_record/main.dart' as app;
import 'package:live_record/home.dart';
import 'package:live_record/settings_page.dart';
import 'package:live_record/upload_page.dart';
import 'package:live_record/recorder_page.dart';

void main() {
  test('Chinese for any zh, English for everything else', () {
    expect([for (final l in [const Locale('zh', 'CN'), const Locale('zh', 'TW'), const Locale.fromSubtags(languageCode: 'zh', scriptCode: 'Hant'),
             const Locale('en', 'US'), const Locale('ja'), null]) app.appLocale(l)],
        [const Locale('zh'), const Locale('zh'), const Locale('zh'), const Locale('en'), const Locale('en'), const Locale('en')]);
  });

  // flutter test's locale is en_US: the pages in English
  testWidgets('application pages remain navigable and dispose their polling', (tester) async {
    SharedPreferences.setMockInitialValues({'server': 'http://127.0.0.1:1'});
    var pending = 0;  // one more each poll: the open status sheet has to follow
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(const MethodChannel('live_record/recorder'), (call) async {
      if (call.method == 'deviceName') return 'Smoke phone';
      if (call.method == 'version') return '1.0.0+1';
      if (call.method == 'nearby') return <Object>[];
      if (call.method == 'status') return {'running': false, 'uploading': false, 'pending': ++pending};
      if (call.method == 'recorder' || call.method == 'permissions') return <String, Object>{};
      return null;
    });
    final handler = FlutterError.onError;
    final platformHandler = tester.binding.platformDispatcher.onError;
    app.main();
    await tester.pumpAndSettle();
    expect(find.byType(Home), findsOneWidget);
    final start = find.widgetWithText(FilledButton, 'Start recording');
    expect(start, findsNothing);  // in the status sheet, behind the top bar's chip
    await tester.tap(find.widgetWithText(ActionChip, 'Not recording'));
    await tester.pumpAndSettle();
    expect(start, findsOneWidget);
    await tester.pump(const Duration(seconds: 2));  // a poll while the sheet is open
    await tester.pump();
    final n = pending;
    expect(find.text('$n pieces to upload'), findsOneWidget);
    await tester.pump(const Duration(seconds: 2));  // the next one
    await tester.pump();
    expect(find.text('${n + 1} pieces to upload'), findsOneWidget);
    await tester.tapAt(const Offset(10, 10));  // the scrim: the sheet goes
    await tester.pumpAndSettle();
    expect(start, findsNothing);
    expect(tester.widgetList<NavigationDestination>(find.byType(NavigationDestination)).map((d) => d.label),
        ['Timeline', 'Live', 'People', 'Me']);
    await tester.tap(find.widgetWithText(NavigationDestination, 'Me'));
    await tester.pumpAndSettle();
    expect(find.byType(ActionChip), findsOneWidget);  // the status is on every tab
    expect(find.text('1.0.0+1'), findsOneWidget);
    for (final entry in <String, Type>{'Settings': SettingsPage, 'Upload': UploadPage, 'Recorder': RecorderPage}.entries) {
      await tester.tap(find.widgetWithText(ListTile, entry.key));
      await tester.pumpAndSettle();
      expect(find.byType(entry.value), findsOneWidget);
      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(find.byType(entry.value), findsNothing);  // gone with its route, polling and all
    }
    await tester.pumpWidget(const SizedBox());
    await tester.pumpAndSettle();
    FlutterError.onError = handler;
    tester.binding.platformDispatcher.onError = platformHandler;
    expect(tester.takeException(), isNull);
  });
}
