"""Run the unit tests and exit with their result (used by CI).

PySide6 sometimes crashes (access violation) while Python tears down Qt
objects at interpreter exit, after every test has already passed: widgets
left over by the tests outlive the application object. Closing and
deleting them, then shutting the application down while Python is still
fully alive, keeps the exit code down to the test results.
"""

import gc
import os
import sys
import unittest


def shut_down_qt() -> None:
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    for widget in list(QApplication.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    if hasattr(app, "shutdown"):
        app.shutdown()
    del app
    gc.collect()


def main() -> int:
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(tests_dir)
    # Same layout as ``python -m unittest discover -s tests`` from the root.
    for path in (root, tests_dir):
        if path not in sys.path:
            sys.path.insert(0, path)
    suite = unittest.defaultTestLoader.discover(tests_dir)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    del suite
    shut_down_qt()
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
