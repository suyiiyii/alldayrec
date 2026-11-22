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
  });

  testWidgets('0.1 s while the transfer runs, then 2 s again', (tester) async {
    final calls = <String>[];
    var wifi = true, done = 0;
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(const MethodChannel('live_record/recorder'), (call) async {
      calls.add(call.method);
      if (call.method == 'recorder') return {'name': 'D3200-TEST', 'model': 'bean'};
      final link = {'code': wifi ? 'beanWifi' : 'beanIdle'};
      if (call.method == 'status') return {'running': true, 'connected': !wifi, 'link': link, 'hw': {'battery': 80}, 'syncDone': done, 'syncTotal': wifi ? 16794 : 0};
      if (call.method == 'progress') return {'wifi': wifi, 'syncDone': done += 100, 'syncTotal': 16794};
      return null;
    });
    int count(String m) => calls.where((c) => c == m).length;
    await tester.pumpWidget(const MaterialApp(localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales, home: Scaffold(body: RecorderPage(onSource: _noop))));
    await tester.pump();
    calls.clear();
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
}

Future<void> _noop(String source) async {}
