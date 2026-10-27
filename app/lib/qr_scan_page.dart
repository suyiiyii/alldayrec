import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import 'l10n/app_localizations.dart';

/// The web page's "连接手机" QR is the web app's URL with the token, `<server>/?token=<token>` (a phone camera opens it
/// signed in): {'server', 'token'} (server keeps a path prefix, drops the trailing `/`), or null for anything else.
/// The token may be empty: a server without auth.
Map<String, String>? parseSignInQr(String raw) {
  final uri = Uri.tryParse(raw);
  if (uri == null || !(uri.isScheme('http') || uri.isScheme('https')) || uri.host.isEmpty) return null;
  final String? token;
  try {
    token = uri.queryParameters['token'];
  } on FormatException {  // a broken %-escape
    return null;
  }
  if (token == null) return null;
  final path = uri.path.endsWith('/') ? uri.path.substring(0, uri.path.length - 1) : uri.path;
  return {'server': '${uri.scheme}://${uri.authority}$path', 'token': token};
}

/// Scans that QR; pops with parseSignInQr's map on a match. Other QR contents get a hint and the scanner stays open
/// (repeated frames of the same one don't spam it).
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
    final r = parseSignInQr(raw);
    if (r == null) {
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(AppLocalizations.of(context).notSignInQr), behavior: SnackBarBehavior.floating));
      return;
    }
    Navigator.pop(context, r);
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
