import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:live_record/home.dart';
import 'package:live_record/l10n/app_localizations.dart';
import 'package:webview_flutter/webview_flutter.dart';

// the callbacks each platform's WebView gives a failed first load, then a good one (live-record#392)
const offlineIos = WebResourceError(errorCode: -1009, description: 'offline', isForMainFrame: true);
const offlineAndroid = WebResourceError(errorCode: -2, description: 'host lookup', errorType: WebResourceErrorType.hostLookup, isForMainFrame: true);

void main() {
  test('iOS: a failed provisional load shows the cover, a good retry clears it', () {
    final w = WebLoad()..started();
    expect(w.error(offlineIos), isTrue);  // no onPageFinished after it on iOS
    expect([w.failed, w.loaded], [true, false]);
    w..started()..finished();
    expect([w.failed, w.loaded, w.attempts], [false, true, 0]);
  });

  test("Android: onPageFinished after the error page does not count as loaded", () {
    final w = WebLoad()..started();
    expect(w.error(offlineAndroid), isTrue);
    w.finished();  // Android's error page
    expect([w.failed, w.loaded], [true, false]);
    w..started()..finished();
    expect([w.failed, w.loaded], [false, true]);
  });

  test('cut-short navigations, subresources and a killed web process are no failed load', () {
    for (final e in const [
      WebResourceError(errorCode: -999, description: 'cancelled', isForMainFrame: true),
      WebResourceError(errorCode: 102, description: 'frame load interrupted', isForMainFrame: true),
      WebResourceError(errorCode: -2, description: 'an image', isForMainFrame: false),
      WebResourceError(errorCode: 2, description: 'gone', errorType: WebResourceErrorType.webContentProcessTerminated, isForMainFrame: true),
    ]) {
      final w = WebLoad()..started();
      expect(w.error(e), isFalse, reason: e.description);
      w.finished();
      expect([w.failed, w.loaded], [false, true], reason: e.description);
    }
  });

  test('retries back off and stop', () {
    final w = WebLoad();
    final waits = [for (Duration? d; (d = w.nextRetry()) != null;) d!.inSeconds];
    expect(waits, [2, 4, 8, 16, 32, 64]);
    w..started()..finished();  // a success starts them afresh
    expect(w.nextRetry(), const Duration(seconds: 2));
  });

  for (final (locale, text) in [(const Locale('zh'), '加载失败，点重试'), (const Locale('en'), "Couldn't load, tap to retry")]) {
    testWidgets('the cover says so and retries on a tap ($locale)', (tester) async {
      var taps = 0;
      await tester.pumpWidget(MaterialApp(locale: locale, localizationsDelegates: AppLocalizations.localizationsDelegates,
          supportedLocales: AppLocalizations.supportedLocales, home: LoadFailed(onRetry: () => taps++)));
      await tester.tap(find.text(text));
      expect(taps, 1);
    });
  }
}
