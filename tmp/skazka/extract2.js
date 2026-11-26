
const fs=require('fs');
const dir='tmp/skazka/pages';
const files=fs.readdirSync(dir).filter(f=>f.endsWith('.html'));
function txt(h){
  h=h.replace(/<style[\s\S]*?<\/style>/gi,' ').replace(/<script[\s\S]*?<\/script>/gi,' ');
  let t=h.replace(/<\/(p|div|h1|h2|h3|h4|li|section|a|span|button|tr|td|th)>/gi,'\n').replace(/<br\s*\/?>/gi,'\n').replace(/<[^>]+>/g,'');
  const reps={'&nbsp;':' ','&mdash;':'—','&laquo;':'«','&raquo;':'»','&quot;':'"','&#39;':"'",'&amp;':'&','&hellip;':'...','&ndash;':'–','&rarr;':'->'};
  for(const k in reps) t=t.split(k).join(reps[k]);
  t=t.replace(/&[a-z0-9#]+;/gi,' ');
  const lines=t.split('\n').map(s=>s.replace(/\s+/g,' ').trim()).filter(Boolean);
  const out=[]; for(const l of lines){ if(out[out.length-1]!==l) out.push(l); }
  return out;
}
const all={};
for(const f of files){ all[f]=txt(fs.readFileSync(dir+'/'+f,'utf8')); }
fs.writeFileSync('tmp/skazka/pages.txt', Object.entries(all).map(([f,l])=>'\n=== '+f+' ('+l.length+' lines) ===\n'+l.join('\n')).join('\n'));
for(const f of files) console.log(f, all[f].length);
