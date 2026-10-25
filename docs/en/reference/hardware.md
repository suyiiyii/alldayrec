---
type: reference
---

# Recorder hardware

The phone alone can record. For all-day wear you can use the open BLE recorder: an nRF52840 board + a PDM microphone + a microSD card + a small battery. It records to the card all the time and syncs whenever it connects to the phone, so nothing is lost when the phone is away.

The full parts list, pinout, soldering order and 3D-printed case are in [`hardware/lr-p3/README.md`](https://github.com/suyiiyii/alldayrec/blob/main/hardware/lr-p3/README.md); firmware, building and over-the-air updates are in [`firmware/devkit/README.md`](https://github.com/suyiiyii/alldayrec/blob/main/firmware/devkit/README.md). This page is a summary.

Since 1.5.5 the Android app also connects to a Feishu recorder bean directly, see [How to bind a recorder](../how-to/bind-recorder.md#feishu-recorder-bean).

## Models

| Model | Board | Firmware variant (`lr.sh`) |
|---|---|---|
| `LR-X1` | Seeed XIAO nRF52840 Sense (built-in mic) + SD module + battery | `sdmod` |
| `LR-P3` | ProMicro nRF52840 (nice!nano compatible) + SD module + PDM mic (SEL to 3V) + slide switch + battery, fly-wired and closed in a 3D-printed case | `promicro-p3` |

Each device's Bluetooth name is its model plus the last two bytes of its identity address, like `LR-P3-XXXX`. The address belongs to the chip, so flashing firmware doesn't change it.

## LR-P3 parts

| Part | Model / spec |
|---|---|
| Board | ProMicro nRF52840 (nice!nano v2 clone) |
| SD module | 3.3 V mini TF card module; a 2 GB SDSC card is enough, and the firmware reads only FAT32 or exFAT |
| Microphone | PDM microphone board WMM7040DTHN0 (SEL to 3V) |
| Battery | 602030 300 mAh lithium battery (with its own protection board) |
| Switch | Three-pin slide switch (SPDT, 2.54 mm pitch), wired as a signal only, not in series with the battery |
| Case | 3D-printed snap-fit case v5.5L, FDM version 36.63 × 24.7 × 18.6 mm |

## LR-P3 LEDs and switch

- Switch: a position counts only after it holds for 0.5 s. When you switch it off, the device finishes the file it is writing, disconnects from the phone and restarts; then, without USB, it powers off (System OFF), and with USB it keeps running but doesn't record. Switching it on or plugging in USB wakes it up.
- Red LED (P0.15) blink codes: no blinks while paused or off; 1 blink per second when not bound to a phone; 3 blinks every 3 s when the card can't be written (no card, card full, write failed, card lent to USB); 1 blink every 3 s when recording with the phone connected; 2 blinks every 3 s when recording to the card only. It stays on for 1 s when recording starts, and blinks fast 3 times when you switch it off.
- The blue LED belongs to the charger chip and lights only while USB is plugged in.
- Low battery: on battery, after 3 readings in a row, one per minute, below 3.3 V, the device finishes up and powers off; plug in USB or flip the switch to wake it.

## Firmware

Build it with nRF Connect SDK v2.7.0, e.g. `./lr.sh build promicro-p3`, `./lr.sh flash promicro-p3`. On a newly soldered board, flash [`firmware/selftest`](https://github.com/suyiiyii/alldayrec/tree/main/firmware/selftest) first and run the full self-test. The firmware started from the [Omi](https://github.com/BasedHardware/omi) devkit.

The app's **录音设备 › 固件更新** (Recorders › Firmware update) lists the firmware released on the server for this model; it upgrades only when you tap one.
