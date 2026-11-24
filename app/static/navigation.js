/* Each view serves one user task; source/review state stays in memory across navigation. */
const routes={
 research:{title:'Research a company.',eyebrow:'FIND DOCUMENTS. FOLLOW THE EVIDENCE.',description:'Enter a ticker, choose sources to read, and search their evidence.'},
 demo:{title:'See how Quarterly works.',eyebrow:'THREE GUIDED EXAMPLES',description:'Explore real Snowflake documents through a short, hands-on walkthrough.'},
 library:{title:'Your source library.',eyebrow:'EVERY SOURCE. ONE ORGANIZED WORKSPACE.',description:'Find filings, uploaded PDFs and saved research sources without searching across sessions.'},
 documents:{title:'Upload & prepare a PDF.',eyebrow:'BRING YOUR OWN SOURCE MATERIAL',description:'Add a document to your source library and choose the pages to prepare for review.'},
 updates:{title:'Update your metric tracker.',eyebrow:'COMPARE. VERIFY. EXPORT.',description:'Compare specific reported numbers across two periods, check their sources, and export accepted updates.'}
};
function navigate(view,{scroll=true}={}){
 if(!routes[view])view='research';
 if(view!=='updates'){document.body.classList.remove('demo-active');$('demo-guide').hidden=true;if(window.clearDemoStory)window.clearDemoStory();}
 for(const page of document.querySelectorAll('[data-page]'))page.hidden=page.dataset.page!==view;
 for(const link of document.querySelectorAll('[data-route]')){const active=link.dataset.route===view||(view==='documents'&&link.dataset.route==='library');link.classList.toggle('active',active);if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');}
 const copy=routes[view];$('view-title').textContent=copy.title;$('view-eyebrow').textContent=copy.eyebrow;$('view-description').textContent=copy.description;$('view-breadcrumb').textContent='WORKSPACE / '+view.toUpperCase();
 if(location.hash!=='#'+view)history.pushState(null,'','#'+view);
 if(view==='library'&&window.loadLibrary)window.loadLibrary().catch(error=>message(error.message));
 if(scroll)window.scrollTo({top:0,behavior:'instant'});
}
window.navigate=navigate;
window.addEventListener('hashchange',()=>{const route=location.hash.slice(1);if(routes[route])navigate(route);});
for(const link of document.querySelectorAll('[data-route]'))link.onclick=event=>{event.preventDefault();navigate(link.dataset.route);};
$('go-to-updates').onclick=()=>navigate('updates');
navigate(location.hash.slice(1)||'research',{scroll:false});
