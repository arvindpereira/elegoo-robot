# Hardware Notes

Target kit: ELEGOO Conqueror Robot Tank Kit with UNO R3, FPV camera, app/IR control, line tracking, and obstacle avoidance.

Working assumptions:

- The UNO R3 / ATmega328P is the main robot controller.
- The FPV/ESP32-CAM module is a camera and Wi-Fi/control bridge.
- The small chip near the UNO USB connector is likely USB-to-serial support, not application logic.

The starter firmware only targets the UNO. It does not reflash the camera module.

## Motor Pins

The default motor pins in `firmware/uno/conqueror_serial/config.h` are placeholders for a common dual H-bridge layout:

```cpp
static const MotorPins LEFT_MOTOR = {7, 8, 5};
static const MotorPins RIGHT_MOTOR = {9, 10, 6};
```

Meaning:

- `in1`, `in2`: direction pins.
- `pwm`: speed pin. This must be a PWM-capable UNO pin for variable speed.

Before running the robot on the floor:

1. Lift the tank so the tracks are off the ground.
2. Upload the firmware.
3. Send `{M,80,80}`.
4. If one side runs backward, swap that side's `in1` and `in2` values in `config.h`, or set `invert = true` in the motor driver wiring once added.
5. Send `{S}` and verify both tracks stop.

## Next Identification Step

Download Elegoo's Conqueror product files from the official download center and compare their `.ino` pin definitions with `config.h`. Copy the confirmed pin map into this repo once found.

