
const fs=require('fs');
let h=fs.readFileSync('tmp/skazka/index.html','utf8');
h=h.replace(/<style[\s\S]*?<\/style>/gi,' ').replace(/<script[\s\S]*?<\/script>/gi,' ').replace(/<noscript[\s\S]*?<\/noscript>/gi,' ');
let t=h.replace(/<\/(p|div|h1|h2|h3|h4|li|section|a|span|button|tr)>/gi,'\n').replace(/<br\s*\/?>/gi,'\n').replace(/<[^>]+>/g,'');
const reps={'&nbsp;':' ','&mdash;':'—','&laquo;':'«','&raquo;':'»','&quot;':'"','&#39;':"'",'&amp;':'&','&rarr;':'->','&hellip;':'...','&ndash;':'–'};
for(const k in reps) t=t.split(k).join(reps[k]);
t=t.replace(/&[a-z0-9#]+;/gi,' ');
const lines=t.split('\n').map(s=>s.replace(/\s+/g,' ').trim()).filter(Boolean);
const out=[]; for(const l of lines){ if(out[out.length-1]!==l) out.push(l); }
fs.writeFileSync('tmp/skazka/index.txt', out.join('\n'));
console.log('lines', out.length, 'chars', t.length);
