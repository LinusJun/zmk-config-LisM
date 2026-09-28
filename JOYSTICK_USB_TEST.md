# JP19 / Switch 5P joystick: isolated USB test

This branch builds only the joystick XIAO. It does not change or require the
LisM dongle, left half, or right half. Flash one of the two Actions artifacts:

- `joystick_usb_wasd`: USB HID W/A/S/D and Space.
- `joystick_usb_adc_diagnostic`: USB serial ADC logs and Space on stick press;
  directional HID output is disabled.

Both images sample X on D4/P0.04, Y on D5/P0.05, and the active-low push
switch on D6/P1.11. The wiring observed in the September 28 photos, **with
the FPC copper contacts facing down into the connector**, is:

| Breakout pad | Wire | Signal | XIAO |
| --- | --- | --- | --- |
| 5 | Red | stick supply | regulated supply, voltage still unverified |
| 4 | Blue | Y | D5 |
| 3 | Purple | push | D6 |
| 2 | White | ground | GND |
| 1 | Yellow | X | D4 |
| 6 | unused | - | - |

Do not copy the 5 V supply from the CiferTech XIAO controller: it uses a
different, generic VRX/VRY joystick module. The safe supply voltage for this
specific Switch replacement stick has **not** been verified. Do not reconnect
the stick supply until its part specification or a reliable test establishes
it. The XIAO itself can be flashed and enumerated over USB with the stick
unpowered; ADC readings without stick power are meaningless.

The WASD image starts disarmed so it cannot type on USB insertion. Leave the
stick centered while powering on. Hold the stick push switch for one second,
then release to arm directional keys. Push sends Space immediately, even before
arming or when ADC calibration fails. If both axis centers are not stable and
away from the ADC rails, arming is refused. The diagnostic image does not send
directional keys even after arming.

For either image, connect to the USB CDC serial port on macOS, usually
`/dev/cu.usbmodem*`, at 115200 baud. Logs report raw 12-bit X/Y readings and
calibrated center about twice per second. Record center and fully left,
right, up, down values. Avoid testing in a text field until the WASD image
is explicitly armed.

The test driver validates neutral at boot, filters samples, and releases
all directions on an ADC error. These checks are diagnostic safeguards;
they do not establish that the stick's electrical supply is safe.
