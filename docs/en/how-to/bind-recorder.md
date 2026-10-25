---
type: how-to
description: "Bind a BLE recorder like LR-P3, or a Feishu recorder bean, to a phone: it uploads while the phone is near and catches up when it is back. Also force-bind, unbind."
---

# How to bind a recorder

A BLE recorder (such as [LR-P3](../reference/hardware.md)) has to be bound to a phone first. Once bound, it starts recording: when the phone is nearby, it uploads in real time; when the phone is away, it stores the audio on its card and catches up in the background once the phone is back. While unbound, it doesn't record, and the app records with the phone's mic. A Feishu recorder bean binds and records differently, see [Feishu recorder bean](#feishu-recorder-bean).

## Bind

1. In the app, open **我的 › 录音设备** (Me › Recorders) and tap **添加录音设备** (Add recorder).
2. The page lists the permissions you haven't granted yet (Bluetooth or "附近的设备" (Nearby devices), notifications, running in the background). Allow each one.
3. Turn the recorder on (plugging in the charging cable is enough). Nearby recorders show up in the list. The name is printed on the recorder; when several are nearby, the one with the strongest signal is marked "最近" (Nearest).
4. Tap **绑定** (Bind) next to it.

When the list marks a recorder "已绑定其他手机" (Bound to another phone), you can tap **强制绑定** (Force bind): the recorder binds to this phone, and the previous phone can no longer connect to it. When it is marked "正连着其他手机" (Connected to another phone), first unbind it on that phone, or turn off that phone's Bluetooth.

## Unbind

On the **录音设备** page, tap **解除绑定** (Unbind).

- With the recorder nearby: it forgets all phones and stops recording; the app first fetches the recordings on its card. This phone switches to its own mic.
- With the recorder away: only this phone drops the binding. The recorder still remembers it and keeps recording. You can force-bind it later when you add it.
- iPhone: the app can't delete the system's own pairing record. After unbinding, go to **Settings › Bluetooth › device name › Forget This Device**. To bind the recorder to this iPhone again later, you must forget it there first.

## Feishu recorder bean

The Android app connects to a Feishu recorder bean directly since 1.5.5. The iPhone app can't yet.

1. Open **添加录音设备** (Add recorder) and allow the permissions, as in steps 1 and 2 of [Bind](#bind).
2. The bean shows up in the list as `D3200-XXXX`, marked "已绑定飞书" (Bound to Feishu) or "未绑定飞书" (Not bound to Feishu). Tap **绑定** (Bind) next to it.

The binding is kept on this phone only. From then on:

- The bean records when its button is pressed; the phone can't start or stop it.
- With the phone nearby, it uploads as it records.
- With the phone away, the audio stays on the bean. Once the phone is back, it fetches the audio while the bean is not recording. Only files recorded after the binding are fetched.
- Files the server has in full are deleted from the bean, one at a time, only while the bean is not recording.

The **录音设备** (Recorders) page has no pause, live listening, USB drive mode or firmware update for a bean.

Unbinding only makes this phone forget the bean and switch to its own mic. Nothing changes on the bean: its button still records, Feishu can still connect, and recordings not fetched yet stay on the bean.

## Old recordings on the card after a phone change

To be added: whether the audio recorded on the card before the binding gets uploaded after the recorder is rebound to another phone.

If you don't have a recorder, you can build an LR-P3 yourself by following [`hardware/lr-p3/README.md`](https://github.com/suyiiyii/alldayrec/blob/main/hardware/lr-p3/README.md).
