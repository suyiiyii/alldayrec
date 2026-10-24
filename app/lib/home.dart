// Application navigation and shared recording lifecycle; pages receive the live settings/status.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:record/record.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_android/webview_flutter_android.dart';
import 'package:webview_flutter_wkwebview/webview_flutter_wkwebview.dart';

import 'l10n/app_localizations.dart';
import 'pause_button.dart';
import 'recorder.dart';
import 'recorder_page.dart';
import 'recorder_status.dart';
import 'settings.dart';
import 'settings_page.dart';
import 'update.dart';
import 'upload_page.dart';

const recordingRed = Color(0xffc62828);

class Home extends StatefulWidget {
  const Home({super.key});
  @override
  State<Home> createState() => _HomeState();
}

class _HomeState extends State<Home> with WidgetsBindingObserver {
  Settings? settings;
  Map status = {};
  String note = '';
  bool stopping = false;
  int page = 0;  // the bottom bar's: 0 时间线, 1 实时, 2 人物 (web pages), 3 我的 (its entries open over it)
  final webs = <WebViewController?>[null, null, null];  // one per web page: each keeps its scroll while another is shown
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
  bool get ble => settings?.source == 'ble';

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    Recorder.foreground(state == AppLifecycleState.resumed);
    if (state == AppLifecycleState.resumed) _slices();
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
    final c = WebViewController.fromPlatformCreationParams(WebViewPlatform.instance is WebKitWebViewPlatform
        ? WebKitWebViewControllerCreationParams(allowsInlineMediaPlayback: true, mediaTypesRequiringUserAction: const {})
        : const PlatformWebViewControllerCreationParams());
    c.setBackgroundColor(Colors.transparent);  // the scaffold shows through until the page paints (no white flash in dark mode)
    final p = c.platform;
    if (p is AndroidWebViewController) p.setMediaPlaybackRequiresUserGesture(false);
    if (p is WebKitWebViewController) p.setInspectable(true);  // Safari's Develop menu on a Mac the phone trusts
    if (p is WebKitWebViewController) p.setAllowsBackForwardNavigationGestures(true);  // iOS: swipe from the left edge = back
    return c
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..clearCache()  // the page changes with server deploys
      // the web page hides its own navigation in the app: Android's WebView says so in its user agent, iOS's does not
      ..setNavigationDelegate(NavigationDelegate(
          onPageFinished: (_) => c.runJavaScript("document.documentElement.classList.add('in-app')").ignore(),
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
      setState(() => note = l.cannotStartRecording('${e.message}'));
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
          if (settings != null) Padding(padding: const EdgeInsets.only(right: 4), child: _chip(cs)),
          if (page < webs.length && webs[page] != null)
            IconButton(icon: const Icon(Icons.refresh), tooltip: l.refresh, onPressed: () => webs[page]!.reload()),
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

  /// The top bar's recording status on every tab, in a few words: red recording, grey not, amber stopping or
  /// something to look at; a tap opens the sheet with all of it.
  Widget _chip(ColorScheme cs) {
    final s = status, battery = (s['hw'] as Map?)?['battery'], amber = Colors.amber.shade700, grey = cs.outline;
    final warn = s['error'] != null || s['note'] != null || note.isNotEmpty, l = AppLocalizations.of(context);
    final (Color dot, String text) = ble
        ? syncing ? (connected ? recordingRed : amber, l.catchingUp)
            : !connected ? (amber, l.notConnected)
            : pauseLeftMin(s) > 0 ? (grey, l.paused)
            : (recordingRed, battery == null ? l.connected : l.connectedBattery(battery))  // hw keeps the last read after a disconnect
        : stopping ? (amber, l.stopping) : running ? (recordingRed, l.recording) : (grey, l.notRecording);
    return ActionChip(
      avatar: Center(child: Container(width: 8, height: 8, decoration: BoxDecoration(color: warn ? amber : dot, shape: BoxShape.circle))),
      label: Text(text),
      labelStyle: TextStyle(fontSize: 13, color: cs.onSurfaceVariant),
      visualDensity: VisualDensity.compact,
      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
      side: BorderSide(color: cs.outlineVariant),
      onPressed: () => _statusSheet(),
    );
  }

  /// Everything about the recording: what the chip sums up with start/stop or pause, and the recorder page (the live
  /// captions are a tab).
  void _statusSheet() => showModalBottomSheet(
        context: context,
        showDragHandle: true,
        builder: (sheet) => ListenableBuilder(
          listenable: rev,
          builder: (_, _) => SafeArea(
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 8, 16, 8),
                child: ble ? _bleBody(Theme.of(sheet).colorScheme) : _micBody(Theme.of(sheet).colorScheme),
              ),
              ListTile(
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
      );

  /// The BLE recorder as the source: nothing to start or stop, only where its audio is.
  Widget _bleBody(ColorScheme cs) {
    final s = status, hw = (s['hw'] as Map?) ?? {}, l = AppLocalizations.of(context);
    final link = '${s['link'] ?? l.starting}';
    final syncDone = (s['syncDone'] as num?) ?? 0, syncTotal = (s['syncTotal'] as num?) ?? 0, syncLive = (s['syncLive'] as num?) ?? 0;
    final (IconData icon, Color color) = syncing
        ? (Icons.sync_rounded, cs.tertiary)
        : connected
            ? (Icons.bluetooth_connected_rounded, Colors.green.shade600)
            : (Icons.bluetooth_searching_rounded, Colors.orange.shade700);
    final small = TextStyle(fontSize: 12, color: cs.onSurfaceVariant);
    final pausedMin = pauseLeftMin(s);
    final detail = [
      if (pausedMin > 0) l.pausedMinutesLeft(pausedMin),
      hwLine(l, hw),
      if ((s['pending'] ?? 0) > 0) l.piecesPending(s['pending']),
    ].where((x) => x.isNotEmpty).join(' · ');
    return Row(children: [
      Icon(icon, color: color),
      const SizedBox(width: 12),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          // LIVE goes on while the backlog is read; before that only the file being recorded is waited
          // for (old firmware: up to 16 MiB; newer firmware starts a new one at connect: a moment)
          Text(!syncing ? link : connected
                  ? l.liveUploadCatchingUp((syncDone / 1024).toStringAsFixed(1), (syncTotal / 1024).toStringAsFixed(1))
                  : l.fetchingCurrentFile((syncLive / 1024).toStringAsFixed(1)),
              style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500), maxLines: 2, overflow: TextOverflow.ellipsis),
          if (syncing) ...[
            const SizedBox(height: 6),
            LinearProgressIndicator(value: connected ? syncDone / syncTotal : null, borderRadius: BorderRadius.circular(2)),
          ],
          if (detail.isNotEmpty) Text(detail, style: small),
          if (s['note'] != null) Text('${s['note']}', style: small.copyWith(color: cs.error), maxLines: 2, overflow: TextOverflow.ellipsis),
          if (note.isNotEmpty) Text(note, style: small.copyWith(color: cs.error)),
        ]),
      ),
      if (connected) PauseButton(paused: pausedMin > 0, onDone: _pauseNote),
    ]);
  }

  void _pauseNote(String? error) {
    if (error != null) setState(() => note = error);
  }

  /// This phone's microphone as the source: started and stopped here.
  Widget _micBody(ColorScheme cs) {
    final s = status, l = AppLocalizations.of(context);
    final small = TextStyle(fontSize: 12, color: cs.onSurfaceVariant);
    String headline, detail = '';
    if (stopping) {
      headline = l.stopping;
      detail = l.waitingLastPieces(s['pending'] ?? 0);
    } else if (running) {
      headline = l.recordingPieceSeconds('${s['slice']}');
      if (s['link'] != null) headline += ' · ${s['link']}';
      detail = l.sentPending('${s['sent']}', '${s['pending']}');
      final hw = hwLine(l, s['hw'] as Map?);
      if (hw.isNotEmpty) detail += ' · $hw';
    } else {
      headline = note.isEmpty ? l.notRecording : note;
      final n = s['pending'] ?? 0;
      if (n > 0) detail = s['uploading'] == true ? l.uploadingLeftover(n) : l.piecesPending(n);
    }
    final warn = [
      if (s['error'] != null) l.recordingError('${s['error']}'),
      if (s['note'] != null) '${s['note']}',  // also when stopped: iOS reports leftovers sent after a kill
      if (running && s['interruptedAt'] != null)
        l.interruptedRecovered(TimeOfDay.fromDateTime(DateTime.fromMillisecondsSinceEpoch(s['interruptedAt'])).format(context)),
    ];
    return Row(children: [
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          Text(headline, style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w500)),
          if (detail.isNotEmpty) Text(detail, style: small),
          for (final w in warn) Text(w, style: small.copyWith(color: cs.error), maxLines: 2, overflow: TextOverflow.ellipsis),
        ]),
      ),
      const SizedBox(width: 12),
      FilledButton.icon(
        style: FilledButton.styleFrom(
            backgroundColor: running ? recordingRed : null, minimumSize: const Size(0, 48), padding: const EdgeInsets.symmetric(horizontal: 20)),
        onPressed: stopping ? null : (running ? _stop : _start),
        icon: Icon(running ? Icons.stop_rounded : Icons.mic_rounded),
        label: Text(running ? l.stop : l.startRecording),
      ),
    ]);
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
