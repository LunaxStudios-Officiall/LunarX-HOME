# Platform limitations

Android/PRoot cannot be promised native PAM, systemd, XFS project quotas, Docker, XRDP or Linux block-device operations. The provider layer reports those capabilities as unavailable and uses local account hashes, persistent internal storage and logical quota accounting. Native Ubuntu still requires target-host package, kernel, display and reverse-proxy validation.
