
const fs=require('fs');
function txt(h){
  h=h.replace(/<style[\s\S]*?<\/style>/gi,' ').replace(/<script[\s\S]*?<\/script>/gi,' ');
  let t=h.replace(/<\/(p|div|h1|h2|h3|h4|li|section|a|span|button|tr|td|th)>/gi,'\n').replace(/<br\s*\/?>/gi,'\n').replace(/<[^>]+>/g,'');
  const reps={'&nbsp;':' ','&mdash;':'—','&laquo;':'«','&raquo;':'»','&quot;':'"','&#39;':"'",'&amp;':'&','&hellip;':'...','&ndash;':'–'};
  for(const k in reps) t=t.split(k).join(reps[k]);
  const lines=t.split('\n').map(s=>s.replace(/\s+/g,' ').trim()).filter(Boolean);
  const out=[]; for(const l of lines){ if(out[out.length-1]!==l) out.push(l); }
  return out;
}
for(const n of ['skazki','reviews','dostavka-i-oplata','foto-s-dedom-morozom','terms-of-service','corporate-clients']){
  const l=txt(fs.readFileSync('tmp/skazka/pages/'+n+'.html','utf8'));
  console.log('\n=== '+n+' ('+l.length+') ===');
  console.log(l.slice(11, 120).join('\n').slice(0,2200));
}
