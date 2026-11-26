
const fs=require('fs');
const h=fs.readFileSync('tmp/skazka/index.html','utf8');
const links=[...new Set([...h.matchAll(/href="([^"]+)"/g)].map(m=>m[1]))];
console.log(links.join('\n'));
