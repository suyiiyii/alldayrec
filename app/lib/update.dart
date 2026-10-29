// The Android app's own updates: the server's /app.apk (server/apk.py reads its version) offered at the top of the home
// page when its build number is higher than this build's, downloaded by the system's DownloadManager (MainActivity
// "download": it carries on with the app closed and resumes after a drop) and handed to the system installer. Checked at
// start and, back in front, once a day; iPhone builds are installed by hand.
import 'dart:async';
import 'dart:convert';
import 'dart:io' show Platform;

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
  bool waiting = false;  // the download not going: no network yet, or a retry
  String? error;  // the last download's failure
  DateTime checked = DateTime(2000);  // the server's last answer
  Timer? watch;  // in front with the offer's download going: its progress, and the installer once it is done

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _check();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    watch?.cancel();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) return watch?.cancel();
    if (offer != null) _watch();
    if (DateTime.now().difference(checked).inHours >= 24) _check();
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
      final mine = await _ch.invokeMethod('app') as int;
      final dismissed = (await SharedPreferences.getInstance()).getInt('apkDismissed') ?? 0;
      final o = apkOffer(res.statusCode == 200 ? jsonDecode(res.body) : null, mine, dismissed);
      if (o == null) _ch.invokeMethod('dropDownload').ignore();  // the download this build was installed from
      if (!mounted) return;
      setState(() => offer = o);
      if (o != null) _watch();  // one left going when the app was closed
    } catch (_) {}
  }

  Future<void> _dismiss() async {
    (await SharedPreferences.getInstance()).setInt('apkDismissed', offer!['versionCode'] as int);
    _ch.invokeMethod('dropDownload').ignore();
    watch?.cancel();
    setState(() { offer = null; progress = null; });
  }

  /// /app.apk by the system's DownloadManager, the one already going or done when there is one.
  Future<void> _download() async {
    final o = offer!, l = AppLocalizations.of(context);
    setState(() { progress = 0; waiting = false; error = null; });
    try {
      if (await _ch.invokeMethod('allowInstalls') != true) return setState(() { progress = null; error = l.allowInstalls; });
      final s = await Settings.load();
      await _ch.invokeMethod('download', {'code': o['versionCode'], 'version': '${o['versionName']}', 'url': '${s.server}/app.apk', 'token': s.token});
      _watch();
    } catch (e) {
      if (mounted) setState(() { progress = null; error = l.downloadFailedRetry('$e'); });
    }
  }

  void _watch() {
    watch?.cancel();
    watch = Timer.periodic(const Duration(seconds: 1), (_) => _poll());
    _poll();
  }

  /// The installer when a download this banner showed going is done; one found done (its installer closed, or done
  /// with the app closed) waits for a tap, which finds it done at once.
  Future<void> _poll() async {
    final o = offer, l = AppLocalizations.of(context);
    if (o == null) return watch?.cancel();
    Map? d;
    try {
      d = await _ch.invokeMethod('downloaded', {'code': o['versionCode']}) as Map?;
    } on PlatformException catch (e) {
      d = {'status': 'failed', 'reason': e.message};
    }
    if (!mounted) return;
    final st = d?['status'];
    if (st == 'running' || st == 'waiting') {
      final t = d!['total'] as int, total = t > 0 ? t : o['size'] as num? ?? 0;  // -1 until the server answers
      return setState(() { progress = total > 0 ? d!['done'] / total : 0; waiting = st == 'waiting'; });
    }
    watch?.cancel();  // done, failed or gone (null): the banner can be tapped again and shows the error
    final shown = progress != null;
    setState(() {
      progress = null;
      if (st == 'failed') error = l.downloadFailedRetry('${d!['reason']}');
    });
    if (!shown || st != 'done') return;
    try {
      await _ch.invokeMethod('install');
    } on PlatformException catch (e) {
      if (mounted) setState(() => error = l.cannotOpenInstaller('${e.message}'));
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
                    if (waiting) Text(l.waitingForNetwork, style: t.textTheme.bodySmall?.copyWith(color: cs.onSecondaryContainer)),
                  ],
                  if (error != null && p == null) Text(error!, style: t.textTheme.bodySmall?.copyWith(color: cs.error)),
                ]),
              ),
            ),
            IconButton(icon: const Icon(Icons.close_rounded), tooltip: l.dontRemindVersion, color: cs.onSecondaryContainer, onPressed: _dismiss),
          ]),
        ),
      ),
    );
  }
}
