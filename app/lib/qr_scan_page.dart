import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import 'l10n/app_localizations.dart';

/// Scans the web page's "连接手机" QR (`liverecord://setup?server=...&token=...`); pops with {'server', 'token'}
/// on a match. Other QR contents get a hint and the scanner stays open (repeated frames of the same one don't spam it).
class QrScanPage extends StatefulWidget {
  const QrScanPage({super.key});
  @override
  State<QrScanPage> createState() => _QrScanPageState();
}

class _QrScanPageState extends State<QrScanPage> {
  final controller = MobileScannerController();
  String? lastRaw;

  void _onDetect(BarcodeCapture capture) {
    if (!mounted) return;  // a stray detection after the page is already popping
    final raw = capture.barcodes.isEmpty ? null : capture.barcodes.first.rawValue;
    if (raw == null || raw == lastRaw) return;
    lastRaw = raw;
    final uri = Uri.tryParse(raw);
    if (uri == null || uri.scheme != 'liverecord' || uri.host != 'setup' || !uri.queryParameters.containsKey('server')) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(AppLocalizations.of(context).notSignInQr), behavior: SnackBarBehavior.floating));
      return;
    }
    Navigator.pop(context, {'server': uri.queryParameters['server']!, 'token': uri.queryParameters['token'] ?? ''});
  }

  @override
  void dispose() {
    controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) =>
      Scaffold(appBar: AppBar(title: Text(AppLocalizations.of(context).scanToSignIn)), body: MobileScanner(controller: controller, onDetect: _onDetect));
}
