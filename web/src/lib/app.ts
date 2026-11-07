// The AllDayRec app around the page. Its WebViews end their user agent with `AllDayRecApp/<version> (android|ios)`
// (home.dart); an older Android app's says only `; wv)` (its WebView's own mark), an older iOS app's nothing.
export const platformOf = (ua: string): "android" | "ios" | null =>
  (/AllDayRecApp\/\S+ \((android|ios)\)/.exec(ua)?.[1] as "android" | "ios" | undefined) ??
  (/; wv\)/.test(ua) ? "android" : null);
export const inApp = platformOf(navigator.userAgent);

// The app's own pages, opened from a page in it through its WebViews' AllDayRec channel (home.dart: the same apps that
// add AllDayRecApp to the user agent). add-recorder: 添加录音设备; record: this phone's microphone starts, its panel opens
type Native = "add-recorder" | "record";
const channel = () => (globalThis as { AllDayRec?: { postMessage(m: string): void } }).AllDayRec;
export const hasNative = () => !!channel();
export const openNative = (page: Native) => channel()?.postMessage(page);

// What a new user does next, from what the server has seen; null once they have a conversation (or one goes on).
// connect: no device has sent audio, in a browser: install the app, scan the QR on 连接手机 (/connect).
// record: the same, in the app (installed and signed in, or it would show no page): add a recorder, or record with the phone.
// speak: some device has sent audio (a phone or a recorder: /api/status recorders), no conversation yet: say something,
// watch it come up on 实时
export type Stage = "connect" | "record" | "speak";
export const stage = (talked: boolean, recorders: number, app: boolean): Stage | null =>
  talked ? null : recorders ? "speak" : app ? "record" : "connect";
