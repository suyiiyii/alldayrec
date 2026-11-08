import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:live_record/l10n/app_localizations.dart';
import 'package:live_record/recorder_page.dart';

// A recorder whose card has no file system: the page offers to format it, asks first with its size, waits for the
// board, then says the new size; a card that reads fine gets no such button.
void main() {
  Map hw = {};
  final asked = <int>[];
  var answer = 0;  // the board's
  setUp(() {
    asked.clear();
    answer = 0;
    hw = {'store': -19, 'free_kb': 0, 'total_kb': 0, 'card': 'blank', 'card_kb': 31166976, 'battery': 80};
  });
  Future<void> open(WidgetTester tester) async {
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(const MethodChannel('live_record/recorder'), (call) async {
      if (call.method == 'recorder') return {'name': 'LR-P3-TEST', 'model': 'LR-P3'};
      if (call.method == 'status') return {'running': true, 'connected': true, 'hw': hw};
      if (call.method == 'formatCard') {
        asked.add(call.arguments['code']);
        await Future.delayed(const Duration(seconds: 20));
        if (answer == 0) hw = {'store': 0, 'free_kb': 31160000, 'total_kb': 31162368, 'card': 'ok', 'card_kb': 31166976, 'battery': 80};
        return answer;
      }
      return null;
    });
    await tester.pumpWidget(const MaterialApp(localizationsDelegates: AppLocalizations.localizationsDelegates,
        supportedLocales: AppLocalizations.supportedLocales, home: Scaffold(body: RecorderPage(onSource: _noop))));
    await tester.pump();
    await tester.pump();
  }

  testWidgets('a card with no file system is formatted after asking', (tester) async {
    await open(tester);
    expect(find.text('Not formatted'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Format card'), 200);
    await tester.tap(find.text('Format card'));
    await tester.pump();
    expect(find.textContaining('Everything on the card will be erased'), findsOneWidget);
    expect(find.textContaining('29.72 GB'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await tester.pump();
    expect(asked, isEmpty);
    await tester.tap(find.text('Format card'));
    await tester.pump();
    await tester.tap(find.text('Erase and format'));
    await tester.pump();
    expect(find.textContaining('Formatting, this takes tens of seconds'), findsOneWidget);
    expect(asked, [31166976]);  // its size as the board gave it: the code
    await tester.pump(const Duration(seconds: 21));
    await tester.pump();
    expect(find.text('Formatted: 29.72 GB. The recorder now records to the card.'), findsOneWidget);
    await tester.tap(find.text('OK'));
    await tester.pump();
    expect(find.text('Format card'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 3));
  });

  testWidgets('a format the board turned down says why', (tester) async {
    answer = -17;
    await open(tester);
    await tester.scrollUntilVisible(find.text('Format card'), 200);
    await tester.tap(find.text('Format card'));
    await tester.pump();
    await tester.tap(find.text('Erase and format'));
    await tester.pump(const Duration(seconds: 21));
    await tester.pump();
    expect(find.text('Not formatted: the card reads fine now, and everything on it is still there.'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 3));
  });

  testWidgets('a card that reads, or a failing one, has no format button', (tester) async {
    for (final card in ['ok', 'failing', 'none', null]) {
      hw = {'store': card == 'ok' ? 0 : -5, 'free_kb': 1, 'total_kb': card == 'ok' ? 2 : 0, 'card': card, 'card_kb': 31166976};
      await open(tester);
      expect(find.text('Format card'), findsNothing);
    }
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 3));
  });
}

Future<void> _noop(String s) async {}
