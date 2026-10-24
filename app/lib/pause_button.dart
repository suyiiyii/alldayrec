import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'recorder.dart';

/// Pause the connected recorder for 15 min or 1 h, or go on: a menu (status sheet) or two buttons (recorder page).
class PauseButton extends StatelessWidget {
  final bool paused, labels;
  final void Function(String? error) onDone;
  const PauseButton({super.key, required this.paused, required this.onDone, this.labels = false});

  Future<void> _pause(int s) async {
    try {
      await Recorder.pause(s);
      onDone(null);
    } on PlatformException catch (e) {
      onDone(e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (paused) return TextButton.icon(onPressed: () => _pause(0), icon: const Icon(Icons.play_arrow_rounded), label: const Text('继续'));
    if (labels) {
      return Row(mainAxisSize: MainAxisSize.min, children: [
        OutlinedButton(onPressed: () => _pause(900), child: const Text('暂停 15 分钟')),
        const SizedBox(width: 8),
        OutlinedButton(onPressed: () => _pause(3600), child: const Text('1 小时')),
      ]);
    }
    return PopupMenuButton<int>(
      icon: const Icon(Icons.pause_rounded),
      tooltip: '暂停录音',
      onSelected: _pause,
      itemBuilder: (_) => const [
        PopupMenuItem(value: 900, child: Text('暂停 15 分钟')),
        PopupMenuItem(value: 3600, child: Text('暂停 1 小时')),
      ],
    );
  }
}
