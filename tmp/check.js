
const https=require('https');
const urls=['/','/ceny','/order','/login','/register','/policy','/fotokniga-ekaterinburg/','/robots.txt','/sitemap.xml','/static/app.css','/assets/style.css','/nonexistent-xyz'];
(async()=>{
for(const u of urls){
  await new Promise(res=>{
    const req=https.request({host:'alyaeva.pro',path:u,method:'GET',headers:{'User-Agent':'Mozilla/5.0','Accept':'text/html'}},r=>{
      let n=0; r.on('data',d=>n+=d.length); r.on('end',()=>{console.log(u,'->',r.statusCode,'bytes',n, 'loc:'+(r.headers.location||''));res();});
    });
    req.on('error',e=>{console.log(u,'ERR',e.message);res();});
    req.end();
  });
}
})();
