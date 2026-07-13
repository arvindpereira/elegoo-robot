#ifndef MOTOR_DRIVER_H
#define MOTOR_DRIVER_H

#include <Arduino.h>
#include "config.h"

class MotorDriver {
public:
  MotorDriver(const MotorPins& leftPins, const MotorPins& rightPins);

  void begin();
  void drive(int16_t leftSpeed, int16_t rightSpeed);
  void stop();

private:
  MotorPins left;
  MotorPins right;

  void driveOne(const MotorPins& pins, int16_t speed);
};

#endif

