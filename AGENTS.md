# Development instructions

This Ubuntu Python application observes detectable Bluetooth BLE addresses.
An address is not a person or a guaranteed unique physical device. Do not merge
rotating addresses based only on names, manufacturers, RSSI or timing.

Run `python -m unittest discover -s tests -v` for synthetic automated tests.
Browser checks are optional: `python tests/browser_v2.py` with Playwright installed.
Distinguish simulated validation from real hardware and controlled experiments.

The web server binds to loopback only. Keep its origin checks and control token.
Never commit local Bluetooth databases, credentials, or raw device captures.
