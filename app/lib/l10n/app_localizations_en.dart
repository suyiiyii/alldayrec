// ignore: unused_import
import 'package:intl/intl.dart' as intl;

import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for English (`en`).
class AppLocalizationsEn extends AppLocalizations {
  AppLocalizationsEn([String locale = 'en']) : super(locale);

  @override
  String get tabTimeline => 'Timeline';

  @override
  String get tabLive => 'Live';

  @override
  String get tabPeople => 'People';

  @override
  String get tabMe => 'Me';

  @override
  String get savedNextRecording =>
      'Saved; takes effect the next time recording starts';

  @override
  String get saved => 'Saved';

  @override
  String get noMicPermission => 'No microphone permission';

  @override
  String cannotStartRecording(Object message) {
    return 'Can\'t start recording: $message';
  }

  @override
  String get refresh => 'Refresh';

  @override
  String get settings => 'Settings';

  @override
  String get recorder => 'Recorder';

  @override
  String get notSignedIn => 'Not signed in';

  @override
  String get deviceStatusAndMore => 'Device status and more';

  @override
  String get deviceStatusAndMoreSub => 'Battery, card, processing, AI access';

  @override
  String get upload => 'Upload';

  @override
  String get about => 'About';

  @override
  String get catchingUp => 'Catching up';

  @override
  String get notConnected => 'Not connected';

  @override
  String get paused => 'Paused';

  @override
  String get connected => 'Connected';

  @override
  String connectedBattery(Object battery) {
    return 'Connected $battery%';
  }

  @override
  String get stopping => 'Stopping';

  @override
  String get recording => 'Recording';

  @override
  String get notRecording => 'Not recording';

  @override
  String get deviceDetails => 'Device details';

  @override
  String get starting => 'Starting';

  @override
  String pausedMinutesLeft(Object minutes) {
    return 'Paused, $minutes min left';
  }

  @override
  String piecesPending(Object count) {
    return '$count pieces to upload';
  }

  @override
  String liveUploadCatchingUp(Object done, Object total) {
    return 'Live upload · catching up $done / $total MB';
  }

  @override
  String fetchingCurrentFile(Object left) {
    return 'Fetching the file being recorded before going live · $left MB left';
  }

  @override
  String waitingLastPieces(Object count) {
    return 'Waiting for the last $count pieces to upload';
  }

  @override
  String recordingPieceSeconds(Object seconds) {
    return 'Recording · $seconds s pieces';
  }

  @override
  String sentPending(Object sent, Object pending) {
    return 'Sent $sent · to upload $pending';
  }

  @override
  String uploadingLeftover(Object count) {
    return 'Uploading $count leftover pieces';
  }

  @override
  String recordingError(Object error) {
    return 'Recording error: $error';
  }

  @override
  String interruptedRecovered(Object time) {
    return 'Interrupted by the system at $time, recovered';
  }

  @override
  String get stop => 'Stop';

  @override
  String get startRecording => 'Start recording';

  @override
  String get emptyNotSignedIn =>
      'Shows once you scan to sign in or enter the token';

  @override
  String get goToSettings => 'Open settings';

  @override
  String unbindTitle(Object name) {
    return 'Unbind $name?';
  }

  @override
  String get unbindLinkedBody =>
      'The recorder forgets every phone and stops recording (the audio on its card has been fetched). This phone switches to its own microphone.';

  @override
  String get unbindAwayBody =>
      'The recorder is not nearby: only this phone lets go of it; the recorder still remembers the binding and keeps recording. You can force-bind it later when adding.';

  @override
  String unbindIosForget(Object name) {
    return 'The app can\'t remove the iPhone\'s own pairing: afterwards go to Settings › Bluetooth › $name › Forget This Device. To bind it to this iPhone again, forget it first.';
  }

  @override
  String get cancel => 'Cancel';

  @override
  String get unbind => 'Unbind';

  @override
  String get connectedCatchingUp => 'Connected · catching up';

  @override
  String get syncing => 'Syncing';

  @override
  String get connecting => 'Connecting';

  @override
  String cardFreeOf(Object total) {
    return 'free of $total';
  }

  @override
  String get addRecorder => 'Add recorder';

  @override
  String get addAgain => 'Add again';

  @override
  String get notBound => 'Not bound';

  @override
  String get recorderUnboundHelp =>
      'A recorder starts recording once bound to this phone: with the phone nearby it uploads live; when the phone is away it records to its card and uploads that in the background when the phone is back, live upload carrying on. Unbound it does not record; this phone\'s microphone is used now.';

  @override
  String get kickedChip => 'Bound by another phone';

  @override
  String get kickedHelp =>
      'Another phone bound this recorder, so this phone no longer connects to it. To take it back, add it again (the other phone is pushed out).';

  @override
  String kickedIosForget(Object name) {
    return ' Before adding it again, go to iPhone Settings › Bluetooth › $name › Forget This Device.';
  }

  @override
  String get recorderHelp =>
      'The recorder records whenever it is on: with the phone nearby it uploads live; when the phone is away it records to its card and uploads that in the background when the phone is back, live upload carrying on. You can pause it when needed.';

  @override
  String get pauseRecording => 'Pause recording';

  @override
  String get pauseNeedsLink => 'Connect the recorder to pause it';

  @override
  String pausedResumesIn(Object minutes) {
    return 'Paused, $minutes min left, then it resumes by itself';
  }

  @override
  String get recordingNow => 'Recording';

  @override
  String get liveListening => 'Live listening';

  @override
  String get listenOnPhone => 'Listen on this phone';

  @override
  String get listenNeedsLink => 'Connect the recorder to listen';

  @override
  String get listenHelp =>
      'The recorder\'s sound plays straight on the phone, not through the server, about half a second late; it turns off when you leave the app. Use headphones: the speaker would be picked up by the microphone';

  @override
  String get battery => 'Battery';

  @override
  String get card => 'Card';

  @override
  String get offlineSync => 'Offline recording sync';

  @override
  String get fetchingCard => 'Fetching the recordings on the card';

  @override
  String get syncLiveHelp =>
      'Live upload carries on and the card\'s backlog uploads in the background; until it is in, the server\'s finals wait at the backlog, drafts and live analysis carry on';

  @override
  String syncFirstHelp(Object left) {
    return 'Live upload starts once the file being recorded is fetched ($left left); older recordings follow in the background';
  }

  @override
  String get syncIdleHelp =>
      'While connected, the audio the recorder recorded offline is fetched in the background without holding up live upload';

  @override
  String get usbDiskMode => 'USB disk mode';

  @override
  String get cardAsUsbDisk => 'Card as a USB disk';

  @override
  String get usbDiskHelp =>
      'Works with the recorder plugged into a computer: an OMI disk appears after about 20 s; offline recording pauses meanwhile';

  @override
  String get ejectFirst =>
      'Eject the OMI disk on the computer before switching back';

  @override
  String get deviceManagement => 'Device';

  @override
  String get unbindAfterSync =>
      'Fetching the recordings on the card; unbind once that is done';

  @override
  String get unbindSub =>
      'The recorder stops recording; this phone switches to its own microphone';

  @override
  String get listenWaiting => 'Waiting for the recorder\'s sound and clock…';

  @override
  String get listenTime => 'Time';

  @override
  String get listenLevel => 'Level';

  @override
  String get listenHum => 'Hum';

  @override
  String get listenZeros => 'Zeros';

  @override
  String get listenClipped => 'Clip';

  @override
  String get listenLost => 'Lost';

  @override
  String get listenDelay => 'Delay';

  @override
  String listenLegend(Object queued) {
    return 'A line a second. Level, hum (50 Hz and harmonics): dBFS; zeros: ms in runs of 10 ms or more of exact zeros (no data from the mic); clip: clipped samples; lost: ms lost over Bluetooth; delay: ms from the recorder\'s mic to the phone\'s output, $queued of it buffered on the phone';
  }

  @override
  String get firmwareUpdate => 'Firmware update';

  @override
  String get downloadingFirmware => 'Downloading the firmware';

  @override
  String downloadFailed(Object error) {
    return 'Download failed: $error';
  }

  @override
  String get updateFirmware => 'Update firmware';

  @override
  String get unknown => 'unknown';

  @override
  String modelMatches(Object model) {
    return 'Model matches: $model';
  }

  @override
  String modelMismatch(Object model, Object firmwareModel) {
    return 'Model mismatch: the recorder is $model, the firmware is for $firmwareModel; it can\'t be updated';
  }

  @override
  String get unplugUsbFirst =>
      'Unplug the recorder\'s USB first: with USB plugged in an update hangs at erasing';

  @override
  String get keepBattery => 'Keep the battery above 30%';

  @override
  String keepBatteryNow(Object battery) {
    return 'Keep the battery above 30% (now $battery%)';
  }

  @override
  String get updateTakesMinutes =>
      'An update takes a few minutes: keep the phone next to the recorder and the app open; nothing is recorded or synced meanwhile';

  @override
  String get updateFailedHelp =>
      'If it fails midway and the recorder is gone: plug in USB and flash it with lr.sh flash (in update mode the recorder has another address, so tapping update again can\'t reach it)';

  @override
  String get startUpdate => 'Start update';

  @override
  String seconds(Object seconds) {
    return '$seconds s';
  }

  @override
  String minutesSeconds(Object minutes, Object seconds) {
    return '$minutes min $seconds s';
  }

  @override
  String get checking => 'Checking';

  @override
  String get updateAvailable => 'Update available';

  @override
  String get upToDate => 'Up to date';

  @override
  String get noFirmware => 'No firmware';

  @override
  String get version => 'Version';

  @override
  String get currentVersion => 'Current version';

  @override
  String get versionNotKnownYet =>
      'Not known yet: read once the recorder connects';

  @override
  String get latestVersion => 'Latest version';

  @override
  String releasedAt(Object when) {
    return 'Released $when';
  }

  @override
  String lookupFailed(Object error) {
    return 'Can\'t look it up: $error';
  }

  @override
  String get askingServer => 'Asking the server';

  @override
  String noFirmwareFor(Object model) {
    return 'The server has no firmware for $model yet';
  }

  @override
  String get lrP1UsbOnly =>
      'LR-P1 updates over USB only: lr.sh flash promicro (a Bluetooth update cut off midway can only be rescued over USB)';

  @override
  String get updateToLatest => 'Update to the latest version';

  @override
  String get alreadyLatest => 'Already the latest version';

  @override
  String get changelog => 'Changelog';

  @override
  String get noReleases => 'No versions released yet';

  @override
  String get tagLatest => 'Latest';

  @override
  String get tagCurrent => 'Current';

  @override
  String get noNotes => 'No notes';

  @override
  String get recentUpdates => 'Recent updates';

  @override
  String get noUpdates90Days => 'No updates in the last 90 days';

  @override
  String get succeeded => 'Succeeded';

  @override
  String get failed => 'Failed';

  @override
  String get noResult => 'No result';

  @override
  String took(Object duration) {
    return 'took $duration';
  }

  @override
  String serverNoTask(Object device) {
    return 'The server has nothing to process for $device yet';
  }

  @override
  String serverStatusFailed(Object error) {
    return 'Can\'t get the server\'s status: $error';
  }

  @override
  String hoursMinutes(Object hours, Object minutes) {
    return '$hours h $minutes min';
  }

  @override
  String minutes(Object minutes) {
    return '$minutes min';
  }

  @override
  String get recordingUploading => 'Recording, uploading as it goes';

  @override
  String get uploading => 'Uploading';

  @override
  String get waitingToUpload => 'Waiting to upload';

  @override
  String get allUploaded => 'All uploaded';

  @override
  String get toUpload => 'To upload';

  @override
  String pieces(Object count) {
    return '$count pieces';
  }

  @override
  String get noBacklog => 'No backlog';

  @override
  String pendingSub(Object size, Object when) {
    return '$size · oldest from $when';
  }

  @override
  String get sentThisRun => 'Sent';

  @override
  String get sinceStart => 'Since the app started';

  @override
  String get uploadNow => 'Upload now';

  @override
  String get serverProcessing => 'Server processing';

  @override
  String get loading => 'Loading…';

  @override
  String get caughtUp => 'Caught up: all audio is transcribed';

  @override
  String serverBehind(Object span, Object queued) {
    return '$span of audio to process · $queued pieces queued';
  }

  @override
  String finalsHeld(Object when, Object backlog) {
    return 'Finals stop at $when while the phone uploads older audio from the card in the background$backlog; they go on once it is in, drafts and live analysis carry on';
  }

  @override
  String backlogLeft(Object size) {
    return ' ($size left)';
  }

  @override
  String backlogLeftEta(Object size, Object eta) {
    return ' ($size left, about $eta)';
  }

  @override
  String etaDone(Object span) {
    return 'Done in about $span';
  }

  @override
  String get received => 'Received';

  @override
  String get finalized => 'Finalized';

  @override
  String bindTitle(Object name) {
    return 'Bind $name?';
  }

  @override
  String get bindForceBody =>
      'It is bound to another phone. Once bound to this phone, the other phone can no longer connect to it.';

  @override
  String get forceBind => 'Force bind';

  @override
  String get allow => 'Allow';

  @override
  String get bluetoothOff =>
      'Bluetooth is off: turn it on and recorders are looked for here';

  @override
  String get permissions => 'Permissions';

  @override
  String get permBluetooth => 'Bluetooth';

  @override
  String get permNearbyDevices => 'Nearby devices';

  @override
  String get permBluetoothWhy => 'Find and connect to the recorder';

  @override
  String get permNotifications => 'Notifications';

  @override
  String get permNotificationsWhy =>
      'A notification shows while the recorder is connected in the background';

  @override
  String get permBackground => 'Background';

  @override
  String get permBackgroundWhy =>
      'Exempt from battery optimization, so the recorder stays connected all day';

  @override
  String get addRecorderHelp =>
      'Turn the recorder on (plugging in the charging cable is enough). An unbound recorder blinks blue slowly; its name is printed on it, or take the one marked “Nearest”.';

  @override
  String get searchingNearby => 'Looking for recorders nearby…';

  @override
  String get signalNear => 'Right here';

  @override
  String get signalMid => 'Nearby';

  @override
  String get signalFar => 'Farther away';

  @override
  String get signalLost => 'Weak signal, not found for now';

  @override
  String get busyOtherPhone =>
      'Connected to another phone: unbind it there first, or turn that phone\'s Bluetooth off';

  @override
  String get boundOtherPhone => 'Bound to another phone';

  @override
  String get oldFirmware => 'Old firmware, state unknown';

  @override
  String get bind => 'Bind';

  @override
  String get nearest => 'Nearest';

  @override
  String get server => 'Server';

  @override
  String get serverAddress => 'Address';

  @override
  String get serverHint => 'http://100.x.y.z:8770 or https://your.server';

  @override
  String get scanToSignIn => 'Scan to sign in';

  @override
  String get deviceName => 'Device name';

  @override
  String get deviceNameHelp =>
      'The phone\'s name from the system settings; a BLE recorder\'s audio uploads under its own name';

  @override
  String get recordingSection => 'Recording';

  @override
  String get keepRecordings => 'Keep recordings on the phone';

  @override
  String get keepRecordingsIos =>
      'Kept after upload in the Files app › On My iPhone › alldayrec › recordings › date';

  @override
  String get keepRecordingsAndroid =>
      'Kept after upload in Android/data/com.suyiiyii.live_record/files/recordings/<date>/';

  @override
  String get pieceLength => 'Piece length (set by the server)';

  @override
  String piecesIos(Object foreground, Object background) {
    return 'Foreground $foreground s · background $background s';
  }

  @override
  String piecesAndroid(Object foreground, Object background, Object screenOff) {
    return 'Foreground $foreground s · background $background s · screen off $screenOff s';
  }

  @override
  String get save => 'Save';

  @override
  String get notSignInQr =>
      'Not a sign-in QR code: scan the one under “Connect phone” on the web page';

  @override
  String get onBattery => 'On battery';

  @override
  String get charging => 'Charging';

  @override
  String get charged => 'Charged';

  @override
  String get cardFullTile => 'Full';

  @override
  String get cardFull => 'Card full';

  @override
  String get cardFullSub => 'Offline recording stopped';

  @override
  String get cardUsbTile => 'USB disk';

  @override
  String get cardUsb => 'Card in USB disk mode';

  @override
  String get cardUsbSub => 'Offline recording paused';

  @override
  String get cardNoneTile => 'No card';

  @override
  String get cardNone => 'No card in';

  @override
  String get cardNoneSub =>
      'No card, or not seated: live upload only, nothing is recorded while the phone is away';

  @override
  String get cardErrorTile => 'Error';

  @override
  String get cardError => 'Card error';

  @override
  String get cardErrorSub =>
      'Reseat or replace the card; live upload carries on';

  @override
  String advBattery(Object volts) {
    return 'Battery $volts V';
  }

  @override
  String get advCardOk => 'Card OK';

  @override
  String get advCardFull => 'Card full';

  @override
  String get advCardError => 'Card error';

  @override
  String cardFreeGb(Object gb) {
    return '$gb GB free on card';
  }

  @override
  String batteryPercent(Object percent) {
    return 'Battery $percent%';
  }

  @override
  String get allowInstalls =>
      'Allow installing unknown apps in the settings that opened, then tap here again';

  @override
  String cannotOpenInstaller(Object error) {
    return 'Can\'t open the installer: $error';
  }

  @override
  String get downloadStalled => 'The download stalled; tap here to retry';

  @override
  String downloadFailedRetry(Object error) {
    return 'Download failed: $error; tap here to retry';
  }

  @override
  String downloadingApp(Object version, Object done, Object total) {
    return 'Downloading version $version · $done / $total MB';
  }

  @override
  String appUpdate(Object version, Object size) {
    return 'Version $version is out$size; tap here to update';
  }

  @override
  String appUpdateSize(Object mb) {
    return ' ($mb MB)';
  }

  @override
  String get dontRemindVersion => 'Don\'t remind me of this version';

  @override
  String get resume => 'Resume';

  @override
  String get pause15Min => 'Pause 15 min';

  @override
  String get oneHour => '1 hour';

  @override
  String get pause1Hour => 'Pause 1 hour';

  @override
  String get linkScanning => 'Looking for the recorder';

  @override
  String linkScanningName(Object name) {
    return 'Looking for $name';
  }

  @override
  String get linkBluetoothOff => 'Bluetooth is off';

  @override
  String get linkNoPermission =>
      'No Bluetooth permission: allow it in Settings › alldayrec';

  @override
  String get linkUnbound => 'No recorder bound';

  @override
  String get linkKicked =>
      'Bound to another phone: add it again on the Recorder page';

  @override
  String linkElsewhere(Object name) {
    return '$name is connected to another phone';
  }

  @override
  String linkBluetoothNotOn(Object s) {
    return 'Bluetooth is not on, retrying in $s s';
  }

  @override
  String linkScanFailed(Object err, Object s) {
    return 'Scan failed ($err), retrying in $s s';
  }

  @override
  String linkConnectFailed(Object s) {
    return 'Could not connect, retrying in $s s';
  }

  @override
  String get linkAway =>
      'The recorder is away and records offline; it catches up when back';

  @override
  String linkAwaySince(Object time) {
    return 'The recorder is away and records offline (last connected $time); it catches up when back';
  }

  @override
  String get linkConnecting => 'Connecting to the recorder';

  @override
  String linkStalePairing(Object name) {
    return 'This iPhone still has an old pairing with $name: go to Settings › Bluetooth › $name › Forget This Device, then come back (retrying in 30 s)';
  }

  @override
  String get linkMtu => 'Connected, negotiating the packet size';

  @override
  String get linkPairing => 'Pairing';

  @override
  String get linkPairingDialog => 'Pairing: tap Pair in the dialog';

  @override
  String get linkEncrypting => 'Setting up an encrypted connection';

  @override
  String get linkPairingFailed => 'Pairing did not finish, retrying in 30 s';

  @override
  String get linkServices => 'Reading the recorder\'s services';

  @override
  String get linkClock => 'Setting the recorder\'s clock';

  @override
  String get linkStatus => 'Reading the recorder\'s status';

  @override
  String get linkForgetOthers => 'Making the recorder forget other phones';

  @override
  String get linkJoining => 'Going live';

  @override
  String get linkConnected => 'Connected · live upload';

  @override
  String linkError(Object detail) {
    return 'Recorder error: $detail';
  }

  @override
  String lastSynced(Object time, Object kb) {
    return 'Fetched $kb KB of the recorder\'s audio at $time';
  }

  @override
  String get usbSwitchingOn => 'Switching to USB disk mode';

  @override
  String get usbSwitchingOff => 'Switching back to recording';

  @override
  String get usbNoComputer =>
      'The recorder is not plugged into a computer\'s USB';

  @override
  String usbFailed(Object err) {
    return 'Switch failed ($err)';
  }

  @override
  String get usbNotYetOn =>
      'Not in USB disk mode yet (the recorder did not answer in time, a slow card takes longer; the switch follows the recorder)';

  @override
  String get usbStillOn =>
      'Still in USB disk mode (the recorder did not answer in time, a slow card takes longer; the switch follows the recorder)';

  @override
  String get usbOn =>
      'USB disk mode: an OMI disk shows up on the computer (about 20 s); eject it there before switching back';

  @override
  String get noteRejected => 'The server could not decode a piece: dropped';

  @override
  String noteUploadFailed(Object detail) {
    return 'Upload failed: $detail';
  }

  @override
  String noteEndFailed(Object detail) {
    return 'End signal failed: $detail';
  }

  @override
  String noteArchiveFailed(Object detail) {
    return 'Archiving failed: $detail';
  }

  @override
  String noteKilled(Object count) {
    return 'The system ended the last recording: uploading the $count pieces left; start again by hand';
  }

  @override
  String micInterrupted(Object time) {
    return 'Interrupted by the system since $time, waiting to recover';
  }

  @override
  String micRouteFailed(Object time, Object detail) {
    return 'Not recording since $time: could not resume after the input changed ($detail); retrying in the foreground';
  }

  @override
  String micResumeFailed(Object time, Object detail) {
    return 'Not recording since $time: could not resume after the interruption ($detail); retrying in the foreground';
  }

  @override
  String get dfuPreparing => 'Preparing the update';

  @override
  String get dfuRebooting => 'The recorder restarts into update mode';

  @override
  String get dfuStarting => 'Starting the update';

  @override
  String dfuProgress(Object pct, Object kbps) {
    return 'Updating $pct% · $kbps KB/s';
  }

  @override
  String get dfuDone => 'Update done, the recorder restarted';

  @override
  String get dfuAborted => 'Update cancelled';

  @override
  String dfuFailed(Object why) {
    return 'Update failed: $why (recorder still there: try again; gone: plug it into USB and flash it with lr.sh flash)';
  }

  @override
  String get dfuNoBluetooth => 'Bluetooth is unavailable';

  @override
  String get dfuUnknown => 'This iPhone does not know the recorder';

  @override
  String get dfuNoConnect => 'Cannot connect to the recorder';

  @override
  String get dfuNoService => 'The recorder has no update service';

  @override
  String get dfuNoReboot => 'The recorder did not restart';

  @override
  String dfuRefused(Object detail) {
    return 'The recorder refused update mode ($detail)';
  }

  @override
  String get dfuDropped => 'The recorder disconnected';

  @override
  String get dfuNoBootloader => 'The recorder did not enter update mode';

  @override
  String get errPermission => 'Allow Bluetooth, then tap Start again';

  @override
  String get errNotStarted => 'Start recording and connect the recorder first';

  @override
  String get errNotConnected => 'The recorder is not connected';

  @override
  String errListen(Object detail) {
    return 'Cannot play: $detail';
  }

  @override
  String get errUpdating => 'An update is under way';

  @override
  String get errLinked => 'The recorder is still connected, try again shortly';

  @override
  String get errNoModel => 'The recorder never connected: its model is unknown';

  @override
  String errModel(Object want, Object model) {
    return 'This firmware is for $want and the recorder is $model: it cannot go on';
  }

  @override
  String errSame(Object version) {
    return 'The recorder already runs $version, no update needed';
  }

  @override
  String get errSha256 => 'The firmware download is incomplete, try again';

  @override
  String errZip(Object detail) {
    return 'Cannot open the firmware package: $detail';
  }
}
