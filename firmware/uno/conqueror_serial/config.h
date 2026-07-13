#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>

struct MotorPins {
  uint8_t in1;
  uint8_t in2;
  uint8_t pwm;
};

static const uint32_t SERIAL_BAUD = 115200;
static const uint16_t DEFAULT_COMMAND_TIMEOUT_MS = 1000;

// Placeholder dual H-bridge pin map. Confirm against Elegoo's Conqueror
// product files before driving on the floor.
static const MotorPins LEFT_MOTOR_PINS = {7, 8, 5};
static const MotorPins RIGHT_MOTOR_PINS = {9, 10, 6};

#endif

