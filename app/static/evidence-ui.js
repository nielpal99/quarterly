/* Shared renderer for every source-evidence result. Text remains untrusted. */
function highlightedPassage(text,terms){
 const container=node('blockquote');
 const wanted=new Set(terms.map(term=>term.toLowerCase()));
 for(const part of text.split(/([A-Za-z0-9]+)/))container.append(node(wanted.has(part.toLowerCase())?'mark':'span',part));
 return container;
}
function sourceRows(rows){
 const wrap=node('div',undefined,'source-row-table');
 const table=node('table');const caption=node('caption','Original table rows — verify periods and units in the source');table.append(caption);
 const body=node('tbody');
 for(const row of rows){const tr=node('tr');const cells=row.trim().replace(/^\||\|$/g,'').split('|');for(const cell of cells)tr.append(node('td',cell.trim()));body.append(tr);}
 table.append(body);wrap.append(table);return wrap;
}
function renderEvidenceResults(box,result){
 const groups=new Map();
 for(const evidence of result.evidence){if(!groups.has(evidence.source_id))groups.set(evidence.source_id,[]);groups.get(evidence.source_id).push(evidence);}
 if(!groups.size){box.append(node('div','No sufficiently matching passages were found. Try a more specific metric or read another source.','evidence-empty'));return;}
 for(const entries of groups.values()){
  const first=entries[0];const group=node('section',undefined,'evidence-document');
  const heading=node('div',undefined,'evidence-document-heading');heading.append(node('h3',first.title));
  const dates=[];if(first.period_end)dates.push('Report period ends '+first.period_end);if(first.period_label)dates.push('Source period: '+first.period_label);if(first.filed_on)dates.push('Filed '+first.filed_on);
  heading.append(node('p',dates.join(' · ')||'Check the source for exact fiscal periods.','evidence-location'));group.append(heading);
  for(const [index,evidence]of entries.entries()){
   const card=node('article',undefined,'research-evidence');card.id='evidence-'+evidence.id.replaceAll(':','-');
   card.append(node('h4',evidence.section||('Matching passage '+(index+1))),node('p',evidence.locator+' · '+(evidence.match_reason||'Matching source passage'),'evidence-location'));
   const passage=evidence.passage||evidence.quote;
   if(evidence.kind==='table_row'){
    const rows=[...(evidence.table_context||[]),evidence.quote];card.append(sourceRows(rows));
   }else card.append(highlightedPassage(passage,evidence.matched_terms||[]));
   const context=node('details',undefined,'evidence-context');context.append(node('summary','Show complete passage & nearby context'));
   if(evidence.context_before)context.append(node('p',evidence.context_before,'surrounding-context'));
   context.append(node('pre',evidence.quote));
   if(evidence.context_after)context.append(node('p',evidence.context_after,'surrounding-context'));
   card.append(context,safeLink(evidence.pdf_url||evidence.url,'Verify original source ↗'));
   if(evidence.image_url){const details=node('details',undefined,'evidence-page-preview');details.append(node('summary','View original PDF page'));const img=node('img');img.src=evidence.image_url;img.alt='Cited original PDF page';img.loading='lazy';details.append(img);card.append(details);}
   group.append(card);
  }
  box.append(group);
 }
}
