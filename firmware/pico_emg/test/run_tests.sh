#!/bin/bash
# Native logic tests for the EMG gripper firmware.
# Compiles the same headers the Pico build uses, so the protocol, DSP,
# control law and safety state machine are exercised on the host. main.cpp
# is the only part this cannot reach.
set -e
cd "$(dirname "$0")/.."
cc -std=c11 -O2 -Wall -Wextra -Wno-unused-parameter \
   -o /tmp/emg_native_tests test/test_native.c -lm
exec /tmp/emg_native_tests
