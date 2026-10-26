// Native recorder bridge and process-wide BLE link hold.
import 'package:flutter/services.dart';

class Recorder {
  static const _ch = MethodChannel('live_record/recorder');
  static Future<void> start({
    required String server,
    required String token,
    required String device,
    required bool archive,
    required String source,
    required Map<String, int> slices,
  }) => _ch.invokeMethod('start', {
        'server': server, 'token': token, 'device': device, 'archive': archive, 'source': source, ...slices});
  static Future<void> slices(Map<String, int> slices) => _ch.invokeMethod('slices', slices);  // a running recorder takes them at once
  static Future<void> stop() => _ch.invokeMethod('stop');
  static Future<void> upload() => _ch.invokeMethod('upload');  // pieces left and not recording: send them anyway
  static Future<void> foreground(bool on) => _ch.invokeMethod('foreground', {'on': on});
  static Future<Map> status() async => (await _ch.invokeMethod('status')) as Map;
  static Future<String> deviceName() async => (await _ch.invokeMethod('deviceName')) as String;
  static Future<String> version() async => (await _ch.invokeMethod('version')) as String;  // this build: 1.4.9+24
  // the BLE recorder: which one is bound, what it last said, how a firmware update goes (firmware.dart)
  static Future<Map> recorder() async => (await _ch.invokeMethod('recorder')) as Map;
  /// Unbinding: true when the recorder was connected and forgot every phone, false when only this phone forgot it.
  static Future<bool> unbind() async => (await _ch.invokeMethod('unbind')) as bool;
  static Future<Map> permissions() async => (await _ch.invokeMethod('permissions')) as Map;
  static Future<void> askPermission(String which) => _ch.invokeMethod('askPermission', {'which': which});
  static Future<void> scan(bool on) => _ch.invokeMethod('scan', {'on': on});  // the add-recorder page's list
  static Future<List> nearby() async => (await _ch.invokeMethod('nearby')) as List;
  static Future<void> bind(String name, String address, bool force) =>
      _ch.invokeMethod('bind', {'name': name, 'address': address, 'force': force});
  static Future<void> pause(int s) => _ch.invokeMethod('pause', {'s': s});  // 0: record again
  static Future<void> usbDisk(bool on) => _ch.invokeMethod('usbDisk', {'on': on});
  static Future<void> listen(bool on) => _ch.invokeMethod('listen', {'on': on});  // the recorder's sound played on this phone
  /// A web page's <input type=file> (导入录音) on Android: the system's file picker; the chosen files' content:// URIs.
  /// Always answers: on a throw the WebView never releases the input, which stays dead until the page reloads.
  static Future<List<String>> pickFiles(bool multiple, List<String> types) async {
    try {
      return await _ch.invokeListMethod<String>('pickFiles', {'multiple': multiple, 'types': types}) ?? const [];
    } catch (_) {
      return const [];
    }
  }
  static bool hold = false;  // the link is taken down on purpose (unbind, firmware update): do not bring it back meanwhile
  static final _errors = <String>{};

  /// An error nothing caught, into the device log (native DeviceLog, kind app_error): each kind and place once a run.
  static void error(Object e, StackTrace? s) {
    final (type, frames) = errorSummary(e, s);
    if (!_errors.add('$type ${frames.firstOrNull}')) return;
    _ch.invokeMethod('error', {'type': type, 'stack': frames.join(' < ')}).catchError((_) => null);
  }

  /// Takes the BLE link down, runs `f`, and lets it come back (the home page's ticker restarts it).
  static Future<void> withoutLink(Future<void> Function() f) async {
    hold = true;
    try {
      await stop();
      while ((await status())['running'] == true) {
        await Future.delayed(const Duration(milliseconds: 300));
      }
      await f();
    } finally {
      hold = false;
    }
  }
}

/// An error as its type and first frames (function, file:line); no message: it may carry data.
(String, List<String>) errorSummary(Object e, StackTrace? s, {int n = 5}) => (
      '${e.runtimeType}',
      (s?.toString() ?? '').split('\n').map((l) => l.replaceFirst(RegExp(r'^#\d+\s+'), '').trim())
          .where((l) => l.isNotEmpty && l != '<asynchronous suspension>').take(n).toList(),
    );
