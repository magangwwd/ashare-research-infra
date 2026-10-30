"""Exercise the real runner orchestration with synthetic external I/O only."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import github_drive_runner as runner


class CurrentOwnershipRunnerTests(unittest.TestCase):
    def execute(self, actual_day="2026-10-29", promote=False, fail_check=False):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);code=root/'code';(code/'configs').mkdir(parents=True)
        (code/'configs/ownership_rebuild.json').write_text(json.dumps({'research_end':'2026-08-11'}))
        scratch=root/'scratch';scratch.mkdir();commands=[];publications=[];seen_configs=[]
        def copy(src,dest):
            if str(src).startswith('gdrive:'):
                dest=Path(dest);dest.mkdir(parents=True,exist_ok=True)
                (dest/'manifest.json').write_text(json.dumps({'as_of_date':'2026-10-29',
                    'validation':{'primary_keys_unique':True,'ohlc_valid':True}}))
            else:publications.append(str(dest))
        def run(*args,**kwargs):
            commands.append(args)
            if len(args)>1 and args[1] in ('download_ownership.py','ownership_pipeline.py','run_pipeline.py'):
                config=json.loads(Path(args[args.index('--config')+1]).read_text());seen_configs.append(config)
                if args[1]=='ownership_pipeline.py':
                    processed=Path(config['processed_data_dir']);processed.mkdir(parents=True)
                    (processed/'ownership_events.parquet').write_bytes(b'synthetic fresh events')
                    self.assertIn('--skip-labels',args)
                if args[1]=='run_pipeline.py':
                    self.assertTrue((Path(config['processed_data_dir'])/'ownership_events.parquet').is_file())
                    output=code/'output/state_definition_20261029';output.mkdir()
                    (output/'summary.json').write_text(json.dumps({'as_of_date':actual_day}))
        with patch.object(runner,'CODE',code),patch.object(runner,'require_drive'),\
             patch.object(runner.tempfile,'mkdtemp',return_value=str(scratch)),\
             patch.object(runner,'copy',side_effect=copy),\
             patch.object(runner,'check',side_effect=RuntimeError('byte mismatch') if fail_check else None),\
             patch.object(runner,'run',side_effect=run),\
             patch.dict(os.environ,{'TUSHARE_TOKEN':'SYNTHETIC_TEST_ONLY','GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}),\
             patch('sys.argv',['github_drive_runner.py','ownership-refresh']+(['--promote-ownership'] if promote else [])):
            if actual_day!='2026-10-29' or fail_check:
                with self.assertRaisesRegex(RuntimeError,'mismatch'):runner.main()
            else:self.assertEqual(runner.main(),0)
        return root,seen_configs,publications,commands

    def test_download_processing_and_state_share_same_current_version(self):
        root,configs,published,commands=self.execute()
        self.assertEqual(len(configs),3)
        self.assertTrue(all(c==configs[0] for c in configs))
        self.assertEqual(configs[0]['research_end'],'2026-10-29')
        self.assertEqual(published,['gdrive:stock_state_strategy/research/ownership/2026-10-29/123-1'])
        self.assertFalse(any('artifacts/legacy' in str(c) for c in configs))

    def test_wrong_state_date_never_publishes_snapshot(self):
        root,configs,published,commands=self.execute('2026-10-28')
        self.assertEqual(published,[])

    def test_promotion_occurs_only_after_snapshot_and_state_checks(self):
        root,configs,published,commands=self.execute(promote=True)
        self.assertEqual(published[-1],'gdrive:stock_state_strategy/output/state_definition_20261029')
        markers=[c for c in commands if len(c)>1 and c[0:2]==('rclone','copyto')
                 and str(c[-1]).endswith('/current_ownership.json')]
        self.assertEqual(len(markers),1)

    def test_failed_snapshot_check_never_switches_pointer(self):
        root,configs,published,commands=self.execute(promote=True,fail_check=True)
        self.assertFalse(any(str(c[-1]).endswith('/current_ownership.json') for c in commands))
