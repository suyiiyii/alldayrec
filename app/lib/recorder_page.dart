import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'add_recorder_page.dart';
import 'firmware.dart';
import 'l10n/app_localizations.dart';
import 'page_widgets.dart';
import 'pause_button.dart';
import 'recorder.dart';
import 'recorder_status.dart';
import 'settings.dart';

/// The BLE recorder: which one is bound and, as last read over the link, its battery, card, offline sync and firmware;
/// the card as a USB disk on a computer, the firmware page (firmware.dart: versions, notes, the update), unbinding.
class RecorderPage extends StatefulWidget {
  final Future<void> Function(String source) onSource;  // bound: 'ble'; unbound: back to 'mic'
  const RecorderPage({super.key, required this.onSource});
  @override
  State<RecorderPage> createState() => _RecorderPageState();
}

class _RecorderPageState extends State<RecorderPage> {
  Map r = {}, st = {};
  Timer? timer;
  bool unbinding = false;

  @override
  void initState() {
    super.initState();
    _load();
    // every 2 s; while listening every second, for its line a second
    timer = Timer.periodic(const Duration(seconds: 1), (t) => st['listen'] != null || t.tick.isEven ? _load() : null);
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    final a = await Recorder.recorder(), b = await Recorder.status();
    if (mounted) setState(() { r = a; st = b; });
  }

  Future<void> _call(Future<void> Function() f) async {
    try {
      await f();
    } on PlatformException catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.message ?? '$e'), behavior: SnackBarBehavior.floating));
    }
    _load();
  }

  Future<void> _add() async {
    final bound = await Navigator.push<bool>(context, MaterialPageRoute(builder: (_) => const AddRecorderPage()));
    if (bound == true) await widget.onSource('ble');
    _load();
  }

  Future<void> _unbind(String name, bool linked) async {
    final l = AppLocalizations.of(context);
    final ok = await showDialog<bool>(context: context, builder: (c) => AlertDialog(
      title: Text(l.unbindTitle(name)),
      content: Text((linked ? l.unbindLinkedBody : l.unbindAwayBody) + (Platform.isIOS ? '\n\n${l.unbindIosForget(name)}' : '')),
      actions: [
        TextButton(onPressed: () => Navigator.pop(c, false), child: Text(l.cancel)),
        TextButton(onPressed: () => Navigator.pop(c, true), child: Text(l.unbind)),
      ],
    ));
    if (ok != true) return;
    // until it is all done, no adding: the page shows 添加录音设备 as soon as the binding is off the prefs, and a bind
    // started then lost its new bond to this unbind's late removeBond (10-05)
    setState(() => unbinding = true);
    try {
      await _call(() async {
        await Recorder.unbind();
        await widget.onSource('mic');
        await releaseHold(name);
      });
    } finally {
      if (mounted) setState(() => unbinding = false);
    }
  }

  void _pauseDone(String? error) {
    if (error != null && mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error), behavior: SnackBarBehavior.floating));
    _load();
  }

  static String _size(num kb) => kb >= 1048576 ? '${(kb / 1048576).toStringAsFixed(2)} GB' : kb >= 1024 ? '${(kb / 1024).toStringAsFixed(1)} MB' : '$kb KB';

  static IconData _batteryIcon(int? p) {
    if (p == null) return Icons.battery_unknown_rounded;
    const icons = [Icons.battery_0_bar_rounded, Icons.battery_1_bar_rounded, Icons.battery_2_bar_rounded, Icons.battery_3_bar_rounded,
      Icons.battery_4_bar_rounded, Icons.battery_5_bar_rounded, Icons.battery_6_bar_rounded, Icons.battery_full_rounded];
    return icons[(p * 7 / 100).round().clamp(0, 7)];
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context), cs = t.colorScheme, l = AppLocalizations.of(context);
    final name = r['name'] as String?, model = r['model'] as String?;
    final hw = (st['hw'] as Map?) ?? {};
    final fw = (hw['fw'] ?? r['fw']) as String?;
    final recording = st['running'] == true, linked = recording && hw.isNotEmpty;
    final live = st['connected'] == true;  // the link is up (its text is for showing, not for comparing)
    final store = hw['store'] as int?, free = (hw['free_kb'] as num?) ?? 0, total = (hw['total_kb'] as num?) ?? 0;
    final battery = hw['battery'] as int?, charge = chargeText(l, hw['charge']);
    final syncDone = (st['syncDone'] as num?) ?? 0, syncTotal = (st['syncTotal'] as num?) ?? 0, syncLive = (st['syncLive'] as num?) ?? 0;
    final syncing = recording && syncTotal > 0;
    final lent = store == -16;
    final dfu = (st['dfu'] ?? r['dfu']) as String?;
    final listen = st['listen'] as List?;  // listening on this phone: its lines

    // connection chip: what the link is doing, in one colour
    final (String chip, Color chipColor, IconData chipIcon) = !recording
        ? (l.notConnected, cs.outline, Icons.bluetooth_disabled_rounded)
        : syncing
            ? (live ? l.connectedCatchingUp : l.syncing, cs.tertiary, Icons.sync_rounded)
            : live
                ? (l.connected, Colors.green.shade600, Icons.bluetooth_connected_rounded)
                : (l.connecting, cs.secondary, Icons.bluetooth_searching_rounded);

    Widget section(String title, Widget child) => sectionCard(context, title, child);
    Widget stat({required IconData icon, required Color color, required String label, required String value, String? sub, double? fraction}) =>
        statTile(context, icon: icon, color: color, label: label, value: value, sub: sub, fraction: fraction ?? 0);

    final batteryColor = battery == null ? cs.outline : battery <= 20 ? cs.error : battery <= 50 ? Colors.orange.shade700 : Colors.green.shade600;
    final card = cardState(l, hw);
    final cardColor = store == null || store == -16 ? cs.outline : store != 0 ? cs.error : free < total * 0.1 ? Colors.orange.shade700 : cs.primary;
    final (String cardValue, String? cardSub) =
        !linked ? ('—', null) : card != null ? (card.tile, card.sub) : (_size(free), l.cardFreeOf(_size(total)));

    final small = t.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant);
    final add = FilledButton.icon(
        onPressed: unbinding ? null : _add, icon: const Icon(Icons.add_rounded), label: Text(name == null ? l.addRecorder : l.addAgain),
        style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)));
    if (name == null) {
      return ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 32), children: [
        heroCard(context, icon: Icons.mic_external_on_rounded, title: l.notBound, chips: [stateChip(l.notBound, cs.outline, Icons.link_off_rounded)]),
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 12),
          child: Text(l.recorderUnboundHelp, style: small),
        ),
        add,
      ]);
    }
    final pausedMin = pauseLeftMin(st);

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 32), children: [
        // the device
        heroCard(context, icon: Icons.mic_external_on_rounded, title: name, chips: [
          r['kicked'] == true ? stateChip(l.kickedChip, cs.error, Icons.link_off_rounded) : stateChip(chip, chipColor, chipIcon),
          if (model != null) Chip(label: Text(model), visualDensity: VisualDensity.compact),
        ]),
        if (r['kicked'] == true) ...[
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Text('${l.kickedHelp}${Platform.isIOS ? l.kickedIosForget(name) : ''}', style: small),
          ),
          add,
        ],
        Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Text(l.recorderHelp, style: small),
        ),
        // pause
        section(l.pauseRecording, Row(children: [
          Expanded(child: Text(!linked ? l.pauseNeedsLink : pausedMin > 0 ? l.pausedResumesIn(pausedMin) : l.recordingNow)),
          if (linked) PauseButton(paused: pausedMin > 0, onDone: _pauseDone, labels: true),
        ])),
        // listening on this phone (Listen.swift, Listen.kt)
        section(l.liveListening, Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            secondary: Icon(Icons.headphones_rounded, color: listen != null ? cs.primary : cs.onSurfaceVariant),
            title: Text(l.listenOnPhone),
            subtitle: Text(!linked && listen == null
                ? l.listenNeedsLink
                : l.listenHelp),
            value: listen != null,
            onChanged: linked || listen != null ? (v) => _call(() => Recorder.listen(v)) : null,
          ),
          if (listen != null) ListenLines(lines: listen),
        ])),
        // battery and card
        const SizedBox(height: 12),
        IntrinsicHeight(
          child: Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            stat(icon: linked && hw['charge'] == 'charging' ? Icons.battery_charging_full_rounded : _batteryIcon(linked ? battery : null),
                color: batteryColor, label: l.battery, value: linked && battery != null ? '$battery%' : '—', sub: linked ? charge : null,
                fraction: linked && battery != null ? battery / 100 : 0),
            const SizedBox(width: 12),
            stat(icon: Icons.sd_card_rounded, color: cardColor, label: l.card, value: cardValue, sub: cardSub,
                fraction: linked && total > 0 ? (total - free) / total : 0),
          ]),
        ),
        // offline sync
        section(l.offlineSync, Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (syncing) ...[
            Row(children: [
              Icon(Icons.downloading_rounded, color: cs.tertiary),
              const SizedBox(width: 8),
              Expanded(child: Text(l.fetchingCard, style: t.textTheme.bodyLarge)),
              Text('${_size(syncDone)} / ${_size(syncTotal)}', style: t.textTheme.bodyMedium?.copyWith(color: cs.onSurfaceVariant)),
            ]),
            const SizedBox(height: 10),
            LinearProgressIndicator(value: syncTotal > 0 ? syncDone / syncTotal : null, minHeight: 8, borderRadius: BorderRadius.circular(4)),
            const SizedBox(height: 6),
            Text(live ? l.syncLiveHelp : l.syncFirstHelp(_size(syncLive)),
                style: t.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
          ] else
            Row(children: [
              Icon(st['lastSync'] != null ? Icons.check_circle_rounded : Icons.cloud_sync_rounded,
                  color: st['lastSync'] != null ? Colors.green.shade600 : cs.outline),
              const SizedBox(width: 8),
              Expanded(child: Text((st['lastSync'] as String?) ?? l.syncIdleHelp)),
            ]),
        ])),
        // USB disk
        section(l.usbDiskMode, Column(children: [
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            secondary: Icon(Icons.usb_rounded, color: lent ? cs.primary : cs.onSurfaceVariant),
            title: Text(l.cardAsUsbDisk),
            subtitle: Text(st['usbNote'] as String? ?? l.usbDiskHelp),
            value: lent,
            onChanged: linked ? (v) => _call(() => Recorder.usbDisk(v)) : null,
          ),
          if (lent)
            Row(children: [
              Icon(Icons.warning_amber_rounded, size: 18, color: Colors.orange.shade700),
              const SizedBox(width: 6),
              Expanded(child: Text(l.ejectFirst)),
            ]),
        ])),
        // firmware and binding
        section(l.deviceManagement, Column(children: [
          FirmwareTile(name: name, model: model, fw: fw, dfu: dfu, dfuBusy: st['dfuBusy'] == true),
          const Divider(height: 20),
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: Icon(Icons.link_off_rounded, color: syncing ? cs.outline : cs.error),
            title: Text(l.unbind, style: TextStyle(color: syncing ? cs.outline : cs.error)),
            subtitle: Text(syncing ? l.unbindAfterSync : l.unbindSub),
            onTap: syncing ? null : () => _unbind(name, linked),
          ),
        ])),
      ]),
    );
  }
}

/// Listening's line a second, newest first: the second's level and hum, ms of exact zeros in runs of 10 ms or more (the
/// mic sent nothing), clipped samples, ms lost on the link, and the delay from the recorder's mic to this phone's output.
class ListenLines extends StatelessWidget {
  final List lines;
  const ListenLines({super.key, required this.lines});

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context), cs = t.colorScheme, l = AppLocalizations.of(context);
    final style = t.textTheme.bodySmall?.copyWith(fontFeatures: const [FontFeature.tabularFigures()]);
    final dim = style?.copyWith(color: cs.onSurfaceVariant);
    if (lines.isEmpty) return Text(l.listenWaiting, style: dim);
    String hms(num ms) {
      final d = DateTime.fromMillisecondsSinceEpoch(ms.toInt());
      return [d.hour, d.minute, d.second].map((x) => '$x'.padLeft(2, '0')).join(':');
    }
    final rows = [
      [l.listenTime, l.listenLevel, l.listenHum, l.listenZeros, l.listenClipped, l.listenLost, l.listenDelay],
      for (final l in lines.reversed.take(8))
        [hms(l['t']), (l['rms'] as num).toStringAsFixed(1), (l['hum'] as num).toStringAsFixed(1), '${l['zero']}', '${l['full']}', '${l['lost']}', '${l['delay']}'],
    ];
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Table(defaultColumnWidth: const IntrinsicColumnWidth(), children: [
        for (final (i, r) in rows.indexed)
          TableRow(children: [
            for (final c in r)
              Padding(padding: const EdgeInsets.fromLTRB(0, 2, 12, 2), child: Text(c, style: i == 0 ? dim : style, textAlign: TextAlign.right)),
          ]),
      ]),
      const SizedBox(height: 6),
      Text(l.listenLegend('${lines.last['queued']}'), style: dim),
    ]);
  }
}
