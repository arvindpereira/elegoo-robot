#include "CommandParser.h"
#include "MotorDriver.h"
#include "config.h"

CommandParser parser;
MotorDriver motors(LEFT_MOTOR_PINS, RIGHT_MOTOR_PINS);

uint32_t lastMotionCommandAt = 0;
uint16_t commandTimeoutMs = DEFAULT_COMMAND_TIMEOUT_MS;
bool motionActive = false;

void setup() {
  Serial.begin(SERIAL_BAUD);
  motors.begin();
  Serial.println(F("{OK,BOOT}"));
}

void loop() {
  Command command;
  if (parser.read(Serial, command)) {
    handleCommand(command);
  }

  if (motionActive && millis() - lastMotionCommandAt > commandTimeoutMs) {
    motors.stop();
    motionActive = false;
    Serial.println(F("{OK,TIMEOUT_STOP}"));
  }
}

void handleCommand(const Command& command) {
  switch (command.type) {
    case CommandType::Ping:
      Serial.println(F("{OK,PONG}"));
      break;
    case CommandType::Stop:
      motors.stop();
      motionActive = false;
      Serial.println(F("{OK,STOP}"));
      break;
    case CommandType::Forward:
      driveTracked(clampSpeed(command.first), clampSpeed(command.first));
      Serial.println(F("{OK,FORWARD}"));
      break;
    case CommandType::Backward:
      driveTracked(-clampSpeed(command.first), -clampSpeed(command.first));
      Serial.println(F("{OK,BACKWARD}"));
      break;
    case CommandType::Left:
      driveTracked(-clampSpeed(command.first), clampSpeed(command.first));
      Serial.println(F("{OK,LEFT}"));
      break;
    case CommandType::Right:
      driveTracked(clampSpeed(command.first), -clampSpeed(command.first));
      Serial.println(F("{OK,RIGHT}"));
      break;
    case CommandType::Move:
      driveTracked(clampSignedSpeed(command.first), clampSignedSpeed(command.second));
      Serial.println(F("{OK,MOVE}"));
      break;
    case CommandType::Timeout:
      commandTimeoutMs = constrain(command.first, 100, 10000);
      Serial.println(F("{OK,TIMEOUT}"));
      break;
    case CommandType::Invalid:
    case CommandType::None:
    default:
      Serial.println(F("{ERR,BAD_COMMAND}"));
      break;
  }
}

void driveTracked(int16_t leftSpeed, int16_t rightSpeed) {
  motors.drive(leftSpeed, rightSpeed);
  lastMotionCommandAt = millis();
  motionActive = leftSpeed != 0 || rightSpeed != 0;
}

int16_t clampSpeed(int16_t value) {
  return constrain(abs(value), 0, 255);
}

int16_t clampSignedSpeed(int16_t value) {
  return constrain(value, -255, 255);
}

