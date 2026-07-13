#include "MotorDriver.h"

MotorDriver::MotorDriver(const MotorPins& leftPins, const MotorPins& rightPins)
    : left(leftPins), right(rightPins) {}

void MotorDriver::begin() {
  pinMode(left.in1, OUTPUT);
  pinMode(left.in2, OUTPUT);
  pinMode(left.pwm, OUTPUT);
  pinMode(right.in1, OUTPUT);
  pinMode(right.in2, OUTPUT);
  pinMode(right.pwm, OUTPUT);
  stop();
}

void MotorDriver::drive(int16_t leftSpeed, int16_t rightSpeed) {
  driveOne(left, constrain(leftSpeed, -255, 255));
  driveOne(right, constrain(rightSpeed, -255, 255));
}

void MotorDriver::stop() {
  driveOne(left, 0);
  driveOne(right, 0);
}

void MotorDriver::driveOne(const MotorPins& pins, int16_t speed) {
  if (speed == 0) {
    digitalWrite(pins.in1, LOW);
    digitalWrite(pins.in2, LOW);
    analogWrite(pins.pwm, 0);
    return;
  }

  const bool forward = speed > 0;
  digitalWrite(pins.in1, forward ? HIGH : LOW);
  digitalWrite(pins.in2, forward ? LOW : HIGH);
  analogWrite(pins.pwm, abs(speed));
}

