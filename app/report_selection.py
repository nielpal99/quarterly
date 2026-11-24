"""Explicit latest, recent-comparison and historical filing selection."""
from datetime import date, timedelta


def select_filings(recent,cutoff,mode):
    candidates=[]
    for i,form in enumerate(recent['form']):
        if form not in ('10-K','10-Q','8-K') or not recent['primaryDocument'][i]:continue
        filed=recent['filingDate'][i]
        if filed>cutoff:continue
        items=(recent.get('items') or ['']*len(recent['form']))[i]
        candidates.append({'index':i,'form':form,'filed':filed,'items':items or '',
            'period_end':(recent.get('reportDate') or ['']*len(recent['form']))[i]})
    candidates.sort(key=lambda c:c['filed'],reverse=True)
    warnings=[]
    if mode=='historical':
        limits={'10-K':1,'10-Q':2,'8-K':2};counts=dict.fromkeys(limits,0);chosen=[]
        for candidate in candidates:
            if counts[candidate['form']]>=limits[candidate['form']]:continue
            chosen.append({**candidate,'role':'historical context'});counts[candidate['form']]+=1
        return chosen,warnings
    periodic=[c for c in candidates if c['form'] in ('10-Q','10-K')]
    # Rank by covered reporting period, rather than a later filing for an older period.
    periodic.sort(key=lambda c:(c['period_end'] or c['filed'],c['filed']),reverse=True)
    selected=periodic[:(2 if mode=='compare' else 1)]
    chosen=[{**candidate,'role':'latest periodic report' if i==0 else 'previous periodic report'} for i,candidate in enumerate(selected)]
    earnings=[c for c in candidates if c['form']=='8-K' and '2.02' in [item.strip() for item in c['items'].split(',')]]
    if selected:
        earliest=(date.fromisoformat(selected[0]['filed'])-timedelta(days=14)).isoformat()
        earnings=[c for c in earnings if c['filed']>=earliest]
    if earnings:
        chosen.append({**earnings[0],'role':'results announcement (Item 2.02)'})
        if selected and earnings[0]['filed']>selected[0]['filed']:
            warnings.append('The results announcement is newer than the periodic report. It may describe a newer reporting period; verify the source before combining figures.')
    else:
        warnings.append('No recent Item 2.02 results announcement was identified. Only the selected periodic report(s) are included; a complete earnings packet is not guaranteed.')
    if not selected:warnings.append('No 10-Q or 10-K was found within the recent-submissions window.')
    if mode=='compare' and len(selected)<2:warnings.append('Fewer than two periodic reports were available for comparison.')
    warnings.append('Results-announcement classification uses SEC Item 2.02 metadata. Supported EX-99 attachments are discovered separately; confirm their type and covered period before combining figures.')
    return chosen,warnings
