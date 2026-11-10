"""Assemblage du MVP graphique Bluetooth BLE."""

import sys
import argparse
from config import add_config_arguments, config_from_args

from PySide6.QtWidgets import QApplication

from interface.window import DetectorWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="Détecteur Bluetooth BLE graphique")
    add_config_arguments(parser)
    args, qt_args = parser.parse_known_args()
    try:
        config = config_from_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    app = QApplication([sys.argv[0], *qt_args])
    window = DetectorWindow(config=config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
