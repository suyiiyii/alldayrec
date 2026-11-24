import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:live_record/home.dart';
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
      return [s.text, ?s.bytes, if (s.n != null) l.wifiStepOf(s.n!, wifiSteps), ?s.reopen].join(' | ');
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
    // a new hotspot after the bean stopped sending (#406): its own step, then the steps again, each with its number
    expect([
      for (final w in [
        {'step': 'reopen', 'reopen': 1}, {'step': 'hotspot', 'reopen': 1}, {'step': 'join', 'try': 1, 'reopen': 2},
        {'step': 'transfer', 'reopen': 3}, {'step': 'back', 'end': 'part', 'reopen': 3}, {'step': 'transfer', 'reopen': 0},
      ]) line(zh, w)
    ], [
      '正在让录音豆换个新热点（第 1/3 次），系统问要不要加入时请马上点「加入」…',
      '正在让录音豆开热点… | 第 1/5 步 | 第 1 次重开',
      '正在加入录音豆的 Wi‑Fi（请在系统弹窗点加入）… | 第 2/5 步 | 第 2 次重开',
      '正在传输 | 21.30 MB / 66.3 MB | 第 4/5 步 | 第 3 次重开',
      '正在连回蓝牙… | 第 5/5 步 | 第 3 次重开',
      '正在传输 | 21.30 MB / 66.3 MB | 第 4/5 步',
    ]);
    expect(line(en, {'step': 'reopen', 'reopen': 2}), 'Asking the bean for a new hotspot (2 of 3); answer Join right away when asked…');
    expect(line(en, {'step': 'join', 'try': 2, 'reopen': 1}), 'Joining the bean\'s Wi‑Fi… (try 2) | Step 2 of 5 | hotspot reopened (1)');
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
    // the three new hotspots used up: what Wi-Fi got, Bluetooth the rest; fewer, the usual half way
    expect(wifiEndText(zh, {'end': 'part', 's': 40, 'ms': 41000, 'reopen': 3}), '热点重开了 3 次：Wi‑Fi 传了 40 秒录音，用时 41 秒；剩下的改用蓝牙');
    expect(wifiEndText(en, {'end': 'part', 's': 40, 'ms': 41000, 'reopen': 3}),
        'Hotspot reopened 3 times: Wi‑Fi got 40 s of recording in 41 s; Bluetooth fetches the rest');
    expect(wifiEndText(zh, {'end': 'part', 's': 720, 'ms': 16000, 'reopen': 1}), '停在半路：取回 12 分钟录音，用时 16 秒；剩下的由蓝牙接着取');
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
  bool waits(Map w) => const ['asked', 'reopen', 'back'].contains(w['step']);  // Bluetooth waited for (the fake's is down)
  Future<void> open(WidgetTester tester, {Map<String, Object?>? at = const {'step': 'transfer'}, Locale locale = const Locale('en'),
      Widget page = const Scaffold(body: RecorderPage(onSource: _noop))}) async {
    SharedPreferences.setMockInitialValues({'server': 'http://127.0.0.1:1', 'source': 'ble'});
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
      // the sync summary as BeanSync.ledger() gives it with the bytes
      Map sync(num d, num t) => {'phase': on() ? 'wifi' : t > 0 ? 'catching' : 'synced', 'done_kb': d, 'total_kb': t};
      if (call.method == 'status') {
        final t = transfer ? 16794 : left;
        return {'running': true, 'connected': !on(), 'link': link, 'hw': {'battery': 80}, 'wifiStep': step, 'wifiNote': note,
          'syncDone': done, 'syncTotal': t, 'sync': sync(done, t)};
      }
      if (call.method == 'progress') {
        final d = transfer ? done += 100 : 0, t = transfer ? 16794 : 0;
        return {'wifi': on(), 'wifiStep': step, 'wifiNote': note, 'syncDone': d, 'syncTotal': t, 'sync': sync(d, t)};
      }
      if (call.method == 'beanWifi') { step = {'step': 'asked'}; return 'ok'; }
      if (call.method == 'version' || call.method == 'deviceName') return 'x';
      if (call.method == 'permissions') return <String, Object>{};
      return null;
    });
    await tester.pumpWidget(MaterialApp(locale: locale, localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales, home: page));
    await tester.pump();
    calls.clear();
  }

  // the owner, 11-23: the home page's chip too, not every 2 s; the same reads, and only while the transfer runs
  testWidgets('the home page\'s chip: 0.05 s while the transfer runs, 2 s before and after', (tester) async {
    await open(tester, at: null, page: const Home());
    await tester.pump(const Duration(seconds: 4));
    expect(count('progress'), 0);  // no transfer: the 2 s status only
    expect(count('status'), 2);
    step = {'step': 'transfer'};
    await tester.pump(const Duration(seconds: 2));  // the status that finds it
    await tester.pump();
    calls.clear();
    for (var i = 0; i < 40; i++) { await tester.pump(fastEvery); }
    expect(count('progress'), 40);
    expect(count('status'), 1);
    expect(find.descendant(of: find.byType(ActionChip), matching: find.text('3.91 MB')), findsOneWidget);  // done 4000 KB after 40 reads
    await tester.pump(fastEvery);
    expect(find.descendant(of: find.byType(ActionChip), matching: find.text('4.00 MB')), findsOneWidget);  // 0.05 s later: 4100 KB

    step = {'step': 'done', 'end': 'all', 's': 45, 'ms': 2000};
    await tester.pump(fastEvery);
    await tester.pump();
    calls.clear();
    await tester.pump(const Duration(seconds: 4));
    expect(count('progress'), 0);  // over: back to the 2 s status
    expect(count('status'), 2);
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 3));  // timers gone with the page
  });

  // #422 review N1: the bean not back at asked, reopen or back (Bluetooth down, nothing moves): the 2 s status only, the
  // home page's chip and the device page alike; the transfer again: 0.05 s
  testWidgets('waiting for Bluetooth: no 0.05 s reads', (tester) async {
    for (final page in [const Home(), const Scaffold(body: RecorderPage(onSource: _noop))]) {
      for (final w in <Map<String, Object?>>[{'step': 'asked'}, {'step': 'reopen', 'reopen': 1}, {'step': 'back', 'end': 'all', 's': 45, 'ms': 2000}]) {
        await open(tester, at: w, page: page);
        await tester.pump(const Duration(seconds: 4));
        expect(count('progress'), 0, reason: '$page $w');
        expect(count('status'), 2, reason: '$page $w');
        await tester.pumpWidget(const SizedBox());
        await tester.pump(const Duration(seconds: 3));
      }
    }
    await open(tester, at: {'step': 'transfer'}, page: const Home());
    await tester.pump(const Duration(seconds: 2));
    calls.clear();
    for (var i = 0; i < 20; i++) { await tester.pump(fastEvery); }
    expect(count('progress'), 20);
    step = {'step': 'back', 'end': 'all', 's': 45, 'ms': 2000};  // the transfer over, Bluetooth not back: off at once
    await tester.pump(fastEvery);
    calls.clear();
    await tester.pump(const Duration(seconds: 4));
    expect(count('progress'), 0);
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 3));
  });

  testWidgets('Bluetooth not back for 3 minutes: said under the tile, after what it got', (tester) async {
    await open(tester, at: {'step': 'done', 'end': 'all', 's': 45, 'ms': 2000}, locale: const Locale('zh'));
    note = {'code': 'beanWifiNoBle'};
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    expect(find.text('传完了：45 秒录音，用时 2 秒 · 上次 Wi‑Fi 快传停在等蓝牙：豆子 3 分钟没连回来。请把豆子放到手机旁并确认它开着；另一台手机连着它的话，先关掉那台的蓝牙。连回后会接着用蓝牙同步'),
        findsOneWidget);
    step = {'step': 'done', 'end': 'fail'};
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    expect(find.text('上次 Wi‑Fi 快传停在等蓝牙：豆子 3 分钟没连回来。请把豆子放到手机旁并确认它开着；另一台手机连着它的话，先关掉那台的蓝牙。连回后会接着用蓝牙同步'), findsOneWidget);
    // back after a failure (#423 review L2): what to do about it first, then Bluetooth not back
    note = {'code': 'beanWifiNoBle', 'before': {'code': 'beanWifiNoSocket'}};
    await tester.pump(const Duration(seconds: 2));
    await tester.pump();
    expect(find.text('上次 Wi‑Fi 快传没成：手机连上了豆子的热点，但连不上豆子；开着 VPN 的话先关掉再试 · 上次 Wi‑Fi 快传停在等蓝牙：豆子 3 分钟没连回来。请把豆子放到手机旁并确认它开着；另一台手机连着它的话，先关掉那台的蓝牙。连回后会接着用蓝牙同步'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

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

  testWidgets('from the tap to Bluetooth back: each step in order, its bar, 0.05 s while the bean is reached', (tester) async {
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
      await tester.pump(const Duration(seconds: 2));  // the 2 s status finds a step after a wait for Bluetooth
      await tester.pump(fastEvery);
      await tester.pump();
      // Bluetooth down at asked and back: the step read once at most (the read that finds it), then the 2 s status only
      expect(count('progress'), waits(w) ? lessThanOrEqualTo(1) : greaterThan(0), reason: '$w');
      final n = now();
      seen.add(n.text);
      // the steps before the transfer: no made-up share, a moving bar; the transfer: its real bytes
      expect(n.bar, w['step'] == 'transfer' ? closeTo(done / 16794, 1e-9) : isNull, reason: '$w');
    }
    expect(seen, [
      '正在准备 Wi‑Fi 快传…', '正在让录音豆开热点…', '正在加入录音豆的 Wi‑Fi（请在系统弹窗点加入）…', '正在加入录音豆的 Wi‑Fi…（第 2 次）',
      '正在连接录音豆…', '正在连接录音豆…（第 2 次）', '正在传输', '正在连回蓝牙…',
    ]);
    expect(find.text('第 5/5 步 · 传完了：35 分钟录音，用时 16 秒'), findsOneWidget);
    step = {'step': 'done', 'end': 'all', 's': 2100, 'ms': 16000};
    await tester.pump(const Duration(seconds: 2));  // Bluetooth back: the 2 s status
    await tester.pump();
    expect(find.textContaining(RegExp('^正在')), findsNothing);  // no step line
    expect(find.text('传完了：35 分钟录音，用时 16 秒'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('a stop on Wi-Fi, a new hotspot (#406): never done meanwhile, buttons off, the whole tap summed up', (tester) async {
    ListTile tile(String t) => tester.widget<ListTile>(find.ancestor(of: find.text(t), matching: find.byType(ListTile)));
    List<String> texts() => tester.widgetList<Text>(find.byType(Text)).map((t) => t.data ?? '').toList();
    await open(tester, at: {'step': 'transfer', 'reopen': 0}, locale: const Locale('zh'));
    final seen = <String>[];
    for (final w in <Map<String, Object?>>[
      {'step': 'transfer', 'reopen': 0}, {'step': 'reopen', 'reopen': 1}, {'step': 'hotspot', 'reopen': 1},
      {'step': 'join', 'try': 1, 'reopen': 1}, {'step': 'socket', 'try': 1, 'reopen': 1}, {'step': 'transfer', 'reopen': 1},
      {'step': 'back', 'end': 'all', 's': 2100, 'ms': 30000, 'reopen': 1},  // both hotspots' 35 minutes and 30 s
    ]) {
      step = w;
      calls.clear();
      await tester.pump(const Duration(seconds: 2));
      await tester.pump(fastEvery);
      await tester.pump();
      expect(count('progress'), waits(w) ? lessThanOrEqualTo(1) : greaterThan(0), reason: '$w');  // 0.05 s but at reopen and back
      expect(tile('Wi‑Fi 快传').onTap, isNull, reason: '$w');
      expect(tile('解除绑定').onTap, isNull, reason: '$w');
      final all = texts(), at = all.indexWhere((s) => s.startsWith('正在'));
      seen.add([all[at], all.skip(at + 1).firstWhere((s) => s.startsWith('第 ') || s.isEmpty)].join(' | '));  // the step, the line under the bar
    }
    expect(seen, [
      '正在传输 | 第 4/5 步',
      '正在让录音豆换个新热点（第 1/3 次），系统问要不要加入时请马上点「加入」… | ',
      '正在让录音豆开热点… | 第 1/5 步 · 第 1 次重开',
      '正在加入录音豆的 Wi‑Fi（请在系统弹窗点加入）… | 第 2/5 步 · 第 1 次重开',
      '正在连接录音豆… | 第 3/5 步 · 第 1 次重开',
      '正在传输 | 第 4/5 步 · 第 1 次重开',
      '正在连回蓝牙… | 第 5/5 步 · 第 1 次重开 · 传完了：35 分钟录音，用时 30 秒',
    ]);
    step = {'step': 'done', 'end': 'all', 's': 2100, 'ms': 30000, 'reopen': 1};
    await tester.pump(const Duration(seconds: 2));  // Bluetooth back: the 2 s status
    await tester.pump();
    expect(find.text('传完了：35 分钟录音，用时 30 秒'), findsOneWidget);
    expect(tile('Wi‑Fi 快传').onTap, isNotNull);

    // the three used up: back over Bluetooth with what Wi-Fi got, the last hotspot's failure not the summary
    await tester.pumpWidget(const SizedBox());
    await open(tester, at: {'step': 'back', 'end': 'part', 's': 40, 'ms': 41000, 'reopen': 3}, locale: const Locale('zh'));
    note = {'code': 'beanWifiNoUnits'};
    await tester.pump(fastEvery);
    await tester.pump();
    expect(find.text('第 5/5 步 · 第 3 次重开 · 热点重开了 3 次：Wi‑Fi 传了 40 秒录音，用时 41 秒；剩下的改用蓝牙'), findsOneWidget);
    step = {...step!, 'step': 'done'};
    await tester.pump(const Duration(seconds: 2));  // Bluetooth back: the 2 s status
    await tester.pump();
    expect(find.text('热点重开了 3 次：Wi‑Fi 传了 40 秒录音，用时 41 秒；剩下的改用蓝牙'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('unbinding is off from the tap to Bluetooth back, each step, said as for the transfer (#405 review)', (tester) async {
    ListTile unbind() => tester.widget<ListTile>(find.ancestor(of: find.text('解除绑定'), matching: find.byType(ListTile)));
    await open(tester, at: null, locale: const Locale('zh'));
    expect(unbind().onTap, isNotNull);
    for (final w in <Map<String, Object?>>[
      {'step': 'asked'}, {'step': 'hotspot'}, {'step': 'join', 'try': 1}, {'step': 'socket', 'try': 2}, {'step': 'transfer'},
      {'step': 'reopen', 'reopen': 1}, {'step': 'back', 'end': 'fail'},
    ]) {
      step = w;
      await tester.pump(const Duration(seconds: 2));  // the 2 s status says the step
      await tester.pump();
      expect(unbind().onTap, isNull, reason: '$w');
      expect(find.text('正在取回录音豆里的录音，取完再解除'), findsOneWidget, reason: '$w');
    }
    step = {'step': 'done', 'end': 'all', 's': 45, 'ms': 2000};
    await tester.pump(const Duration(seconds: 2));  // Bluetooth back: the 2 s status (no 0.05 s reads while it is waited for)
    await tester.pump();
    expect(unbind().onTap, isNotNull);  // over: unbinding again
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('why one failed, said where the tile is: low battery, recording, the join cancelled', (tester) async {
    for (final (code, want) in [
      ({'code': 'beanWifiLowBattery', 'battery': 20}, '上次 Wi‑Fi 快传没开始：豆子电量 20%，低于 30% 时它不开热点；充到 30% 以上再点'),
      ({'code': 'beanWifiRecording'}, '豆子正在录音：先在豆子上停录，再点 Wi‑Fi 快传'),
      ({'code': 'beanWifiGone'}, '上次 Wi‑Fi 快传没开始：录音豆断开了；连回来后再点一次'),
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
    await open(tester, locale: const Locale('zh'));
    step = {'step': 'back', 'end': 'fail'};
    note = {'code': 'beanWifiNoSocket'};
    await tester.pump(fastEvery);  // with the step, at once: the read that finds it
    await tester.pump();
    expect(find.text('第 5/5 步 · 上次 Wi‑Fi 快传没成：手机连上了豆子的热点，但连不上豆子；开着 VPN 的话先关掉再试'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });
}

Future<void> _noop(String source) async {}
