import unittest
import pandas as pd
from ownership_quality_diagnostics import ownership_quality_diagnostics


class OwnershipQualityDiagnosticTests(unittest.TestCase):
    def rows(self):
        return pd.DataFrame({'vendor_symbol':['A','B','C','D','E'],
            'current_bar_available':[True,True,True,True,False],
            'holder_disclosure_age_days':[80,181,None,80,None],
            'composition_disclosure_age_days':[80,80,80,80,None],
            'holder_update_count':[2,2,1,2,0], 'composition_update_count':[2,2,2,1,0],
            'composition_quality_valid':[True,True,True,False,False],
            'ownership_eligible':[True,False,False,False,False]})

    def test_primary_counts_partition_the_whole_universe(self):
        out,summary=ownership_quality_diagnostics(self.rows())
        self.assertEqual(sum(summary['primary_reason_counts'].values()),5)
        self.assertEqual(summary['ownership_failed_current_rows'],3)
        self.assertEqual(out.primary_reason.tolist(),['PASS','holder_expired_or_future','holder_missing','composition_insufficient_updates','NO_CURRENT_BAR'])

    def test_marginal_counts_preserve_overlapping_failures(self):
        out,summary=ownership_quality_diagnostics(self.rows())
        self.assertEqual(summary['nonexclusive_failure_counts']['composition_quality_invalid'],1)
        self.assertTrue(out.loc[3,'composition_insufficient_updates'])

    def test_diagnostic_mismatch_cannot_be_silently_reported(self):
        frame=self.rows();frame.loc[0,'ownership_eligible']=False
        with self.assertRaises(RuntimeError):ownership_quality_diagnostics(frame)

    def test_future_age_is_not_missing_or_safe(self):
        frame=self.rows();frame.loc[1,'holder_disclosure_age_days']=-1
        out,summary=ownership_quality_diagnostics(frame)
        self.assertTrue(out.loc[1,'holder_expired_or_future'])
        self.assertFalse(out.loc[1,'holder_missing'])


if __name__ == '__main__':unittest.main()
