import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:live_record/l10n/app_localizations.dart';
import 'package:live_record/recorder_page.dart';
import 'package:live_record/recorder_status.dart';

// A bean's Wi-Fi fast transfer on the device page: its progress to a tenth of a percent, read every 0.1 s while it
// runs (the light `progress` call, not `status`), back to the 2 s status once it is over or the page is left.
void main() {
  test('progress to a tenth of a percent, sizes as before', () {
    expect(progressText(3482, 16794), '20.7% · 3.4 MB / 16.4 MB');
    expect(progressText(0, 16794), '0.0% · 0 KB / 16.4 MB');
    expect(progressText(16794, 16794), '100.0% · 16.4 MB / 16.4 MB');
    expect(progressText(512, 900), '56.9% · 512 KB / 900 KB');
    expect(progressText(17000, 16794), '100.0% · 16.6 MB / 16.4 MB');  // an estimated total overshot: never past 100%
  });

  // A fake bean on the method channel: a fast transfer while [wifi], [left] KB still to send once it ends.
  late List<String> calls;
  late bool wifi;
  late int done, left;
  int count(String m) => calls.where((c) => c == m).length;
  Future<void> open(WidgetTester tester) async {
    calls = [];
    wifi = true;
    done = left = 0;
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);  // in front, as the app on a phone
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(const MethodChannel('live_record/recorder'), (call) async {
      calls.add(call.method);
      if (call.method == 'recorder') return {'name': 'D3200-TEST', 'model': 'bean'};
      final link = {'code': wifi ? 'beanWifi' : 'beanIdle'};
      if (call.method == 'status') return {'running': true, 'connected': !wifi, 'link': link, 'hw': {'battery': 80}, 'syncDone': done, 'syncTotal': wifi ? 16794 : left};
      if (call.method == 'progress') return {'wifi': wifi, 'syncDone': done += 100, 'syncTotal': 16794};
      return null;
    });
    await tester.pumpWidget(const MaterialApp(localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales, home: Scaffold(body: RecorderPage(onSource: _noop))));
    await tester.pump();
    calls.clear();
  }

  testWidgets('0.1 s while the transfer runs, then 2 s again', (tester) async {
    await open(tester);
    for (var i = 0; i < 20; i++) { await tester.pump(fastEvery); }
    expect(count('progress'), 20);  // 2 s: 20 reads of the progress, one status
    expect(count('status'), 1);
    expect(find.text('11.9% · 2.0 MB / 16.4 MB'), findsOneWidget);  // done 2000 KB after 20 reads

    wifi = false;
    await tester.pump(fastEvery);
    await tester.pump();
    calls.clear();
    await tester.pump(const Duration(seconds: 4));
    expect(count('progress'), 0);  // over: status only, every 2 s
    expect(count('status'), 2);
    expect(find.textContaining('% · '), findsNothing);  // no transfer: no progress line

    wifi = true;  // a new transfer, then the page left: no more reads
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    await tester.pump(fastEvery);
    expect(count('progress'), greaterThan(0));
    await tester.pumpWidget(const SizedBox());
    calls.clear();
    await tester.pump(const Duration(seconds: 2));
    expect(calls, isEmpty);
  });

  testWidgets('in the background: no 0.1 s reads, back on coming back', (tester) async {
    await open(tester);
    await tester.pump(fastEvery);
    expect(count('progress'), 1);
    for (final s in [AppLifecycleState.inactive, AppLifecycleState.hidden, AppLifecycleState.paused]) { tester.binding.handleAppLifecycleStateChanged(s); }
    calls.clear();
    for (var i = 0; i < 20; i++) { await tester.pump(fastEvery); }
    expect(count('progress'), 0);  // 2 s behind: not one read
    for (final s in [AppLifecycleState.hidden, AppLifecycleState.inactive, AppLifecycleState.resumed]) { tester.binding.handleAppLifecycleStateChanged(s); }
    calls.clear();
    for (var i = 0; i < 10; i++) { await tester.pump(fastEvery); }
    expect(count('progress'), 10);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('a transfer that fails half way: back to the 2 s status and the Bluetooth sync line', (tester) async {
    await open(tester);
    for (var i = 0; i < 10; i++) { await tester.pump(fastEvery); }
    expect(find.text('6.0% · 1000 KB / 16.4 MB'), findsOneWidget);
    wifi = false;  // failed: off Wi-Fi with 15794 KB still on the card, Bluetooth takes it from there
    left = 16794;
    await tester.pump(fastEvery);
    await tester.pump();
    calls.clear();
    await tester.pump(const Duration(seconds: 4));
    expect(count('progress'), 0);
    expect(count('status'), 2);
    expect(find.text('6.5% · 1.1 MB / 16.4 MB'), findsOneWidget);  // the status' numbers now, not the fast reads'
    await tester.pumpWidget(const SizedBox());
  });
}

Future<void> _noop(String source) async {}
