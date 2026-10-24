// The Android app's own updates: the server's /app.apk (server/apk.py reads its version) offered at the top of the home
// page when its build number is higher than this build's, downloaded here and handed to the system installer
// (MainActivity "install"). Checked at start and, back in front, once a day; iPhone builds are installed by hand.
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'l10n/app_localizations.dart';
import 'settings.dart';

const _ch = MethodChannel('live_record/recorder');

/// The server's APK (/api/app/latest) when its versionCode is above this build's and above the one whose offer was
/// closed; null otherwise, also for anything that is not such an answer.
Map? apkOffer(Object? latest, int mine, int dismissed) {
  final code = latest is Map ? latest['versionCode'] : null;
  return code is int && code > mine && code > dismissed ? latest as Map : null;
}

class UpdateBanner extends StatefulWidget {
  const UpdateBanner({super.key});
  @override
  State<UpdateBanner> createState() => _UpdateBannerState();
}

class _UpdateBannerState extends State<UpdateBanner> with WidgetsBindingObserver {
  Map? offer;
  double? progress;  // downloading: the share so far
  String? error;  // the last download's failure
  DateTime checked = DateTime(2000);  // the server's last answer

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _check();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && DateTime.now().difference(checked).inHours >= 24) _check();
  }

  /// Quiet: no token, offline, a 401 or a server without the route only mean no offer (and another try next time in front).
  Future<void> _check() async {
    if (!Platform.isAndroid || progress != null) return;
    try {
      final s = await Settings.load();
      if (s.token.isEmpty) return;
      final res = await http.get(Uri.parse('${s.server}/api/app/latest'), headers: {'Authorization': 'Bearer ${s.token}'})
          .timeout(const Duration(seconds: 30));
      if (res.statusCode != 200 && res.statusCode != 404) return;  // 404: no APK on this server
      checked = DateTime.now();
      final app = await _ch.invokeMethod('app') as Map;
      final dismissed = (await SharedPreferences.getInstance()).getInt('apkDismissed') ?? 0;
      final o = apkOffer(res.statusCode == 200 ? jsonDecode(res.body) : null, app['code'] as int, dismissed);
      if (o == null) File(app['apk'] as String).delete().ignore();  // the download this build was installed from
      if (mounted) setState(() => offer = o);
    } catch (_) {}
  }

  Future<void> _dismiss() async {
    (await SharedPreferences.getInstance()).setInt('apkDismissed', offer!['versionCode'] as int);
    setState(() => offer = null);
  }

  /// /app.apk with the token, into the file MainActivity hands to the installer.
  Future<void> _download() async {
    final size = offer!['size'] as num? ?? 0, l = AppLocalizations.of(context);
    setState(() { progress = 0; error = null; });
    final c = HttpClient()..connectionTimeout = const Duration(seconds: 15);
    try {
      if (await _ch.invokeMethod('allowInstalls') != true) {
        error = l.allowInstalls;
        return;
      }
      final s = await Settings.load(), app = await _ch.invokeMethod('app') as Map;
      final req = await c.getUrl(Uri.parse('${s.server}/app.apk'));
      req.headers.set('Authorization', 'Bearer ${s.token}');
      final res = await req.close();
      if (res.statusCode != 200) throw 'HTTP ${res.statusCode}';
      final total = res.contentLength > 0 ? res.contentLength : size, out = File(app['apk'] as String).openWrite();
      var got = 0;
      try {
        await for (final b in res.timeout(const Duration(seconds: 60))) {  // a stalled download fails instead of hanging
          out.add(b);
          got += b.length;
          if (mounted && total > 0) setState(() => progress = got / total);
        }
      } finally {
        await out.close();
      }
      await _ch.invokeMethod('install');
    } on PlatformException catch (e) {
      error = l.cannotOpenInstaller('${e.message}');
    } on TimeoutException {
      error = l.downloadStalled;
    } catch (e) {
      error = l.downloadFailedRetry('$e');
    } finally {
      c.close(force: true);
      if (mounted) setState(() => progress = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final o = offer, p = progress;
    if (o == null) return const SizedBox.shrink();
    final t = Theme.of(context), cs = t.colorScheme, l = AppLocalizations.of(context);
    String mb(num b) => (b / 1048576).toStringAsFixed(b < 10485760 ? 1 : 0);
    final size = o['size'] as num? ?? 0;
    return Material(
      color: cs.secondaryContainer,
      child: InkWell(
        onTap: p == null ? _download : null,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 4, 4, 4),
          child: Row(children: [
            Icon(Icons.system_update_rounded, color: cs.onSecondaryContainer),
            const SizedBox(width: 12),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
                  Text(p != null ? l.downloadingApp('${o['versionName']}', mb(p * size), mb(size))
                      : l.appUpdate('${o['versionName']}', size > 0 ? l.appUpdateSize(mb(size)) : ''),
                      style: TextStyle(color: cs.onSecondaryContainer, fontWeight: FontWeight.w500)),
                  if (p != null) ...[
                    const SizedBox(height: 6),
                    LinearProgressIndicator(value: p, borderRadius: BorderRadius.circular(2)),
                  ],
                  if (error != null && p == null) Text(error!, style: t.textTheme.bodySmall?.copyWith(color: cs.error)),
                ]),
              ),
            ),
            if (p == null)
              IconButton(icon: const Icon(Icons.close_rounded), tooltip: l.dontRemindVersion, color: cs.onSecondaryContainer, onPressed: _dismiss)
            else
              const SizedBox(width: 12),
          ]),
        ),
      ),
    );
  }
}
