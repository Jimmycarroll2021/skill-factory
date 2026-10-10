"""Patch release regressions using temporary projects and simulated commands."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import hardening
import quality


class ReadinessTests(unittest.TestCase):
    tearDown = hardening.ReleaseTests.tearDown

    def setUp(self):
        hardening.ReleaseTests.setUp(self)
        self.commit('.gitignore', '.claude/state/\n')

    def commit(self, name, content):
        (self.root / name).write_text(content, encoding='utf-8')
        subprocess.run(['git', 'add', name], cwd=self.root, check=True)
        subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                        'commit', '-qm', 'fixture'], cwd=self.root, check=True)

    def test_docs_profile_requires_resolvable_base(self):
        subprocess.run(['git', 'branch', '-m', 'master'], cwd=self.root, check=True)
        self.commit('app.py', 'print(1)\n')
        self.assertFalse(quality.docs_only(self.root, {'mission': {}}))
        self.assertFalse(quality.docs_only(self.root, {'mission': {'base': 'main'}}))

    def test_docs_profile_uses_origin_head_and_covers_committed_code(self):
        head = quality.git(self.root, 'rev-parse', 'HEAD')
        subprocess.run(['git', 'update-ref', 'refs/remotes/origin/main', head], cwd=self.root, check=True)
        subprocess.run(['git', 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main'],
                       cwd=self.root, check=True)
        self.commit('app.py', 'print(1)\n')
        self.assertFalse(quality.docs_only(self.root, {'mission': {}}))

    def test_docs_profile_accepts_docs_and_rejects_untracked_or_dirty_code(self):
        self.commit('app.py', 'print(1)\n')
        base = quality.git(self.root, 'rev-parse', 'HEAD')
        self.commit('README.md', 'Updated documentation.\n')
        self.assertTrue(quality.docs_only(self.root, {'mission': {'base': base}}))
        (self.root / 'app.py').write_text('print(2)\n', encoding='utf-8')
        self.assertFalse(quality.docs_only(self.root, {'mission': {'base': base}}))
        (self.root / 'app.py').write_text('print(1)\n', encoding='utf-8')
        (self.root / 'new.py').write_text('print(3)\n', encoding='utf-8')
        self.assertFalse(quality.docs_only(self.root, {'mission': {'base': base}}))

    def gate_with_process(self, stack, deadline=None, timeout_error=False):
        proc = Mock(returncode=0)
        if timeout_error:
            proc.communicate.side_effect = [subprocess.TimeoutExpired('fixture', 1), ('', '')]
        else:
            proc.communicate.return_value = ('fixture passed', '')
        commands = {'lint': {'skip': 'fixture'}, 'test': 'fixture-command', 'build': {'skip': 'fixture'}}
        with patch.object(quality, 'defaults', return_value=(stack, commands, None)), \
                patch.object(quality, 'git', return_value='fixture-head'), \
                patch.object(quality.subprocess, 'run', return_value=Mock(stdout=b'')), \
                patch.object(quality.subprocess, 'Popen', return_value=proc) as spawn, \
                patch.object(quality.time, 'time', return_value=100), \
                patch('runner.terminate') as terminate:
            evidence = quality.gate(self.root, {}, deadline)
        return proc, spawn, terminate, evidence

    def test_standalone_harness_timeout_allows_serial_installed_suite(self):
        proc, _, _, evidence = self.gate_with_process('harness')
        self.assertTrue(evidence['pass'])
        self.assertEqual(proc.communicate.call_args.kwargs['timeout'], 5400)

    def test_project_timeout_remains_900_seconds(self):
        proc, _, _, _ = self.gate_with_process('node')
        self.assertEqual(proc.communicate.call_args.kwargs['timeout'], 900)

    def test_controller_deadline_takes_precedence(self):
        proc, _, _, _ = self.gate_with_process('harness', deadline=130)
        self.assertEqual(proc.communicate.call_args.kwargs['timeout'], 30)

    def test_expired_deadline_does_not_launch_check(self):
        _, spawn, _, evidence = self.gate_with_process('harness', deadline=99)
        spawn.assert_not_called()
        self.assertFalse(evidence['pass'])
        self.assertEqual(evidence['checks'][1]['exit'], 124)

    def test_timeout_terminates_process_and_fails_gate(self):
        _, _, terminate, evidence = self.gate_with_process('harness', timeout_error=True)
        terminate.assert_called_once()
        self.assertFalse(evidence['pass'])
        self.assertEqual(evidence['checks'][1]['exit'], 124)

    def test_installed_suite_inherits_explicit_job_limit(self):
        source = Path(__file__).resolve().parents[1]
        fixture = self.root / 'harness'
        fixture.mkdir()
        # Installed-copy self-tests run before the target's first commit, so Git's
        # tracked-file list is not a source inventory. Use the real install manifest.
        for entry in (source / 'scripts/manifest.txt').read_text().splitlines():
            pattern = entry.split('#', 1)[0].strip()
            if not pattern:
                continue
            for path in source.glob(pattern):
                if not path.is_file():
                    continue
                target = fixture / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        observed = self.root / 'nested-jobs.txt'
        (fixture / 'tests/run.sh').write_text(
            '#!/usr/bin/env bash\nprintf "%s" "${QS_TEST_JOBS:-unset}" > ' +
            shlex.quote(observed.as_posix()) + '\n', encoding='utf-8')
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(fixture)], check=True)
        env = dict(os.environ, QS_TEST_JOBS='1', QS_PYTHON=sys.executable.replace('\\', '/'),
                   CLAUDE_PROJECT_DIR=str(fixture))
        env.pop('QS_INIT_NESTED', None)
        env.pop('QS_TEST_LOG_DIR', None)
        result = subprocess.run([quality.bash(), 'tests/init.sh'], cwd=fixture, env=env,
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-3000:])
        self.assertEqual(observed.read_text(), '1')


if __name__ == '__main__':
    unittest.main(verbosity=1)
