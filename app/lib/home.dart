// Application navigation and shared recording lifecycle; pages receive the live settings/status.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:record/record.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_android/webview_flutter_android.dart';
import 'package:webview_flutter_wkwebview/webview_flutter_wkwebview.dart';

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
  int page = 0;  // the bottom bar's: 0 今天, 1 对话, 2 人物 (web pages), 3 我的 (its entries open over it)
  final webs = <WebViewController?>[null, null, null];  // one per web page: each keeps its scroll while another is shown
  Timer? ticker;
  int ticks = 0;
  String version = '';

  static const titles = ['今天', '对话', '人物', '我的'];
  static const paths = ['/', '/conversations', '/speakers'];

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

  /// The tab a web path belongs to: 今天 (and the pages under it: 日历, 进度, 设备...), 对话 (the list and one
  /// conversation), 人物 (the list and one person), 我的 (the web /me). null: none in particular (stay where it is)
  static int? _tabOf(String path) => path == '/' ? 0
      : path == '/conversations' || path.startsWith('/c/') ? 1
      : path == '/speakers' || path.startsWith('/p/') ? 2
      : path == '/me' ? 3 : null;

  /// A tab's WebView went to another tab's page (今天's 待办 opens a conversation): that tab shows it, and this one
  /// steps back to where it was. A page is a tab, not a place inside one.
  void _follow(int tab, String url) {
    final to = _tabOf(Uri.parse(url).path);
    if (to == null || to == tab) return;
    if (to < webs.length) (webs[to] ??= _web(tab: to)).loadRequest(_url(Uri.parse(url).path));
    setState(() => page = to);
    final c = webs[tab]!;
    c.canGoBack().then((b) => b ? c.goBack() : c.loadRequest(_url(paths[tab])));
  }

  WebViewController _web({int? tab}) {
    // the web pages play audio from script (the conversation player's next window, a seek): no tap needed for that
    final c = WebViewController.fromPlatformCreationParams(WebViewPlatform.instance is WebKitWebViewPlatform
        ? WebKitWebViewControllerCreationParams(allowsInlineMediaPlayback: true, mediaTypesRequiringUserAction: const {})
        : const PlatformWebViewControllerCreationParams());
    final p = c.platform;
    if (p is AndroidWebViewController) p.setMediaPlaybackRequiresUserGesture(false);
    if (p is WebKitWebViewController) p.setInspectable(true);  // Safari's Develop menu on a Mac the phone trusts
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

  /// A web page (实时字幕: /live, 设备状态与更多: /me) in a WebView of its own, there while open;
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
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(running && !changed ? '已保存，下次开始录音时生效' : '已保存'), behavior: SnackBarBehavior.floating));
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

  Future<void> _back() async {  // back key: history inside the page's WebView first, then to 今天, then leave
    final w = page < webs.length ? webs[page] : null;
    if (w != null && await w.canGoBack()) return w.goBack();
    if (page != 0) return setState(() => page = 0);
    SystemNavigator.pop();
  }

  Future<void> _start() async {
    if (settings!.source == 'mic' && !await AudioRecorder().hasPermission()) return setState(() => note = '没有麦克风权限');
    note = '';
    try {
      final s = settings!;
      await Recorder.start(server: s.server, token: s.token, device: s.device, archive: s.archive, source: s.source,
          slices: await Slices.fetch(s) ?? await Slices.cached());
    } on PlatformException catch (e) {
      setState(() => note = '无法开始录音 ${e.message}');
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
    final cs = Theme.of(context).colorScheme;
    return PopScope(canPop: false, onPopInvokedWithResult: (didPop, _) => didPop ? null : _back(), child: Scaffold(
      appBar: AppBar(
        title: Text(titles[page]),
        actions: [
          if (running) const Padding(padding: EdgeInsets.only(right: 4), child: _Live()),
          if (page < webs.length && webs[page] != null)
            IconButton(icon: const Icon(Icons.refresh), tooltip: '刷新', onPressed: () => webs[page]!.reload()),
        ],
      ),
      body: settings == null
          ? const Center(child: CircularProgressIndicator())
          : Column(children: [const UpdateBanner(), Expanded(child: IndexedStack(index: page, children: [  // keeps the WebView alive across pages
              for (final w in webs) w == null ? _Empty(onSetup: _openSettings) : WebViewWidget(controller: w),
              _me(cs),
            ]))]),
      bottomNavigationBar: settings == null ? null : Column(mainAxisSize: MainAxisSize.min, children: [
        if (page == 0) _controlBar(cs),
        NavigationBar(
          selectedIndex: page,
          onDestinationSelected: (i) => setState(() => page = i),
          destinations: const [
            NavigationDestination(icon: Icon(Icons.home_outlined), selectedIcon: Icon(Icons.home), label: '今天'),
            NavigationDestination(icon: Icon(Icons.forum_outlined), selectedIcon: Icon(Icons.forum), label: '对话'),
            NavigationDestination(icon: Icon(Icons.people_outline), selectedIcon: Icon(Icons.people), label: '人物'),
            NavigationDestination(icon: Icon(Icons.person_outline), selectedIcon: Icon(Icons.person), label: '我的'),
          ],
        ),
      ]),
    ));
  }

  void _openSettings() => _open('设置', SettingsPage(settings: settings!, onSaved: _saved));
  void _openRecorder() => _open('录音设备', RecorderPage(onSource: _setSource));

  /// 我的: this phone, and the native pages.
  Widget _me(ColorScheme cs) {
    final s = settings!;
    Widget entry(IconData icon, String title, VoidCallback onTap, [String? sub]) => ListTile(
        leading: Icon(icon), title: Text(title), subtitle: sub == null ? null : Text(sub), onTap: onTap,
        trailing: Icon(Icons.chevron_right_rounded, color: cs.onSurfaceVariant));
    return ListView(children: [
      ListTile(
        leading: CircleAvatar(backgroundColor: cs.primaryContainer, child: Icon(Icons.smartphone_rounded, color: cs.onPrimaryContainer)),
        title: Text(s.device),
        subtitle: Text(s.token.isEmpty ? '还没登录' : Uri.tryParse(s.server)?.host ?? s.server),
      ),
      const Divider(),
      entry(Icons.bluetooth_outlined, '录音设备', _openRecorder),
      entry(Icons.subtitles_outlined, '实时字幕', s.token.isEmpty ? _openSettings : () => _openWeb('实时字幕', '/live')),
      entry(Icons.devices_outlined, '设备状态与更多', s.token.isEmpty ? _openSettings : () => _openWeb('设备状态与更多', '/me'),
          '电量、存储卡、处理进度、接入 AI'),
      entry(Icons.cloud_upload_outlined, '上传', () => _open('上传', UploadPage(settings: s))),
      entry(Icons.settings_outlined, '设置', _openSettings),
      entry(Icons.info_outline, '关于', () => showAboutDialog(context: context, applicationName: 'alldayrec', applicationVersion: version),
          version),
    ]);
  }

  /// The BLE recorder as the source: nothing to start or stop, only where its audio is.
  Widget _bleBar(ColorScheme cs) {
    final s = status, hw = (s['hw'] as Map?) ?? {};
    final link = '${s['link'] ?? '正在启动'}';
    final syncDone = (s['syncDone'] as num?) ?? 0, syncTotal = (s['syncTotal'] as num?) ?? 0, syncLive = (s['syncLive'] as num?) ?? 0;
    final connected = link.startsWith('已连接'), syncing = syncTotal > 0;
    final (IconData icon, Color color) = syncing
        ? (Icons.sync_rounded, cs.tertiary)
        : connected
            ? (Icons.bluetooth_connected_rounded, Colors.green.shade600)
            : (Icons.bluetooth_searching_rounded, Colors.orange.shade700);
    final small = TextStyle(fontSize: 12, color: cs.onSurfaceVariant);
    final pausedMin = pauseLeftMin(s);
    final detail = [
      if (pausedMin > 0) '已暂停，还剩 $pausedMin 分钟',
      hwLine(hw),
      if ((s['pending'] ?? 0) > 0) '待传 ${s['pending']} 块',
    ].where((x) => x.isNotEmpty).join(' · ');
    return Material(
      color: cs.surfaceContainer,
      child: InkWell(
        onTap: _openRecorder,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 12, 16, 12),
          child: Row(children: [
            Icon(icon, color: color),
            const SizedBox(width: 12),
            Expanded(
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
                // LIVE goes on while the backlog is read; before that only the file being recorded is waited
                // for (old firmware: up to 16 MiB; newer firmware starts a new one at connect: a moment)
                Text(!syncing ? link : connected
                        ? '实时上传 · 后台补传 ${(syncDone / 1024).toStringAsFixed(1)} / ${(syncTotal / 1024).toStringAsFixed(1)} MB'
                        : '接上实时前先取正在录的这段 · 还差 ${(syncLive / 1024).toStringAsFixed(1)} MB',
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
            Icon(Icons.chevron_right_rounded, color: cs.onSurfaceVariant),
          ]),
        ),
      ),
    );
  }

  void _pauseNote(String? error) {
    if (error != null) setState(() => note = error);
  }

  Widget _controlBar(ColorScheme cs) {
    if (ble) return _bleBar(cs);
    final s = status;
    final small = TextStyle(fontSize: 12, color: cs.onSurfaceVariant);
    String headline, detail = '';
    if (stopping) {
      headline = '停止中';
      detail = '等最后 ${s['pending'] ?? 0} 块传完';
    } else if (running) {
      headline = '录音中 · ${s['slice']} 秒一块';
      if (s['link'] != null) headline += ' · ${s['link']}';
      detail = '已传 ${s['sent']} · 待传 ${s['pending']}';
      final hw = hwLine(s['hw'] as Map?);
      if (hw.isNotEmpty) detail += ' · $hw';
    } else {
      headline = note.isEmpty ? '未在录音' : note;
      final n = s['pending'] ?? 0;
      if (n > 0) detail = s['uploading'] == true ? '正在补传 $n 块' : '待传 $n 块';
    }
    final warn = [
      if (s['error'] != null) '录音错误 ${s['error']}',
      if (s['note'] != null) '${s['note']}',  // also when stopped: iOS reports leftovers sent after a kill
      if (running && s['interruptedAt'] != null)
        '${TimeOfDay.fromDateTime(DateTime.fromMillisecondsSinceEpoch(s['interruptedAt'])).format(context)} 被系统中断过，已恢复',
    ];
    return Material(
      color: cs.surfaceContainer,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 10, 16, 10),
        child: Row(children: [
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
            label: Text(running ? '停止' : '开始录音'),
          ),
        ]),
      ),
    );
  }
}

class _Live extends StatelessWidget {
  const _Live();
  @override
  Widget build(BuildContext context) => Row(mainAxisSize: MainAxisSize.min, children: [
        Container(width: 8, height: 8, decoration: const BoxDecoration(color: recordingRed, shape: BoxShape.circle)),
        const SizedBox(width: 6),
        const Text('REC', style: TextStyle(color: recordingRed, fontWeight: FontWeight.w600, fontSize: 13)),
      ]);
}

class _Empty extends StatelessWidget {
  final VoidCallback onSetup;
  const _Empty({required this.onSetup});
  @override
  Widget build(BuildContext context) => Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(Icons.key_outlined, size: 40, color: Theme.of(context).colorScheme.outline),
          const SizedBox(height: 12),
          const Text('扫码登录或填好 Token 后显示'),
          const SizedBox(height: 12),
          OutlinedButton(onPressed: onSetup, child: const Text('去设置')),
        ]),
      );
}
