const entries=[...document.querySelectorAll('.entry')];
function filter(){const q=document.querySelector('#search').value.toLocaleLowerCase();const category=document.querySelector('#category').value;const errors=document.querySelector('#errorsOnly').checked;let visible=0;for(const e of entries){e.hidden=Boolean((q&&!e.querySelector('summary').textContent.toLocaleLowerCase().includes(q))||(category&&e.dataset.category!==category)||(errors&&e.dataset.errors!=='True'));if(!e.hidden)visible++;}document.querySelector('#count').textContent=`${visible} / ${entries.length}`;}
for(const id of ['search','category','errorsOnly'])document.getElementById(id).addEventListener('input',filter);
function reveal(){const id=location.hash.slice(1);const e=document.getElementById(id);if(e?.classList.contains('entry')){e.hidden=false;e.open=true;e.scrollIntoView();}}
window.addEventListener('hashchange',reveal);filter();reveal();
