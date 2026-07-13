#ifndef COMMAND_PARSER_H
#define COMMAND_PARSER_H

#include <Arduino.h>

enum class CommandType : uint8_t {
  None,
  Ping,
  Stop,
  Forward,
  Backward,
  Left,
  Right,
  Move,
  Timeout,
  Invalid
};

struct Command {
  CommandType type;
  int16_t first;
  int16_t second;
};

class CommandParser {
public:
  bool read(Stream& stream, Command& command);

private:
  static const uint8_t BUFFER_SIZE = 32;
  char buffer[BUFFER_SIZE];
  uint8_t length = 0;
  bool inFrame = false;

  Command parseFrame();
  static int16_t parseIntOrZero(const char* value);
};

#endif

