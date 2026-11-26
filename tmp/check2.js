
const { spawnSync } = require('child_process');
const DOCKER='C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe';
const urls=['https://alyaeva.pro/','https://alyaeva.pro/ceny','https://alyaeva.pro/order','https://alyaeva.pro/login','https://alyaeva.pro/register','https://alyaeva.pro/policy','https://alyaeva.pro/fotokniga-ekaterinburg/','https://alyaeva.pro/robots.txt','https://alyaeva.pro/sitemap.xml','https://alyaeva.pro/static/app.css','https://alyaeva.pro/assets/style.css','https://alyaeva.pro/nonexistent-xyz'];
for(const u of urls){
  const r=spawnSync(DOCKER,['run','--rm','curlimages/curl:latest','-s','--max-time','20','-o','/dev/null','-w','%{http_code}','-H','Accept: text/html',u],{encoding:'utf8',timeout:40000});
  console.log(u,'->',(r.stdout||'').trim()||'(нет ответа)');
}
const rb=spawnSync(DOCKER,['run','--rm','curlimages/curl:latest','-s','--max-time','20','https://alyaeva.pro/robots.txt'],{encoding:'utf8',timeout:40000});
console.log('--- ROBOTS ---\n'+(rb.stdout||''));
const sm=spawnSync(DOCKER,['run','--rm','curlimages/curl:latest','-s','--max-time','20','https://alyaeva.pro/sitemap.xml'],{encoding:'utf8',timeout:40000});
console.log('--- SITEMAP ---\n'+(sm.stdout||'').slice(0,3000));
