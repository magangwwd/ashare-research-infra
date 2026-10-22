"""Keep the confirmed history range without discarding identity context."""
import pandas as pd
from . import clean

def rows(config,dataset,frame,layer):
    from .download import DATASETS
    if frame.empty or not config.get('_recovery_scope'):return frame
    strategy=DATASETS[dataset].strategy
    if strategy in ('snapshot','derived','index_metadata','industry_metadata'):return frame
    floor=pd.Timestamp(config['intraday' if dataset=='stk_mins' else 'download']['history_start'])
    field=DATASETS[dataset].date_field
    if dataset=='trade_cal':field='cal_date' if layer=='raw' else 'calendar_date'
    if layer=='clean' and field not in frame:field=next((f.name for f in clean.SCHEMAS[dataset] if f.source==field),field)
    if field not in frame:return frame
    dates=pd.to_datetime(frame[field],format='mixed',errors='raise');keep=dates.ge(floor)|dates.isna()
    fields=[]
    if strategy in ('industry_history','security_history'):fields+=['out_date','end_date','float_date']
    if dataset in clean.FINANCIAL:fields+=['end_date' if layer=='raw' else 'report_period']
    for f in fields:
        if f in frame:
            end=pd.to_datetime(frame[f],format='mixed',errors='raise');keep|=end.ge(floor)
            if strategy in ('industry_history','security_history') and f in ('out_date','end_date'):keep|=end.isna()
    return frame.loc[keep].copy()

def install(module):
    cls=module.Store;save=cls.save;save_clean=cls.save_clean;receipt=cls.receipt
    def scoped_save(self,dataset,raw,replace_dates=None):return save(self,dataset,rows(self.config,dataset,raw,'raw'),replace_dates)
    def scoped_clean(self,dataset,cleaned,replace_dates=None):return save_clean(self,dataset,rows(self.config,dataset,cleaned,'clean'),replace_dates)
    def scoped_receipt(self,dataset,params,count,raw=None,**kwargs):
        if raw is not None:
            raw=rows(self.config,dataset,raw,'raw');count=len(raw)
        return receipt(self,dataset,params,count,raw,**kwargs)
    cls.save=scoped_save;cls.save_clean=scoped_clean;cls.receipt=scoped_receipt
