let briefPacketId=null;
function renderBriefControls(){
 const packet=researchPacket;if(!packet)return;
 const changed=briefPacketId!==packet.id;
 const selected=new Set([...$('brief-sources').querySelectorAll('input:checked')].map(n=>n.value));
 $('brief-sources').replaceChildren();
 const ready=packet.sources.filter(s=>s.state==='ready');
 for(const source of ready){
  const label=node('label',undefined,'check-label');const check=node('input');check.type='checkbox';check.value=source.id;check.checked=changed||selected.has(source.id);
  label.append(check,node('span',source.metadata.title));$('brief-sources').append(label);
 }
 if(!ready.length)$('brief-sources').append(node('p','Ingest sources first. Only searchable evidence can be included.','hint'));
 $('build-research-brief').disabled=!ready.length;
 $('brief-synthesize').disabled=$('synthesize').disabled;
 if(changed){briefPacketId=packet.id;$('research-brief').replaceChildren();$('brief-progress').textContent='';loadBriefHistory(packet.id).catch(e=>message(e.message));}
}
async function loadBriefHistory(packetId){
 const items=await api('/api/research/packets/'+packetId+'/briefs');if(researchPacket?.id!==packetId)return;
 $('research-brief-history').replaceChildren();
 for(const item of items){const b=node('button','Open saved brief · '+new Date(item.created*1000).toLocaleString(),'secondary');b.onclick=async()=>{try{const brief=await api('/api/research/briefs/'+item.id);if(researchPacket?.id===packetId)renderResearchBrief(brief);}catch(e){message(e.message);}};$('research-brief-history').append(b);}
}
function renderResearchBrief(brief){
 const box=$('research-brief');box.replaceChildren(node('h3',brief.company+' / earnings brief'),node('p',brief.mode==='excerpts'?'Source excerpts · no synthesized conclusions':'Cited conclusions · verify against sources','hint'),node('p',brief.notice,'hint'));
 const download=node('a','Download saved brief ↓','button secondary');download.href='/api/research/briefs/'+brief.id+'/markdown';box.append(download);
 box.append(node('p',brief.sources.length+' selected source(s) · saved '+new Date(brief.created*1000).toLocaleString(),'hint'));
 for(const section of brief.sections){
  const area=node('section');area.append(node('h3',section.title));const answer=section.answer;
  if(answer?.sufficient){for(const claim of answer.claims){area.append(node('p',claim.text));for(const id of claim.citation_ids){const e=section.evidence.find(e=>e.id===id);if(e)area.append(safeLink(e.pdf_url||e.url,e.title+' / '+e.locator));}}}
  else if(answer||!section.evidence.length)area.append(node('p','Insufficient evidence for this section.','hint'));
  else area.append(node('p','Matching excerpts; confirm the covered period and context.','hint'));
  renderEvidenceResults(area,section);box.append(area);
 }
}
$('build-research-brief').onclick=async()=>{
 if(!researchPacket)return;const packetId=researchPacket.id;
 const source_ids=[...$('brief-sources').querySelectorAll('input:checked')].map(n=>n.value);
 if(!source_ids.length){message('Choose at least one searchable source.');return;}
 const button=$('build-research-brief');button.disabled=true;$('brief-progress').textContent='Building and saving brief…';
 try{
  const brief=await api('/api/research/packets/'+packetId+'/briefs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_ids,synthesize:$('brief-synthesize').checked,provider:researchAnswerProvider})});
  if(researchPacket?.id!==packetId)return;renderResearchBrief(brief);await loadBriefHistory(packetId);$('brief-progress').textContent='Brief saved. Review each section against its source evidence.';
 }catch(e){if(researchPacket?.id===packetId)$('brief-progress').textContent=e.message;}
 finally{if(researchPacket?.id===packetId)button.disabled=false;}
};
if(researchPacket)renderBriefControls();
