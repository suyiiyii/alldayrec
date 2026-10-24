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
    expect(find.text('开始录音'), findsNothing);  // in the status sheet, behind the top bar's chip
    await tester.tap(find.widgetWithText(ActionChip, '未在录音'));
    await tester.pumpAndSettle();
    expect(find.text('开始录音'), findsOneWidget);
    expect(tester.getTopLeft(find.text('实时字幕')).dy, lessThan(tester.getTopLeft(find.text('开始录音')).dy));  // first
    await tester.pump(const Duration(seconds: 2));  // a poll while the sheet is open
    await tester.pump();
    final n = pending;
    expect(find.text('待传 $n 块'), findsOneWidget);
    await tester.pump(const Duration(seconds: 2));  // the next one
    await tester.pump();
    expect(find.text('待传 ${n + 1} 块'), findsOneWidget);
    await tester.tapAt(const Offset(10, 10));  // the scrim: the sheet goes
    await tester.pumpAndSettle();
    expect(find.text('开始录音'), findsNothing);
    expect(tester.widgetList<NavigationDestination>(find.byType(NavigationDestination)).map((d) => d.label),
        ['今天', '对话', '人物', '我的']);
    await tester.tap(find.widgetWithText(NavigationDestination, '我的'));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(ActionChip, '未在录音'), findsOneWidget);  // the status is on every tab
    expect(find.text('1.0.0+1'), findsOneWidget);
    for (final entry in <String, Type>{'设置': SettingsPage, '上传': UploadPage, '录音设备': RecorderPage}.entries) {
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
