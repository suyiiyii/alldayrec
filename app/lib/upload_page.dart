import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';

import 'l10n/app_localizations.dart';
import 'page_widgets.dart';
import 'recorder.dart';
import 'recorder_status.dart';
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
  bool serverFailed = false;  // serverNote says the server could not be asked
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
    if (!mounted) return;
    final l = AppLocalizations.of(context);  // not before the await: initState calls this
    setState(() => st = phone);
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
      if (mounted) setState(() { server = mine; serverNote = mine == null ? l.serverNoTask(dev) : ''; serverFailed = false; });
    } catch (e) {
      if (mounted) setState(() { serverNote = l.serverStatusFailed('$e'); serverFailed = true; });
    }
  }

  static String _at(Object? v) {
    if (v == null) return '—';
    final t = v is int ? DateTime.fromMillisecondsSinceEpoch(v) : DateTime.tryParse('$v');
    if (t == null) return '—';
    String two(int n) => n.toString().padLeft(2, '0');
    return '${two(t.month)}-${two(t.day)} ${two(t.hour)}:${two(t.minute)}';
  }

  static String _size(num b) => b < 1048576 ? '${(b / 1024).round()} KB' : '${(b / 1048576).toStringAsFixed(1)} MB';

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context), cs = t.colorScheme, srv = server, l = AppLocalizations.of(context);
    final pending = (st['pending'] ?? 0) as int, bytes = (st['pendingBytes'] ?? 0) as num;
    final running = st['running'] == true, uploading = st['uploading'] == true;
    final (String chip, Color chipColor, IconData chipIcon) = running
        ? (l.recordingUploading, Colors.red.shade600, Icons.fiber_manual_record_rounded)
        : uploading
            ? (l.uploading, cs.tertiary, Icons.cloud_upload_rounded)
            : pending > 0
                ? (l.waitingToUpload, Colors.orange.shade700, Icons.schedule_rounded)
                : (l.allUploaded, Colors.green.shade600, Icons.cloud_done_rounded);
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
                label: l.toUpload,
                value: pending == 0 ? '0' : l.pieces(pending),
                sub: pending == 0 ? l.noBacklog : l.pendingSub(_size(bytes), _at(st['oldest']))),
            const SizedBox(width: 12),
            statTile(context,
                icon: Icons.cloud_done_rounded, color: cs.primary, label: l.sentThisRun, value: l.pieces(st['sent'] ?? 0), sub: l.sinceStart),
          ]),
        ),
        if (st['note'] != null)
          Card.filled(
            margin: const EdgeInsets.only(top: 12),
            color: cs.errorContainer,
            child: ListTile(
                leading: Icon(Icons.error_outline_rounded, color: cs.onErrorContainer),
                title: Text(nativeText(context, st['note']), style: TextStyle(color: cs.onErrorContainer))),
          ),
        if (pending > 0 && !running && !uploading)
          Padding(
              padding: const EdgeInsets.only(top: 12),
              child: FilledButton.icon(onPressed: Recorder.upload, icon: const Icon(Icons.cloud_upload_rounded), label: Text(l.uploadNow))),
        sectionCard(
            context,
            l.serverProcessing,
            srv == null
                ? Row(children: [
                    Icon(serverFailed ? Icons.cloud_off_rounded : Icons.hourglass_empty_rounded,
                        color: serverFailed ? cs.error : cs.outline),
                    const SizedBox(width: 8),
                    Expanded(child: Text(serverNote.isEmpty ? l.loading : serverNote)),
                  ])
                : Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Row(children: [
                      Icon(caughtUp ? Icons.check_circle_rounded : Icons.autorenew_rounded,
                          color: caughtUp ? Colors.green.shade600 : cs.tertiary),
                      const SizedBox(width: 8),
                      Expanded(
                          child: Text(caughtUp ? l.caughtUp : l.serverBehind(durationText(l, left), '${srv['queued']}'),
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
                              ? l.finalsHeld(_at(hold), backlogKb == null || backlogKb <= 0 ? ''
                                  : backlogEta == null ? l.backlogLeft(_size(backlogKb * 1024))
                                  : l.backlogLeftEta(_size(backlogKb * 1024), durationText(l, backlogEta)))
                              : eta == null
                                  ? ''
                                  : l.etaDone(durationText(l, eta)),
                          style: t.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
                    ],
                    const Divider(height: 24),
                    ListTile(
                        contentPadding: EdgeInsets.zero,
                        dense: true,
                        leading: const Icon(Icons.download_done_rounded),
                        title: Text(l.received),
                        trailing: Text(_at(srv['received_to']))),
                    ListTile(
                        contentPadding: EdgeInsets.zero,
                        dense: true,
                        leading: const Icon(Icons.task_alt_rounded),
                        title: Text(l.finalized),
                        trailing: Text(_at(srv['done_to']))),
                  ])),
      ]),
    );
  }
}
