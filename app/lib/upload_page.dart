import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';

import 'page_widgets.dart';
import 'recorder.dart';
import 'settings.dart';

/// The phone's upload queue (from the native side's status) and how far the server has got with this device's audio.
class UploadPage extends StatefulWidget {
  final Settings settings;
  const UploadPage({super.key, required this.settings});
  @override
  State<UploadPage> createState() => _UploadPageState();
}

class _UploadPageState extends State<UploadPage> {
  Map st = {};  // the native side's status
  Map? server;  // this device's entry of /api/status
  String serverNote = '', dev = '';  // dev: the name its audio goes up as (the BLE recorder's own, or the setting)
  Timer? timer;

  @override
  void initState() {
    super.initState();
    timer = Timer.periodic(const Duration(seconds: 5), (_) => _fetch());
    _fetch();
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> _fetch() async {
    final s = widget.settings, phone = await Recorder.status();
    if (mounted) setState(() => st = phone);
    if (s.token.isEmpty) return;
    try {
      final c = HttpClient()..connectionTimeout = const Duration(seconds: 10);
      final req = await c.getUrl(Uri.parse('${s.server}/api/status'));
      req.headers.set('Authorization', 'Bearer ${s.token}');
      final res = await req.close().timeout(const Duration(seconds: 15));
      final body = jsonDecode(await res.transform(utf8.decoder).join()) as Map;
      c.close();
      // the BLE recorder's audio goes up under its own name (LR-P1-BDAE), not the device setting
      final ble = s.source == 'ble' ? (await Recorder.recorder())['name'] as String? : null;
      dev = ble?.replaceAll(RegExp(r'[^A-Za-z0-9._-]'), '_') ?? s.device;
      final mine = (body['devices'] as List? ?? []).cast<Map>().where((d) => d['device'] == dev).firstOrNull;
      if (mounted) setState(() { server = mine; serverNote = mine == null ? '服务端还没有 $dev 的处理任务' : ''; });
    } catch (e) {
      if (mounted) setState(() => serverNote = '取不到服务端状态：$e');
    }
  }

  static String _at(Object? v) {
    if (v == null) return '—';
    final t = v is int ? DateTime.fromMillisecondsSinceEpoch(v) : DateTime.tryParse('$v');
    if (t == null) return '—';
    String two(int n) => n.toString().padLeft(2, '0');
    return '${two(t.month)}-${two(t.day)} ${two(t.hour)}:${two(t.minute)}';
  }

  static String _span(num s) {
    final m = (s / 60).round();
    return m >= 60 ? '${m ~/ 60} 小时 ${m % 60} 分' : '$m 分钟';
  }

  static String _size(num b) => b < 1048576 ? '${(b / 1024).round()} KB' : '${(b / 1048576).toStringAsFixed(1)} MB';

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context), cs = t.colorScheme, srv = server;
    final pending = (st['pending'] ?? 0) as int, bytes = (st['pendingBytes'] ?? 0) as num;
    final running = st['running'] == true, uploading = st['uploading'] == true;
    final (String chip, Color chipColor, IconData chipIcon) = running
        ? ('录音中，边录边传', Colors.red.shade600, Icons.fiber_manual_record_rounded)
        : uploading
            ? ('正在上传', cs.tertiary, Icons.cloud_upload_rounded)
            : pending > 0
                ? ('等待上传', Colors.orange.shade700, Icons.schedule_rounded)
                : ('已全部上传', Colors.green.shade600, Icons.cloud_done_rounded);
    final left = (srv?['left_s'] ?? 0) as num, eta = srv?['eta_s'] as num?, hold = srv?['hold'];
    final backlogKb = (srv?['hw'] as Map?)?['backlog_kb'] as num?, backlogEta = (srv?['hw'] as Map?)?['backlog_eta_s'] as num?;
    final caughtUp = left < 30;
    final name = dev.isEmpty ? widget.settings.device : dev;
    return RefreshIndicator(
      onRefresh: _fetch,
      child: ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 32), children: [
        heroCard(context,
            icon: widget.settings.source == 'ble' ? Icons.mic_external_on_rounded : Icons.smartphone_rounded,
            title: name,
            chips: [stateChip(chip, chipColor, chipIcon)]),
        const SizedBox(height: 12),
        IntrinsicHeight(
          child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            statTile(context,
                icon: Icons.inventory_2_rounded,
                color: pending > 0 ? Colors.orange.shade700 : Colors.green.shade600,
                label: '待传',
                value: pending == 0 ? '0' : '$pending 块',
                sub: pending == 0 ? '没有积压' : '${_size(bytes)} · 最早录于 ${_at(st['oldest'])}'),
            const SizedBox(width: 12),
            statTile(context,
                icon: Icons.cloud_done_rounded, color: cs.primary, label: '本次已传', value: '${st['sent'] ?? 0} 块', sub: '这次开机以来'),
          ]),
        ),
        if (st['note'] != null)
          Card.filled(
            margin: const EdgeInsets.only(top: 12),
            color: cs.errorContainer,
            child: ListTile(
                leading: Icon(Icons.error_outline_rounded, color: cs.onErrorContainer),
                title: Text('${st['note']}', style: TextStyle(color: cs.onErrorContainer))),
          ),
        if (pending > 0 && !running && !uploading)
          Padding(
              padding: const EdgeInsets.only(top: 12),
              child: FilledButton.icon(onPressed: Recorder.upload, icon: const Icon(Icons.cloud_upload_rounded), label: const Text('立即上传'))),
        sectionCard(
            context,
            '服务端处理',
            srv == null
                ? Row(children: [
                    Icon(serverNote.startsWith('取不到') ? Icons.cloud_off_rounded : Icons.hourglass_empty_rounded,
                        color: serverNote.startsWith('取不到') ? cs.error : cs.outline),
                    const SizedBox(width: 8),
                    Expanded(child: Text(serverNote.isEmpty ? '加载中…' : serverNote)),
                  ])
                : Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Row(children: [
                      Icon(caughtUp ? Icons.check_circle_rounded : Icons.autorenew_rounded,
                          color: caughtUp ? Colors.green.shade600 : cs.tertiary),
                      const SizedBox(width: 8),
                      Expanded(
                          child: Text(caughtUp ? '已追上，录音都已转写' : '还有 ${_span(left)} 录音待处理 · 排队 ${srv['queued']} 块',
                              style: t.textTheme.bodyLarge)),
                    ]),
                    if (!caughtUp) ...[
                      const SizedBox(height: 10),
                      const LinearProgressIndicator(minHeight: 6),
                      const SizedBox(height: 6),
                      // held: finals wait for the backlog the phone reads in the background; the server's
                      // own estimate does not know when that is in
                      Text(
                          hold != null
                              ? '定稿停在 ${_at(hold)}，等手机在后台补传卡里更早的录音'
                                  '${backlogKb != null && backlogKb > 0 ? '（还有 ${_size(backlogKb * 1024)}${backlogEta != null ? '，约 ${_span(backlogEta)}' : ''}）' : ''}'
                                  '，补完再接着定稿；初稿和实时分析照常'
                              : eta == null
                                  ? ''
                                  : '预计约 ${_span(eta)} 后完成',
                          style: t.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
                    ],
                    const Divider(height: 24),
                    ListTile(
                        contentPadding: EdgeInsets.zero,
                        dense: true,
                        leading: const Icon(Icons.download_done_rounded),
                        title: const Text('已收到'),
                        trailing: Text(_at(srv['received_to']))),
                    ListTile(
                        contentPadding: EdgeInsets.zero,
                        dense: true,
                        leading: const Icon(Icons.task_alt_rounded),
                        title: const Text('已定稿'),
                        trailing: Text(_at(srv['done_to']))),
                  ])),
      ]),
    );
  }
}
