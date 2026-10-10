"""Launcher/preflight/publishing compatibility cases with simulated providers."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import hardening

import preflight
import quality
import runner
import runtime


class ControllerTests(unittest.TestCase):
    setUp = hardening.ReleaseTests.setUp
    tearDown = hardening.ReleaseTests.tearDown

    def setup_launcher(self):
        source = Path(__file__).resolve().parents[1]
        shutil.copytree(source/'scripts', self.root/'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(source/'.claude/agents', self.root/'.claude/agents')
        (self.root/'.github').mkdir(exist_ok=True)
        shutil.copy(source/'.github/pull_request_template.md', self.root/'.github/pull_request_template.md')
        runtime.atomic(self.root/'.claude/settings.json', {'permissions':{'allow':['Bash(git status *)']}, 'hooks':{}})
        self.config['hashes'] = runner.hashes(self.root)
        runtime.atomic(runtime.state()/'controller.json', self.config)
        self.check = {'errors':[], 'brief':self.b, 'auth':{'authMethod':'claude.ai'}}

    def call_main(self, lead_rc=0, payload=None, reason=None, finalize_rc=0):
        self.setup_launcher()
        return self.invoke(lead_rc, payload, reason, finalize_rc)

    def invoke(self, lead_rc=0, payload=None, reason=None, finalize_rc=0, real_ledger=False):
        """One runner.main() against the current state; real_ledger runs the real `ledger.py archive-stale`."""
        calls=[]
        def launch(args, root, output, deadline, env):
            calls.append(args)
            runtime.atomic(output, payload if payload is not None else {'session_id':'session-123','is_error':False})
            return lead_rc, reason
        original = runner.run
        stubbed = ('scripts/brief.py',) if real_ledger else ('scripts/brief.py','scripts/ledger.py')
        def commands(args, root, **kwargs):
            if len(args)>1 and args[1] in stubbed:
                return 'current'
            return original(args,root,**kwargs)
        with patch.object(preflight,'inspect',return_value=self.check), patch.object(runner,'launch',side_effect=launch), \
                patch.object(runner,'run',side_effect=commands), patch.object(runner,'finalize',return_value=finalize_rc), \
                patch.dict(os.environ,{'QS_OVERSEER':'0','QS_SLEEP':'0'}):
            code=runner.main()
        return code,calls

    def test_early_lead_crashes_keep_controller_state_until_restart_limit(self):
        # The lead dies before `ledger.py init` every time: no ledgers ever exist. The terminal RUN_STATE each
        # failure leaves must not let archive-stale reset the deadline or the launch counter.
        self.setup_launcher()
        deadline=runtime.load(runtime.state()/'controller.json')['deadline']
        launched=0
        for attempt in range(1,7):
            code,calls=self.invoke(lead_rc=1,payload={},real_ledger=True)
            launched+=len(calls)
            self.assertEqual(code,3)
            self.assertEqual(runtime.load(runtime.state()/'controller.json')['deadline'],deadline)
        self.assertEqual(launched,5)
        self.assertEqual(runtime.load(self.root/'docs/RUN_STATE')['reason'],'restart limit (5) reached')
        self.assertFalse((self.root/'docs/runs').exists())

    def stale_run(self, ledgers):
        """An earlier mission (different goal) that ended terminal, left in this checkout."""
        self.setup_launcher()
        earlier=dict(self.b,mission={'goal':'An earlier mission','deliverables':['README.md']})
        runtime.atomic(runtime.state()/'controller.json',dict(self.config,brief=earlier))
        runner.finish(self.root,'DONE','old')
        runtime.atomic(runtime.state()/'session_id','old-session\n')
        if ledgers:
            runtime.atomic(self.root/'docs/ledgers/task.json',{'goal':'An earlier mission','plan':[]})

    def test_lone_terminal_state_from_other_goal_is_archived_and_run_starts(self):
        self.stale_run(ledgers=False)
        code,calls=self.invoke(real_ledger=True)
        self.assertEqual((code,len(calls)),(0,1))
        self.assertEqual(len(list((self.root/'docs/runs').iterdir())),1)
        self.assertEqual(runtime.load(runtime.state()/'controller.json')['brief']['mission']['goal'],'test')

    def test_stale_other_goal_run_is_archived_and_not_resumed(self):
        self.stale_run(ledgers=True)
        code,calls=self.invoke(real_ledger=True)
        self.assertEqual((code,len(calls)),(0,1))
        self.assertNotIn('--resume',calls[0])
        self.assertNotIn('old-session',calls[0])
        self.assertEqual(len(list((self.root/'docs/runs').iterdir())),1)

    def archive(self):
        """`runner.main(['--archive'])` with the real ledger; no model may be launched."""
        out,err=io.StringIO(),io.StringIO()
        with patch.object(preflight,'inspect',return_value=self.check), patch.object(runner,'launch') as launch, \
                redirect_stdout(out), redirect_stderr(err):
            code=runner.main(['--archive'])
        launch.assert_not_called()
        return code,out.getvalue(),err.getvalue()

    def test_archive_terminal_run_then_new_goal_starts_fresh(self):
        self.setup_launcher()
        runtime.atomic(runtime.state()/'session_id','session-1\n')
        runner.finish(self.root,'DONE_PARTIAL','budget exhausted before final verification')
        code,out,_=self.archive()
        self.assertEqual(code,0)
        self.assertIn('archived docs/runs/',out)
        for gone in (runtime.state()/'controller.json',runtime.state()/'session_id',self.root/'docs/RUN_STATE'):
            self.assertFalse(gone.exists(),gone)
        new=dict(self.b,mission={'goal':'next goal','deliverables':['README.md']})
        self.check['brief']=new
        runtime.atomic(runtime.state()/'brief.json',new)
        code,calls=self.invoke(real_ledger=True)
        self.assertEqual((code,len(calls)),(0,1))
        self.assertNotIn('--resume',calls[0])
        self.assertEqual(runtime.load(runtime.state()/'controller.json')['brief']['mission']['goal'],'next goal')

    def test_archive_refused_while_run_not_terminal(self):
        self.setup_launcher()
        runtime.atomic(runtime.state()/'session_id','session-1\n')
        code,_,err=self.archive()
        self.assertEqual(code,2)
        self.assertIn('touch .claude/state/cancel',err)
        self.assertTrue((runtime.state()/'controller.json').exists())
        self.assertTrue((runtime.state()/'session_id').exists())

    def test_run_sh_passes_archive_flag(self):
        self.setup_launcher()
        env=dict(os.environ,QS_PYTHON=sys.executable)
        refused=subprocess.run([quality.bash(),'scripts/run.sh','--archive'],cwd=self.root,env=env,capture_output=True,text=True,timeout=60)
        self.assertEqual(refused.returncode,2,refused.stderr)
        runner.finish(self.root,'SAFE_STOP','cancelled')
        archived=subprocess.run([quality.bash(),'scripts/run.sh','--archive'],cwd=self.root,env=env,capture_output=True,text=True,timeout=60)
        self.assertEqual(archived.returncode,0,archived.stderr)
        self.assertIn('archived docs/runs/',archived.stdout)

    def test_budget_only_change_resumes_with_new_deadline(self):
        (self.root/'BRIEF.yaml').write_text('budgets: {wall_clock_min: 60}\n')
        self.setup_launcher()
        runtime.atomic(runtime.state()/'session_id','session-1\n')
        runner.finish(self.root,'DONE_PARTIAL','wall-clock deadline exceeded')
        (self.root/'BRIEF.yaml').write_text('budgets: {wall_clock_min: 120}\n')
        self.check['brief']=dict(self.b,budgets=dict(self.b['budgets'],wall_clock_min=120))
        code,calls=self.invoke()
        self.assertEqual((code,len(calls)),(0,1))
        self.assertEqual(calls[0][calls[0].index('--resume')+1],'session-1')
        config=runtime.load(runtime.state()/'controller.json')
        started=runner.dt.datetime.fromisoformat((runtime.state()/'started_at').read_text().strip()).timestamp()
        self.assertAlmostEqual(config['deadline'],started+120*60,places=3)
        self.assertEqual(config['brief']['budgets']['wall_clock_min'],120)
        self.assertEqual(runtime.load(self.root/'docs/RUN_STATE')['state'],'DONE_PARTIAL')
        self.assertEqual(config['hashes'],runner.hashes(self.root))

    def test_goal_change_without_archive_refused_with_hint(self):
        self.setup_launcher()
        runner.finish(self.root,'DONE_PARTIAL','budget exhausted before final verification')
        self.check['brief']=dict(self.b,mission={'goal':'other goal','deliverables':['README.md']})
        err=io.StringIO()
        with redirect_stderr(err):
            code,calls=self.invoke()
        self.assertEqual((code,len(calls)),(2,0))
        self.assertIn('bash scripts/run.sh --archive',err.getvalue())

    def test_undecodable_chatty_stderr_does_not_block_the_child(self):
        # 0x81 is invalid UTF-8 and undefined in cp1252: a strict decoder kills the drain thread, the pipe fills
        # and the child blocks until the deadline.
        child="import sys; sys.stderr.buffer.write(b'\\x81' + b'x' * 1000000); sys.stderr.flush()"
        started=time.time()
        rc,reason=runner.launch([sys.executable,'-c',child],self.root,runtime.state()/'child.json',time.time()+15,dict(os.environ))
        self.assertEqual((rc,reason),(0,None))
        self.assertLess(time.time()-started,10)
        self.assertIn('�',(runtime.state()/'last_stderr.txt').read_text(encoding='utf-8'))

    def test_fresh_start_passes_flags_and_saves_session(self):
        code,calls=self.call_main()
        self.assertEqual(code,0)
        for flag in ('-p','--permission-prompts','--allowedTools','--strict-mcp-config','--settings','--agents','--max-turns'):
            self.assertIn(flag,calls[0])
        self.assertNotIn('--bare',calls[0])
        self.assertNotIn('--max-budget-usd',calls[0])
        self.assertEqual((runtime.state()/'session_id').read_text().strip(),'session-123')

    def test_resume_uses_saved_session(self):
        runtime.atomic(runtime.state()/'session_id','previous-session\n')
        code,calls=self.call_main()
        self.assertEqual(code,0)
        self.assertEqual(calls[0][calls[0].index('--resume')+1],'previous-session')

    def test_resume_restores_only_controller_report_changes(self):
        report=self.root/'docs/REPORT.md'
        runtime.atomic(report,'# Lead report\n\nEvidence remains.\n')
        runner.finish(self.root,'ERROR','publication unavailable')
        runner.restore_controller_report(self.root)
        self.assertEqual(report.read_text(),'# Lead report\n\nEvidence remains.\n')
        runner.finish(self.root,'SAFE_STOP','interrupted')
        report.write_text(report.read_text()+'User addition.\n')
        runner.restore_controller_report(self.root)
        self.assertTrue(report.read_text().endswith('User addition.\n'))

    def test_done_returns_without_model(self):
        runtime.atomic(self.root/'docs/COMPLETION.json',{'schema':3,'state':'DONE'})
        code,calls=self.call_main()
        self.assertEqual((code,len(calls)),(0,0))

    def test_halt_returns_without_model(self):
        runtime.atomic(self.root/'docs/COMPLETION.json',{'schema':3,'state':'HALT'})
        code,calls=self.call_main()
        self.assertEqual((code,len(calls)),(4,0))

    def test_restart_limit_produces_report(self):
        with runtime.transaction() as db:
            runtime.put(db,'launches',5)
        code,calls=self.call_main()
        self.assertEqual((code,len(calls)),(3,0))
        self.assertIn('restart limit',(self.root/'docs/REPORT.md').read_text())

    def test_expired_resume_does_not_launch(self):
        self.config['deadline']=time.time()-1
        runtime.atomic(runtime.state()/'controller.json',self.config)
        code,calls=self.call_main()
        self.assertEqual((code,len(calls)),(3,0))

    def test_cancel_and_deadline_states(self):
        code,_=self.call_main(reason='cancelled')
        self.assertEqual(code,3)
        self.assertEqual(runtime.load(self.root/'docs/RUN_STATE')['state'],'SAFE_STOP')

    def test_model_error_does_not_finalize_successfully(self):
        code,_=self.call_main(lead_rc=1,payload={'session_id':'session-123','is_error':True})
        self.assertEqual(code,3)
        self.assertEqual(runtime.load(self.root/'docs/RUN_STATE')['state'],'SAFE_STOP')

    def retryable(self):
        return runtime.load(self.root/'docs/COMPLETION.json')['retryable']

    def test_lead_crash_is_retryable(self):
        code,_=self.call_main(lead_rc=1,payload={})
        self.assertEqual(code,3)
        self.assertIs(self.retryable(),True)

    def test_exception_error_is_retryable(self):
        with patch.object(runner,'restore_controller_report',side_effect=OSError('disk unavailable')):
            code,_=self.call_main()
        self.assertEqual(code,5)
        self.assertEqual(runtime.load(self.root/'docs/RUN_STATE')['state'],'ERROR')
        self.assertIs(self.retryable(),True)

    def test_final_outcomes_are_not_retryable(self):
        self.setup_launcher()
        for label,kwargs in (('cancelled',{'reason':'cancelled'}),('deadline',{'reason':'wall-clock deadline exceeded'})):
            with self.subTest(label):
                self.invoke(**kwargs)
                self.assertIs(self.retryable(),False)
        with self.subTest('restart limit'):
            with runtime.transaction() as db:
                runtime.put(db,'launches',5)
            self.invoke()
            self.assertIn('restart limit',runtime.load(self.root/'docs/RUN_STATE')['reason'])
            self.assertIs(self.retryable(),False)
        with self.subTest('budget'):
            runtime.atomic(runtime.state()/'steps','1000')
            self.invoke()
            self.assertIs(self.retryable(),False)
        for state in ('DONE','HALT','DONE_PARTIAL'):
            with self.subTest(state):
                runner.finish(self.root,state,'final')
                self.assertIs(self.retryable(),False)

    def test_killed_run_recovers_transcript_session(self):
        transcript=runtime.state()/'session-abcd.jsonl';transcript.write_text('{}\n')
        runtime.atomic(runtime.state()/'transcript_path',str(transcript))
        code,_=self.call_main(lead_rc=137,payload={})
        self.assertEqual(code,3)
        self.assertEqual((runtime.state()/'session_id').read_text().strip(),'session-abcd')

    def test_missing_saved_transcript_stops_before_model(self):
        runtime.atomic(runtime.state()/'transcript_path',str(self.root/'missing.jsonl'))
        code,calls=self.call_main()
        self.assertEqual((code,len(calls)),(3,0))

    def test_exhausted_steps_stop_before_model(self):
        runtime.atomic(runtime.state()/'steps','1000')
        code,calls=self.call_main()
        self.assertEqual((code,len(calls)),(3,0))

    def test_process_success_propagates_verification_failure(self):
        code,_=self.call_main(finalize_rc=5)
        self.assertEqual(code,5)

    def test_preflight_failure_no_model(self):
        self.setup_launcher()
        self.check['errors']=['brief missing']
        with patch.object(preflight,'inspect',return_value=self.check), patch.object(runner,'launch') as launch:
            self.assertEqual(runner.main(),2)
            launch.assert_not_called()

    def test_changed_brief_refused_without_model(self):
        self.setup_launcher()
        self.check['brief']=dict(self.b,maintenance=True)
        with patch.object(preflight,'inspect',return_value=self.check), patch.object(runner,'run',return_value='current'), patch.object(runner,'launch') as launch:
            self.assertEqual(runner.main(),2)
            launch.assert_not_called()

    def test_auth_diagnostics_redact_identifiers(self):
        self.setup_launcher()
        (self.root/'BRIEF.yaml').write_text('mission: {}')
        def command(args,root):
            if args[0]==quality.bash():return 'GNU bash, version 5.2'
            if args==['claude','auth','status']:return json.dumps({'loggedIn':True,'authMethod':'claude.ai','subscriptionType':'max','email':'private@example.invalid'})
            if args==['claude','--version']:return '2.1.288 (Claude Code)'
            if args==['claude','--help']:return '--permission-prompts --strict-mcp-config --allowedTools --max-budget-usd'
            return ''
        with patch.object(preflight,'command',side_effect=command), patch.object(shutil,'which',return_value='/tool'):
            info=preflight.inspect(self.root)
        self.assertNotIn('private@example.invalid',json.dumps(info))
        self.assertEqual(info['auth']['subscriptionType'],'max')

    def test_sandbox_status_reports_platform_and_dependencies(self):
        runtime.atomic(self.root/'.claude/settings.json',{'sandbox':{'enabled':True}})
        windows=preflight.sandbox_status(self.root,system='win32')
        self.assertFalse(windows['available']); self.assertIn('WSL2',windows['reason'])
        missing=preflight.sandbox_status(self.root,system='linux',which=lambda exe: None)
        self.assertFalse(missing['available']); self.assertIn('bubblewrap',missing['reason']); self.assertIn('socat',missing['reason'])
        self.assertTrue(preflight.sandbox_status(self.root,system='linux',which=lambda exe: '/usr/bin/'+exe)['available'])
        self.assertTrue(preflight.sandbox_status(self.root,system='darwin',which=lambda exe: None)['available'])
        runtime.atomic(self.root/'.claude/settings.json',{'sandbox':{'enabled':False}})
        self.assertIn('disabled',preflight.sandbox_status(self.root,system='linux',which=lambda exe: '/usr/bin/'+exe)['reason'])
        self.assertIn('available',preflight.inspect(self.root)['sandbox'])

    def test_docs_profile_needs_a_resolvable_baseline(self):
        self.setup_launcher()
        (runtime.state()/'controller.json').unlink()
        yaml=('mission:\n  goal: test\n  deliverables:\n    - README.md\n{base}success_criteria:\n  - {{kind: file, path: README.md}}\n'
              'budgets:\n  tokens: 100\n  cost_usd: 1\n  wall_clock_min: 10\n  steps: 10\npermissions:\n  irreversible:\n'
              '    default: skip-and-record\nambiguity_policy: choose-default-and-record\nquality:\n  profile: docs\n')
        (self.root/'BRIEF.yaml').write_text(yaml.format(base=''))
        with patch.object(preflight,'command',side_effect=ValueError('fixture')):
            info=preflight.inspect(self.root)
        self.assertTrue(any('origin/HEAD' in x for x in info['errors']),info['errors'])
        (self.root/'BRIEF.yaml').write_text(yaml.format(base='  base: main\n'))
        with patch.object(preflight,'command',side_effect=ValueError('fixture')):
            info=preflight.inspect(self.root)
        self.assertFalse(any('origin/HEAD' in x for x in info['errors']),info['errors'])

    def test_incompatible_old_runtime_preserved(self):
        self.setup_launcher()
        (runtime.state()/'controller.json').unlink()
        runtime.atomic(runtime.state()/'session_id','old')
        # Minimal valid YAML with optional quality overrides in block form.
        (self.root/'BRIEF.yaml').write_text('''mission:
  goal: test
  deliverables:
    - README.md
success_criteria:
  - {kind: file, path: README.md}
budgets:
  tokens: 100
  cost_usd: 1
  wall_clock_min: 10
  steps: 10
permissions:
  irreversible:
    default: skip-and-record
ambiguity_policy: choose-default-and-record
quality:
  profile: docs
''')
        with patch.object(preflight,'command',side_effect=ValueError('fixture')):
            info=preflight.inspect(self.root)
        self.assertTrue(any('v0.2 active state' in x for x in info['errors']))
        self.assertEqual((runtime.state()/'session_id').read_text(),'old')

    def test_pr_scope_excludes_known_generated_files(self):
        numstat='10\t2\tsrc/app.py\n500\t0\tpackage-lock.json\n1\t1\ttests/test_app.py'
        with patch.object(runner,'run',return_value=numstat):
            scope=runner.pr_scope(self.root,'main','abc')
        self.assertEqual(scope['reviewable_lines'],14)
        self.assertEqual(scope['files'],3)
        self.assertEqual(scope['excluded'],['package-lock.json'])

    def test_pr_body_uses_repo_template_and_controller_evidence(self):
        (self.root/'.github').mkdir(exist_ok=True)
        shutil.copy(Path(__file__).resolve().parents[1]/'.github/pull_request_template.md',
                    self.root/'.github/pull_request_template.md')
        runtime.atomic(self.root/'docs/REPORT.md','# Lead report\n\nVerified evidence.\n')
        with patch.object(runner,'pr_scope',return_value={'reviewable_lines':123,'files':7,'excluded':['uv.lock']}):
            body=runner.render_pr_body(self.root,self.b,'mission/test','main','abc123')
        self.assertIn('**Mission:** `mission/test`',body)
        self.assertIn('**Reviewable changed lines:** `123`',body)
        self.assertIn('**Files changed:** `7`',body)
        self.assertIn('Commit: `abc123`',body)
        self.assertIn('- [x] `bash scripts/gate.sh` passed',body)
        self.assertIn('- [x] Controller/security verification completed',body)
        self.assertIn('Verified evidence.',body)
        self.assertIn('uv.lock',body)
        self.assertIn('- [ ] `ci-required` passed',body)
        self.assertIn('- Not declared in the mission brief',body)
        self.assertNotIn('`<REQ-xxx>`',body)

    def test_pr_body_lists_brief_requirements(self):
        (self.root/'.github').mkdir(exist_ok=True)
        shutil.copy(Path(__file__).resolve().parents[1]/'.github/pull_request_template.md',
                    self.root/'.github/pull_request_template.md')
        self.b['mission']['requirements']=['REQ-007','REQ-012']
        with patch.object(runner,'pr_scope',return_value={'reviewable_lines':1,'files':1,'excluded':[]}):
            body=runner.render_pr_body(self.root,self.b,'mission/test','main','abc123')
        self.assertIn('**Requirements:**\n- `REQ-007`\n- `REQ-012`\n',body)
        self.assertNotIn('Not declared in the mission brief',body)

    def test_publication_reconciles_existing_pr(self):
        self.b['permissions']['irreversible']['allow']=['git push origin mission/*','gh pr create*']
        pub={'url':'https://github.com/example/test/pull/1','headRefOid':'abc','headRefName':'mission/test','baseRefName':'main'}
        mutations=[]
        def provider(args,root,**kw):
            if args[:3]==['gh','repo','view']:return 'main'
            if args[:2]==['git','ls-remote']:return 'abc\trefs/heads/mission/test'
            if args[:3]==['gh','pr','list']:return json.dumps([pub])
            mutations.append(args);return ''
        with patch.object(runner,'run',side_effect=provider):
            first=runner.publish(self.root,self.config,'mission/test','abc')
            second=runner.publish(self.root,self.config,'mission/test','abc')
        self.assertEqual(first,second)
        self.assertFalse(mutations)

    def test_pr_mismatch_refuses_done(self):
        self.b['permissions']['irreversible']['default']='allow'
        pub={'url':'https://github.com/example/test/pull/1','headRefOid':'wrong','headRefName':'mission/test','baseRefName':'main'}
        def provider(args,root,**kw):
            if args[:3]==['gh','repo','view']:return 'main'
            if args[:2]==['git','ls-remote']:return 'abc\trefs/heads/mission/test'
            if args[:3]==['gh','pr','list']:return json.dumps([pub])
            return ''
        with patch.object(runner,'run',side_effect=provider), self.assertRaises(ValueError):
            runner.publish(self.root,self.config,'mission/test','abc')

    def test_publication_never_main(self):
        self.b['permissions']['irreversible']['default']='allow'
        with patch.object(runner,'run',return_value='main'),self.assertRaises(ValueError):
            runner.publish(self.root,self.config,'main','abc')

    def test_cancellation_during_final_verification_prevents_publication(self):
        (self.root/'.git/info/exclude').write_text('.claude/state/\ndocs/REPORT.md\n')
        tp=runtime.state()/'usage.jsonl';tp.write_text('{}\n')
        runtime.atomic(runtime.state()/'transcript_path',str(tp))
        runtime.atomic(self.root/'docs/RESULT.json',{'state':'READY'})
        head=quality.git(self.root,'rev-parse','HEAD')
        with runtime.transaction() as db:
            runtime.put(db,'security',{'verdict':'PASS','head':head})
        for stage in ('before', 'gate', 'criteria', 'publication', 'publication-stop'):
            with self.subTest(stage=stage):
                (runtime.state()/'cancel').unlink(missing_ok=True)
                def gate(*args):
                    if stage=='gate': runtime.atomic(runtime.state()/'cancel','stop')
                    return {'pass':True}
                def criteria(*args):
                    if stage=='criteria': runtime.atomic(runtime.state()/'cancel','stop')
                    return {'failed':0,'deferred':0}
                def publication(*args):
                    if stage=='publication-stop': raise runner.Cancelled('cancelled')
                    runtime.atomic(runtime.state()/'cancel','stop')
                    return {'url':'https://example.invalid/pr'}
                if stage=='before': runtime.atomic(runtime.state()/'cancel','stop')
                with patch.object(quality,'gate',side_effect=gate), \
                        patch.object(runner,'verify_criteria',side_effect=criteria), \
                        patch.object(runner,'publish',side_effect=publication) as pub:
                    self.assertEqual(runner.finalize(self.root,self.config),3)
                    if stage not in ('publication', 'publication-stop'): pub.assert_not_called()
                result=runtime.load(self.root/'docs/COMPLETION.json')
                self.assertEqual(result['state'],'SAFE_STOP')
                self.assertFalse(result['retryable'])

    def stage_pr_template(self):
        (self.root/'.github').mkdir(exist_ok=True)
        shutil.copy(Path(__file__).resolve().parents[1]/'.github/pull_request_template.md',
                    self.root/'.github/pull_request_template.md')

    def test_publication_rechecks_cancellation_before_push_and_pr_create(self):
        self.stage_pr_template()
        self.b['permissions']['irreversible']['default']='allow'
        self.b['mission']['base']='main'
        for stop_after in ('start', 'remote', 'pr-list'):
            with self.subTest(stop_after=stop_after):
                (runtime.state()/'cancel').unlink(missing_ok=True)
                calls=[]
                if stop_after=='start': runtime.atomic(runtime.state()/'cancel','stop')
                def provider(args,root,**kw):
                    calls.append(args)
                    if args[:2]==['git','ls-remote']:
                        if stop_after=='remote': runtime.atomic(runtime.state()/'cancel','stop')
                        return '' if stop_after=='remote' else 'abc\trefs/heads/mission/test'
                    if args[:3]==['gh','pr','list']:
                        runtime.atomic(runtime.state()/'cancel','stop')
                        return '[]'
                    self.fail('unexpected mutation: '+repr(args))
                with patch.object(runner,'run',side_effect=provider), self.assertRaisesRegex(runner.Cancelled,'cancelled'):
                    runner.publish(self.root,self.config,'mission/test','abc')
                self.assertEqual(len(calls),{'start':0,'remote':1,'pr-list':2}[stop_after])

    def test_expired_publication_makes_no_provider_call(self):
        self.config['deadline']=time.time()-1
        with patch.object(runner,'run') as provider, self.assertRaisesRegex(ValueError,'deadline'):
            runner.publish(self.root,self.config,'mission/test','abc')
        provider.assert_not_called()

    def test_cancellation_after_publication_mutation_preserves_receipt(self):
        self.stage_pr_template()
        self.b['permissions']['irreversible']['default']='allow'
        self.b['mission']['base']='main'
        for stage in ('push', 'pr-create'):
            with self.subTest(stage=stage):
                (runtime.state()/'cancel').unlink(missing_ok=True)
                with runtime.transaction() as db:
                    runtime.put(db,'publication',{})
                calls=[]
                def provider(args,root,**kw):
                    calls.append(args)
                    if args[:2]==['git','ls-remote']:
                        return '' if stage=='push' else 'abc\trefs/heads/mission/test'
                    if args[:2]==['git','push']:
                        runtime.atomic(runtime.state()/'cancel','stop')
                        return ''
                    if args[:3]==['gh','pr','list']: return '[]'
                    if args[:3]==['git','diff','--numstat']: return '1\t1\ta.py'
                    if args[:3]==['gh','pr','create']:
                        runtime.atomic(runtime.state()/'cancel','stop')
                        return 'https://github.com/example/test/pull/1'
                    self.fail('unexpected publication call: '+repr(args))
                with patch.object(runner,'run',side_effect=provider), self.assertRaises(runner.Cancelled):
                    runner.publish(self.root,self.config,'mission/test','abc')
                with runtime.transaction() as db:
                    receipt=runtime.get(db,'publication')
                self.assertEqual(receipt['head'],'abc')
                self.assertEqual(receipt['status'],'pushed' if stage=='push' else 'pr-created')
                if stage=='pr-create': self.assertEqual(receipt['url'],'https://github.com/example/test/pull/1')
                self.assertEqual(len(calls),2 if stage=='push' else 4)  # ls-remote, pr list, diff for the body, pr create

    def test_cancelled_resume_reports_previous_publication_receipt(self):
        receipt={'status':'pushed','branch':'mission/test','base':'main','head':'saved-commit'}
        with runtime.transaction() as db:
            runtime.put(db,'publication',receipt)
        self.assertEqual(runner.finish(self.root,'SAFE_STOP','cancelled'),3)
        completion=runtime.load(self.root/'docs/COMPLETION.json')
        self.assertEqual(completion['publication'],receipt)
        self.assertFalse(completion['retryable'])
        self.assertIn('saved-commit',(self.root/'docs/REPORT.md').read_text())

    def test_publication_rechecks_deadline_between_calls(self):
        self.b['permissions']['irreversible']['default']='allow'
        calls=[]
        def provider(args,root,**kw):
            calls.append(args)
            self.assertLessEqual(kw['timeout'],30)
            self.config['deadline']=time.time()-1
            return 'main'
        with patch.object(runner,'run',side_effect=provider), self.assertRaisesRegex(ValueError,'deadline'):
            runner.publish(self.root,self.config,'mission/test','abc')
        self.assertEqual(len(calls),1)


if __name__=='__main__':
    unittest.main()
