// 11-24 (#421's review): the chip showed a fast transfer's whole 「x / y MB」 and on a 320 wide phone, or with the system's
// text made bigger, it cut the top bar's title to 「时…」. The chip now shows the bytes read so far only; the title stays
// whole at 320/360/390 wide, text ×1.0/×1.3, with the WebView pages' refresh button or without, in Chinese (in English
// 「Timeline」 is cut by a few px at 320 wide, ×1.3, with the refresh button). Measured in Arial Unicode, near the phones'
// own fonts (the test font's glyphs are all 1 em wide); it is on macOS only.
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:live_record/l10n/app_localizations.dart';
import 'package:live_record/recorder_status.dart';

const _font = '/System/Library/Fonts/Supplemental/Arial Unicode.ttf';

void main() {
  final skip = File(_font).existsSync() ? null : 'no $_font (macOS only)';
  setUpAll(() async {
    if (skip != null) return;
    final bytes = ByteData.sublistView(File(_font).readAsBytesSync());
    for (final family in ['Roboto', 'CupertinoSystemText', 'CupertinoSystemDisplay', '.SF UI Text', '.SF UI Display']) {
      await (FontLoader(family)..addFont(Future.value(bytes))).load();
    }
  });
  // a fast transfer's bytes at their widest: 1023.44 MB of 1.21 GB
  const status = {'running': true, 'source': 'ble', 'connected': false, 'link': {'code': 'beanWifi'}, 'pending': 0,
    'wifiStep': {'step': 'transfer', 'reopen': 0}, 'syncDone': 1048000, 'syncTotal': 1268000,
    'hw': {'name': 'D3200-TEST', 'model': 'D3200', 'battery': 76, 'recording': false},
    'sync': {'phase': 'wifi', 'owed_s': 4620, 'eta_s': 60, 'held': null, 'stuck_s': 0, 'done_kb': 1048000, 'total_kb': 1268000}};
  for (final platform in [TargetPlatform.iOS, TargetPlatform.android]) {
    for (final width in [320.0, 360.0, 390.0]) {
      for (final scale in [1.0, 1.3]) {
        for (final refresh in [false, true]) {
          testWidgets('${platform.name} $width wide, text ×$scale, refresh $refresh: the title whole', skip: skip != null,
              (tester) async {
            tester.view.physicalSize = Size(width * 3, 2532);
            tester.view.devicePixelRatio = 3;
            addTearDown(tester.view.reset);
            debugDefaultTargetPlatformOverride = platform;
            await tester.pumpWidget(MaterialApp(locale: const Locale('zh'), localizationsDelegates: AppLocalizations.localizationsDelegates,
                supportedLocales: AppLocalizations.supportedLocales,
                home: Builder(builder: (c) => MediaQuery(data: MediaQuery.of(c).copyWith(textScaler: TextScaler.linear(scale)),
                    child: Scaffold(appBar: AppBar(  // as home.dart's
                        title: Text(AppLocalizations.of(c).tabTimeline, key: const Key('title')),
                        actionsPadding: const EdgeInsets.only(right: 8),
                        actions: [
                          StatusChip(status: status, ble: true, onPressed: () {}),
                          if (refresh) IconButton(icon: const Icon(Icons.refresh), visualDensity: VisualDensity.compact, onPressed: () {}),
                        ]))))));
            await tester.pump(const Duration(milliseconds: 100));
            debugDefaultTargetPlatformOverride = null;
            double whole(RenderParagraph p) => (TextPainter(text: p.text, textDirection: TextDirection.ltr, textScaler: p.textScaler)..layout()).width;
            final title = tester.renderObject<RenderParagraph>(find.descendant(of: find.byKey(const Key('title')), matching: find.byType(RichText)));
            expect(title.size.width + 0.5, greaterThanOrEqualTo(whole(title)), reason: 'the title cut');
            for (final e in find.descendant(of: find.byType(ActionChip), matching: find.byType(RichText)).evaluate()) {
              final p = e.renderObject! as RenderParagraph;
              expect(p.size.width + 0.5, greaterThanOrEqualTo(whole(p)), reason: 'the chip cut: ${p.text.toPlainText()}');
            }
            expect(tester.takeException(), isNull);
          });
        }
      }
    }
  }
}
