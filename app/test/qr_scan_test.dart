// parseSignInQr: the web page's sign-in QR is `<server>/?token=<token>`; anything else is not a sign-in QR.
import 'package:flutter_test/flutter_test.dart';
import 'package:live_record/qr_scan_page.dart';

void main() {
  test('web URL with the token', () {
    expect(parseSignInQr('https://example.com/?token=a%2Bb%3D'), {'server': 'https://example.com', 'token': 'a+b='});
    expect(parseSignInQr('http://192.0.2.5:8770/?token=t1'), {'server': 'http://192.0.2.5:8770', 'token': 't1'});
    expect(parseSignInQr('https://example.com/rec/?token=t1#x'), {'server': 'https://example.com/rec', 'token': 't1'});
    expect(parseSignInQr('http://192.0.2.5:8770/?token='), {'server': 'http://192.0.2.5:8770', 'token': ''});  // no auth
  });

  test('anything else', () {
    expect(parseSignInQr('https://example.com/'), isNull);
    expect(parseSignInQr('ftp://example.com/?token=t1'), isNull);
    expect(parseSignInQr('hello token=t1'), isNull);
    expect(parseSignInQr('https://example.com/?token=%E0%A4%A'), isNull);  // broken %-escape
  });
}
