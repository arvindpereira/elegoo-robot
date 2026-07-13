#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>

struct MotorPins {
  uint8_t direction;
  uint8_t pwm;
};

static const uint32_t SERIAL_BAUD = 115200;
static const uint16_t DEFAULT_COMMAND_TIMEOUT_MS = 1000;

// Confirmed from ELEGOO Conqueror Robot Tank Kit 2024.06.05:
// 02 Main Program/TB6612/ConquerorCar_TB6612_20240605/DeviceDriverSet_xxx0.h
static const uint8_t MOTOR_STANDBY_PIN = 3;
static const MotorPins LEFT_MOTOR_PINS = {8, 6};  // BIN_1, PWMB
static const MotorPins RIGHT_MOTOR_PINS = {7, 5}; // AIN_1, PWMA

#endif
