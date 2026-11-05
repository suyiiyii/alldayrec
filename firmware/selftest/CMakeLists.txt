cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(lr_selftest)
target_sources(app PRIVATE src/main.c)
# LR-N4's pins and checks (selftest.py build --n4): -DLR_N4=1
if(LR_N4)
  target_compile_definitions(app PRIVATE LR_N4)
endif()
