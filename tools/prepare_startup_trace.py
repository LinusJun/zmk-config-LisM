"""Add display/backlight boot evidence without reinitializing a working panel."""
from pathlib import Path
import hashlib, sys
from prepare_upstream_esb import replace

def prepare_display(source, destination):
    raw=Path(source).read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='5fb57bfd40f30b09cbf0246831892eecd82b60b07c1bc02d9d3fa8ef1a2544c5'
    text=raw.decode()
    text=replace(text, '    display_blanking_off(display);', '''    int lism_unblank_rc = display_blanking_off(display);
    LOG_INF("LisM LCD unblank rc=%d", lism_unblank_rc);''')
    text=replace(text, 'void initialize_display(struct k_work *work) {', '''void initialize_display(struct k_work *work) {
    LOG_INF("LisM UI init entered, LCD_ready=%d", device_is_ready(display));''')
    text=replace(text, '    lv_scr_load(screen);', '''    lv_scr_load(screen);
    LOG_INF("LisM UI screen loaded");''')
    Path(destination).write_text(text)

def prepare_brightness(path):
    raw=Path(path).read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='a65598f9e48a099207144c71ebfde128991588d4ee834de38dbfc4d4b5f2feb6'
    text=raw.decode()
    text=replace(text, '''    led_set_brightness(pwm_leds_dev, DISP_BL, CONFIG_PROSPECTOR_FIXED_BRIGHTNESS);

    return 0;''', '''    int rc = device_is_ready(pwm_leds_dev)
        ? led_set_brightness(pwm_leds_dev, DISP_BL, CONFIG_PROSPECTOR_FIXED_BRIGHTNESS)
        : -ENODEV;
    LOG_INF("LisM backlight init: ready=%d rc=%d brightness=%d",
            device_is_ready(pwm_leds_dev), rc, CONFIG_PROSPECTOR_FIXED_BRIGHTNESS);
    return rc;''')
    Path(path).write_text('#include <errno.h>\n'+text)

if __name__=='__main__':
    prepare_display(sys.argv[1],sys.argv[2])
    prepare_brightness(sys.argv[3])
