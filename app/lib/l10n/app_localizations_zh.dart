// ignore: unused_import
import 'package:intl/intl.dart' as intl;

import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for Chinese (`zh`).
class AppLocalizationsZh extends AppLocalizations {
  AppLocalizationsZh([String locale = 'zh']) : super(locale);

  @override
  String get tabTimeline => '时间线';

  @override
  String get tabLive => '实时';

  @override
  String get tabPeople => '人物';

  @override
  String get tabMe => '我的';

  @override
  String get savedNextRecording => '已保存，下次开始录音时生效';

  @override
  String get saved => '已保存';

  @override
  String get noMicPermission => '没有麦克风权限';

  @override
  String cannotStartRecording(Object message) {
    return '无法开始录音 $message';
  }

  @override
  String get refresh => '刷新';

  @override
  String get settings => '设置';

  @override
  String get recorder => '录音设备';

  @override
  String get notSignedIn => '还没登录';

  @override
  String get deviceStatusAndMore => '设备状态与更多';

  @override
  String get deviceStatusAndMoreSub => '电量、存储卡、处理进度、接入 AI';

  @override
  String get upload => '上传';

  @override
  String get about => '关于';

  @override
  String get catchingUp => '补传中';

  @override
  String get notConnected => '未连接';

  @override
  String get connected => '已连接';

  @override
  String get stopping => '停止中';

  @override
  String get notRecording => '未在录音';

  @override
  String statePaused(Object minutes) {
    return '已暂停 · $minutes 分钟后继续';
  }

  @override
  String stateCatchingUp(Object time) {
    return '补传中 · 还剩约 $time';
  }

  @override
  String get stateAway => '录音设备不在附近';

  @override
  String stateAwaySince(Object time) {
    return '录音设备不在附近 · 上次 $time 在线';
  }

  @override
  String get statePausedAway => '已暂停 · 不在附近';

  @override
  String statePausedAwaySince(Object time) {
    return '已暂停 · 不在附近（上次 $time 在线）';
  }

  @override
  String get awayHelp => '它照常录到自己的卡里，回到手机附近后自动补传';

  @override
  String get phoneMic => '手机麦克风';

  @override
  String get backlog => '待补传';

  @override
  String get allSynced => '已同步';

  @override
  String aboutTime(Object time) {
    return '约 $time';
  }

  @override
  String get signal => '信号';

  @override
  String get signalStrong => '强';

  @override
  String get signalFair => '中';

  @override
  String get signalWeak => '弱';

  @override
  String get deviceDetails => '设备详情';

  @override
  String get starting => '正在启动';

  @override
  String piecesPending(Object count) {
    return '待传 $count 块';
  }

  @override
  String waitingLastPieces(Object count) {
    return '等最后 $count 块传完';
  }

  @override
  String uploadingLeftover(Object count) {
    return '正在补传 $count 块';
  }

  @override
  String recordingError(Object error) {
    return '录音错误 $error';
  }

  @override
  String interruptedRecovered(Object time) {
    return '$time 被系统中断过，已恢复';
  }

  @override
  String get stop => '停止';

  @override
  String get startRecording => '开始录音';

  @override
  String get emptyNotSignedIn => '扫码登录或填好 Token 后显示';

  @override
  String get goToSettings => '去设置';

  @override
  String unbindTitle(Object name) {
    return '解除绑定 $name？';
  }

  @override
  String get unbindLinkedBody => '录音设备会忘掉所有手机、停止录音（卡上的录音已取回）。这台手机改用手机麦克风。';

  @override
  String get unbindAwayBody =>
      '录音设备不在附近：只在这台手机上解除，它自己还记着绑定、会继续录音。以后可以在添加时强制绑定。';

  @override
  String unbindIosForget(Object name) {
    return 'iPhone 自己的配对记录 App 删不掉：之后到 设置 › 蓝牙 › $name › 忽略此设备；以后要在这台 iPhone 上再绑定它，必须先忽略。';
  }

  @override
  String get cancel => '取消';

  @override
  String get unbind => '解除绑定';

  @override
  String get connectedCatchingUp => '已连接 · 补传中';

  @override
  String get syncing => '同步中';

  @override
  String get connecting => '连接中';

  @override
  String cardFreeOf(Object total) {
    return '可用，共 $total';
  }

  @override
  String get addRecorder => '添加录音设备';

  @override
  String get addAgain => '重新添加';

  @override
  String get notBound => '未绑定';

  @override
  String get recorderUnboundHelp =>
      '录音设备绑定到这台手机后开始录音：手机在附近时实时上传，不在时存进它的卡里，回来后在后台自动补传，实时上传照常。没绑定时它不录音，现在用的是手机麦克风。';

  @override
  String get kickedChip => '已被其他手机绑定';

  @override
  String get kickedHelp => '另一台手机绑定了这台录音设备，这台手机不再连它。要拿回来，就重新添加（原来那台手机会被顶掉）。';

  @override
  String kickedIosForget(Object name) {
    return '重新添加前，先到 iPhone 设置 › 蓝牙 › $name › 忽略此设备。';
  }

  @override
  String get recorderHelp =>
      '录音设备开着就一直在录：手机在附近时实时上传，不在时存进它的卡里，回来后在后台自动补传，实时上传照常。需要时可以暂停。';

  @override
  String get pauseRecording => '暂停录音';

  @override
  String get pauseNeedsLink => '连上录音设备后可以暂停';

  @override
  String pausedResumesIn(Object minutes) {
    return '已暂停，还剩 $minutes 分钟，到时自动继续';
  }

  @override
  String get recordingNow => '正在录音';

  @override
  String get liveListening => '实时监听';

  @override
  String get listenOnPhone => '在这台手机上听';

  @override
  String get listenNeedsLink => '连上录音设备后可以听';

  @override
  String get listenHelp =>
      '录音设备的声音直接在手机上播放，不经服务器，慢约半秒，离开 App 自动关闭。建议戴耳机，外放会被麦克风收回去';

  @override
  String get battery => '电量';

  @override
  String get card => '存储卡';

  @override
  String get offlineSync => '离线录音同步';

  @override
  String get fetchingCard => '正在取回卡上的录音';

  @override
  String get syncLiveHelp => '实时上传照常，卡上积压的录音在后台补传；补完前服务端定稿先停在积压处，初稿和实时分析照常';

  @override
  String syncFirstHelp(Object left) {
    return '正在录的这段取完（还差 $left）就开始实时上传，更早的录音之后在后台补传';
  }

  @override
  String get syncIdleHelp => '连上录音设备时在后台自动取回它离线录下的音频，不耽误实时上传';

  @override
  String get usbDiskMode => 'U 盘模式';

  @override
  String get cardAsUsbDisk => '存储卡当 U 盘';

  @override
  String get usbDiskHelp => '录音设备插在电脑上时可用，约 20 秒后出现 OMI 盘；期间离线录音暂停';

  @override
  String get ejectFirst => '切回前先在电脑上推出 OMI 盘';

  @override
  String get deviceManagement => '设备管理';

  @override
  String get unbindAfterSync => '正在取回卡上的录音，取完再解除';

  @override
  String get unbindSub => '录音设备停止录音，这台手机改用手机麦克风';

  @override
  String get listenWaiting => '等录音设备的声音和对时……';

  @override
  String get listenTime => '时间';

  @override
  String get listenLevel => '电平';

  @override
  String get listenHum => '哼声';

  @override
  String get listenZeros => '全0';

  @override
  String get listenClipped => '满幅';

  @override
  String get listenLost => '丢失';

  @override
  String get listenDelay => '延迟';

  @override
  String listenLegend(Object queued) {
    return '每秒一行。电平、哼声（50 Hz 及谐波）：dBFS；全0：连续 10 ms 以上全是 0 的毫秒数（麦克风没数据）；满幅：削顶的样本数；丢失：蓝牙丢掉的毫秒数；延迟：从录音设备的麦克风到手机输出的毫秒数，其中 $queued 是手机上的缓冲';
  }

  @override
  String get firmwareUpdate => '固件更新';

  @override
  String get downloadingFirmware => '正在下载固件';

  @override
  String downloadFailed(Object error) {
    return '下载失败：$error';
  }

  @override
  String get updateFirmware => '升级固件';

  @override
  String get unknown => '未知';

  @override
  String modelMatches(Object model) {
    return '型号一致：$model';
  }

  @override
  String modelMismatch(Object model, Object firmwareModel) {
    return '型号不符：录音设备是 $model，固件是 $firmwareModel，不能升级';
  }

  @override
  String get unplugUsbFirst => '先拔掉录音设备的 USB：插着 USB 升级会卡在擦除';

  @override
  String get keepBattery => '电量保持在 30% 以上';

  @override
  String keepBatteryNow(Object battery) {
    return '电量保持在 30% 以上（现在 $battery%）';
  }

  @override
  String get updateTakesMinutes => '升级要几分钟：手机放在录音设备旁边，别关 App；期间不录音也不同步';

  @override
  String get updateFailedHelp =>
      '中途失败、录音设备不见了：插 USB 用 lr.sh flash 刷（升级模式下的录音设备换了地址，再点升级连不上）';

  @override
  String get startUpdate => '开始升级';

  @override
  String seconds(Object seconds) {
    return '$seconds 秒';
  }

  @override
  String minutesSeconds(Object minutes, Object seconds) {
    return '$minutes 分 $seconds 秒';
  }

  @override
  String get checking => '检查中';

  @override
  String get updateAvailable => '有新版本';

  @override
  String get upToDate => '已是最新';

  @override
  String get noFirmware => '没有固件';

  @override
  String get version => '版本';

  @override
  String get currentVersion => '当前版本';

  @override
  String get versionNotKnownYet => '还不知道：连上录音设备后读取';

  @override
  String get latestVersion => '最新版本';

  @override
  String releasedAt(Object when) {
    return '发布于 $when';
  }

  @override
  String lookupFailed(Object error) {
    return '查不到：$error';
  }

  @override
  String get askingServer => '正在查服务器';

  @override
  String noFirmwareFor(Object model) {
    return '服务器上还没有 $model 的固件';
  }

  @override
  String get lrP1UsbOnly =>
      'LR-P1 只能插 USB 升级：lr.sh flash promicro（蓝牙升级中途一断，只能插 USB 救回）';

  @override
  String get updateToLatest => '升级到最新版本';

  @override
  String get alreadyLatest => '已是最新版本';

  @override
  String get changelog => '更新日志';

  @override
  String get noReleases => '还没有发布过的版本';

  @override
  String get tagLatest => '最新';

  @override
  String get tagCurrent => '当前';

  @override
  String get noNotes => '没有说明';

  @override
  String get recentUpdates => '最近的升级';

  @override
  String get noUpdates90Days => '最近 90 天没有升级过';

  @override
  String get succeeded => '成功';

  @override
  String get failed => '失败';

  @override
  String get noResult => '没有结果';

  @override
  String took(Object duration) {
    return '用时 $duration';
  }

  @override
  String serverNoTask(Object device) {
    return '服务端还没有 $device 的处理任务';
  }

  @override
  String serverStatusFailed(Object error) {
    return '取不到服务端状态：$error';
  }

  @override
  String hoursMinutes(Object hours, Object minutes) {
    return '$hours 小时 $minutes 分';
  }

  @override
  String minutes(Object minutes) {
    return '$minutes 分钟';
  }

  @override
  String get recordingUploading => '录音中，边录边传';

  @override
  String get uploading => '正在上传';

  @override
  String get waitingToUpload => '等待上传';

  @override
  String get allUploaded => '已全部上传';

  @override
  String get toUpload => '待传';

  @override
  String pieces(Object count) {
    return '$count 块';
  }

  @override
  String get noBacklog => '没有积压';

  @override
  String pendingSub(Object size, Object when) {
    return '$size · 最早录于 $when';
  }

  @override
  String get sentThisRun => '本次已传';

  @override
  String get sinceStart => '这次开机以来';

  @override
  String get uploadNow => '立即上传';

  @override
  String get serverProcessing => '服务端处理';

  @override
  String get loading => '加载中…';

  @override
  String get caughtUp => '已追上，录音都已转写';

  @override
  String serverBehind(Object span, Object queued) {
    return '还有 $span 录音待处理 · 排队 $queued 块';
  }

  @override
  String finalsHeld(Object when, Object backlog) {
    return '定稿停在 $when，等手机在后台补传卡里更早的录音$backlog，补完再接着定稿；初稿和实时分析照常';
  }

  @override
  String backlogLeft(Object size) {
    return '（还有 $size）';
  }

  @override
  String backlogLeftEta(Object size, Object eta) {
    return '（还有 $size，约 $eta）';
  }

  @override
  String etaDone(Object span) {
    return '预计约 $span 后完成';
  }

  @override
  String get received => '已收到';

  @override
  String get finalized => '已定稿';

  @override
  String bindTitle(Object name) {
    return '绑定 $name？';
  }

  @override
  String get bindForceBody => '它已经绑定了别的手机。绑定到这台手机后，原来的手机将无法再连接它。';

  @override
  String get forceBind => '强制绑定';

  @override
  String get allow => '允许';

  @override
  String get bluetoothOff => '蓝牙没有打开：打开后这里会开始找录音设备';

  @override
  String get permissions => '权限';

  @override
  String get permBluetooth => '蓝牙';

  @override
  String get permNearbyDevices => '附近的设备';

  @override
  String get permBluetoothWhy => '找到并连接录音设备';

  @override
  String get permNotifications => '通知';

  @override
  String get permNotificationsWhy => '后台连接录音设备时要显示一条通知';

  @override
  String get permBackground => '后台运行';

  @override
  String get permBackgroundWhy => '不受电池优化限制，录音设备才能整天连着';

  @override
  String get addRecorderHelp =>
      '打开录音设备（插上充电线即可）。没绑定的录音设备蓝灯慢闪，名字印在它身上，或看标着「最近」的那台。';

  @override
  String get searchingNearby => '正在找附近的录音设备…';

  @override
  String get signalNear => '就在旁边';

  @override
  String get signalMid => '附近';

  @override
  String get signalFar => '较远';

  @override
  String get signalLost => '信号弱，暂时找不到';

  @override
  String get busyOtherPhone => '正连着其他手机：先在那台手机上解除绑定，或关掉它的蓝牙';

  @override
  String get boundOtherPhone => '已绑定其他手机';

  @override
  String get oldFirmware => '旧固件，状态未知';

  @override
  String get bind => '绑定';

  @override
  String get nearest => '最近';

  @override
  String get server => '服务器';

  @override
  String get serverAddress => '地址';

  @override
  String get serverHint => 'http://100.x.y.z:8770 或 https://your.server';

  @override
  String get scanToSignIn => '扫码登录';

  @override
  String get deviceName => '设备名';

  @override
  String get deviceNameHelp => '取自系统设置里的手机名称；BLE 录音硬件的音频按硬件名上传';

  @override
  String get recordingSection => '录音';

  @override
  String get keepRecordings => '本地保留录音';

  @override
  String get keepRecordingsIos =>
      '上传后存到「文件」App › 我的 iPhone › alldayrec › recordings › 日期';

  @override
  String get keepRecordingsAndroid =>
      '上传后存到 Android/data/com.suyiiyii.live_record/files/recordings/日期/';

  @override
  String get pieceLength => '切片长度（服务端下发）';

  @override
  String piecesIos(Object foreground, Object background) {
    return '前台 $foreground 秒 · 后台 $background 秒';
  }

  @override
  String piecesAndroid(Object foreground, Object background, Object screenOff) {
    return '前台 $foreground 秒 · 后台 $background 秒 · 熄屏 $screenOff 秒';
  }

  @override
  String get save => '保存';

  @override
  String get notSignInQr => '不是登录二维码，请在网页的「连接手机」里扫';

  @override
  String get onBattery => '用电池';

  @override
  String get charging => '充电中';

  @override
  String get charged => '已充满';

  @override
  String caseBattery(Object percent) {
    return '充电仓 $percent%';
  }

  @override
  String get cardFullTile => '已满';

  @override
  String get cardFull => '存储卡已满';

  @override
  String get cardFullSub => '离线录音已停止';

  @override
  String get cardUsbTile => 'U 盘中';

  @override
  String get cardUsb => '存储卡在 U 盘模式';

  @override
  String get cardUsbSub => '离线录音暂停';

  @override
  String get cardNoneTile => '没插卡';

  @override
  String get cardNone => '没插存储卡';

  @override
  String get cardNoneSub => '没插卡或没插好：只能实时上传，手机不在旁边时录不下来';

  @override
  String get cardErrorTile => '出错';

  @override
  String get cardError => '存储卡出错';

  @override
  String get cardErrorSub => '重插或换一张卡；实时上传照常';

  @override
  String advBattery(Object volts) {
    return '电池 $volts V';
  }

  @override
  String get advCardOk => '卡正常';

  @override
  String get advCardFull => '卡满';

  @override
  String get advCardError => '卡出错';

  @override
  String cardFreeGb(Object gb) {
    return '卡剩 $gb GB';
  }

  @override
  String batteryPercent(Object percent) {
    return '电量 $percent%';
  }

  @override
  String get allowInstalls => '在打开的设置里允许安装未知应用，回来再点这里';

  @override
  String cannotOpenInstaller(Object error) {
    return '没法打开安装界面：$error';
  }

  @override
  String get downloadStalled => '下载卡住了，点这里重试';

  @override
  String downloadFailedRetry(Object error) {
    return '下载失败：$error，点这里重试';
  }

  @override
  String downloadingApp(Object version, Object done, Object total) {
    return '正在下载新版本 $version · $done / $total MB';
  }

  @override
  String appUpdate(Object version, Object size) {
    return '有新版本 $version$size，点这里更新';
  }

  @override
  String appUpdateSize(Object mb) {
    return '（$mb MB）';
  }

  @override
  String get dontRemindVersion => '这个版本不再提示';

  @override
  String get resume => '继续';

  @override
  String get pause15Min => '暂停 15 分钟';

  @override
  String get oneHour => '1 小时';

  @override
  String get pause1Hour => '暂停 1 小时';

  @override
  String get linkScanning => '正在找录音设备';

  @override
  String linkScanningName(Object name) {
    return '正在找 $name';
  }

  @override
  String get linkBluetoothOff => '蓝牙已关闭';

  @override
  String get linkNoPermission => '没有蓝牙权限：在 设置 › alldayrec 里允许蓝牙';

  @override
  String get linkUnbound => '未绑定录音设备';

  @override
  String get linkKicked => '已被其他手机绑定：在「录音设备」页重新添加';

  @override
  String linkElsewhere(Object name) {
    return '$name 正连着其他手机';
  }

  @override
  String linkBluetoothNotOn(Object s) {
    return '蓝牙未打开，$s 秒后重试';
  }

  @override
  String linkScanFailed(Object err, Object s) {
    return '扫描失败 $err，$s 秒后重试';
  }

  @override
  String linkConnectFailed(Object s) {
    return '连接失败，$s 秒后重试';
  }

  @override
  String get linkAway => '录音设备不在附近，它在离线录音，回来后自动补传';

  @override
  String linkAwaySince(Object time) {
    return '录音设备不在附近，它在离线录音（最后连接 $time），回来后自动补传';
  }

  @override
  String get linkConnecting => '正在连接录音设备';

  @override
  String linkStalePairing(Object name) {
    return '这台 iPhone 还留着与 $name 的旧配对：到 设置 › 蓝牙 › $name › 忽略此设备，再回来（30 秒后自动重试）';
  }

  @override
  String get linkMtu => '已连接，协商包长';

  @override
  String get linkPairing => '正在配对';

  @override
  String get linkPairingDialog => '正在配对：在弹出的对话框里点「配对」';

  @override
  String get linkEncrypting => '正在建立加密连接';

  @override
  String get linkPairingFailed => '配对没有完成，30 秒后重试';

  @override
  String get linkServices => '读取设备服务';

  @override
  String get linkClock => '给设备对时';

  @override
  String get linkStatus => '读取设备状态';

  @override
  String get linkForgetOthers => '让录音设备忘掉其他手机';

  @override
  String get linkJoining => '正在接上实时';

  @override
  String get linkConnected => '已连接 · 实时上传';

  @override
  String get linkBeanIdle => '录音豆已连上 · 没在录音';

  @override
  String get beanFeishuBound => '已绑定飞书';

  @override
  String get beanFeishuNotBound => '未绑定飞书';

  @override
  String get beanRecorderHelp =>
      '手机连上录音豆就让它开始录音，按一下按键停止。手机在附近时边录边传。手机不在时录的，按键停下后补传。服务端收全的录音会从豆子上删掉。';

  @override
  String get beanFetching => '正在取回录音豆里的录音';

  @override
  String get beanSyncHelp => '录音豆没在录音时，取回它录下而这台手机还没有的部分';

  @override
  String get beanUnbindSub => '这台手机不再连它，改用手机麦克风；录音豆本身什么也不改';

  @override
  String get beanUnbindAfterSync => '正在取回录音豆里的录音，取完再解除';

  @override
  String get beanUnbindBody =>
      '只是这台手机忘掉这颗录音豆、改用手机麦克风。录音豆本身什么也不改：按键照样录音，飞书照常能连。还没取回的录音留在豆子里。';

  @override
  String linkError(Object detail) {
    return '录音设备出错：$detail';
  }

  @override
  String lastSynced(Object time, Object kb) {
    return '$time 补传录音设备的录音 $kb KB';
  }

  @override
  String get usbSwitchingOn => '正在切换到 U 盘模式';

  @override
  String get usbSwitchingOff => '正在切回录音';

  @override
  String get usbNoComputer => '录音设备没接在电脑的 USB 上';

  @override
  String usbFailed(Object err) {
    return '切换失败 $err';
  }

  @override
  String get usbNotYetOn => '还没切到 U 盘模式（录音设备没有及时回应，卡慢时会晚一些；开关跟着录音设备的状态）';

  @override
  String get usbStillOn => '还在 U 盘模式（录音设备没有及时回应，卡慢时会晚一些；开关跟着录音设备的状态）';

  @override
  String get usbOn => 'U 盘模式：电脑上出现 OMI 盘（约 20 秒）；用完先在电脑上推出，再切回';

  @override
  String get noteRejected => '服务端无法解码一块，已丢弃';

  @override
  String noteUploadFailed(Object detail) {
    return '上传失败 $detail';
  }

  @override
  String noteEndFailed(Object detail) {
    return '结束信号失败 $detail';
  }

  @override
  String noteArchiveFailed(Object detail) {
    return '归档失败 $detail';
  }

  @override
  String noteKilled(Object count) {
    return '上次录音被系统终止，未传的 $count 块正在补传，需要手动重新开始';
  }

  @override
  String micInterrupted(Object time) {
    return '$time 起被系统中断，等待恢复';
  }

  @override
  String micRouteFailed(Object time, Object detail) {
    return '$time 起没有录音：切换输入后恢复失败（$detail），回到前台时重试';
  }

  @override
  String micResumeFailed(Object time, Object detail) {
    return '$time 起没有录音：中断后恢复失败（$detail），回到前台时重试';
  }

  @override
  String get dfuPreparing => '准备升级';

  @override
  String get dfuRebooting => '录音设备重启进入升级模式';

  @override
  String get dfuStarting => '开始升级';

  @override
  String dfuProgress(Object pct, Object kbps) {
    return '升级中 $pct% · $kbps KB/s';
  }

  @override
  String get dfuDone => '升级完成，录音设备已重启';

  @override
  String get dfuAborted => '升级已取消';

  @override
  String dfuFailed(Object why) {
    return '升级失败：$why（录音设备还在就再试一次，不见了就插 USB 用 lr.sh flash 刷）';
  }

  @override
  String get dfuNoBluetooth => '蓝牙不可用';

  @override
  String get dfuUnknown => '这台 iPhone 不认识录音设备';

  @override
  String get dfuNoConnect => '连不上录音设备';

  @override
  String get dfuNoService => '录音设备上没有升级服务';

  @override
  String get dfuNoReboot => '录音设备没有重启';

  @override
  String dfuRefused(Object detail) {
    return '录音设备拒绝进入升级模式（$detail）';
  }

  @override
  String get dfuDropped => '录音设备断开了';

  @override
  String get dfuNoBootloader => '录音设备没有进入升级模式';

  @override
  String get errPermission => '允许蓝牙权限后再点开始';

  @override
  String get errNotStarted => '先开始录音，连上录音设备';

  @override
  String get errNotConnected => '录音设备没连上';

  @override
  String errListen(Object detail) {
    return '没法播放：$detail';
  }

  @override
  String get errUpdating => '正在升级';

  @override
  String get errLinked => '录音设备的连接还没断开，稍后再试';

  @override
  String get errNoModel => '还没连上过录音设备，不知道它的型号';

  @override
  String errModel(Object want, Object model) {
    return '这个固件是 $want 的，录音设备是 $model，不能刷';
  }

  @override
  String errSame(Object version) {
    return '录音设备已经是 $version，不用再升级';
  }

  @override
  String get errSha256 => '固件下载不完整，再试一次';

  @override
  String errZip(Object detail) {
    return '固件包打不开：$detail';
  }
}
