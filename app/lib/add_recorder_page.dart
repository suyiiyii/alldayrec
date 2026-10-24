import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';

import 'l10n/app_localizations.dart';
import 'page_widgets.dart';
import 'recorder.dart';
import 'recorder_status.dart';
import 'settings.dart';

/// The add-recorder page's rows after a scan (Recorder.nearby: the ones seen in the last 5 s, strongest first). A row
/// keeps its place while the page is open, so the one under a finger is the one it pressed: one already listed is
/// updated where it is (found by address, or by its full name: one name, one row), a new one goes to the end (the first
/// scan's come strongest first) and one missing from 3 scans in a row stays where it was, 'gone' (Android restarts its
/// scan every 30 s, which empties one). 'nearest' marks the strongest of several not gone, and moves to another only
/// 6 dB stronger: RSSI swings a few dB from scan to scan.
List<Map> mergeNearby(List<Map> rows, List scan) {
  final out = [for (final r in rows) {...r, 'missed': (r['missed'] as int) + 1}];
  for (final Map d in scan) {
    var i = out.indexWhere((r) => r['address'] == d['address']);
    if (i < 0 && RegExp(r'^LR-[A-Z0-9]+-[0-9A-F]{4}$').hasMatch(d['name'])) i = out.indexWhere((r) => r['name'] == d['name']);
    if (i < 0) {
      out.add({...d, 'missed': 0});
    } else if (out[i]['missed'] != 0) {  // else this scan has it twice (one name, two addresses): the first, stronger, stays
      out[i] = {...d, 'missed': 0, 'nearest': out[i]['nearest']};
    }
  }
  for (final r in out) {
    r['gone'] = r['missed'] >= 3;
  }
  final live = out.where((r) => !r['gone']).toList();
  Map? best;
  for (final r in live) {
    if (best == null || r['rssi'] > best['rssi']) best = r;
  }
  final held = live.where((r) => r['nearest'] == true).firstOrNull;
  final nearest = held != null && best!['rssi'] - held['rssi'] < 6 ? held : best;
  for (final r in out) {
    r['nearest'] = live.length > 1 && identical(r, nearest);
  }
  return out;
}

/// Adding a recorder: the permissions the link needs, then the recorders around in the order found (mergeNearby: they
/// do not move while the page is open, the nearest is marked) with the state each advertises. An unbound one binds with
/// one tap; one bound to another phone only after a confirmation (it then forgets that phone); one connected to another
/// phone cannot be reached until that phone lets go; one no longer seen is greyed out. Pops true once one is chosen.
class AddRecorderPage extends StatefulWidget {
  const AddRecorderPage({super.key});
  @override
  State<AddRecorderPage> createState() => _AddRecorderPageState();
}

class _AddRecorderPageState extends State<AddRecorderPage> {
  Map perms = {};
  List<Map> found = [];
  Timer? timer;
  bool scanning = false;

  @override
  void initState() {
    super.initState();
    _tick();
    timer = Timer.periodic(const Duration(seconds: 1), (_) => _tick());
  }

  @override
  void dispose() {
    timer?.cancel();
    Recorder.scan(false);
    super.dispose();
  }

  int ticks = 0;

  Future<void> _tick() async {
    final p = await Recorder.permissions();
    // (re)started once permitted and then every 30 s: a scan started with Bluetooth off finds nothing, and Android
    // allows an app 5 scan starts in 30 s
    if (p['bluetooth'] == true && p['bluetoothOn'] == true && (!scanning || ++ticks % 30 == 0)) {
      scanning = true;
      await Recorder.scan(true);
    }
    final f = await Recorder.nearby();
    if (mounted) setState(() { perms = p; found = mergeNearby(found, f); });
  }

  Future<void> _bind(Map d, bool force) async {
    if (force) {
      final l = AppLocalizations.of(context);
      final ok = await showDialog<bool>(context: context, builder: (c) => AlertDialog(
        title: Text(l.bindTitle(d['name'])),
        content: Text(l.bindForceBody),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: Text(l.cancel)),
          TextButton(onPressed: () => Navigator.pop(c, true), child: Text(l.forceBind)),
        ],
      ));
      if (ok != true) return;
    }
    final old = (await Recorder.recorder())['name'] as String?;
    await Recorder.withoutLink(() => Recorder.bind(d['name'], d['address'], force));
    if (old != null && old != d['name']) await releaseHold(old);  // the one bound before: no longer this phone's
    if (mounted) Navigator.pop(context, true);
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context), cs = t.colorScheme, l = AppLocalizations.of(context);
    final small = t.textTheme.bodySmall?.copyWith(color: cs.onSurfaceVariant);
    Widget perm(String key, String title, String why) => ListTile(
          contentPadding: EdgeInsets.zero,
          leading: Icon(perms[key] == true ? Icons.check_circle_rounded : Icons.error_outline_rounded,
              color: perms[key] == true ? Colors.green.shade600 : cs.error),
          title: Text(title),
          subtitle: Text(why),
          trailing: perms[key] == true ? null : TextButton(onPressed: () => Recorder.askPermission(key), child: Text(l.allow)),
        );
    return Scaffold(
      appBar: AppBar(title: Text(l.addRecorder)),
      body: ListView(padding: const EdgeInsets.fromLTRB(16, 4, 16, 32), children: [
        if (perms['bluetoothOn'] == false)
          Padding(padding: const EdgeInsets.only(top: 12), child: Text(l.bluetoothOff, style: TextStyle(color: cs.error))),
        if (['bluetooth', 'notifications', 'battery'].any((k) => perms.isNotEmpty && perms[k] != true))
          sectionCard(context, l.permissions, Column(children: [
            perm('bluetooth', Platform.isIOS ? l.permBluetooth : l.permNearbyDevices, l.permBluetoothWhy),
            perm('notifications', l.permNotifications, l.permNotificationsWhy),
            perm('battery', l.permBackground, l.permBackgroundWhy),
          ])),
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 12),
          child: Text(l.addRecorderHelp, style: small),
        ),
        if (perms['bluetooth'] == true && found.isEmpty)
          Padding(padding: const EdgeInsets.all(24), child: Center(child: Text(l.searchingNearby))),
        for (final d in found) _row(d, cs, l),
      ]),
    );
  }

  Widget _row(Map d, ColorScheme cs, AppLocalizations l) {
    final state = d['state'] as int, rssi = d['rssi'] as int;
    // busy: not connectable (connected to another phone), told by the advertisement itself; the state byte comes in
    // the scan response, which a busy recorder seldom gets across
    final busy = d['busy'] == true || (state >= 0 && state & 2 != 0), bound = state >= 0 && state & 1 != 0;
    final near = [l.signalFar, l.signalMid, l.signalNear][signalLevel(rssi)];
    final (String what, Widget? action) = d['gone']
        ? (l.signalLost, null)
        : busy
            ? (l.busyOtherPhone, null)
            : bound
                ? (l.boundOtherPhone, TextButton(onPressed: () => _bind(d, true), child: Text(l.forceBind)))
                : (state < 0 ? l.oldFirmware : l.notBound, FilledButton(onPressed: () => _bind(d, false), child: Text(l.bind)));
    return Card.outlined(
      child: ListTile(
        enabled: !d['gone'],
        leading: Icon(Icons.mic_external_on_rounded, color: d['gone'] || busy || bound ? cs.outline : cs.primary),
        title: Row(children: [
          Flexible(child: Text(d['name'])),
          if (d['nearest']) Padding(padding: const EdgeInsets.only(left: 8), child: Badge(label: Text(l.nearest), backgroundColor: cs.primary)),
        ]),
        subtitle: Text(d['gone'] ? what : [what, near, advText(l, d)].where((s) => s.isNotEmpty).join(' · ')),
        trailing: action,
      ),
    );
  }
}
