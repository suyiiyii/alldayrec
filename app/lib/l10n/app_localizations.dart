import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/intl.dart' as intl;

import 'app_localizations_en.dart';
import 'app_localizations_zh.dart';

// ignore_for_file: type=lint

/// Callers can lookup localized strings with an instance of AppLocalizations
/// returned by `AppLocalizations.of(context)`.
///
/// Applications need to include `AppLocalizations.delegate()` in their app's
/// `localizationDelegates` list, and the locales they support in the app's
/// `supportedLocales` list. For example:
///
/// ```dart
/// import 'l10n/app_localizations.dart';
///
/// return MaterialApp(
///   localizationsDelegates: AppLocalizations.localizationsDelegates,
///   supportedLocales: AppLocalizations.supportedLocales,
///   home: MyApplicationHome(),
/// );
/// ```
///
/// ## Update pubspec.yaml
///
/// Please make sure to update your pubspec.yaml to include the following
/// packages:
///
/// ```yaml
/// dependencies:
///   # Internationalization support.
///   flutter_localizations:
///     sdk: flutter
///   intl: any # Use the pinned version from flutter_localizations
///
///   # Rest of dependencies
/// ```
///
/// ## iOS Applications
///
/// iOS applications define key application metadata, including supported
/// locales, in an Info.plist file that is built into the application bundle.
/// To configure the locales supported by your app, you’ll need to edit this
/// file.
///
/// First, open your project’s ios/Runner.xcworkspace Xcode workspace file.
/// Then, in the Project Navigator, open the Info.plist file under the Runner
/// project’s Runner folder.
///
/// Next, select the Information Property List item, select Add Item from the
/// Editor menu, then select Localizations from the pop-up menu.
///
/// Select and expand the newly-created Localizations item then, for each
/// locale your application supports, add a new item and select the locale
/// you wish to add from the pop-up menu in the Value field. This list should
/// be consistent with the languages listed in the AppLocalizations.supportedLocales
/// property.
abstract class AppLocalizations {
  AppLocalizations(String locale)
    : localeName = intl.Intl.canonicalizedLocale(locale.toString());

  final String localeName;

  static AppLocalizations of(BuildContext context) {
    return Localizations.of<AppLocalizations>(context, AppLocalizations)!;
  }

  static const LocalizationsDelegate<AppLocalizations> delegate =
      _AppLocalizationsDelegate();

  /// A list of this localizations delegate along with the default localizations
  /// delegates.
  ///
  /// Returns a list of localizations delegates containing this delegate along with
  /// GlobalMaterialLocalizations.delegate, GlobalCupertinoLocalizations.delegate,
  /// and GlobalWidgetsLocalizations.delegate.
  ///
  /// Additional delegates can be added by appending to this list in
  /// MaterialApp. This list does not have to be used at all if a custom list
  /// of delegates is preferred or required.
  static const List<LocalizationsDelegate<dynamic>> localizationsDelegates =
      <LocalizationsDelegate<dynamic>>[
        delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
      ];

  /// A list of this localizations delegate's supported locales.
  static const List<Locale> supportedLocales = <Locale>[
    Locale('en'),
    Locale('zh'),
  ];

  /// No description provided for @tabTimeline.
  ///
  /// In zh, this message translates to:
  /// **'时间线'**
  String get tabTimeline;

  /// No description provided for @tabLive.
  ///
  /// In zh, this message translates to:
  /// **'实时'**
  String get tabLive;

  /// No description provided for @tabPeople.
  ///
  /// In zh, this message translates to:
  /// **'人物'**
  String get tabPeople;

  /// No description provided for @tabMe.
  ///
  /// In zh, this message translates to:
  /// **'我的'**
  String get tabMe;

  /// No description provided for @savedNextRecording.
  ///
  /// In zh, this message translates to:
  /// **'已保存，下次开始录音时生效'**
  String get savedNextRecording;

  /// No description provided for @saved.
  ///
  /// In zh, this message translates to:
  /// **'已保存'**
  String get saved;

  /// No description provided for @noMicPermission.
  ///
  /// In zh, this message translates to:
  /// **'没有麦克风权限'**
  String get noMicPermission;

  /// No description provided for @cannotStartRecording.
  ///
  /// In zh, this message translates to:
  /// **'无法开始录音 {message}'**
  String cannotStartRecording(Object message);

  /// No description provided for @refresh.
  ///
  /// In zh, this message translates to:
  /// **'刷新'**
  String get refresh;

  /// No description provided for @settings.
  ///
  /// In zh, this message translates to:
  /// **'设置'**
  String get settings;

  /// No description provided for @recorder.
  ///
  /// In zh, this message translates to:
  /// **'录音设备'**
  String get recorder;

  /// No description provided for @notSignedIn.
  ///
  /// In zh, this message translates to:
  /// **'还没登录'**
  String get notSignedIn;

  /// No description provided for @deviceStatusAndMore.
  ///
  /// In zh, this message translates to:
  /// **'设备状态与更多'**
  String get deviceStatusAndMore;

  /// No description provided for @deviceStatusAndMoreSub.
  ///
  /// In zh, this message translates to:
  /// **'电量、存储卡、处理进度、接入 AI'**
  String get deviceStatusAndMoreSub;

  /// No description provided for @upload.
  ///
  /// In zh, this message translates to:
  /// **'上传'**
  String get upload;

  /// No description provided for @about.
  ///
  /// In zh, this message translates to:
  /// **'关于'**
  String get about;

  /// No description provided for @catchingUp.
  ///
  /// In zh, this message translates to:
  /// **'补传中'**
  String get catchingUp;

  /// No description provided for @notConnected.
  ///
  /// In zh, this message translates to:
  /// **'未连接'**
  String get notConnected;

  /// No description provided for @paused.
  ///
  /// In zh, this message translates to:
  /// **'已暂停'**
  String get paused;

  /// No description provided for @connected.
  ///
  /// In zh, this message translates to:
  /// **'已连接'**
  String get connected;

  /// No description provided for @connectedBattery.
  ///
  /// In zh, this message translates to:
  /// **'已连接 {battery}%'**
  String connectedBattery(Object battery);

  /// No description provided for @stopping.
  ///
  /// In zh, this message translates to:
  /// **'停止中'**
  String get stopping;

  /// No description provided for @recording.
  ///
  /// In zh, this message translates to:
  /// **'录音中'**
  String get recording;

  /// No description provided for @notRecording.
  ///
  /// In zh, this message translates to:
  /// **'未在录音'**
  String get notRecording;

  /// No description provided for @deviceDetails.
  ///
  /// In zh, this message translates to:
  /// **'设备详情'**
  String get deviceDetails;

  /// No description provided for @starting.
  ///
  /// In zh, this message translates to:
  /// **'正在启动'**
  String get starting;

  /// No description provided for @pausedMinutesLeft.
  ///
  /// In zh, this message translates to:
  /// **'已暂停，还剩 {minutes} 分钟'**
  String pausedMinutesLeft(Object minutes);

  /// No description provided for @piecesPending.
  ///
  /// In zh, this message translates to:
  /// **'待传 {count} 块'**
  String piecesPending(Object count);

  /// No description provided for @liveUploadCatchingUp.
  ///
  /// In zh, this message translates to:
  /// **'实时上传 · 后台补传 {done} / {total} MB'**
  String liveUploadCatchingUp(Object done, Object total);

  /// No description provided for @fetchingCurrentFile.
  ///
  /// In zh, this message translates to:
  /// **'接上实时前先取正在录的这段 · 还差 {left} MB'**
  String fetchingCurrentFile(Object left);

  /// No description provided for @waitingLastPieces.
  ///
  /// In zh, this message translates to:
  /// **'等最后 {count} 块传完'**
  String waitingLastPieces(Object count);

  /// No description provided for @recordingPieceSeconds.
  ///
  /// In zh, this message translates to:
  /// **'录音中 · {seconds} 秒一块'**
  String recordingPieceSeconds(Object seconds);

  /// No description provided for @sentPending.
  ///
  /// In zh, this message translates to:
  /// **'已传 {sent} · 待传 {pending}'**
  String sentPending(Object sent, Object pending);

  /// No description provided for @uploadingLeftover.
  ///
  /// In zh, this message translates to:
  /// **'正在补传 {count} 块'**
  String uploadingLeftover(Object count);

  /// No description provided for @recordingError.
  ///
  /// In zh, this message translates to:
  /// **'录音错误 {error}'**
  String recordingError(Object error);

  /// No description provided for @interruptedRecovered.
  ///
  /// In zh, this message translates to:
  /// **'{time} 被系统中断过，已恢复'**
  String interruptedRecovered(Object time);

  /// No description provided for @stop.
  ///
  /// In zh, this message translates to:
  /// **'停止'**
  String get stop;

  /// No description provided for @startRecording.
  ///
  /// In zh, this message translates to:
  /// **'开始录音'**
  String get startRecording;

  /// No description provided for @emptyNotSignedIn.
  ///
  /// In zh, this message translates to:
  /// **'扫码登录或填好 Token 后显示'**
  String get emptyNotSignedIn;

  /// No description provided for @goToSettings.
  ///
  /// In zh, this message translates to:
  /// **'去设置'**
  String get goToSettings;

  /// No description provided for @unbindTitle.
  ///
  /// In zh, this message translates to:
  /// **'解除绑定 {name}？'**
  String unbindTitle(Object name);

  /// No description provided for @unbindLinkedBody.
  ///
  /// In zh, this message translates to:
  /// **'录音设备会忘掉所有手机、停止录音（卡上的录音已取回）。这台手机改用手机麦克风。'**
  String get unbindLinkedBody;

  /// No description provided for @unbindAwayBody.
  ///
  /// In zh, this message translates to:
  /// **'录音设备不在附近：只在这台手机上解除，它自己还记着绑定、会继续录音。以后可以在添加时强制绑定。'**
  String get unbindAwayBody;

  /// No description provided for @unbindIosForget.
  ///
  /// In zh, this message translates to:
  /// **'iPhone 自己的配对记录 App 删不掉：之后到 设置 › 蓝牙 › {name} › 忽略此设备；以后要在这台 iPhone 上再绑定它，必须先忽略。'**
  String unbindIosForget(Object name);

  /// No description provided for @cancel.
  ///
  /// In zh, this message translates to:
  /// **'取消'**
  String get cancel;

  /// No description provided for @unbind.
  ///
  /// In zh, this message translates to:
  /// **'解除绑定'**
  String get unbind;

  /// No description provided for @connectedCatchingUp.
  ///
  /// In zh, this message translates to:
  /// **'已连接 · 补传中'**
  String get connectedCatchingUp;

  /// No description provided for @syncing.
  ///
  /// In zh, this message translates to:
  /// **'同步中'**
  String get syncing;

  /// No description provided for @connecting.
  ///
  /// In zh, this message translates to:
  /// **'连接中'**
  String get connecting;

  /// No description provided for @cardFreeOf.
  ///
  /// In zh, this message translates to:
  /// **'可用，共 {total}'**
  String cardFreeOf(Object total);

  /// No description provided for @addRecorder.
  ///
  /// In zh, this message translates to:
  /// **'添加录音设备'**
  String get addRecorder;

  /// No description provided for @addAgain.
  ///
  /// In zh, this message translates to:
  /// **'重新添加'**
  String get addAgain;

  /// No description provided for @notBound.
  ///
  /// In zh, this message translates to:
  /// **'未绑定'**
  String get notBound;

  /// No description provided for @recorderUnboundHelp.
  ///
  /// In zh, this message translates to:
  /// **'录音设备绑定到这台手机后开始录音：手机在附近时实时上传，不在时存进它的卡里，回来后在后台自动补传，实时上传照常。没绑定时它不录音，现在用的是手机麦克风。'**
  String get recorderUnboundHelp;

  /// No description provided for @kickedChip.
  ///
  /// In zh, this message translates to:
  /// **'已被其他手机绑定'**
  String get kickedChip;

  /// No description provided for @kickedHelp.
  ///
  /// In zh, this message translates to:
  /// **'另一台手机绑定了这台录音设备，这台手机不再连它。要拿回来，就重新添加（原来那台手机会被顶掉）。'**
  String get kickedHelp;

  /// No description provided for @kickedIosForget.
  ///
  /// In zh, this message translates to:
  /// **'重新添加前，先到 iPhone 设置 › 蓝牙 › {name} › 忽略此设备。'**
  String kickedIosForget(Object name);

  /// No description provided for @recorderHelp.
  ///
  /// In zh, this message translates to:
  /// **'录音设备开着就一直在录：手机在附近时实时上传，不在时存进它的卡里，回来后在后台自动补传，实时上传照常。需要时可以暂停。'**
  String get recorderHelp;

  /// No description provided for @pauseRecording.
  ///
  /// In zh, this message translates to:
  /// **'暂停录音'**
  String get pauseRecording;

  /// No description provided for @pauseNeedsLink.
  ///
  /// In zh, this message translates to:
  /// **'连上录音设备后可以暂停'**
  String get pauseNeedsLink;

  /// No description provided for @pausedResumesIn.
  ///
  /// In zh, this message translates to:
  /// **'已暂停，还剩 {minutes} 分钟，到时自动继续'**
  String pausedResumesIn(Object minutes);

  /// No description provided for @recordingNow.
  ///
  /// In zh, this message translates to:
  /// **'正在录音'**
  String get recordingNow;

  /// No description provided for @liveListening.
  ///
  /// In zh, this message translates to:
  /// **'实时监听'**
  String get liveListening;

  /// No description provided for @listenOnPhone.
  ///
  /// In zh, this message translates to:
  /// **'在这台手机上听'**
  String get listenOnPhone;

  /// No description provided for @listenNeedsLink.
  ///
  /// In zh, this message translates to:
  /// **'连上录音设备后可以听'**
  String get listenNeedsLink;

  /// No description provided for @listenHelp.
  ///
  /// In zh, this message translates to:
  /// **'录音设备的声音直接在手机上播放，不经服务器，慢约半秒，离开 App 自动关闭。建议戴耳机，外放会被麦克风收回去'**
  String get listenHelp;

  /// No description provided for @battery.
  ///
  /// In zh, this message translates to:
  /// **'电量'**
  String get battery;

  /// No description provided for @card.
  ///
  /// In zh, this message translates to:
  /// **'存储卡'**
  String get card;

  /// No description provided for @offlineSync.
  ///
  /// In zh, this message translates to:
  /// **'离线录音同步'**
  String get offlineSync;

  /// No description provided for @fetchingCard.
  ///
  /// In zh, this message translates to:
  /// **'正在取回卡上的录音'**
  String get fetchingCard;

  /// No description provided for @syncLiveHelp.
  ///
  /// In zh, this message translates to:
  /// **'实时上传照常，卡上积压的录音在后台补传；补完前服务端定稿先停在积压处，初稿和实时分析照常'**
  String get syncLiveHelp;

  /// No description provided for @syncFirstHelp.
  ///
  /// In zh, this message translates to:
  /// **'正在录的这段取完（还差 {left}）就开始实时上传，更早的录音之后在后台补传'**
  String syncFirstHelp(Object left);

  /// No description provided for @syncIdleHelp.
  ///
  /// In zh, this message translates to:
  /// **'连上录音设备时在后台自动取回它离线录下的音频，不耽误实时上传'**
  String get syncIdleHelp;

  /// No description provided for @usbDiskMode.
  ///
  /// In zh, this message translates to:
  /// **'U 盘模式'**
  String get usbDiskMode;

  /// No description provided for @cardAsUsbDisk.
  ///
  /// In zh, this message translates to:
  /// **'存储卡当 U 盘'**
  String get cardAsUsbDisk;

  /// No description provided for @usbDiskHelp.
  ///
  /// In zh, this message translates to:
  /// **'录音设备插在电脑上时可用，约 20 秒后出现 OMI 盘；期间离线录音暂停'**
  String get usbDiskHelp;

  /// No description provided for @ejectFirst.
  ///
  /// In zh, this message translates to:
  /// **'切回前先在电脑上推出 OMI 盘'**
  String get ejectFirst;

  /// No description provided for @deviceManagement.
  ///
  /// In zh, this message translates to:
  /// **'设备管理'**
  String get deviceManagement;

  /// No description provided for @unbindAfterSync.
  ///
  /// In zh, this message translates to:
  /// **'正在取回卡上的录音，取完再解除'**
  String get unbindAfterSync;

  /// No description provided for @unbindSub.
  ///
  /// In zh, this message translates to:
  /// **'录音设备停止录音，这台手机改用手机麦克风'**
  String get unbindSub;

  /// No description provided for @listenWaiting.
  ///
  /// In zh, this message translates to:
  /// **'等录音设备的声音和对时……'**
  String get listenWaiting;

  /// No description provided for @listenTime.
  ///
  /// In zh, this message translates to:
  /// **'时间'**
  String get listenTime;

  /// No description provided for @listenLevel.
  ///
  /// In zh, this message translates to:
  /// **'电平'**
  String get listenLevel;

  /// No description provided for @listenHum.
  ///
  /// In zh, this message translates to:
  /// **'哼声'**
  String get listenHum;

  /// No description provided for @listenZeros.
  ///
  /// In zh, this message translates to:
  /// **'全0'**
  String get listenZeros;

  /// No description provided for @listenClipped.
  ///
  /// In zh, this message translates to:
  /// **'满幅'**
  String get listenClipped;

  /// No description provided for @listenLost.
  ///
  /// In zh, this message translates to:
  /// **'丢失'**
  String get listenLost;

  /// No description provided for @listenDelay.
  ///
  /// In zh, this message translates to:
  /// **'延迟'**
  String get listenDelay;

  /// No description provided for @listenLegend.
  ///
  /// In zh, this message translates to:
  /// **'每秒一行。电平、哼声（50 Hz 及谐波）：dBFS；全0：连续 10 ms 以上全是 0 的毫秒数（麦克风没数据）；满幅：削顶的样本数；丢失：蓝牙丢掉的毫秒数；延迟：从录音设备的麦克风到手机输出的毫秒数，其中 {queued} 是手机上的缓冲'**
  String listenLegend(Object queued);

  /// No description provided for @firmwareUpdate.
  ///
  /// In zh, this message translates to:
  /// **'固件更新'**
  String get firmwareUpdate;

  /// No description provided for @downloadingFirmware.
  ///
  /// In zh, this message translates to:
  /// **'正在下载固件'**
  String get downloadingFirmware;

  /// No description provided for @downloadFailed.
  ///
  /// In zh, this message translates to:
  /// **'下载失败：{error}'**
  String downloadFailed(Object error);

  /// No description provided for @updateFirmware.
  ///
  /// In zh, this message translates to:
  /// **'升级固件'**
  String get updateFirmware;

  /// No description provided for @unknown.
  ///
  /// In zh, this message translates to:
  /// **'未知'**
  String get unknown;

  /// No description provided for @modelMatches.
  ///
  /// In zh, this message translates to:
  /// **'型号一致：{model}'**
  String modelMatches(Object model);

  /// No description provided for @modelMismatch.
  ///
  /// In zh, this message translates to:
  /// **'型号不符：录音设备是 {model}，固件是 {firmwareModel}，不能升级'**
  String modelMismatch(Object model, Object firmwareModel);

  /// No description provided for @unplugUsbFirst.
  ///
  /// In zh, this message translates to:
  /// **'先拔掉录音设备的 USB：插着 USB 升级会卡在擦除'**
  String get unplugUsbFirst;

  /// No description provided for @keepBattery.
  ///
  /// In zh, this message translates to:
  /// **'电量保持在 30% 以上'**
  String get keepBattery;

  /// No description provided for @keepBatteryNow.
  ///
  /// In zh, this message translates to:
  /// **'电量保持在 30% 以上（现在 {battery}%）'**
  String keepBatteryNow(Object battery);

  /// No description provided for @updateTakesMinutes.
  ///
  /// In zh, this message translates to:
  /// **'升级要几分钟：手机放在录音设备旁边，别关 App；期间不录音也不同步'**
  String get updateTakesMinutes;

  /// No description provided for @updateFailedHelp.
  ///
  /// In zh, this message translates to:
  /// **'中途失败、录音设备不见了：插 USB 用 lr.sh flash 刷（升级模式下的录音设备换了地址，再点升级连不上）'**
  String get updateFailedHelp;

  /// No description provided for @startUpdate.
  ///
  /// In zh, this message translates to:
  /// **'开始升级'**
  String get startUpdate;

  /// No description provided for @seconds.
  ///
  /// In zh, this message translates to:
  /// **'{seconds} 秒'**
  String seconds(Object seconds);

  /// No description provided for @minutesSeconds.
  ///
  /// In zh, this message translates to:
  /// **'{minutes} 分 {seconds} 秒'**
  String minutesSeconds(Object minutes, Object seconds);

  /// No description provided for @checking.
  ///
  /// In zh, this message translates to:
  /// **'检查中'**
  String get checking;

  /// No description provided for @updateAvailable.
  ///
  /// In zh, this message translates to:
  /// **'有新版本'**
  String get updateAvailable;

  /// No description provided for @upToDate.
  ///
  /// In zh, this message translates to:
  /// **'已是最新'**
  String get upToDate;

  /// No description provided for @noFirmware.
  ///
  /// In zh, this message translates to:
  /// **'没有固件'**
  String get noFirmware;

  /// No description provided for @version.
  ///
  /// In zh, this message translates to:
  /// **'版本'**
  String get version;

  /// No description provided for @currentVersion.
  ///
  /// In zh, this message translates to:
  /// **'当前版本'**
  String get currentVersion;

  /// No description provided for @versionNotKnownYet.
  ///
  /// In zh, this message translates to:
  /// **'还不知道：连上录音设备后读取'**
  String get versionNotKnownYet;

  /// No description provided for @latestVersion.
  ///
  /// In zh, this message translates to:
  /// **'最新版本'**
  String get latestVersion;

  /// No description provided for @releasedAt.
  ///
  /// In zh, this message translates to:
  /// **'发布于 {when}'**
  String releasedAt(Object when);

  /// No description provided for @lookupFailed.
  ///
  /// In zh, this message translates to:
  /// **'查不到：{error}'**
  String lookupFailed(Object error);

  /// No description provided for @askingServer.
  ///
  /// In zh, this message translates to:
  /// **'正在查服务器'**
  String get askingServer;

  /// No description provided for @noFirmwareFor.
  ///
  /// In zh, this message translates to:
  /// **'服务器上还没有 {model} 的固件'**
  String noFirmwareFor(Object model);

  /// No description provided for @lrP1UsbOnly.
  ///
  /// In zh, this message translates to:
  /// **'LR-P1 只能插 USB 升级：lr.sh flash promicro（蓝牙升级中途一断，只能插 USB 救回）'**
  String get lrP1UsbOnly;

  /// No description provided for @updateToLatest.
  ///
  /// In zh, this message translates to:
  /// **'升级到最新版本'**
  String get updateToLatest;

  /// No description provided for @alreadyLatest.
  ///
  /// In zh, this message translates to:
  /// **'已是最新版本'**
  String get alreadyLatest;

  /// No description provided for @changelog.
  ///
  /// In zh, this message translates to:
  /// **'更新日志'**
  String get changelog;

  /// No description provided for @noReleases.
  ///
  /// In zh, this message translates to:
  /// **'还没有发布过的版本'**
  String get noReleases;

  /// No description provided for @tagLatest.
  ///
  /// In zh, this message translates to:
  /// **'最新'**
  String get tagLatest;

  /// No description provided for @tagCurrent.
  ///
  /// In zh, this message translates to:
  /// **'当前'**
  String get tagCurrent;

  /// No description provided for @noNotes.
  ///
  /// In zh, this message translates to:
  /// **'没有说明'**
  String get noNotes;

  /// No description provided for @recentUpdates.
  ///
  /// In zh, this message translates to:
  /// **'最近的升级'**
  String get recentUpdates;

  /// No description provided for @noUpdates90Days.
  ///
  /// In zh, this message translates to:
  /// **'最近 90 天没有升级过'**
  String get noUpdates90Days;

  /// No description provided for @succeeded.
  ///
  /// In zh, this message translates to:
  /// **'成功'**
  String get succeeded;

  /// No description provided for @failed.
  ///
  /// In zh, this message translates to:
  /// **'失败'**
  String get failed;

  /// No description provided for @noResult.
  ///
  /// In zh, this message translates to:
  /// **'没有结果'**
  String get noResult;

  /// No description provided for @took.
  ///
  /// In zh, this message translates to:
  /// **'用时 {duration}'**
  String took(Object duration);

  /// No description provided for @serverNoTask.
  ///
  /// In zh, this message translates to:
  /// **'服务端还没有 {device} 的处理任务'**
  String serverNoTask(Object device);

  /// No description provided for @serverStatusFailed.
  ///
  /// In zh, this message translates to:
  /// **'取不到服务端状态：{error}'**
  String serverStatusFailed(Object error);

  /// No description provided for @hoursMinutes.
  ///
  /// In zh, this message translates to:
  /// **'{hours} 小时 {minutes} 分'**
  String hoursMinutes(Object hours, Object minutes);

  /// No description provided for @minutes.
  ///
  /// In zh, this message translates to:
  /// **'{minutes} 分钟'**
  String minutes(Object minutes);

  /// No description provided for @recordingUploading.
  ///
  /// In zh, this message translates to:
  /// **'录音中，边录边传'**
  String get recordingUploading;

  /// No description provided for @uploading.
  ///
  /// In zh, this message translates to:
  /// **'正在上传'**
  String get uploading;

  /// No description provided for @waitingToUpload.
  ///
  /// In zh, this message translates to:
  /// **'等待上传'**
  String get waitingToUpload;

  /// No description provided for @allUploaded.
  ///
  /// In zh, this message translates to:
  /// **'已全部上传'**
  String get allUploaded;

  /// No description provided for @toUpload.
  ///
  /// In zh, this message translates to:
  /// **'待传'**
  String get toUpload;

  /// No description provided for @pieces.
  ///
  /// In zh, this message translates to:
  /// **'{count} 块'**
  String pieces(Object count);

  /// No description provided for @noBacklog.
  ///
  /// In zh, this message translates to:
  /// **'没有积压'**
  String get noBacklog;

  /// No description provided for @pendingSub.
  ///
  /// In zh, this message translates to:
  /// **'{size} · 最早录于 {when}'**
  String pendingSub(Object size, Object when);

  /// No description provided for @sentThisRun.
  ///
  /// In zh, this message translates to:
  /// **'本次已传'**
  String get sentThisRun;

  /// No description provided for @sinceStart.
  ///
  /// In zh, this message translates to:
  /// **'这次开机以来'**
  String get sinceStart;

  /// No description provided for @uploadNow.
  ///
  /// In zh, this message translates to:
  /// **'立即上传'**
  String get uploadNow;

  /// No description provided for @serverProcessing.
  ///
  /// In zh, this message translates to:
  /// **'服务端处理'**
  String get serverProcessing;

  /// No description provided for @loading.
  ///
  /// In zh, this message translates to:
  /// **'加载中…'**
  String get loading;

  /// No description provided for @caughtUp.
  ///
  /// In zh, this message translates to:
  /// **'已追上，录音都已转写'**
  String get caughtUp;

  /// No description provided for @serverBehind.
  ///
  /// In zh, this message translates to:
  /// **'还有 {span} 录音待处理 · 排队 {queued} 块'**
  String serverBehind(Object span, Object queued);

  /// No description provided for @finalsHeld.
  ///
  /// In zh, this message translates to:
  /// **'定稿停在 {when}，等手机在后台补传卡里更早的录音{backlog}，补完再接着定稿；初稿和实时分析照常'**
  String finalsHeld(Object when, Object backlog);

  /// No description provided for @backlogLeft.
  ///
  /// In zh, this message translates to:
  /// **'（还有 {size}）'**
  String backlogLeft(Object size);

  /// No description provided for @backlogLeftEta.
  ///
  /// In zh, this message translates to:
  /// **'（还有 {size}，约 {eta}）'**
  String backlogLeftEta(Object size, Object eta);

  /// No description provided for @etaDone.
  ///
  /// In zh, this message translates to:
  /// **'预计约 {span} 后完成'**
  String etaDone(Object span);

  /// No description provided for @received.
  ///
  /// In zh, this message translates to:
  /// **'已收到'**
  String get received;

  /// No description provided for @finalized.
  ///
  /// In zh, this message translates to:
  /// **'已定稿'**
  String get finalized;

  /// No description provided for @bindTitle.
  ///
  /// In zh, this message translates to:
  /// **'绑定 {name}？'**
  String bindTitle(Object name);

  /// No description provided for @bindForceBody.
  ///
  /// In zh, this message translates to:
  /// **'它已经绑定了别的手机。绑定到这台手机后，原来的手机将无法再连接它。'**
  String get bindForceBody;

  /// No description provided for @forceBind.
  ///
  /// In zh, this message translates to:
  /// **'强制绑定'**
  String get forceBind;

  /// No description provided for @allow.
  ///
  /// In zh, this message translates to:
  /// **'允许'**
  String get allow;

  /// No description provided for @bluetoothOff.
  ///
  /// In zh, this message translates to:
  /// **'蓝牙没有打开：打开后这里会开始找录音设备'**
  String get bluetoothOff;

  /// No description provided for @permissions.
  ///
  /// In zh, this message translates to:
  /// **'权限'**
  String get permissions;

  /// No description provided for @permBluetooth.
  ///
  /// In zh, this message translates to:
  /// **'蓝牙'**
  String get permBluetooth;

  /// No description provided for @permNearbyDevices.
  ///
  /// In zh, this message translates to:
  /// **'附近的设备'**
  String get permNearbyDevices;

  /// No description provided for @permBluetoothWhy.
  ///
  /// In zh, this message translates to:
  /// **'找到并连接录音设备'**
  String get permBluetoothWhy;

  /// No description provided for @permNotifications.
  ///
  /// In zh, this message translates to:
  /// **'通知'**
  String get permNotifications;

  /// No description provided for @permNotificationsWhy.
  ///
  /// In zh, this message translates to:
  /// **'后台连接录音设备时要显示一条通知'**
  String get permNotificationsWhy;

  /// No description provided for @permBackground.
  ///
  /// In zh, this message translates to:
  /// **'后台运行'**
  String get permBackground;

  /// No description provided for @permBackgroundWhy.
  ///
  /// In zh, this message translates to:
  /// **'不受电池优化限制，录音设备才能整天连着'**
  String get permBackgroundWhy;

  /// No description provided for @addRecorderHelp.
  ///
  /// In zh, this message translates to:
  /// **'打开录音设备（插上充电线即可）。没绑定的录音设备蓝灯慢闪，名字印在它身上，或看标着「最近」的那台。'**
  String get addRecorderHelp;

  /// No description provided for @searchingNearby.
  ///
  /// In zh, this message translates to:
  /// **'正在找附近的录音设备…'**
  String get searchingNearby;

  /// No description provided for @signalNear.
  ///
  /// In zh, this message translates to:
  /// **'就在旁边'**
  String get signalNear;

  /// No description provided for @signalMid.
  ///
  /// In zh, this message translates to:
  /// **'附近'**
  String get signalMid;

  /// No description provided for @signalFar.
  ///
  /// In zh, this message translates to:
  /// **'较远'**
  String get signalFar;

  /// No description provided for @signalLost.
  ///
  /// In zh, this message translates to:
  /// **'信号弱，暂时找不到'**
  String get signalLost;

  /// No description provided for @busyOtherPhone.
  ///
  /// In zh, this message translates to:
  /// **'正连着其他手机：先在那台手机上解除绑定，或关掉它的蓝牙'**
  String get busyOtherPhone;

  /// No description provided for @boundOtherPhone.
  ///
  /// In zh, this message translates to:
  /// **'已绑定其他手机'**
  String get boundOtherPhone;

  /// No description provided for @oldFirmware.
  ///
  /// In zh, this message translates to:
  /// **'旧固件，状态未知'**
  String get oldFirmware;

  /// No description provided for @bind.
  ///
  /// In zh, this message translates to:
  /// **'绑定'**
  String get bind;

  /// No description provided for @nearest.
  ///
  /// In zh, this message translates to:
  /// **'最近'**
  String get nearest;

  /// No description provided for @server.
  ///
  /// In zh, this message translates to:
  /// **'服务器'**
  String get server;

  /// No description provided for @serverAddress.
  ///
  /// In zh, this message translates to:
  /// **'地址'**
  String get serverAddress;

  /// No description provided for @serverHint.
  ///
  /// In zh, this message translates to:
  /// **'http://100.x.y.z:8770 或 https://your.server'**
  String get serverHint;

  /// No description provided for @scanToSignIn.
  ///
  /// In zh, this message translates to:
  /// **'扫码登录'**
  String get scanToSignIn;

  /// No description provided for @deviceName.
  ///
  /// In zh, this message translates to:
  /// **'设备名'**
  String get deviceName;

  /// No description provided for @deviceNameHelp.
  ///
  /// In zh, this message translates to:
  /// **'取自系统设置里的手机名称；BLE 录音硬件的音频按硬件名上传'**
  String get deviceNameHelp;

  /// No description provided for @recordingSection.
  ///
  /// In zh, this message translates to:
  /// **'录音'**
  String get recordingSection;

  /// No description provided for @keepRecordings.
  ///
  /// In zh, this message translates to:
  /// **'本地保留录音'**
  String get keepRecordings;

  /// No description provided for @keepRecordingsIos.
  ///
  /// In zh, this message translates to:
  /// **'上传后存到「文件」App › 我的 iPhone › alldayrec › recordings › 日期'**
  String get keepRecordingsIos;

  /// No description provided for @keepRecordingsAndroid.
  ///
  /// In zh, this message translates to:
  /// **'上传后存到 Android/data/com.suyiiyii.live_record/files/recordings/日期/'**
  String get keepRecordingsAndroid;

  /// No description provided for @pieceLength.
  ///
  /// In zh, this message translates to:
  /// **'切片长度（服务端下发）'**
  String get pieceLength;

  /// No description provided for @piecesIos.
  ///
  /// In zh, this message translates to:
  /// **'前台 {foreground} 秒 · 后台 {background} 秒'**
  String piecesIos(Object foreground, Object background);

  /// No description provided for @piecesAndroid.
  ///
  /// In zh, this message translates to:
  /// **'前台 {foreground} 秒 · 后台 {background} 秒 · 熄屏 {screenOff} 秒'**
  String piecesAndroid(Object foreground, Object background, Object screenOff);

  /// No description provided for @save.
  ///
  /// In zh, this message translates to:
  /// **'保存'**
  String get save;

  /// No description provided for @notSignInQr.
  ///
  /// In zh, this message translates to:
  /// **'不是登录二维码，请在网页的「连接手机」里扫'**
  String get notSignInQr;

  /// No description provided for @onBattery.
  ///
  /// In zh, this message translates to:
  /// **'用电池'**
  String get onBattery;

  /// No description provided for @charging.
  ///
  /// In zh, this message translates to:
  /// **'充电中'**
  String get charging;

  /// No description provided for @charged.
  ///
  /// In zh, this message translates to:
  /// **'已充满'**
  String get charged;

  /// No description provided for @cardFullTile.
  ///
  /// In zh, this message translates to:
  /// **'已满'**
  String get cardFullTile;

  /// No description provided for @cardFull.
  ///
  /// In zh, this message translates to:
  /// **'存储卡已满'**
  String get cardFull;

  /// No description provided for @cardFullSub.
  ///
  /// In zh, this message translates to:
  /// **'离线录音已停止'**
  String get cardFullSub;

  /// No description provided for @cardUsbTile.
  ///
  /// In zh, this message translates to:
  /// **'U 盘中'**
  String get cardUsbTile;

  /// No description provided for @cardUsb.
  ///
  /// In zh, this message translates to:
  /// **'存储卡在 U 盘模式'**
  String get cardUsb;

  /// No description provided for @cardUsbSub.
  ///
  /// In zh, this message translates to:
  /// **'离线录音暂停'**
  String get cardUsbSub;

  /// No description provided for @cardNoneTile.
  ///
  /// In zh, this message translates to:
  /// **'没插卡'**
  String get cardNoneTile;

  /// No description provided for @cardNone.
  ///
  /// In zh, this message translates to:
  /// **'没插存储卡'**
  String get cardNone;

  /// No description provided for @cardNoneSub.
  ///
  /// In zh, this message translates to:
  /// **'没插卡或没插好：只能实时上传，手机不在旁边时录不下来'**
  String get cardNoneSub;

  /// No description provided for @cardErrorTile.
  ///
  /// In zh, this message translates to:
  /// **'出错'**
  String get cardErrorTile;

  /// No description provided for @cardError.
  ///
  /// In zh, this message translates to:
  /// **'存储卡出错'**
  String get cardError;

  /// No description provided for @cardErrorSub.
  ///
  /// In zh, this message translates to:
  /// **'重插或换一张卡；实时上传照常'**
  String get cardErrorSub;

  /// No description provided for @advBattery.
  ///
  /// In zh, this message translates to:
  /// **'电池 {volts} V'**
  String advBattery(Object volts);

  /// No description provided for @advCardOk.
  ///
  /// In zh, this message translates to:
  /// **'卡正常'**
  String get advCardOk;

  /// No description provided for @advCardFull.
  ///
  /// In zh, this message translates to:
  /// **'卡满'**
  String get advCardFull;

  /// No description provided for @advCardError.
  ///
  /// In zh, this message translates to:
  /// **'卡出错'**
  String get advCardError;

  /// No description provided for @cardFreeGb.
  ///
  /// In zh, this message translates to:
  /// **'卡剩 {gb} GB'**
  String cardFreeGb(Object gb);

  /// No description provided for @batteryPercent.
  ///
  /// In zh, this message translates to:
  /// **'电量 {percent}%'**
  String batteryPercent(Object percent);

  /// No description provided for @allowInstalls.
  ///
  /// In zh, this message translates to:
  /// **'在打开的设置里允许安装未知应用，回来再点这里'**
  String get allowInstalls;

  /// No description provided for @cannotOpenInstaller.
  ///
  /// In zh, this message translates to:
  /// **'没法打开安装界面：{error}'**
  String cannotOpenInstaller(Object error);

  /// No description provided for @downloadStalled.
  ///
  /// In zh, this message translates to:
  /// **'下载卡住了，点这里重试'**
  String get downloadStalled;

  /// No description provided for @downloadFailedRetry.
  ///
  /// In zh, this message translates to:
  /// **'下载失败：{error}，点这里重试'**
  String downloadFailedRetry(Object error);

  /// No description provided for @downloadingApp.
  ///
  /// In zh, this message translates to:
  /// **'正在下载新版本 {version} · {done} / {total} MB'**
  String downloadingApp(Object version, Object done, Object total);

  /// No description provided for @appUpdate.
  ///
  /// In zh, this message translates to:
  /// **'有新版本 {version}{size}，点这里更新'**
  String appUpdate(Object version, Object size);

  /// No description provided for @appUpdateSize.
  ///
  /// In zh, this message translates to:
  /// **'（{mb} MB）'**
  String appUpdateSize(Object mb);

  /// No description provided for @dontRemindVersion.
  ///
  /// In zh, this message translates to:
  /// **'这个版本不再提示'**
  String get dontRemindVersion;

  /// No description provided for @resume.
  ///
  /// In zh, this message translates to:
  /// **'继续'**
  String get resume;

  /// No description provided for @pause15Min.
  ///
  /// In zh, this message translates to:
  /// **'暂停 15 分钟'**
  String get pause15Min;

  /// No description provided for @oneHour.
  ///
  /// In zh, this message translates to:
  /// **'1 小时'**
  String get oneHour;

  /// No description provided for @pause1Hour.
  ///
  /// In zh, this message translates to:
  /// **'暂停 1 小时'**
  String get pause1Hour;

  /// No description provided for @linkScanning.
  ///
  /// In zh, this message translates to:
  /// **'正在找录音设备'**
  String get linkScanning;

  /// No description provided for @linkScanningName.
  ///
  /// In zh, this message translates to:
  /// **'正在找 {name}'**
  String linkScanningName(Object name);

  /// No description provided for @linkBluetoothOff.
  ///
  /// In zh, this message translates to:
  /// **'蓝牙已关闭'**
  String get linkBluetoothOff;

  /// No description provided for @linkNoPermission.
  ///
  /// In zh, this message translates to:
  /// **'没有蓝牙权限：在 设置 › alldayrec 里允许蓝牙'**
  String get linkNoPermission;

  /// No description provided for @linkUnbound.
  ///
  /// In zh, this message translates to:
  /// **'未绑定录音设备'**
  String get linkUnbound;

  /// No description provided for @linkKicked.
  ///
  /// In zh, this message translates to:
  /// **'已被其他手机绑定：在「录音设备」页重新添加'**
  String get linkKicked;

  /// No description provided for @linkElsewhere.
  ///
  /// In zh, this message translates to:
  /// **'{name} 正连着其他手机'**
  String linkElsewhere(Object name);

  /// No description provided for @linkBluetoothNotOn.
  ///
  /// In zh, this message translates to:
  /// **'蓝牙未打开，{s} 秒后重试'**
  String linkBluetoothNotOn(Object s);

  /// No description provided for @linkScanFailed.
  ///
  /// In zh, this message translates to:
  /// **'扫描失败 {err}，{s} 秒后重试'**
  String linkScanFailed(Object err, Object s);

  /// No description provided for @linkConnectFailed.
  ///
  /// In zh, this message translates to:
  /// **'连接失败，{s} 秒后重试'**
  String linkConnectFailed(Object s);

  /// No description provided for @linkAway.
  ///
  /// In zh, this message translates to:
  /// **'录音设备不在附近，它在离线录音，回来后自动补传'**
  String get linkAway;

  /// No description provided for @linkAwaySince.
  ///
  /// In zh, this message translates to:
  /// **'录音设备不在附近，它在离线录音（最后连接 {time}），回来后自动补传'**
  String linkAwaySince(Object time);

  /// No description provided for @linkConnecting.
  ///
  /// In zh, this message translates to:
  /// **'正在连接录音设备'**
  String get linkConnecting;

  /// No description provided for @linkStalePairing.
  ///
  /// In zh, this message translates to:
  /// **'这台 iPhone 还留着与 {name} 的旧配对：到 设置 › 蓝牙 › {name} › 忽略此设备，再回来（30 秒后自动重试）'**
  String linkStalePairing(Object name);

  /// No description provided for @linkMtu.
  ///
  /// In zh, this message translates to:
  /// **'已连接，协商包长'**
  String get linkMtu;

  /// No description provided for @linkPairing.
  ///
  /// In zh, this message translates to:
  /// **'正在配对'**
  String get linkPairing;

  /// No description provided for @linkPairingDialog.
  ///
  /// In zh, this message translates to:
  /// **'正在配对：在弹出的对话框里点「配对」'**
  String get linkPairingDialog;

  /// No description provided for @linkEncrypting.
  ///
  /// In zh, this message translates to:
  /// **'正在建立加密连接'**
  String get linkEncrypting;

  /// No description provided for @linkPairingFailed.
  ///
  /// In zh, this message translates to:
  /// **'配对没有完成，30 秒后重试'**
  String get linkPairingFailed;

  /// No description provided for @linkServices.
  ///
  /// In zh, this message translates to:
  /// **'读取设备服务'**
  String get linkServices;

  /// No description provided for @linkClock.
  ///
  /// In zh, this message translates to:
  /// **'给设备对时'**
  String get linkClock;

  /// No description provided for @linkStatus.
  ///
  /// In zh, this message translates to:
  /// **'读取设备状态'**
  String get linkStatus;

  /// No description provided for @linkForgetOthers.
  ///
  /// In zh, this message translates to:
  /// **'让录音设备忘掉其他手机'**
  String get linkForgetOthers;

  /// No description provided for @linkJoining.
  ///
  /// In zh, this message translates to:
  /// **'正在接上实时'**
  String get linkJoining;

  /// No description provided for @linkConnected.
  ///
  /// In zh, this message translates to:
  /// **'已连接 · 实时上传'**
  String get linkConnected;

  /// No description provided for @linkError.
  ///
  /// In zh, this message translates to:
  /// **'录音设备出错：{detail}'**
  String linkError(Object detail);

  /// No description provided for @lastSynced.
  ///
  /// In zh, this message translates to:
  /// **'{time} 补传录音设备的录音 {kb} KB'**
  String lastSynced(Object time, Object kb);

  /// No description provided for @usbSwitchingOn.
  ///
  /// In zh, this message translates to:
  /// **'正在切换到 U 盘模式'**
  String get usbSwitchingOn;

  /// No description provided for @usbSwitchingOff.
  ///
  /// In zh, this message translates to:
  /// **'正在切回录音'**
  String get usbSwitchingOff;

  /// No description provided for @usbNoComputer.
  ///
  /// In zh, this message translates to:
  /// **'录音设备没接在电脑的 USB 上'**
  String get usbNoComputer;

  /// No description provided for @usbFailed.
  ///
  /// In zh, this message translates to:
  /// **'切换失败 {err}'**
  String usbFailed(Object err);

  /// No description provided for @usbNotYetOn.
  ///
  /// In zh, this message translates to:
  /// **'还没切到 U 盘模式（录音设备没有及时回应，卡慢时会晚一些；开关跟着录音设备的状态）'**
  String get usbNotYetOn;

  /// No description provided for @usbStillOn.
  ///
  /// In zh, this message translates to:
  /// **'还在 U 盘模式（录音设备没有及时回应，卡慢时会晚一些；开关跟着录音设备的状态）'**
  String get usbStillOn;

  /// No description provided for @usbOn.
  ///
  /// In zh, this message translates to:
  /// **'U 盘模式：电脑上出现 OMI 盘（约 20 秒）；用完先在电脑上推出，再切回'**
  String get usbOn;

  /// No description provided for @noteRejected.
  ///
  /// In zh, this message translates to:
  /// **'服务端无法解码一块，已丢弃'**
  String get noteRejected;

  /// No description provided for @noteUploadFailed.
  ///
  /// In zh, this message translates to:
  /// **'上传失败 {detail}'**
  String noteUploadFailed(Object detail);

  /// No description provided for @noteEndFailed.
  ///
  /// In zh, this message translates to:
  /// **'结束信号失败 {detail}'**
  String noteEndFailed(Object detail);

  /// No description provided for @noteArchiveFailed.
  ///
  /// In zh, this message translates to:
  /// **'归档失败 {detail}'**
  String noteArchiveFailed(Object detail);

  /// No description provided for @noteKilled.
  ///
  /// In zh, this message translates to:
  /// **'上次录音被系统终止，未传的 {count} 块正在补传，需要手动重新开始'**
  String noteKilled(Object count);

  /// No description provided for @micInterrupted.
  ///
  /// In zh, this message translates to:
  /// **'{time} 起被系统中断，等待恢复'**
  String micInterrupted(Object time);

  /// No description provided for @micRouteFailed.
  ///
  /// In zh, this message translates to:
  /// **'{time} 起没有录音：切换输入后恢复失败（{detail}），回到前台时重试'**
  String micRouteFailed(Object time, Object detail);

  /// No description provided for @micResumeFailed.
  ///
  /// In zh, this message translates to:
  /// **'{time} 起没有录音：中断后恢复失败（{detail}），回到前台时重试'**
  String micResumeFailed(Object time, Object detail);

  /// No description provided for @dfuPreparing.
  ///
  /// In zh, this message translates to:
  /// **'准备升级'**
  String get dfuPreparing;

  /// No description provided for @dfuRebooting.
  ///
  /// In zh, this message translates to:
  /// **'录音设备重启进入升级模式'**
  String get dfuRebooting;

  /// No description provided for @dfuStarting.
  ///
  /// In zh, this message translates to:
  /// **'开始升级'**
  String get dfuStarting;

  /// No description provided for @dfuProgress.
  ///
  /// In zh, this message translates to:
  /// **'升级中 {pct}% · {kbps} KB/s'**
  String dfuProgress(Object pct, Object kbps);

  /// No description provided for @dfuDone.
  ///
  /// In zh, this message translates to:
  /// **'升级完成，录音设备已重启'**
  String get dfuDone;

  /// No description provided for @dfuAborted.
  ///
  /// In zh, this message translates to:
  /// **'升级已取消'**
  String get dfuAborted;

  /// No description provided for @dfuFailed.
  ///
  /// In zh, this message translates to:
  /// **'升级失败：{why}（录音设备还在就再试一次，不见了就插 USB 用 lr.sh flash 刷）'**
  String dfuFailed(Object why);

  /// No description provided for @dfuNoBluetooth.
  ///
  /// In zh, this message translates to:
  /// **'蓝牙不可用'**
  String get dfuNoBluetooth;

  /// No description provided for @dfuUnknown.
  ///
  /// In zh, this message translates to:
  /// **'这台 iPhone 不认识录音设备'**
  String get dfuUnknown;

  /// No description provided for @dfuNoConnect.
  ///
  /// In zh, this message translates to:
  /// **'连不上录音设备'**
  String get dfuNoConnect;

  /// No description provided for @dfuNoService.
  ///
  /// In zh, this message translates to:
  /// **'录音设备上没有升级服务'**
  String get dfuNoService;

  /// No description provided for @dfuNoReboot.
  ///
  /// In zh, this message translates to:
  /// **'录音设备没有重启'**
  String get dfuNoReboot;

  /// No description provided for @dfuRefused.
  ///
  /// In zh, this message translates to:
  /// **'录音设备拒绝进入升级模式（{detail}）'**
  String dfuRefused(Object detail);

  /// No description provided for @dfuDropped.
  ///
  /// In zh, this message translates to:
  /// **'录音设备断开了'**
  String get dfuDropped;

  /// No description provided for @dfuNoBootloader.
  ///
  /// In zh, this message translates to:
  /// **'录音设备没有进入升级模式'**
  String get dfuNoBootloader;

  /// No description provided for @errPermission.
  ///
  /// In zh, this message translates to:
  /// **'允许蓝牙权限后再点开始'**
  String get errPermission;

  /// No description provided for @errNotStarted.
  ///
  /// In zh, this message translates to:
  /// **'先开始录音，连上录音设备'**
  String get errNotStarted;

  /// No description provided for @errNotConnected.
  ///
  /// In zh, this message translates to:
  /// **'录音设备没连上'**
  String get errNotConnected;

  /// No description provided for @errListen.
  ///
  /// In zh, this message translates to:
  /// **'没法播放：{detail}'**
  String errListen(Object detail);

  /// No description provided for @errUpdating.
  ///
  /// In zh, this message translates to:
  /// **'正在升级'**
  String get errUpdating;

  /// No description provided for @errLinked.
  ///
  /// In zh, this message translates to:
  /// **'录音设备的连接还没断开，稍后再试'**
  String get errLinked;

  /// No description provided for @errNoModel.
  ///
  /// In zh, this message translates to:
  /// **'还没连上过录音设备，不知道它的型号'**
  String get errNoModel;

  /// No description provided for @errModel.
  ///
  /// In zh, this message translates to:
  /// **'这个固件是 {want} 的，录音设备是 {model}，不能刷'**
  String errModel(Object want, Object model);

  /// No description provided for @errSame.
  ///
  /// In zh, this message translates to:
  /// **'录音设备已经是 {version}，不用再升级'**
  String errSame(Object version);

  /// No description provided for @errSha256.
  ///
  /// In zh, this message translates to:
  /// **'固件下载不完整，再试一次'**
  String get errSha256;

  /// No description provided for @errZip.
  ///
  /// In zh, this message translates to:
  /// **'固件包打不开：{detail}'**
  String errZip(Object detail);
}

class _AppLocalizationsDelegate
    extends LocalizationsDelegate<AppLocalizations> {
  const _AppLocalizationsDelegate();

  @override
  Future<AppLocalizations> load(Locale locale) {
    return SynchronousFuture<AppLocalizations>(lookupAppLocalizations(locale));
  }

  @override
  bool isSupported(Locale locale) =>
      <String>['en', 'zh'].contains(locale.languageCode);

  @override
  bool shouldReload(_AppLocalizationsDelegate old) => false;
}

AppLocalizations lookupAppLocalizations(Locale locale) {
  // Lookup logic when only language code is specified.
  switch (locale.languageCode) {
    case 'en':
      return AppLocalizationsEn();
    case 'zh':
      return AppLocalizationsZh();
  }

  throw FlutterError(
    'AppLocalizations.delegate failed to load unsupported locale "$locale". This is likely '
    'an issue with the localizations generation tool. Please file an issue '
    'on GitHub with a reproducible sample app and the gen-l10n configuration '
    'that was used.',
  );
}
