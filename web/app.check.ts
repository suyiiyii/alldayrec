// node --experimental-strip-types app.check.ts: platformOf and stage (src/lib/app.ts)
import assert from "node:assert/strict";
import { platformOf, stage } from "./src/lib/app.ts";

const android =
  "Mozilla/5.0 (Linux; Android 14; K; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0 Mobile Safari/537.36";
const ios =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148";
assert.equal(platformOf(`${android} AllDayRecApp/1.6.5+42 (android)`), "android");
assert.equal(platformOf(`${ios} AllDayRecApp/1.6.5+42 (ios)`), "ios");
assert.equal(platformOf(android), "android"); // an older app: its WebView's own mark
assert.equal(platformOf(ios), null); // an older iOS app, or Safari
assert.equal(
  platformOf(
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.1 Safari/605.1.15",
  ),
  null,
);

// (talked, recorders, in the app)
assert.equal(stage(false, 0, false), "connect"); // a browser, nothing sent yet: install the app, scan
assert.equal(stage(false, 0, true), "record"); // the app is in and signed in: add a recorder or record with the phone
assert.equal(stage(false, 1, false), "speak"); // a phone has sent audio: no install or scan again in a browser
assert.equal(stage(false, 2, true), "speak");
assert.equal(stage(true, 0, false), null); // a conversation is in: the feed
assert.equal(stage(true, 1, true), null);
console.log("app ok");
