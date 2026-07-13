# Hardware Notes

Target kit: ELEGOO Conqueror Robot Tank Kit with UNO R3, FPV camera, app/IR control, line tracking, and obstacle avoidance.

Working assumptions:

- The UNO R3 / ATmega328P is the main robot controller.
- The FPV/ESP32-CAM module is a camera and Wi-Fi/control bridge.
- The small chip near the UNO USB connector is likely USB-to-serial support, not application logic.

The starter firmware only targets the UNO. It does not reflash the camera module.

## Motor Pins

The motor pins in `firmware/uno/conqueror_serial/config.h` are taken from ELEGOO's 2024 Conqueror source:

```text
vendor/elegoo/ELEGOO Conqueror Robot Tank Kit 2024.06.05.zip
  ELEGOO Conqueror Robot Tank Kit 2024.06.05/
    02 Manual & Main Code & APP/
      02 Main Program/
        TB6612/
          ConquerorCar_TB6612_20240605/
            DeviceDriverSet_xxx0.h
```

The relevant ELEGOO definitions are:

```cpp
#define PIN_Motor_PWMA 5
#define PIN_Motor_PWMB 6
#define PIN_Motor_BIN_1 8
#define PIN_Motor_AIN_1 7
#define PIN_Motor_STBY 3
```

Meaning:

- Right motor: `AIN_1 = 7`, `PWMA = 5`.
- Left motor: `BIN_1 = 8`, `PWMB = 6`.
- Shared standby/enable: `STBY = 3`.
- Direction is one digital pin per motor; PWM controls speed.

Other confirmed pins:

| Function | Pin |
| --- | --- |
| RGB LED | `4` |
| Button | `2` |
| Line sensor left | `A2` |
| Line sensor middle | `A1` |
| Line sensor right | `A0` |
| Battery voltage | `A3` |
| Ultrasonic trigger | `13` |
| Ultrasonic echo | `12` |
| Servo Z | `10` |
| Servo Y | `11` |
| IR receiver | `9` |

Before running the robot on the floor:

1. Lift the tank so the tracks are off the ground.
2. Upload the firmware.
3. Send `{M,80,80}`.
4. If one side runs backward, we should add an invert flag rather than changing the confirmed ELEGOO pin numbers.
5. Send `{S}` and verify both tracks stop.

## Next Identification Step

Bench-test `{M,80,80}` with the tracks lifted. If the physical direction is inverted, update the firmware behavior while keeping this pin map as the hardware truth.
