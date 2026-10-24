import 'dart:io';

import 'package:flutter/material.dart';

import 'l10n/app_localizations.dart';
import 'qr_scan_page.dart';
import 'settings.dart';

class SettingsPage extends StatefulWidget {
  final Settings settings;
  final Future<void> Function() onSaved;
  const SettingsPage({super.key, required this.settings, required this.onSaved});
  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  late final server = TextEditingController(text: widget.settings.server),
      token = TextEditingController(text: widget.settings.token);
  late bool archive = widget.settings.archive;
  bool showToken = false;

  Future<void> _save() async {
    FocusScope.of(context).unfocus();
    final s = widget.settings;
    s.server = server.text.trim().replaceFirst(RegExp(r'/$'), '');
    s.token = token.text.trim();
    s.archive = archive;
    await widget.onSaved();
  }

  /// The web page's "连接手机" QR fills these two fields instead of typing them by hand, then saves the same way
  /// the 保存 button does.
  Future<void> _scan() async {
    final r = await Navigator.push<Map<String, String>>(
        context, MaterialPageRoute(builder: (_) => const QrScanPage()));
    if (r == null) return;
    server.text = r['server']!;
    token.text = r['token']!;
    await _save();
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context), l = AppLocalizations.of(context);
    Widget section(String s) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 24, 4, 8),
        child: Text(s, style: t.textTheme.labelLarge?.copyWith(color: t.colorScheme.primary)));
    return ListView(padding: const EdgeInsets.fromLTRB(16, 0, 16, 32), children: [
      section(l.server),
      TextField(controller: server, keyboardType: TextInputType.url,
          decoration: InputDecoration(labelText: l.serverAddress, hintText: l.serverHint, border: const OutlineInputBorder())),
      const SizedBox(height: 12),
      TextField(
        controller: token,
        obscureText: !showToken,
        decoration: InputDecoration(
          labelText: 'Token',
          border: const OutlineInputBorder(),
          suffixIcon: IconButton(
              icon: Icon(showToken ? Icons.visibility_off_outlined : Icons.visibility_outlined),
              onPressed: () => setState(() => showToken = !showToken)),
        ),
      ),
      const SizedBox(height: 12),
      OutlinedButton.icon(
          onPressed: _scan, icon: const Icon(Icons.qr_code_scanner_rounded), label: Text(l.scanToSignIn)),
      const SizedBox(height: 12),
      InputDecorator(
          decoration: InputDecoration(labelText: l.deviceName, helperText: l.deviceNameHelp, helperMaxLines: 3,
              border: const OutlineInputBorder()),
          child: Text(widget.settings.device)),
      section(l.recordingSection),
      Card.outlined(
        margin: EdgeInsets.zero,
        child: Column(children: [
          SwitchListTile(
            title: Text(l.keepRecordings),
            subtitle: Text(Platform.isIOS ? l.keepRecordingsIos : l.keepRecordingsAndroid),
            value: archive,
            onChanged: (v) => setState(() => archive = v),
          ),
          const Divider(height: 1),
          FutureBuilder<Map<String, int>>(
            future: Slices.cached(),
            builder: (_, snap) {
              final s = snap.data ?? defaultSlices;
              return ListTile(
                title: Text(l.pieceLength),
                subtitle: Text(Platform.isIOS  // iOS has no reliable screen-off signal; background stands for it
                    ? l.piecesIos(s['foreground']!, s['background']!)
                    : l.piecesAndroid(s['foreground']!, s['background']!, s['screenOff']!)),
              );
            },
          ),
        ]),
      ),
      const SizedBox(height: 24),
      FilledButton(onPressed: _save, style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)), child: Text(l.save)),
    ]);
  }
}
