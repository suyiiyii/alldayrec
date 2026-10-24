// nativeText: what the native side says as codes (Ble.kt/Ble.swift, RecorderService.kt/Recorder.swift, Dfu.kt/DfuRun.swift),
// in words in either language.
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:live_record/l10n/app_localizations.dart';
import 'package:live_record/recorder_status.dart';

// every code the native side sends, with the values it sends with it
final codes = <Map<String, Object?>>[
  {'code': 'scanning'}, {'code': 'scanning', 'name': 'LR-P3-2420'}, {'code': 'bluetoothOff'}, {'code': 'noPermission'},
  {'code': 'unbound'}, {'code': 'kicked'}, {'code': 'elsewhere', 'name': 'LR-P3-2420'}, {'code': 'bluetoothNotOn', 's': 5},
  {'code': 'scanFailed', 'err': 2, 's': 5}, {'code': 'connectFailed', 's': 5}, {'code': 'away', 'lastSeen': null},
  {'code': 'away', 'lastSeen': 1800000000000}, {'code': 'connecting'}, {'code': 'stalePairing', 'name': 'LR-P3-2420'},
  {'code': 'stalePairing', 'name': null}, {'code': 'mtu'}, {'code': 'pairing'}, {'code': 'pairingDialog'}, {'code': 'encrypting'},
  {'code': 'pairingFailed'}, {'code': 'services'}, {'code': 'clock'}, {'code': 'status'}, {'code': 'forgetOthers'}, {'code': 'joining'},
  {'code': 'connected'}, {'code': 'linkError', 'detail': 'GATT 133'}, {'code': 'synced', 'at': 1800000000000, 'kb': 1234},
  {'code': 'notConnected'}, {'code': 'usbSwitchingOn'}, {'code': 'usbSwitchingOff'}, {'code': 'usbNoComputer'},
  {'code': 'usbFailed', 'err': -19}, {'code': 'usbNotYetOn'}, {'code': 'usbStillOn'}, {'code': 'usbOn'}, {'code': 'rejected'},
  {'code': 'uploadFailed', 'detail': 'HTTP 502'}, {'code': 'endFailed', 'detail': 'HTTP 502'}, {'code': 'archiveFailed', 'detail': 'EIO'},
  {'code': 'killed', 'count': 7}, {'code': 'interrupted', 'at': 1800000000000},
  {'code': 'routeFailed', 'at': 1800000000000, 'detail': 'busy'}, {'code': 'resumeFailed', 'at': 1800000000000, 'detail': 'busy'},
  {'code': 'dfuPreparing'}, {'code': 'dfuRebooting'}, {'code': 'dfuStarting'}, {'code': 'dfuProgress', 'pct': 42, 'kbps': 1.94},
  {'code': 'dfuDone'}, {'code': 'dfuAborted'}, {'code': 'dfuFailed', 'why': null, 'detail': 'Device disconnected'},
  for (final why in ['dfuNoBluetooth', 'dfuUnknown', 'dfuNoConnect', 'dfuNoService', 'dfuNoReboot', 'dfuDropped', 'dfuNoBootloader'])
    {'code': 'dfuFailed', 'why': why, 'detail': null},
  {'code': 'dfuFailed', 'why': 'dfuRefused', 'detail': 'Write not permitted'},
  {'code': 'permission'}, {'code': 'notStarted'}, {'code': 'listen', 'detail': 'busy'}, {'code': 'updating'}, {'code': 'linked'},
  {'code': 'noModel'}, {'code': 'model', 'want': 'LR-X1', 'model': 'LR-P3'}, {'code': 'same', 'version': '20261011.164128-098861b'},
  {'code': 'sha256'}, {'code': 'zip', 'detail': 'bad zip'},
];

void main() {
  Future<BuildContext> contextIn(WidgetTester tester, Locale locale) async {
    late BuildContext context;
    await tester.pumpWidget(MaterialApp(
      locale: locale,
      localizationsDelegates: AppLocalizations.localizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: Builder(builder: (c) {
        context = c;
        return const SizedBox();
      }),
    ));
    return context;
  }

  testWidgets('every code has its words in both languages', (tester) async {
    final han = RegExp(r'[一-鿿]');
    final zh = await contextIn(tester, const Locale('zh')), zhTexts = [for (final v in codes) nativeText(zh, v)];
    final en = await contextIn(tester, const Locale('en')), enTexts = [for (final v in codes) nativeText(en, v)];
    for (final (i, v) in codes.indexed) {
      expect(zhTexts[i], allOf(isNot(v['code']), contains(han)), reason: '$v');
      expect(enTexts[i], allOf(isNot(v['code']), isNot(contains(han))), reason: '$v');
    }
  });

  testWidgets('the words and their values', (tester) async {
    final zh = await contextIn(tester, const Locale('zh'));
    expect(nativeText(zh, {'code': 'connected'}), '已连接 · 实时上传');
    expect(nativeText(zh, {'code': 'scanning', 'name': 'LR-P3-2420'}), '正在找 LR-P3-2420');
    expect(nativeText(zh, {'code': 'dfuProgress', 'pct': 42, 'kbps': 1.94}), '升级中 42% · 1.9 KB/s');
    expect(nativeText(zh, {'code': 'dfuFailed', 'why': 'dfuNoConnect'}), '升级失败：连不上录音设备（录音设备还在就再试一次，不见了就插 USB 用 lr.sh flash 刷）');
    final en = await contextIn(tester, const Locale('en'));
    expect(nativeText(en, {'code': 'dfuFailed', 'why': 'dfuRefused', 'detail': 'Write not permitted'}),
        'Update failed: The recorder refused update mode (Write not permitted) (recorder still there: try again; gone: plug it into USB and flash it with lr.sh flash)');
    expect(nativeText(en, {'code': 'dfuFailed', 'detail': 'Device disconnected'}), startsWith('Update failed: Device disconnected ('));
    expect(platformText(en, PlatformException(code: 'model', details: {'code': 'model', 'want': 'LR-X1', 'model': 'LR-P3'})),
        'This firmware is for LR-X1 and the recorder is LR-P3: it cannot go on');
    expect(platformText(en, PlatformException(code: 'listen', message: 'busy')), 'Cannot play: busy');
    expect(platformText(en, PlatformException(code: 'start', message: 'Error Domain=x Code=1')), 'Error Domain=x Code=1');  // the system's own
    expect(nativeText(en, 'java.io.IOException: EIO'), 'java.io.IOException: EIO');
  });
}
