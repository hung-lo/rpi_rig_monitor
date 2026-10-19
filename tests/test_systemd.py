import getpass
import grp
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "deploy" / "rpi-rig-supervisor.service"


class SystemdUnitTests(unittest.TestCase):
    def test_supervisor_unit_keeps_experiments_out_of_service_kill_scope(self):
        content = UNIT.read_text()
        self.assertIn("Restart=on-failure", content)
        self.assertIn("KillMode=process", content)
        self.assertIn("StateDirectory=rpi-rig-monitor", content)
        self.assertIn("RuntimeDirectory=rpi-rig-monitor", content)

    @unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze is unavailable")
    def test_systemd_analyze_verify(self):
        content = UNIT.read_text()
        content = content.replace("User=pi", "User=" + getpass.getuser())
        content = content.replace("Group=pi", "Group=" + grp.getgrgid(os.getgid()).gr_name)
        content = content.replace("WorkingDirectory=/opt/rpi_rig_monitor", "WorkingDirectory=" + str(ROOT))
        content = content.replace("/opt/rpi_rig_monitor/.venv/bin/python", sys.executable)
        with tempfile.TemporaryDirectory() as directory:
            unit = Path(directory) / "rpi-rig-supervisor.service"
            unit.write_text(content)
            result = subprocess.run(["systemd-analyze", "verify", str(unit)], capture_output=True, text=True,
                                    check=False)
        diagnostic = result.stdout + result.stderr
        if "Failed to create directory '/run/systemd/'" in diagnostic:
            self.skipTest("systemd-analyze cannot access /run in this sandbox")
        self.assertEqual(result.returncode, 0, diagnostic)


if __name__ == "__main__":
    unittest.main()
