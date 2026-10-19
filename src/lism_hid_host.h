#pragma once
/* Sole split scan owner retained by the ESB control-plane patches.
 * No generic HID receiver implementation is linked in this branch. */
void lism_hid_scan_resume(void);
int lism_hid_scan_pause(void);
void lism_hid_scan_diagnostics(void);
