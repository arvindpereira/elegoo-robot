#include "CommandParser.h"
#include <stdlib.h>
#include <string.h>

bool CommandParser::read(Stream& stream, Command& command) {
  while (stream.available() > 0) {
    const char ch = static_cast<char>(stream.read());

    if (ch == '{') {
      inFrame = true;
      length = 0;
      continue;
    }

    if (!inFrame) {
      continue;
    }

    if (ch == '}') {
      buffer[length] = '\0';
      command = parseFrame();
      inFrame = false;
      length = 0;
      return true;
    }

    if (length < BUFFER_SIZE - 1) {
      buffer[length++] = ch;
    } else {
      command = {CommandType::Invalid, 0, 0};
      inFrame = false;
      length = 0;
      return true;
    }
  }

  return false;
}

Command CommandParser::parseFrame() {
  char* token = strtok(buffer, ",");
  if (token == nullptr || token[0] == '\0') {
    return {CommandType::Invalid, 0, 0};
  }

  char* firstArg = strtok(nullptr, ",");
  char* secondArg = strtok(nullptr, ",");
  const int16_t first = parseIntOrZero(firstArg);
  const int16_t second = parseIntOrZero(secondArg);

  switch (token[0]) {
    case 'P':
      return {CommandType::Ping, 0, 0};
    case 'S':
      return {CommandType::Stop, 0, 0};
    case 'F':
      return {CommandType::Forward, first, 0};
    case 'B':
      return {CommandType::Backward, first, 0};
    case 'L':
      return {CommandType::Left, first, 0};
    case 'R':
      return {CommandType::Right, first, 0};
    case 'M':
      return {CommandType::Move, first, second};
    case 'T':
      return {CommandType::Timeout, first, 0};
    default:
      return {CommandType::Invalid, 0, 0};
  }
}

int16_t CommandParser::parseIntOrZero(const char* value) {
  if (value == nullptr) {
    return 0;
  }
  return static_cast<int16_t>(atoi(value));
}

