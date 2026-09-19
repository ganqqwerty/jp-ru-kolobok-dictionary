const entries=[...document.querySelectorAll('.entry')];
let activeBatch=document.querySelector('.batch-tab')?.dataset.batch||'';
function filter(){const q=document.querySelector('#search').value.toLocaleLowerCase();const category=document.querySelector('#category').value;const errors=document.querySelector('#errorsOnly').checked;let visible=0;for(const e of entries){e.hidden=Boolean((activeBatch&&e.dataset.batch!==activeBatch)||(q&&!e.querySelector('summary').textContent.toLocaleLowerCase().includes(q))||(category&&e.dataset.category!==category)||(errors&&e.dataset.errors!=='True'));if(!e.hidden)visible++;}document.querySelector('#count').textContent=`${visible} / ${entries.length}`;for(const tab of document.querySelectorAll('.batch-tab'))tab.setAttribute('aria-selected',String(tab.dataset.batch===activeBatch));}
for(const id of ['search','category','errorsOnly'])document.getElementById(id).addEventListener('input',filter);
for(const tab of document.querySelectorAll('.batch-tab'))tab.addEventListener('click',()=>{activeBatch=tab.dataset.batch;filter();});
function reveal(){const id=location.hash.slice(1);const e=document.getElementById(id);if(e?.classList.contains('entry')){activeBatch=e.dataset.batch;filter();e.hidden=false;e.open=true;e.scrollIntoView();}}
window.addEventListener('hashchange',reveal);filter();reveal();
