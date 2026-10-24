# alldayrec app

The iOS and Android app (Flutter): records the phone's mic or the BLE recorder, uploads the audio to your server, and
shows its pages. Installing it and connecting it to a server: the Phone app section of the [main README](../README.en.md#phone-app).

```bash
PUB_HOSTED_URL=https://pub.flutter-io.cn flutter pub get --enforce-lockfile   # the pub host recorded in pubspec.lock
flutter build apk --release                                                   # Android
flutter build ios --no-codesign                                               # iOS; to install on a phone, build from Xcode with
                                                                              # ios/Flutter/Signing.local.xcconfig (see its .example)
flutter analyze --no-pub && flutter test --no-pub -j 4
```
