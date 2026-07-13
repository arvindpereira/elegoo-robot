#include "MotorDriver.h"

MotorDriver::MotorDriver(const MotorPins& leftPins, const MotorPins& rightPins, uint8_t standbyPin)
    : left(leftPins), right(rightPins), standby(standbyPin) {}

void MotorDriver::begin() {
  pinMode(left.direction, OUTPUT);
  pinMode(left.pwm, OUTPUT);
  pinMode(right.direction, OUTPUT);
  pinMode(right.pwm, OUTPUT);
  pinMode(standby, OUTPUT);
  stop();
}

void MotorDriver::drive(int16_t leftSpeed, int16_t rightSpeed) {
  leftSpeed = constrain(leftSpeed, -255, 255);
  rightSpeed = constrain(rightSpeed, -255, 255);
  if (leftSpeed == 0 && rightSpeed == 0) {
    stop();
    return;
  }

  digitalWrite(standby, HIGH);
  driveOne(left, leftSpeed);
  driveOne(right, rightSpeed);
}

void MotorDriver::stop() {
  analogWrite(left.pwm, 0);
  analogWrite(right.pwm, 0);
  digitalWrite(standby, LOW);
}

void MotorDriver::driveOne(const MotorPins& pins, int16_t speed) {
  if (speed == 0) {
    analogWrite(pins.pwm, 0);
    return;
  }

  const bool forward = speed > 0;
  digitalWrite(pins.direction, forward ? HIGH : LOW);
  analogWrite(pins.pwm, abs(speed));
}
