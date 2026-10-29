#!/bin/bash
# live-record builds of the devkit firmware, all from the command line (NCS v2.7.0 via nrfutil sdk-manager):
#   ./lr.sh build [sdmod|expansion]   sdmod: SPI SD module on the right header row (overlay/lr_sdmod.overlay); expansion: Seeed
#                                     Expansion Board Base for XIAO, SD CS on D2 (tests only)
#   ./lr.sh build promicro            ProMicro nRF52840 (nice!nano clone) + external PDM mic and SD module
#   ./lr.sh build promicro-p2         the same board as LR-P2: the spare ProMicro kept for tests (bootloader 0.10.0)
#   ./lr.sh build promicro-p3         the same board as LR-P3: the ProMicro wired into the boxed recorder
#   ./lr.sh flash [sdmod|expansion|promicro|promicro-p2|promicro-p3]   a running firmware is sent to the UF2 bootloader by a 1200 bps touch
#   ./lr.sh pkg [sdmod|promicro|promicro-p2|promicro-p3]    the build as a DFU package (build/lr-V/lr-V.zip) for lr_dfu.py; lr_release.py
#                                     builds, packs and uploads it to the server, where the app finds it
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)"
V=${2:-sdmod}
C=xiao_ble_sense_devkitv2-adafruit
OVL="$D/overlay/$C.overlay;$D/overlay/lr_sd.overlay"
# no speaker, no haptics; no Omi button: its code drives D4 and listens on D5, the I2C pins we keep free
EXTRA=(-DCONFIG_OMI_ENABLE_SPEAKER=n -DCONFIG_OMI_ENABLE_HAPTIC=n -DCONFIG_OMI_ENABLE_BUTTON=n)
case $V in
  sdmod) OVL="$OVL;$D/overlay/lr_sdmod.overlay" ;;
  expansion) OVL="$OVL;$D/overlay/xiao_expansion_base.overlay" ;;
  promicro|promicro-p2|promicro-p3) M=LR-P1; [ "$V" = promicro ] || M=LR-P${V#promicro-p}  # LR-P2/P3: the same board, their own model and name
            OVL="$OVL;$D/overlay/lr_promicro.overlay"  # battery on VDDH, P0.13/P0.17 are not charger pins here
            EXTRA+=(-DCONFIG_LR_BATTERY_VDDH=y "-DCONFIG_LR_NAME_PREFIX=\"$M\"" "-DCONFIG_BT_DEVICE_NAME=\"$M\""
                    "-DCONFIG_BT_DIS_MODEL=\"$M\"" '-DCONFIG_BT_DIS_HW_REV_STR="ProMicro nRF52840"'
                    -DCONFIG_LR_ONE_LED=y  # only P0.15's LED is fitted: short flash codes
                    -DCONFIG_CLOCK_CONTROL_NRF_K32SRC_100PPM=y)  # its 32.768 kHz crystals run +47..+81 ppm
            # only LR-P3 has the switch on P1.15, and keeps a power trail while its battery cuts out
            # and phone mode (the card idle while the phone acks what it has: lr_store.c)
            [ "$V" != promicro-p3 ] || EXTRA+=(-DCONFIG_LR_SWITCH=y -DCONFIG_LR_POWER_TRACE=y -DCONFIG_LR_STORE_PHONE=y) ;;
  *) echo "unknown variant $V" >&2; exit 2 ;;
esac
# LR_CARD_ON=1 ./lr.sh build promicro-p3: phone mode with the card's supply left on (A/B of its power), in build/lr-V-cardon
if [ "${LR_CARD_ON:-}" = 1 ]; then
  EXTRA+=(-DCONFIG_LR_STORE_CARD_OFF=n)
  V="$V-cardon"
fi
# LR_LOG=1 ./lr.sh build V: logs on the USB serial port (cat /dev/cu.usbmodem*), in build/lr-V-log
if [ "${LR_LOG:-}" = 1 ]; then
  OVL="$OVL;$D/overlay/lr_log.overlay"
  EXTRA+=(-DCONFIG_CONSOLE=y -DCONFIG_UART_CONSOLE=y -DCONFIG_LOG=y -DCONFIG_LOG_PRINTK=y -DCONFIG_LOG_DEFAULT_LEVEL=3)
  V="$V-log"
fi
UF2="$D/build/lr-$V/zephyr/zephyr.uf2"
# the firmware revision clients read (DIS) and compare with the server's newest package: commit time and hash, + if
# dirty; a later commit sorts later as text (the older date-only form sorts before a same-day one)
FWV="$(git -C "$D" log -1 --format=%cd --date=format:%Y%m%d.%H%M%S)-$(git -C "$D" rev-parse --short HEAD)$(git -C "$D" diff --quiet HEAD -- . || echo +)"
EXTRA+=("-DCONFIG_BT_DIS_FW_REV_STR=\"$FWV\"")

case ${1:-} in
build)
  cd "${NCS:-/opt/nordic/ncs/v2.7.0}"  # CI: NCS is a west workspace of v2.7.0
  # Native CI can select a user-owned SDK/toolchain installation; a loaded Zephyr SDK needs no launcher.
  TC=(nrfutil sdk-manager toolchain launch --ncs-version v2.7.0)
  [ -z "${LR_NCS_INSTALL_DIR:-}" ] || TC+=(--install-dir "$LR_NCS_INSTALL_DIR")
  TC+=(--); [ -z "${ZEPHYR_SDK_INSTALL_DIR:-}" ] || TC=(env)
  "${TC[@]}" west build -b xiao_ble/nrf52840/sense -d "$D/build/lr-$V" "$D" ${LR_BUILD_JOBS:+-o=-j$LR_BUILD_JOBS} -- \
    -DNCS_TOOLCHAIN_VERSION=NONE -DCONF_FILE="$D/prj_$C.conf" -DCACHED_CONF_FILE="$D/prj_$C.conf" \
    -DDTC_OVERLAY_FILE="$OVL" -DPLATFORM=nrf52840 "${EXTRA[@]}"
  ;;
pkg)
  [ -f "$UF2" ] || { echo "build first: $UF2 missing" >&2; exit 1; }
  # sd-req 0xFFFE: any SoftDevice (the XIAO's S140 7.3.0, the ProMicro's 6.1.1); the app does not use it
  uvx adafruit-nrfutil dfu genpkg --dev-type 0x0052 --sd-req 0xFFFE --application "${UF2%.uf2}.hex" "$D/build/lr-$V/lr-$V.zip"
  ;;
flash)
  [ -f "$UF2" ] || { echo "build first: $UF2 missing" >&2; exit 1; }
  uf2drive() { for v in /Volumes/*; do [ -f "$v/INFO_UF2.TXT" ] && { echo "$v"; return; }; done; return 0; }  # XIAO-SENSE, NICENANO, ...
  if [ -z "$(uf2drive)" ]; then
    port=$(ls /dev/cu.usbmodem* 2>/dev/null | head -1) || true
    if [ -n "$port" ]; then /bin/stty -f "$port" 1200 || true  # macOS stty: GNU stty (coreutils first in PATH) has no -f
    else  # no serial port (a firmware without it): ask over Bluetooth, LR_NAME picks the board
      uv run --no-project --with bleak python "$D/lr_dfu.py" --uf2 --name "${LR_NAME:-LR-}" || { echo "no board on USB or Bluetooth" >&2; exit 1; }
    fi
    for _ in $(seq 40); do [ -n "$(uf2drive)" ] && break; sleep 0.5; done
  fi
  drive=$(uf2drive)
  [ -n "$drive" ] || { echo "bootloader drive did not show up: double-tap reset once (old firmware?)" >&2; exit 1; }
  sleep 1
  /bin/cp -X "$UF2" "$drive/" 2>/dev/null || true  # macOS cp (-X: no resource forks; GNU cp has no -X); the board reboots mid-copy, so its complaint is expected
  for _ in $(seq 40); do ls /dev/cu.usbmodem* >/dev/null 2>&1 && break; sleep 0.5; done
  echo "flashed $V via $drive: $(ls /dev/cu.usbmodem* 2>/dev/null || echo 'no serial port yet')"
  ;;
*) sed -n 2,6p "$0"; exit 2 ;;
esac
