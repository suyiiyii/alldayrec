// 11-23: the top bar's chip said 补传中 (a bean's recording read from behind: backlog_s, no syncTotal) while the device
// page said 已连接 with its idle sync line. Both now ask catchingUp(); for each status they say the same.
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:live_record/l10n/app_localizations.dart';
import 'package:live_record/recorder_page.dart';
import 'package:live_record/recorder_status.dart';

Future<void> _noop(String _) async {}

void main() {
  const hw = {'name': 'D3200-TEST', 'battery': 100, 'charge': 'battery', 'recording': true};
  final statuses = <String, Map<String, Object?>>{
    // the owner's screenshot: 77 min lacking, read now, no closed-file backlog counted
    'read from behind': {'running': true, 'connected': true, 'link': {'code': 'connected'}, 'syncDone': 0, 'syncTotal': 0,
      'hw': {...hw, 'backlog_s': 4620, 'backlog_eta_s': 600, 'backlog_held': null}},
    'held till the stop': {'running': true, 'connected': true, 'link': {'code': 'connected'}, 'syncDone': 0, 'syncTotal': 0,
      'hw': {...hw, 'backlog_s': 4620, 'backlog_eta_s': null, 'backlog_held': 'recording'}},
    'closed files': {'running': true, 'connected': true, 'link': {'code': 'beanIdle'}, 'syncDone': 2000, 'syncTotal': 16794,
      'hw': {...hw, 'recording': false, 'backlog_s': 4620, 'backlog_eta_s': 600, 'backlog_held': null}},
    'all here': {'running': true, 'connected': true, 'link': {'code': 'connected'}, 'syncDone': 0, 'syncTotal': 0, 'hw': hw},
  };

  for (final MapEntry(key: name, value: status) in statuses.entries) {
    testWidgets('$name: the chip and the device page agree', (tester) async {
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(const MethodChannel('live_record/recorder'), (call) async {
        if (call.method == 'recorder') return {'name': 'D3200-TEST', 'model': 'bean'};
        if (call.method == 'status') return status;
        return null;
      });
      late bool chip;
      await tester.pumpWidget(MaterialApp(locale: const Locale('zh'), localizationsDelegates: AppLocalizations.localizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales,
          home: Scaffold(body: Builder(builder: (context) {
            chip = chipSpec(context, status, ble: true).spin;  // the chip turns while a backlog is read
            return const RecorderPage(onSource: _noop);
          }))));
      await tester.pump();
      final page = find.text('已连接 · 补传中').evaluate().isNotEmpty;
      expect(page, chip);
      expect(find.text('正在取回录音豆里的录音').evaluate().isNotEmpty, chip);
      expect(find.text('连上录音设备时在后台自动取回它离线录下的音频，不耽误实时上传').evaluate().isNotEmpty, !chip);
      if (name == 'read from behind') {
        expect(find.text('豆子上还有 1 小时 17 分没传到手机 · 补传中 · 补完约 10 分钟'), findsOneWidget);  // the chip's words
        expect(find.textContaining(' / '), findsNothing);  // no bytes to count
      }
      if (name == 'closed files') expect(find.text('1.95 MB / 16.4 MB'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });
  }
}
