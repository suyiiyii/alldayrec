import 'dart:io';

import 'package:flutter/material.dart';

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
    final t = Theme.of(context);
    Widget section(String s) => Padding(
        padding: const EdgeInsets.fromLTRB(4, 24, 4, 8),
        child: Text(s, style: t.textTheme.labelLarge?.copyWith(color: t.colorScheme.primary)));
    return ListView(padding: const EdgeInsets.fromLTRB(16, 0, 16, 32), children: [
      section('服务器'),
      TextField(controller: server, keyboardType: TextInputType.url,
          decoration: const InputDecoration(labelText: '地址', hintText: 'https://your.server', border: OutlineInputBorder())),
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
          onPressed: _scan, icon: const Icon(Icons.qr_code_scanner_rounded), label: const Text('扫码登录')),
      const SizedBox(height: 12),
      InputDecorator(
          decoration: const InputDecoration(labelText: '设备名', helperText: '取自系统设置里的手机名称；BLE 录音硬件的音频按硬件名上传',
              border: OutlineInputBorder()),
          child: Text(widget.settings.device)),
      section('录音'),
      Card.outlined(
        margin: EdgeInsets.zero,
        child: Column(children: [
          SwitchListTile(
            title: const Text('本地保留录音'),
            subtitle: Text(Platform.isIOS
                ? '上传后存到「文件」App › 我的 iPhone › alldayrec › recordings › 日期'
                : '上传后存到 Android/data/com.suyiiyii.live_record/files/recordings/日期/'),
            value: archive,
            onChanged: (v) => setState(() => archive = v),
          ),
          const Divider(height: 1),
          FutureBuilder<Map<String, int>>(
            future: Slices.cached(),
            builder: (_, snap) {
              final s = snap.data ?? defaultSlices;
              return ListTile(
                title: const Text('切片长度（服务端下发）'),
                subtitle: Text(Platform.isIOS  // iOS has no reliable screen-off signal; background stands for it
                    ? '前台 ${s['foreground']} 秒 · 后台 ${s['background']} 秒'
                    : '前台 ${s['foreground']} 秒 · 后台 ${s['background']} 秒 · 熄屏 ${s['screenOff']} 秒'),
              );
            },
          ),
        ]),
      ),
      const SizedBox(height: 24),
      FilledButton(onPressed: _save, style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(48)), child: const Text('保存')),
    ]);
  }
}
