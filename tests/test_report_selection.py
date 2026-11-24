from app.report_selection import select_filings

RECENT={
 'form':['8-K','8-K','10-Q','10-Q','10-K'],
 'filingDate':['2026-11-01','2026-10-28','2026-10-29','2026-07-23','2026-01-29'],
 'primaryDocument':['director.htm','earnings.htm','q3.htm','q2.htm','annual.htm'],
 'reportDate':['2026-11-01','2026-10-28','2026-09-30','2026-06-30','2025-12-31'],
 'items':['5.02','2.02, 9.01','','','']}


def test_latest_excludes_unrelated_events_and_old_reports():
 selected,warnings=select_filings(RECENT,'2026-11-16','latest')
 assert {s['index'] for s in selected}=={1,2}
 assert next(s for s in selected if s['form']=='8-K')['role'].startswith('results announcement')


def test_compare_adds_previous_periodic_report():
 selected,_=select_filings(RECENT,'2026-11-16','compare')
 assert {s['index'] for s in selected}=={1,2,3}


def test_missing_item_metadata_does_not_claim_earnings():
 recent={k:v for k,v in RECENT.items() if k!='items'}
 selected,warnings=select_filings(recent,'2026-11-16','latest')
 assert len(selected)==1 and selected[0]['form']=='10-Q'
 assert any('No recent Item 2.02' in w for w in warnings)


def test_historical_cutoff_and_announcement_newer_than_report():
 selected,_=select_filings(RECENT,'2026-08-01','historical')
 assert {s['index'] for s in selected}=={3,4}
 recent={k:list(v) for k,v in RECENT.items()}
 recent['filingDate'][1]='2026-11-02'
 _,warnings=select_filings(recent,'2026-11-16','latest')
 assert any('newer than the periodic' in w for w in warnings)
