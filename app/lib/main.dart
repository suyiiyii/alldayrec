// alldayrec client. Native code keeps the microphone open (or follows the BLE recorder), cuts the stream into pieces
// whose length follows the app state and uploads them in order: on Android RecorderService.kt + Ble.kt (Opus/Ogg,
// START_STICKY, survives a kill), on iOS Recorder.swift + Ble.swift (Opus/Ogg, `audio` and `bluetooth-central`
// background modes). This side is settings, status and the pages in a WebView.
import 'package:flutter/material.dart';

import 'home.dart';
import 'l10n/app_localizations.dart';
import 'recorder.dart';

/// The app's language from the system's: Chinese for any zh (zh-Hans, zh-Hant, zh-HK...), English for everything else.
Locale appLocale(Locale? system) => system?.languageCode == 'zh' ? const Locale('zh') : const Locale('en');

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  // errors nothing caught go into the device log too; Flutter reports them as before
  FlutterError.onError = (d) {
    FlutterError.presentError(d);
    Recorder.error(d.exception, d.stack);
  };
  WidgetsBinding.instance.platformDispatcher.onError = (e, s) {
    Recorder.error(e, s);
    return false;  // not handled here: the engine reports it as before
  };
  runApp(MaterialApp(
    home: const Home(),
    localizationsDelegates: AppLocalizations.localizationsDelegates,
    supportedLocales: AppLocalizations.supportedLocales,
    localeResolutionCallback: (system, _) => appLocale(system),
    debugShowCheckedModeBanner: false,
    theme: ThemeData(colorSchemeSeed: const Color(0xff3b4cca), useMaterial3: true),
    darkTheme: ThemeData(colorSchemeSeed: const Color(0xff3b4cca), brightness: Brightness.dark, useMaterial3: true,
        scaffoldBackgroundColor: const Color(0xff15171e)),  // the web's dark --bg, so page and chrome are one surface
    themeMode: ThemeMode.system,
  ));
}
