// mergeNearby: the add-recorder page's rows keep their places while scans come and go.
import 'package:flutter_test/flutter_test.dart';
import 'package:live_record/add_recorder_page.dart';

// a scan entry as Recorder.nearby gives it; the native lists come strongest first
Map rec(String name, int rssi, {String? address}) =>
    {'name': name, 'address': address ?? name, 'rssi': rssi, 'state': 0, 'busy': false};

List<Map> scan(List<Map> rows, List<Map> found) => mergeNearby(rows, [...found]..sort((a, b) => b['rssi'] - a['rssi']));

List<String> names(List<Map> rows) => [for (final r in rows) r['name']];
List<String> gone(List<Map> rows) => [for (final r in rows) if (r['gone']) r['name']];
String? nearest(List<Map> rows) => rows.where((r) => r['nearest']).firstOrNull?['name'];

void main() {
  const a = 'LR-P3-0A01', b = 'LR-P3-0B02', c = 'LR-X1-0C03', d = 'LR-P3-0D04';

  test('the first scan is strongest first, later scans move nothing', () {
    var rows = scan([], [rec(a, -70), rec(b, -50), rec(c, -60)]);
    expect(names(rows), [b, c, a]);
    expect(nearest(rows), b);
    for (final s in [
      [rec(a, -40), rec(b, -80), rec(c, -55)],
      [rec(a, -90), rec(b, -45), rec(c, -42)],
      [rec(c, -30)],  // a and b missed once
      [rec(a, -60), rec(b, -61), rec(c, -62)],
    ]) {
      rows = scan(rows, s);
      expect(names(rows), [b, c, a]);
      expect(gone(rows), isEmpty);
    }
    expect(rows.last['rssi'], -60);  // the signal shown is the latest
  });

  test('one missing from 3 scans stays in place, greyed, and comes back there', () {
    var rows = scan([], [rec(a, -50), rec(b, -60), rec(c, -70)]);
    rows = scan(rows, [rec(a, -50), rec(c, -70)]);
    rows = scan(rows, [rec(a, -50), rec(c, -70)]);
    expect(gone(rows), isEmpty);
    rows = scan(rows, [rec(a, -50), rec(c, -70)]);
    expect(names(rows), [a, b, c]);
    expect(gone(rows), [b]);
    rows = scan(rows, [rec(b, -40), rec(a, -50), rec(c, -70)]);
    expect(names(rows), [a, b, c]);
    expect(gone(rows), isEmpty);
  });

  test('an empty scan (Android restarting its scan) greys nothing', () {
    var rows = scan([], [rec(a, -50), rec(b, -60)]);
    rows = scan(rows, []);
    expect(names(rows), [a, b]);
    expect(gone(rows), isEmpty);
    expect(nearest(rows), a);
  });

  test('a new one goes to the end, even the strongest', () {
    var rows = scan([], [rec(a, -50), rec(b, -60)]);
    rows = scan(rows, [rec(d, -30), rec(a, -50), rec(b, -60)]);
    expect(names(rows), [a, b, d]);
    rows = scan(rows, [rec(c, -90), rec(d, -30), rec(a, -50)]);
    expect(names(rows), [a, b, d, c]);
  });

  test('one name, one row: a second address, or a second name for one address', () {
    var rows = scan([], [rec(a, -50, address: 'u1'), rec(b, -60)]);
    rows = scan(rows, [rec(a, -45, address: 'u1'), rec(a, -50, address: 'u2'), rec(b, -60)]);
    expect(names(rows), [a, b]);
    expect(rows.first['address'], 'u1');
    rows = scan(rows, [rec(a, -45, address: 'u2'), rec(b, -60)]);  // u1 not heard: the row follows u2
    expect(names(rows), [a, b]);
    expect(rows.first['address'], 'u2');
    // a busy recorder on iOS shows its board name until a scan response brings the full one, under the same identifier
    rows = scan(rows, [rec('LR-P3', -70, address: 'u3')]);
    rows = scan(rows, [rec(c, -70, address: 'u3')]);
    expect(names(rows), [a, b, c]);
    // a stale cached name on Android: the same address under two names
    rows = scan(rows, [rec(c, -70, address: 'u3'), rec('LR-P2-0C03', -71, address: 'u3')]);
    expect(names(rows), [a, b, c]);
  });

  test('two recorders both showing only their board name are two rows', () {
    final rows = scan([], [rec('LR-P3', -50, address: 'u1'), rec('LR-P3', -60, address: 'u2')]);
    expect(rows.map((r) => r['address']), ['u1', 'u2']);
  });

  test('"nearest" moves only to one 6 dB stronger, and never to a greyed one', () {
    var rows = scan([], [rec(a, -60), rec(b, -62)]);
    expect(nearest(rows), a);
    rows = scan(rows, [rec(a, -60), rec(b, -56)]);
    expect(nearest(rows), a);
    rows = scan(rows, [rec(a, -60), rec(b, -54)]);
    expect(nearest(rows), b);
    for (var i = 0; i < 3; i++) {
      rows = scan(rows, [rec(a, -70)]);
    }
    expect(gone(rows), [b]);
    expect(nearest(rows), isNull);  // one left: nothing to tell apart
    rows = scan(rows, [rec(a, -70), rec(c, -80)]);
    expect(nearest(rows), a);
  });

  test('a bean keeps its row as its address changes (its name is from its MAC)', () {
    const bean = 'D3200-0A0B';
    var rows = scan([], [rec(bean, -50, address: 'r1'), rec(a, -60)]);
    rows = scan(rows, [rec(bean, -52, address: 'r2'), rec(a, -60)]);
    expect(names(rows), [bean, a]);
    expect(rows.first['address'], 'r2');
  });
}
