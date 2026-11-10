cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(hcirelay)

# Zephyr's own HCI controller sample, unchanged, plus the 1200 bps touch
target_sources(app PRIVATE ${ZEPHYR_BASE}/samples/bluetooth/hci_uart/src/main.c src/touch.c)
