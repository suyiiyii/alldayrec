// Application navigation and shared recording lifecycle; pages receive the live settings/status.
import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:record/record.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_android/webview_flutter_android.dart';
import 'package:webview_flutter_wkwebview/webview_flutter_wkwebview.dart';

import 'l10n/app_localizations.dart';
import 'page_widgets.dart';
import 'pause_button.dart';
import 'recorder.dart';
import 'recorder_page.dart';
import 'recorder_status.dart';
import 'settings.dart';
import 'settings_page.dart';
import 'update.dart';
import 'upload_page.dart';

/// Debug builds: `--dart-define=FAKE_STATUS=recording` (or syncing, fetching, paused, disconnected, pausedAway, low,
/// charging, mic, warning) shows that canned status instead of the recorder's, for screenshots of the chip and its panel.
const fakeStatus = kDebugMode ? String.fromEnvironment('FAKE_STATUS') : '';

/// A canned native status (Recorder.status) by name, as the BLE link or the microphone would report it.
Map cannedStatus(String name) {
  final now = DateTime.now().millisecondsSinceEpoch;
  final hw = {'name': 'LR-P3-0000', 'model': 'LR-P3', 'battery': 87, 'charge': 'battery', 'rssi': -62, 'store': 0,
    'free_kb': 25000000, 'total_kb': 31000000, 'backlog_kb': 0};
  final ble = {'running': true, 'source': 'ble', 'connected': true, 'link': {'code': 'connected'}, 'hw': hw, 'pending': 0};
  return switch (name) {
    'recording' => ble,
    'syncing' => {...ble, 'syncDone': 790500, 'syncTotal': 1254000, 'hw': {...hw, 'backlog_kb': 464000, 'backlog_eta_s': 1200}},
    'fetching' => {...cannedStatus('syncing'), 'connected': false, 'link': {'code': 'joining'}},  // the file being recorded, before LIVE
    'paused' => {...ble, 'pausedUntil': now + 12 * 60000 - 1000},
    'disconnected' => {...ble, 'connected': false, 'link': {'code': 'away', 'lastSeen': now - 47 * 60000}},
    'pausedAway' => {...cannedStatus('disconnected'), 'pausedUntil': now + 12 * 60000 - 1000},
    'low' => {...ble, 'hw': {...hw, 'battery': 14, 'rssi': -84}},
    'charging' => {...ble, 'hw': {...hw, 'battery': 62, 'charge': 'charging'}},
    'mic' => {'running': true, 'source': 'mic', 'pending': 2, 'pendingBytes': 61000, 'sent': 312, 'slice': 10},
    'warning' => {...ble, 'note': {'code': 'uploadFailed', 'detail': 'HTTP 502'}, 'pending': 14, 'pendingBytes': 2400000},
    _ => {},
  };
}

class Home extends StatefulWidget {
  const Home({super.key});
  @override
  State<Home> createState() => _HomeState();
}

class _HomeState extends State<Home> with WidgetsBindingObserver {
  Settings? settings;
  Map status = fakeStatus.isEmpty ? {} : cannedStatus(fakeStatus);
  String note = '';
  bool stopping = false;
  int page = 0;  // the bottom bar's: 0 时间线, 1 实时, 2 人物 (web pages), 3 我的 (its entries open over it)
  final webs = <WebViewController?>[null, null, null];  // one per web page: each keeps its scroll while another is shown
  // WebViews whose page iOS took away while the app was in the background (it reclaims web processes idle for hours:
  // the three tabs stayed blank, 10-25): loaded again when the app comes back to the front
  final dropped = <WebViewController>{};
  Timer? ticker;
  int ticks = 0;
  String version = '';

  static const paths = ['/', '/live', '/speakers'];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    Settings.load().then((s) {
      if (!mounted) return;
      setState(() {
        settings = s;
        _loadWeb();
      });
      _slices();
    });
    Recorder.version().then((v) => mounted ? setState(() => version = v) : null);
    ticker = Timer.periodic(const Duration(seconds: 2), (_) async {  // the service may be running from before this UI opened
      if (fakeStatus.isNotEmpty) return setState(() => status = cannedStatus(fakeStatus));
      if (++ticks % 150 == 0) _slices();  // every 5 min: the server may have changed the lengths
      final s = await Recorder.status();
      if (!mounted) return;
      // uploading does not wait for recording: pieces left over (a stop, a kill, a lost network) go now
      if (s['running'] != true && s['uploading'] != true && (s['pending'] ?? 0) > 0 && settings?.token.isNotEmpty == true) {
        await Recorder.upload();
      }
      if (mounted) setState(() => status = s);
      // the BLE recorder records by itself: with it as the source the link just runs, no start/stop (a failed start,
      // e.g. a permission still to grant, is tried again every 30 s)
      if (ble && !Recorder.hold && s['dfuBusy'] != true && s['running'] != true && settings!.token.isNotEmpty &&
          DateTime.now().difference(autoStarted).inSeconds >= 30) {
        autoStarted = DateTime.now();
        await _start();
      }
    });
  }

  @override
  void dispose() {
    ticker?.cancel();
    rev.dispose();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  DateTime autoStarted = DateTime(2000);
  bool get ble => fakeStatus.isNotEmpty ? status['source'] == 'ble' : settings?.source == 'ble';

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    Recorder.foreground(state == AppLifecycleState.resumed);
    if (state == AppLifecycleState.resumed) {
      _slices();
      for (final c in dropped) {
        c.reload();
      }
      dropped.clear();
    }
    if (state == AppLifecycleState.paused) Recorder.listen(false);  // listening is for the app in front only
  }

  /// Ask the server for piece lengths; a running recorder switches to them at its next piece.
  Future<void> _slices() async {
    final s = settings;
    if (s == null) return;
    final got = await Slices.fetch(s);
    if (got != null && running) await Recorder.slices(got);
  }

  bool get running => status['running'] == true;
  bool get connected => status['connected'] == true;  // the BLE recorder's link
  bool get syncing => ((status['syncTotal'] as num?) ?? 0) > 0;  // its card's backlog being read

  /// Bumped by every setState: the status sheet is a route of its own, outside this build, and rebuilds on it.
  final rev = ValueNotifier(0);
  @override
  void setState(VoidCallback fn) {
    super.setState(fn);
    rev.value++;
  }

  /// The tab a web path belongs to: 时间线 (the feed, one conversation, the calendar, the todos), 实时 (the live
  /// captions), 人物 (the list and one person), 我的 (the web /me). null: none in particular (stay where it is: 进度,
  /// 设备...)
  static int? _tabOf(String path) =>
      path == '/' || path == '/conversations' || path.startsWith('/c/') || path == '/calendar' || path == '/todos' ? 0
      : path == '/live' ? 1
      : path == '/speakers' || path.startsWith('/p/') ? 2
      : path == '/me' ? 3 : null;

  /// A tab's WebView went to another tab's page (实时's last conversation opens in 时间线): that tab shows it, and this one
  /// steps back to where it was. A page is a tab, not a place inside one.
  void _follow(int tab, String url) {
    final to = _tabOf(Uri.parse(url).path);
    if (to == null || to == tab) return;
    if (to < webs.length) (webs[to] ??= _web(tab: to)).loadRequest(_url(Uri.parse(url).path));
    _show(to);
    final c = webs[tab]!;
    c.canGoBack().then((b) => b ? c.goBack() : c.loadRequest(_url(paths[tab])));
  }

  WebViewController _web({int? tab}) {
    // the web pages play audio from script (the conversation player's next window, a seek): no tap needed for that
    // the microphone for a page's getUserMedia (问 AI's hold-to-talk, /record) once the app has it: Android's WebView
    // refuses without this, iOS's would ask again for each page on top of the app's own prompt
    final c = WebViewController.fromPlatformCreationParams(
        WebViewPlatform.instance is WebKitWebViewPlatform
            ? WebKitWebViewControllerCreationParams(allowsInlineMediaPlayback: true, mediaTypesRequiringUserAction: const {})
            : const PlatformWebViewControllerCreationParams(),
        onPermissionRequest: (r) async {
          final rec = AudioRecorder();
          final ok = r.types.difference({WebViewPermissionResourceType.microphone}).isEmpty && await rec.hasPermission();
          rec.dispose();
          ok ? r.grant() : r.deny();
        });
    c.setBackgroundColor(Colors.transparent);  // the scaffold shows through until the page paints (no white flash in dark mode)
    final p = c.platform;
    if (p is AndroidWebViewController) {
      p.setMediaPlaybackRequiresUserGesture(false);
      // a page's <input type=file> (导入录音): Android's WebView opens no picker by itself (iOS's does)
      p.setOnShowFileSelector((s) => Recorder.pickFiles(s.mode == FileSelectorMode.openMultiple, s.acceptTypes));
    }
    if (p is WebKitWebViewController) p.setInspectable(true);  // Safari's Develop menu on a Mac the phone trusts
    if (p is WebKitWebViewController) p.setAllowsBackForwardNavigationGestures(true);  // iOS: swipe from the left edge = back
    return c
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..clearCache()  // the page changes with server deploys
      // the web page hides its own navigation in the app: Android's WebView says so in its user agent, iOS's does not
      ..setNavigationDelegate(NavigationDelegate(
          onPageFinished: (_) => c.runJavaScript("document.documentElement.classList.add('in-app')").ignore(),
          // iOS ended the page's web process (memory, or idle for hours): reload, in front now, else on coming back
          onWebResourceError: (e) {
            if (e.errorType != WebResourceErrorType.webContentProcessTerminated) return;
            if (WidgetsBinding.instance.lifecycleState == AppLifecycleState.resumed) {
              c.reload();
            } else {
              dropped.add(c);
            }
          },
          onUrlChange: tab == null ? null : (u) => u.url != null ? _follow(tab, u.url!) : null));
  }

  Uri _url(String path) => Uri.parse('${settings!.server}$path?token=${settings!.token}');  // ?token= sets the cookie

  void _loadWeb() {
    if (settings!.token.isEmpty) return;
    for (var i = 0; i < webs.length; i++) {
      (webs[i] ??= _web(tab: i)).loadRequest(_url(paths[i]));
    }
  }

  /// One of 我的's entries, over the bottom bar; back (the key or the app bar's arrow) comes back to 我的.
  void _open(String title, Widget body) =>
      Navigator.push(context, MaterialPageRoute(builder: (_) => Scaffold(appBar: AppBar(title: Text(title)), body: body)));

  /// A web page (设备状态与更多: /me) in a WebView of its own, there while open;
  /// back (the key or the app bar's arrow) goes through its history first, as in the tabs' WebViews.
  void _openWeb(String title, String path) {
    final c = _web()..loadRequest(_url(path));
    _open(title, PopScope(canPop: false, onPopInvokedWithResult: (didPop, _) async {
      if (didPop) return;
      if (await c.canGoBack()) return c.goBack();
      if (mounted) Navigator.pop(context);
    }, child: WebViewWidget(controller: c)));
  }

  Future<void> _saved() async {
    await settings!.save();
    _loadWeb();
    // the source changed under a running recorder: stop it; with the BLE recorder as the new source the ticker starts it
    final changed = running && status['source'] != settings!.source;
    if (changed) await _stop();
    autoStarted = DateTime(2000);
    if (!mounted) return;
    Navigator.popUntil(context, (r) => r.isFirst);
    final l = AppLocalizations.of(context);
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(running && !changed ? l.savedNextRecording : l.saved), behavior: SnackBarBehavior.floating));
    setState(() => page = 0);
  }

  /// The recorder page binds (ble) or unbinds (mic): the source follows, a running recorder of the other source stops;
  /// with the BLE recorder the ticker starts the link.
  Future<void> _setSource(String source) async {
    settings!.source = source;
    await settings!.save();
    if (running && status['source'] != source) await _stop();
    autoStarted = DateTime(2000);
    if (mounted) setState(() {});
  }

  /// Shows a tab. The page leaving stays alive and, to the browser, visible: it is told so its sound stops.
  void _show(int to) {
    if (to == page) return;
    if (page < webs.length) webs[page]?.runJavaScript("window.dispatchEvent(new Event('app-hidden'))");
    setState(() => page = to);
  }

  Future<void> _back() async {  // back key: history inside the page's WebView first, then to 时间线, then leave
    final w = page < webs.length ? webs[page] : null;
    if (w != null && await w.canGoBack()) return w.goBack();
    if (page != 0) return _show(0);
    SystemNavigator.pop();
  }

  Future<void> _start() async {
    final l = AppLocalizations.of(context);
    if (settings!.source == 'mic' && !await AudioRecorder().hasPermission()) return setState(() => note = l.noMicPermission);
    note = '';
    try {
      final s = settings!;
      await Recorder.start(server: s.server, token: s.token, device: s.device, archive: s.archive, source: s.source,
          slices: await Slices.fetch(s) ?? await Slices.cached());
    } on PlatformException catch (e) {
      setState(() => note = l.cannotStartRecording(platformText(context, e)));
    }
  }

  Future<void> _stop() async {
    setState(() => stopping = true);
    await Recorder.stop();
    while ((await Recorder.status())['running'] == true) {
      await Future.delayed(const Duration(milliseconds: 500));
    }
    setState(() => stopping = false);
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme, l = AppLocalizations.of(context);
    final titles = [l.tabTimeline, l.tabLive, l.tabPeople, l.tabMe];
    return PopScope(canPop: false, onPopInvokedWithResult: (didPop, _) => didPop ? null : _back(), child: Scaffold(
      appBar: AppBar(
        title: Text(titles[page]),
        actionsPadding: const EdgeInsets.only(right: 8),  // the chip alone (我的) off the edge
        actions: [
          if (settings != null)
            StatusChip(status: status, ble: ble, stopping: stopping, onPressed: _statusSheet,
                warn: status['error'] != null || status['note'] != null || note.isNotEmpty),
          if (page < webs.length && webs[page] != null)  // the WebView can get stuck
            IconButton(icon: const Icon(Icons.refresh), tooltip: l.refresh, visualDensity: VisualDensity.compact, onPressed: () => webs[page]!.reload()),
        ],
      ),
      body: settings == null
          ? const Center(child: CircularProgressIndicator())
          : Column(children: [const UpdateBanner(), Expanded(child: IndexedStack(index: page, children: [  // keeps the WebView alive across pages
              for (final w in webs) w == null ? _Empty(onSetup: _openSettings) : WebViewWidget(controller: w),
              _me(cs),
            ]))]),
      bottomNavigationBar: settings == null ? null : NavigationBar(
        selectedIndex: page,
        onDestinationSelected: _show,
        destinations: [
          NavigationDestination(icon: const Icon(Icons.view_agenda_outlined), selectedIcon: const Icon(Icons.view_agenda), label: titles[0]),
          NavigationDestination(icon: const Icon(Icons.subtitles_outlined), selectedIcon: const Icon(Icons.subtitles), label: titles[1]),
          NavigationDestination(icon: const Icon(Icons.people_outline), selectedIcon: const Icon(Icons.people), label: titles[2]),
          NavigationDestination(icon: const Icon(Icons.person_outline), selectedIcon: const Icon(Icons.person), label: titles[3]),
        ],
      ),
    ));
  }

  void _openSettings() => _open(AppLocalizations.of(context).settings, SettingsPage(settings: settings!, onSaved: _saved));
  void _openRecorder() => _open(AppLocalizations.of(context).recorder, RecorderPage(onSource: _setSource));

  /// 我的: this phone, and the native pages.
  Widget _me(ColorScheme cs) {
    final s = settings!, l = AppLocalizations.of(context);
    Widget entry(IconData icon, String title, VoidCallback onTap, [String? sub]) => ListTile(
        leading: Icon(icon), title: Text(title), subtitle: sub == null ? null : Text(sub), onTap: onTap,
        trailing: Icon(Icons.chevron_right_rounded, color: cs.onSurfaceVariant));
    return ListView(children: [
      ListTile(
        leading: CircleAvatar(backgroundColor: cs.primaryContainer, child: Icon(Icons.smartphone_rounded, color: cs.onPrimaryContainer)),
        title: Text(s.device),
        subtitle: Text(s.token.isEmpty ? l.notSignedIn : Uri.tryParse(s.server)?.host ?? s.server),
      ),
      const Divider(),
      entry(Icons.bluetooth_outlined, l.recorder, _openRecorder),
      entry(Icons.devices_outlined, l.deviceStatusAndMore, s.token.isEmpty ? _openSettings : () => _openWeb(l.deviceStatusAndMore, '/me'),
          l.deviceStatusAndMoreSub),
      entry(Icons.cloud_upload_outlined, l.upload, () => _open(l.upload, UploadPage(settings: s))),
      entry(Icons.settings_outlined, l.settings, _openSettings),
      entry(Icons.info_outline, l.about, () => showAboutDialog(context: context, applicationName: 'alldayrec', applicationVersion: version),
          version),
    ]);
  }

  /// The recording's status panel, from the top bar's chip: what goes on in words, the recorder's battery, what is
  /// left to catch up and its signal, pause or start/stop, and the recorder page.
  void _statusSheet() => showModalBottomSheet(
        context: context,
        showDragHandle: true,
        backgroundColor: Theme.of(context).colorScheme.surface,  // the tiles' cards (surfaceContainerLow) stand out on it
        builder: (sheet) => ListenableBuilder(
          listenable: rev,
          builder: (_, _) => SafeArea(
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                _panelHeader(sheet),
                ...ble ? _bleBody(sheet) : _micBody(sheet),
                const SizedBox(height: 4),
                ListTile(
                  contentPadding: const EdgeInsets.symmetric(horizontal: 4),
                  leading: const Icon(Icons.bluetooth_outlined),
                  title: Text(AppLocalizations.of(sheet).deviceDetails),
                  trailing: const Icon(Icons.chevron_right_rounded),
                  onTap: () {
                    Navigator.pop(sheet);
                    _openRecorder();
                  },
                ),
              ]),
            ),
          ),
        ),
      );

  /// What records, what it does in a few words (the chip's line), and what went wrong.
  Widget _panelHeader(BuildContext context) {
    final s = status, hw = (s['hw'] as Map?) ?? {}, l = AppLocalizations.of(context), t = Theme.of(context), cs = t.colorScheme;
    final c = chipSpec(context, s, ble: ble, stopping: stopping);
    final away = ble && !connected && (s['link'] as Map?)?['code'] == 'away';
    final warn = [
      if (s['error'] != null) l.recordingError(nativeText(context, s['error'])),
      if (s['note'] != null) nativeText(context, s['note']),  // also when stopped: iOS reports leftovers sent after a kill
      if (!ble && running && s['interruptedAt'] != null)
        l.interruptedRecovered(TimeOfDay.fromDateTime(DateTime.fromMillisecondsSinceEpoch(s['interruptedAt'])).format(context)),
      if (note.isNotEmpty) note,
    ];
    final small = t.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant);
    return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      CircleAvatar(radius: 22, backgroundColor: c.color.withValues(alpha: 0.14), child: Icon(c.icon, color: c.color)),
      const SizedBox(width: 12),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(ble ? (hw['name'] ?? hw['model'] ?? l.recorder) as String : l.phoneMic,
              style: t.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
          Text(c.line, style: t.textTheme.bodyMedium?.copyWith(color: cs.onSurfaceVariant)),
          if (away && pauseLeftMin(s) == 0) Text(l.awayHelp, style: small),  // it records to its card meanwhile (not while paused)
          if (ble && syncing) ...[  // LIVE goes on while the backlog is read; before LIVE only the file being recorded
            const SizedBox(height: 8),
            LinearProgressIndicator(value: connected ? (status['syncDone'] ?? 0) / status['syncTotal'] : null, borderRadius: BorderRadius.circular(2)),
          ],
          for (final w in warn) Text(w, style: small?.copyWith(color: cs.error), maxLines: 2, overflow: TextOverflow.ellipsis),
        ]),
      ),
    ]);
  }

  /// The BLE recorder as the source: nothing to start or stop; its battery, what is left to catch up, its signal (each
  /// when known: the link keeps the last read, greyed while away), and its pause.
  List<Widget> _bleBody(BuildContext context) {
    final s = status, hw = (s['hw'] as Map?) ?? {}, l = AppLocalizations.of(context), cs = Theme.of(context).colorScheme;
    final battery = hw['battery'] as int?, rssi = hw['rssi'] as int?, eta = hw['backlog_eta_s'] as num?;
    final left = ((s['syncTotal'] as num?) ?? 0) - ((s['syncDone'] as num?) ?? 0), pending = (s['pending'] as num?) ?? 0;
    final pausedMin = pauseLeftMin(s);
    final tiles = [
      if (battery != null)
        statTile(context,
            icon: hw['charge'] == 'charging' ? Icons.battery_charging_full_rounded : batteryIcon(battery),
            color: connected ? batteryColor(cs, battery) : cs.outline, label: l.battery, value: '$battery%',
            sub: connected ? chargeText(l, hw['charge']) : null),
      if (connected || syncing)
        statTile(context,
            icon: Icons.cloud_upload_rounded, color: syncing || pending > 0 ? cs.primary : Colors.green.shade600, label: l.backlog,
            value: syncing ? sizeText(left) : pending > 0 ? sizeText(((s['pendingBytes'] as num?) ?? 0) ~/ 1024) : l.allSynced,
            sub: [
              if (syncing && eta != null && eta > 0) l.aboutTime(durationText(l, eta)),
              if (pending > 0) l.piecesPending(pending),
            ].join(' · ')),
      if (connected && rssi != null)
        statTile(context,
            icon: [Icons.signal_cellular_alt_1_bar_rounded, Icons.signal_cellular_alt_2_bar_rounded, Icons.signal_cellular_alt_rounded][signalLevel(rssi)],
            color: signalLevel(rssi) == 0 ? Colors.orange.shade700 : cs.primary, label: l.signal,
            value: [l.signalWeak, l.signalFair, l.signalStrong][signalLevel(rssi)], sub: '$rssi dBm'),
    ];
    return [
      ..._tileRow(tiles),
      if (connected) ...[
        const SizedBox(height: 12),
        Align(alignment: AlignmentDirectional.centerStart, child: PauseButton(paused: pausedMin > 0, onDone: _pauseNote, labels: true)),
      ],
    ];
  }

  /// The panel's tiles, three to a row: each keeps its width when another is absent.
  static List<Widget> _tileRow(List<Widget> tiles) => [
        if (tiles.isNotEmpty) ...[
          const SizedBox(height: 16),
          IntrinsicHeight(child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            for (final (i, w) in tiles.indexed) ...[if (i > 0) const SizedBox(width: 8), w],
            for (var i = tiles.length; i < 3; i++) ...[const SizedBox(width: 8), const Spacer()],
          ])),
        ],
      ];

  void _pauseNote(PlatformException? e) {
    if (e != null && mounted) setState(() => note = platformText(context, e));
  }

  /// This phone's microphone as the source: what waits to be uploaded, and start/stop here.
  List<Widget> _micBody(BuildContext context) {
    final s = status, l = AppLocalizations.of(context), cs = Theme.of(context).colorScheme, n = (s['pending'] as num?) ?? 0;
    return [
      ..._tileRow([
        if (n > 0)
          statTile(context, icon: Icons.cloud_upload_rounded, color: cs.primary, label: l.toUpload,
              value: sizeText(((s['pendingBytes'] as num?) ?? 0) ~/ 1024),
              sub: s['uploading'] == true && !running ? l.uploadingLeftover(n) : l.piecesPending(n)),
      ]),
      const SizedBox(height: 12),
      FilledButton.icon(
        style: FilledButton.styleFrom(backgroundColor: running ? recordingRed : null, minimumSize: const Size.fromHeight(48)),
        onPressed: stopping ? null : (running ? _stop : _start),
        icon: Icon(running ? Icons.stop_rounded : Icons.mic_rounded),
        label: Text(running ? l.stop : l.startRecording),
      ),
    ];
  }
}

class _Empty extends StatelessWidget {
  final VoidCallback onSetup;
  const _Empty({required this.onSetup});
  @override
  Widget build(BuildContext context) => Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(Icons.key_outlined, size: 40, color: Theme.of(context).colorScheme.outline),
          const SizedBox(height: 12),
          Text(AppLocalizations.of(context).emptyNotSignedIn),
          const SizedBox(height: 12),
          OutlinedButton(onPressed: onSetup, child: Text(AppLocalizations.of(context).goToSettings)),
        ]),
      );
}
