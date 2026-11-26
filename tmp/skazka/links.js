
const fs=require('fs');
const h=fs.readFileSync('tmp/skazka/index.html','utf8');
const links=[...h.matchAll(/href="(\/[^"'>]*)/g)].map(m=>m[1]);
const uniq=[...new Set(links)];
fs.writeFileSync('tmp/skazka/links.txt', uniq.join('\n'));
const top={}; for(const l of uniq){ const p=l.split('/').filter(Boolean)[0]||'(root)'; top[p]=(top[p]||0)+1; }
console.log('links', uniq.length, JSON.stringify(top));
const imgs=[...h.matchAll(/<img[^>]*>/g)].map(m=>m[0]);
console.log('imgs', imgs.length);
console.log(imgs.slice(0,6).join('\n').slice(0,2500));
const srcs=[...new Set(imgs.map(i=>(i.match(/src="([^"]+)"/)||[])[1]).filter(Boolean))];
console.log('unique srcs', srcs.length);
console.log(srcs.slice(0,10).join('\n'));
