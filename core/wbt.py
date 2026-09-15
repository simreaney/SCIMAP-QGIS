"""WhiteboxTools discovery and invocation.

The plugin shells out to the ``whitebox_tools`` binary directly rather than
going through the ``wbt_for_qgis`` plugin, which raises PyQt6 CrashExit errors
under QGIS 4.
"""

import os
import shutil
import subprocess

from qgis.core import QgsApplication, QgsSettings

SETTINGS_KEY = 'SCIMAP/wbtExecutable'


def store_executable(path):
    """Persist a user-supplied WhiteboxTools path for later runs."""
    QgsSettings().setValue(SETTINGS_KEY, path)


def stored_executable():
    return QgsSettings().value(SETTINGS_KEY, '') or ''


def find_executable(preferred=''):
    """Locate the WhiteboxTools binary, preferring an explicit *preferred* path."""
    bundled = os.path.join(
        QgsApplication.qgisSettingsDirPath(),
        "python", "plugins", "wbt_for_qgis", "WBT", "whitebox_tools",
    )
    candidates = [
        preferred,
        QgsSettings().value(SETTINGS_KEY, ''),
        QgsSettings().value("Wbt/executable", ""),
        bundled,
        bundled + ".exe",
    ]

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate

    return shutil.which("whitebox_tools")


def run_wbt(tool_name, args_dict, feedback, preferred_executable=''):
    """Run a WhiteboxTools tool, raising RuntimeError on failure."""
    wbt_exe = find_executable(preferred_executable)
    if not wbt_exe:
        raise RuntimeError(
            "Could not find the 'whitebox_tools' executable. Install WhiteboxTools "
            "and either place it on PATH or set its location in the SCIMAP panel."
        )

    cmd = [wbt_exe, f'--run={tool_name}']
    for k, v in args_dict.items():
        if isinstance(v, bool):
            if v:
                cmd.append(f'--{k}')
        else:
            cmd.append(f'--{k}={v}')

    feedback.pushInfo(f"Running WBT natively: {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"WhiteboxTools failed: {res.stderr or res.stdout}")
