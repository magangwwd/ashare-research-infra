"""Exercise the real runner orchestration with synthetic external I/O only."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import github_drive_runner as runner


class CurrentOwnershipRunnerTests(unittest.TestCase):
    def execute(self, actual_day="2026-10-29", promote=False, fail_check=False, reuse=False, prior_success=True):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);code=root/'code';(code/'configs').mkdir(parents=True)
        (code/'configs/ownership_rebuild.json').write_text(json.dumps({'research_end':'2026-08-11'}))
        scratch=root/'scratch';scratch.mkdir();commands=[];publications=[];seen_configs=[]
        def copy(src,dest):
            if str(src).startswith('gdrive:'):
                dest=Path(dest);dest.mkdir(parents=True,exist_ok=True)
                (dest/'manifest.json').write_text(json.dumps({'as_of_date':'2026-10-29',
                    'validation':{'primary_keys_unique':True,'ohlc_valid':True}}))
                if str(src).endswith('/processed'):
                    (dest/'ownership_events.parquet').write_bytes(b'verified prior source')
            else:publications.append(str(dest))
        def run(*args,**kwargs):
            commands.append(args)
            if args[:2]==('rclone','copyto') and str(args[2]).endswith('/ownership-refresh/run_status.json'):
                Path(args[3]).write_text(json.dumps({'task':'ownership-refresh','status':'PASSED_AND_SAVED_TO_DRIVE' if prior_success else 'FAILED',
                    'as_of':'2026-10-29','ownership_snapshot':'gdrive:stock_state_strategy/research/ownership/2026-10-29/999-1'}))
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
             patch.dict(os.environ,{'TUSHARE_TOKEN':'' if reuse else 'SYNTHETIC_TEST_ONLY','GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}),\
             patch('sys.argv',['github_drive_runner.py','ownership-refresh']+(['--promote-ownership'] if promote else [])+(['--reuse-ownership-run','999-1'] if reuse else [])):
            if actual_day!='2026-10-29' or fail_check or not prior_success:
                with self.assertRaises(RuntimeError):runner.main()
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

    def test_verified_reuse_skips_vendor_calls_without_token(self):
        root,configs,published,commands=self.execute(promote=True,reuse=True)
        self.assertEqual(len(configs),1)
        self.assertFalse(any(len(c)>1 and c[1] in ('download_ownership.py','ownership_pipeline.py') for c in commands))
        self.assertEqual(published[-1],'gdrive:stock_state_strategy/output/state_definition_20261029')

    def test_failed_prior_run_cannot_be_promoted(self):
        root,configs,published,commands=self.execute(promote=True,reuse=True,prior_success=False)
        self.assertEqual(published,[])

    def test_published_manifest_fingerprints_actual_inputs_outputs_and_lineage(self):
        root,configs,published,commands=self.execute(promote=True,reuse=True)
        snapshot=Path(configs[0]['processed_data_dir']).parent
        manifest=json.loads((snapshot/'snapshot_manifest.json').read_text())
        self.assertEqual(manifest['as_of_date'],'2026-10-29')
        self.assertEqual(manifest['reused_ownership_source'],
            'stock_state_strategy/research/ownership/2026-10-29/999-1')
        self.assertEqual(manifest['files_sha256']['processed/ownership_events.parquet'],
            runner.file_sha256(snapshot/'processed/ownership_events.parquet'))
        self.assertIn('current_config.json',manifest['files_sha256'])
        self.assertIn('state/summary.json',manifest['files_sha256'])
        pointer=json.loads((root/'scratch/current_ownership.json').read_text())
        self.assertEqual(pointer['snapshot_manifest_sha256'],runner.file_sha256(snapshot/'snapshot_manifest.json'))

    def test_archived_configuration_never_contains_ephemeral_absolute_paths(self):
        root,configs,published,commands=self.execute(promote=True,reuse=True)
        snapshot=Path(configs[0]['processed_data_dir']).parent
        record=json.loads((snapshot/'current_config.json').read_text())
        self.assertEqual(record['path_base'],'snapshot_directory')
        self.assertEqual(record['market_data_path_base'],'project_root')
        for key in ('market_data_root','raw_data_dir','processed_data_dir','output_dir'):
            self.assertFalse(Path(record[key]).is_absolute())
        self.assertNotIn(str(root),json.dumps(record))
