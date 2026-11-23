import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:live_record/l10n/app_localizations.dart';
import 'package:live_record/l10n/app_localizations_en.dart';
import 'package:live_record/l10n/app_localizations_zh.dart';
import 'package:live_record/recorder_page.dart';
import 'package:live_record/recorder_status.dart';

// A bean's Wi-Fi fast transfer on the device page: each step from the tap to Bluetooth back, the bytes to a hundredth
// of a MB, read every 0.05 s while it runs (the light `progress` call, not `status`), back to the 2 s status once it is
// over or the page is left.
void main() {
  test('the bytes: what came to a hundredth of a MB, the total as before, no percentage', () {
    expect(progressText(21811, 67891), '21.30 MB / 66.3 MB');
    expect(progressText(3482, 16794), '3.40 MB / 16.4 MB');
    expect(progressText(0, 16794), '0 KB / 16.4 MB');  // under 1 MB: whole KB, as sizeText
    expect(progressText(512, 900), '512 KB / 900 KB');
    expect(progressText(1024, 16794), '1.00 MB / 16.4 MB');
    expect(progressText(1572864, 2097152), '1.50 GB / 2.00 GB');  // GB: two decimals both, as sizeText
    expect(progressText(17000, 16794), '16.60 MB / 16.4 MB');  // an estimated total overshot: the real bytes still
    expect(sizeText(16794), '16.4 MB');  // elsewhere unchanged
  });

  test('each step\'s words and number, zh and en', () {
    final zh = AppLocalizationsZh(), en = AppLocalizationsEn();
    String line(AppLocalizations l, Map w) {
      final s = wifiStepSpec(l, w, 21811, 67891)!;
      return [s.text, ?s.bytes, if (s.n != null) l.wifiStepOf(s.n!, wifiSteps)].join(' | ');
    }
    expect([
      for (final w in [
        {'step': 'asked'}, {'step': 'hotspot'}, {'step': 'join', 'try': 1}, {'step': 'join', 'try': 2},
        {'step': 'socket', 'try': 1}, {'step': 'socket', 'try': 3}, {'step': 'transfer'}, {'step': 'back', 'end': 'all'},
      ]) line(zh, w)
    ], [
      '正在准备 Wi‑Fi 快传…',
      '正在让录音豆开热点… | 第 1/5 步',
      '正在加入录音豆的 Wi‑Fi（请在系统弹窗点加入）… | 第 2/5 步',
      '正在加入录音豆的 Wi‑Fi…（第 2 次） | 第 2/5 步',
      '正在连接录音豆… | 第 3/5 步',
      '正在连接录音豆…（第 3 次） | 第 3/5 步',
      '正在传输 | 21.30 MB / 66.3 MB | 第 4/5 步',
      '正在连回蓝牙… | 第 5/5 步',
    ]);
    expect(line(en, {'step': 'socket', 'try': 2}), 'Connecting to the bean… (try 2) | Step 3 of 5');
    expect(line(en, {'step': 'transfer'}), 'Transferring | 21.30 MB / 66.3 MB | Step 4 of 5');
    expect(wifiStepSpec(zh, {'step': 'done', 'end': 'all'}, 0, 0), isNull);  // over: no step line
    expect(wifiStepSpec(zh, null, 0, 0), isNull);

    expect(wifiEndText(zh, {'end': 'all', 's': 2100, 'ms': 16400}), '传完了：35 分钟录音，用时 16 秒');
    expect(wifiEndText(zh, {'end': 'all', 's': 45, 'ms': 125000}), '传完了：45 秒录音，用时 2 分 5 秒');
    expect(wifiEndText(zh, {'end': 'part', 's': 720, 'ms': 16000}), '停在半路：取回 12 分钟录音，用时 16 秒；剩下的由蓝牙接着取');
    expect(wifiEndText(zh, {'end': 'none'}), '录音都在手机上了，不用快传');
    expect(wifiEndText(zh, {'end': 'fail'}), 'Wi‑Fi 快传没成，已回到蓝牙');
    expect(wifiEndText(en, {'end': 'all', 's': 2100, 'ms': 16400}), 'Done: 35 min of recording in 16 s');
    expect(wifiEndText(zh, null), isNull);
  });

  // A fake bean on the method channel: a fast transfer at [step] (null: none), [left] KB still to send once it ends,
  // [note] why the last one failed.
  late List<String> calls;
  late Map<String, Object?>? step;
  late Map<String, Object?>? note;
  late int done, left;
  int count(String m) => calls.where((c) => c == m).length;
  double? bar(WidgetTester tester) =>  // the sync section's bar (the stat tiles have theirs)
      tester.widget<LinearProgressIndicator>(find.byWidgetPredicate((w) => w is LinearProgressIndicator && w.minHeight == 8)).value;
  bool on() => step != null && step!['step'] != 'done';
  Future<void> open(WidgetTester tester, {Map<String, Object?>? at = const {'step': 'transfer'}, Locale locale = const Locale('en')}) async {
    calls = [];
    step = at;
    note = null;
    done = left = 0;
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);  // in front, as the app on a phone
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(const MethodChannel('live_record/recorder'), (call) async {
      calls.add(call.method);
      if (call.method == 'recorder') return {'name': 'D3200-TEST', 'model': 'bean'};
      final transfer = step?['step'] == 'transfer';
      final link = {'code': transfer ? 'beanWifi' : 'beanIdle'};
      if (call.method == 'status') {
        return {'running': true, 'connected': !on(), 'link': link, 'hw': {'battery': 80}, 'wifiStep': step, 'wifiNote': note,
          'syncDone': done, 'syncTotal': transfer ? 16794 : left};
      }
      if (call.method == 'progress') return {'wifi': on(), 'wifiStep': step, 'wifiNote': note, 'syncDone': transfer ? done += 100 : 0, 'syncTotal': transfer ? 16794 : 0};
      if (call.method == 'beanWifi') { step = {'step': 'asked'}; return 'ok'; }
      return null;
    });
    await tester.pumpWidget(MaterialApp(locale: locale, localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales, home: const Scaffold(body: RecorderPage(onSource: _noop))));
    await tester.pump();
    calls.clear();
  }

  testWidgets('0.05 s while the transfer runs, then 2 s again', (tester) async {
    await open(tester);
    expect(fastEvery, const Duration(milliseconds: 50));
    for (var i = 0; i < 40; i++) { await tester.pump(fastEvery); }
    expect(count('progress'), 40);  // 2 s: 40 reads of the progress, one status
    expect(count('status'), 1);
    expect(find.text('3.91 MB / 16.4 MB'), findsOneWidget);  // done 4000 KB after 40 reads
    expect(find.textContaining(RegExp(r'\.\d%')), findsNothing);  // no percentage (the battery's 80% is not one)
    expect(bar(tester), closeTo(4000 / 16794, 1e-9));

    step = {'step': 'done', 'end': 'all', 's': 45, 'ms': 2000};
    await tester.pump(fastEvery);
    await tester.pump();
    calls.clear();
    await tester.pump(const Duration(seconds: 4));
    expect(count('progress'), 0);  // over: status only, every 2 s
    expect(count('status'), 2);
    expect(find.textContaining(' / '), findsNothing);  // no transfer: no progress line
    expect(find.text('Done: 45 s of recording in 2 s'), findsOneWidget);  // how it ended, under the Wi-Fi fast transfer tile

    step = {'step': 'transfer'};  // a new transfer, then the page left: no more reads
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    await tester.pump(fastEvery);
    expect(count('progress'), greaterThan(0));
    await tester.pumpWidget(const SizedBox());
    calls.clear();
    await tester.pump(const Duration(seconds: 2));
    expect(calls, isEmpty);
  });

  testWidgets('in the background: no 0.05 s reads, back on coming back', (tester) async {
    await open(tester);
    await tester.pump(fastEvery);
    expect(count('progress'), 1);
    for (final s in [AppLifecycleState.inactive, AppLifecycleState.hidden, AppLifecycleState.paused]) { tester.binding.handleAppLifecycleStateChanged(s); }
    calls.clear();
    for (var i = 0; i < 40; i++) { await tester.pump(fastEvery); }
    expect(count('progress'), 0);  // 2 s behind: not one read
    for (final s in [AppLifecycleState.hidden, AppLifecycleState.inactive, AppLifecycleState.resumed]) { tester.binding.handleAppLifecycleStateChanged(s); }
    calls.clear();
    for (var i = 0; i < 20; i++) { await tester.pump(fastEvery); }
    expect(count('progress'), 20);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('a transfer that fails half way: back to the 2 s status and the Bluetooth sync line', (tester) async {
    await open(tester);
    for (var i = 0; i < 10; i++) { await tester.pump(fastEvery); }
    expect(find.text('1000 KB / 16.4 MB'), findsOneWidget);
    step = {'step': 'done', 'end': 'part', 's': 20, 'ms': 1000};  // stopped: 15794 KB still on the card, Bluetooth takes it from there
    left = 16794;
    done = 1100;
    await tester.pump(fastEvery);
    await tester.pump();
    calls.clear();
    await tester.pump(const Duration(seconds: 4));
    expect(count('progress'), 0);
    expect(count('status'), 2);
    expect(find.text('1.07 MB / 16.4 MB'), findsOneWidget);  // the status' numbers now, the Bluetooth line's
    expect(find.text('Stopped half way: 20 s of recording in 1 s; Bluetooth fetches the rest'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('from the tap to Bluetooth back: each step in order, its bar, read at 0.05 s throughout', (tester) async {
    await open(tester, at: null, locale: const Locale('zh'));
    await tester.tap(find.text('Wi‑Fi 快传'));
    await tester.pump();
    await tester.pump();
    final seen = <String>[];
    ({String text, double? bar}) now() => (
      text: tester.widgetList<Text>(find.byType(Text)).map((t) => t.data ?? '').firstWhere((s) => s.startsWith('正在')),
      bar: bar(tester));
    for (final w in <Map<String, Object?>>[
      {'step': 'asked'}, {'step': 'hotspot'}, {'step': 'join', 'try': 1}, {'step': 'join', 'try': 2},
      {'step': 'socket', 'try': 1}, {'step': 'socket', 'try': 2}, {'step': 'transfer'},
      {'step': 'back', 'end': 'all', 's': 2100, 'ms': 16000},
    ]) {
      step = w;
      calls.clear();
      await tester.pump(fastEvery);
      await tester.pump();
      expect(count('progress'), 1, reason: '$w');
      final n = now();
      seen.add(n.text);
      // the steps before the transfer: no made-up share, a moving bar; the transfer: its real bytes
      expect(n.bar, w['step'] == 'transfer' ? closeTo(100 / 16794, 1e-9) : isNull, reason: '$w');
    }
    expect(seen, [
      '正在准备 Wi‑Fi 快传…', '正在让录音豆开热点…', '正在加入录音豆的 Wi‑Fi（请在系统弹窗点加入）…', '正在加入录音豆的 Wi‑Fi…（第 2 次）',
      '正在连接录音豆…', '正在连接录音豆…（第 2 次）', '正在传输', '正在连回蓝牙…',
    ]);
    expect(find.text('第 5/5 步 · 传完了：35 分钟录音，用时 16 秒'), findsOneWidget);
    step = {'step': 'done', 'end': 'all', 's': 2100, 'ms': 16000};
    await tester.pump(fastEvery);
    await tester.pump();
    expect(find.textContaining(RegExp('^正在')), findsNothing);  // no step line
    expect(find.text('传完了：35 分钟录音，用时 16 秒'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('why one failed, said where the tile is: low battery, recording, the join cancelled', (tester) async {
    for (final (code, want) in [
      ({'code': 'beanWifiLowBattery', 'battery': 20}, '上次 Wi‑Fi 快传没开始：豆子电量 20%，低于 30% 时它不开热点；充到 30% 以上再点'),
      ({'code': 'beanWifiRecording'}, '豆子正在录音：先在豆子上停录，再点 Wi‑Fi 快传'),
      ({'code': 'beanWifiUnavailable'}, '上次 Wi‑Fi 快传没成：手机问要不要加入豆子的网络时点了取消，或没找到豆子的热点；已回到蓝牙，可以再点一次'),
    ]) {
      await open(tester, at: {'step': 'done', 'end': 'fail'}, locale: const Locale('zh'));
      note = code;
      await tester.pump(const Duration(seconds: 2));
      await tester.pump();
      expect(find.text(want), findsOneWidget, reason: '$code');
      await tester.pumpWidget(const SizedBox());
    }
    // failed after the hotspot: said on the way back too
    await open(tester, at: {'step': 'back', 'end': 'fail'}, locale: const Locale('zh'));
    note = {'code': 'beanWifiNoSocket'};
    await tester.pump(fastEvery);  // with the step, at once
    await tester.pump();
    expect(find.text('第 5/5 步 · 上次 Wi‑Fi 快传没成：手机连上了豆子的热点，但连不上豆子；开着 VPN 的话先关掉再试'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });
}

Future<void> _noop(String source) async {}
