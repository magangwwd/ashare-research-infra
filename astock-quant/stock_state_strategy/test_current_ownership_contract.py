import unittest
from pathlib import Path
from current_ownership_contract import current_config, batch_can_resume, verified_ownership_prefix


class CurrentOwnershipContractTests(unittest.TestCase):
    def template(self):
        return {"research_end":"2026-08-11", "processed_data_dir":"artifacts/legacy/processed",
                "buy_cost_rate":0.0013, "nested":{"frozen":True}}

    def test_end_date_is_actual_gold_date(self):
        config=current_config(self.template(), Path('/tmp/gold'), Path('/tmp/ownership/run1'), '2026-10-29')
        self.assertEqual(config['research_end'],'2026-10-29')

    def test_source_template_is_unchanged(self):
        old=self.template();config=current_config(old,Path('/tmp/gold'),Path('/tmp/ownership/run1'),'2026-10-29')
        config['nested']['frozen']=False
        self.assertEqual(old,self.template())

    def test_current_state_reads_exact_new_processed_directory(self):
        config=current_config(self.template(),Path('/tmp/gold'),Path('/tmp/ownership/run1'),'2026-10-29')
        self.assertEqual(config['processed_data_dir'], '/tmp/ownership/run1/processed')
        self.assertEqual(config['buy_cost_rate'],0.0013)

    def test_runs_are_isolated(self):
        first=current_config({},Path('/tmp/gold'),Path('/tmp/ownership/run1'),'2026-10-29')
        second=current_config({},Path('/tmp/gold'),Path('/tmp/ownership/run2'),'2026-10-29')
        self.assertNotEqual(first['raw_data_dir'],second['raw_data_dir'])

    def test_legacy_target_is_forbidden(self):
        with self.assertRaises(ValueError):current_config({},Path('/tmp/gold'),Path('/tmp/legacy/run1'),'2026-10-29')

    def test_invalid_date_is_rejected(self):
        with self.assertRaises(ValueError):current_config({},Path('/tmp/gold'),Path('/tmp/run1'),'2026-02-30')

    def metadata(self):return {'start_date':'20200101','end_date':'20261029','requested_symbols':['A','B']}

    def test_same_batch_can_resume(self):self.assertTrue(batch_can_resume(self.metadata(),'20200101','20261029',['A','B']))
    def test_new_end_date_cannot_silently_skip(self):self.assertFalse(batch_can_resume(self.metadata(),'20200101','20261030',['A','B']))
    def test_changed_universe_cannot_skip(self):self.assertFalse(batch_can_resume(self.metadata(),'20200101','20261029',['B','C']))
    def test_old_metadata_without_scope_cannot_skip(self):self.assertFalse(batch_can_resume({'start_date':'20200101','end_date':'20261029'},'20200101','20261029',['A','B']))

    def pointer(self): return {'as_of_date':'2026-10-29',
        'snapshot_prefix':'stock_state_strategy/research/ownership/2026-10-29/123-1',
        'verification':'RCLONE_CHECK_PASSED'}

    def test_verified_current_source_is_selected(self):
        self.assertEqual(verified_ownership_prefix(self.pointer(),'2026-10-29'),self.pointer()['snapshot_prefix'])

    def test_stale_pointer_is_rejected(self):
        with self.assertRaises(RuntimeError):verified_ownership_prefix(self.pointer(),'2026-10-30')

    def test_unverified_pointer_is_rejected(self):
        p=self.pointer();p['verification']='PENDING'
        with self.assertRaises(RuntimeError):verified_ownership_prefix(p,'2026-10-29')

    def test_pointer_cannot_select_legacy_or_other_remote_paths(self):
        for value in ('stock_state_strategy/artifacts/legacy','../private','other:folder'):
            p=self.pointer();p['snapshot_prefix']=value
            with self.subTest(value=value),self.assertRaises(RuntimeError):verified_ownership_prefix(p,'2026-10-29')


if __name__ == '__main__':unittest.main()
