# ELEGOO Source Notes

Downloaded archive:

```text
vendor/elegoo/ELEGOO Conqueror Robot Tank Kit 2024.06.05.zip
```

Important source folders:

```text
02 Manual & Main Code & APP/02 Main Program/TB6612/ConquerorCar_TB6612_20240605/
02 Manual & Main Code & APP/02 Main Program/DRV8835/ConquerorCar_DRV8835_20220322/
02 Manual & Main Code & APP/04 Code of Carmer (ESP32)/
04 Related chip information/Communication protocol for Conqueror.pdf
```

Use the `TB6612` program as the primary reference for this robot generation. It is the newer source dated 2024-06-05 and its README identifies it as the TB6612 motor-drive version.

The UNO main sketch is:

```text
ConquerorCar_TB6612_20240605.ino
```

The hardware pin definitions are in:

```text
DeviceDriverSet_xxx0.h
```

Motor map:

| ELEGOO symbol | UNO pin | Meaning |
| --- | --- | --- |
| `PIN_Motor_STBY` | `3` | TB6612 standby/enable |
| `PIN_Motor_AIN_1` | `7` | Right motor direction |
| `PIN_Motor_PWMA` | `5` | Right motor PWM |
| `PIN_Motor_BIN_1` | `8` | Left motor direction |
| `PIN_Motor_PWMB` | `6` | Left motor PWM |

ELEGOO comments label motor group A as right and group B as left.

See also:

- `docs/esp32-camera-and-control.md` for camera HTTP endpoints, TCP bridge behavior, and the JSON control protocol.
