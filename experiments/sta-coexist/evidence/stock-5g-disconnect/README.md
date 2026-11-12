# 5 GHz stock WLAN disconnect investigation

The 5 GHz override candidate was not deployed. Live stock module note hash and firmware hash matched the known stock identities. No Owly/Wonder experimental interface was active.

The phone repeatedly authenticated to TEST_WIFI at 5200 MHz, obtained 192.0.2.3 and briefly reached VALIDATED. Kernel logs showed locally generated reason-3 disconnects. `dumpsys wifi` recorded the primary manager connecting TEST_WIFI and a SECONDARY_LONG_LIVED manager connecting old TEST_WIFI every approximately 3–4 seconds. Both managers reported interface wlan0. Secondary requestor was com.android.settings. This points to a primary/secondary STA interface conflict; its underlying cause (including possible stale framework/HAL state) is not established.

Actions: Wi-Fi disable/enable without reboot; the conflict recurred, gateway ping 20/30. System setting slave_wifi_on was 1. A reversible write to 0 did not terminate the active secondary manager, and ping remained 20/30. Setting restored to 1; see setting-change.json. No persistent setting change remains from this probe. User was asked to disable dual-WLAN through the actual system UI so its service releases the secondary connection.

No further experimental driver deployment until ordinary WLAN is stable. Raw dumps contain device/network identifiers and are local diagnostic evidence, not intended for public upload.

After the user's UI action, stock STA connected at 5200 MHz and gateway ping passed 20/20 (average 16.563 ms), with only the primary connection shown in cmd wifi status. Evidence: wifi-after-user-fix.txt and ping-after-user-fix.txt. This supports the secondary-STA conflict diagnosis; the deeper source of duplicated interface assignment remains unproven.
