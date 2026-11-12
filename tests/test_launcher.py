import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

class LauncherTests(unittest.TestCase):
    def test_rejects_wsl_and_non_linux_or_macos(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_uname = pathlib.Path(directory) / 'uname'
            fake_uname.write_text('#!/bin/sh\ncase "$1" in -s) echo "$TEST_SYSTEM";; -r) echo "$TEST_RELEASE";; esac\n')
            fake_uname.chmod(0o755)
            env = dict(os.environ, PATH=directory + os.pathsep + os.environ['PATH'])
            for system, release in [('Linux', '5.15.0-microsoft-standard-WSL2'), ('FreeBSD', '14.0')]:
                env.update(TEST_SYSTEM=system, TEST_RELEASE=release)
                result = subprocess.run([str(ROOT / 'start.sh')], env=env, capture_output=True, text=True, timeout=3)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('native macOS and Linux', result.stderr)

class LauncherPrerequisiteTests(unittest.TestCase):
    def run_with_tools(self, python_script=None, partial_venv=False, system="Darwin", executable_venv=False):
        import shutil
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            project = root / 'project'; project.mkdir()
            shutil.copy(ROOT / 'start.sh', project / 'start.sh')
            tools = root / 'tools'; tools.mkdir()
            (tools / 'dirname').symlink_to('/usr/bin/dirname')
            uname = tools / 'uname'
            uname.write_text('#!/bin/sh\ncase "$1" in -s) echo ' + system + ';; -r) echo 1;; esac\n'); uname.chmod(0o755)
            if python_script is not None:
                python = tools / 'python3'; python.write_text('#!/bin/sh\n' + python_script + '\n'); python.chmod(0o755)
            if partial_venv: (project / '.venv').mkdir()
            if executable_venv:
                bindir = project / '.venv' / 'bin'; bindir.mkdir(parents=True)
                executable = bindir / 'python'; executable.write_text('#!/bin/sh\necho started\n'); executable.chmod(0o755)
            return subprocess.run([str(project / 'start.sh')], env=dict(os.environ, PATH=str(tools)), capture_output=True, text=True, timeout=5)

    def test_accepts_macos_and_native_linux_names(self):
        for system in ('Darwin', 'Linux'):
            result = self.run_with_tools('exit 0', system=system, executable_venv=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('started', result.stdout)

    def test_missing_python_reports_prerequisite(self):
        result = self.run_with_tools()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Python 3.10 or newer is required', result.stderr)

    def test_old_python_reports_prerequisite(self):
        result = self.run_with_tools('exit 1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Python 3.10 or newer is required', result.stderr)

    def test_venv_creation_failure_has_actionable_message(self):
        result = self.run_with_tools('case "$1" in -c) exit 0;; *) exit 1;; esac')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Could not create a virtual environment', result.stderr)

    def test_partial_venv_has_actionable_message(self):
        result = self.run_with_tools('exit 0', partial_venv=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Remove .venv and retry', result.stderr)

if __name__ == '__main__': unittest.main()
