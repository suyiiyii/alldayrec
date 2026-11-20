// apkOffer: the update banner shows the server's APK only when its build number is higher than this build's and than
// the one whose offer was closed that day; checkResult: what 我的 › 检查更新 says.
import 'package:flutter_test/flutter_test.dart';
import 'package:live_record/update.dart';

Map apk(int code) => {'versionName': '1.4.$code', 'versionCode': code, 'size': 71311007};

void main() {
  test('newer than this build: offered', () {
    expect(apkOffer(apk(18), 17, 0), apk(18));
  });
  test('the same or an older build: nothing', () {
    expect(apkOffer(apk(17), 17, 0), isNull);
    expect(apkOffer(apk(16), 17, 0), isNull);
  });
  test('closed for that version: not again, a later one is', () {
    expect(apkOffer(apk(18), 17, 18), isNull);
    expect(apkOffer(apk(19), 17, 18), apk(19));
  });
  test('anything else from the server: nothing', () {
    expect(apkOffer(null, 17, 0), isNull);
    expect(apkOffer({'detail': 'no apk uploaded'}, 17, 0), isNull);
    expect(apkOffer({'versionCode': '18'}, 17, 0), isNull);
  });
  test('closed today: not again today, again the next day', () {
    final at = DateTime(2026, 11, 20, 23, 50);
    expect(dismissedToday(18, '2026-11-20', at), 18);
    expect(dismissedToday(18, '2026-11-20', at.add(const Duration(minutes: 20))), 0);
    expect(dismissedToday(18, null, at), 0);  // closed before 1.6.13: closing was for good, now offered again
    expect(apkOffer(apk(18), 17, dismissedToday(18, '2026-11-19', at)), apk(18));
  });
  test('check now: newer, latest, or why neither', () {
    expect(checkResult(200, apk(18), 17), UpdateCheck.newer);
    expect(checkResult(200, apk(17), 17), UpdateCheck.latest);
    expect(checkResult(200, apk(16), 17), UpdateCheck.latest);
    expect(checkResult(200, {'versionCode': '18'}, 17), UpdateCheck.failed);
    expect(checkResult(200, null, 17), UpdateCheck.failed);
    expect(checkResult(401, null, 17), UpdateCheck.unauthorized);
    expect(checkResult(404, null, 17), UpdateCheck.noApk);
    expect(checkResult(502, null, 17), UpdateCheck.failed);
  });
}
