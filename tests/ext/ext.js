const puppeteer=require('/workspace/shots-tool/node_modules/puppeteer-core');
const W=ms=>new Promise(r=>setTimeout(r,ms));const SH='/workspace/fantasy-app/shots/';
(async()=>{
 const PD='/tmp/exttest/prof'+Date.now();require('fs').mkdirSync(PD+'/Default',{recursive:true});require('fs').writeFileSync(PD+'/Default/Preferences',JSON.stringify({extensions:{ui:{developer_mode:true}}}));
 const b=await puppeteer.launch({executablePath:'/tmp/exttest/browsers/chromium/linux-1721551/chrome-linux/chrome',headless:'new',ignoreDefaultArgs:['--disable-extensions'],args:['--no-sandbox','--user-data-dir='+PD,'--disable-extensions-except=/tmp/exttest/ext','--load-extension=/tmp/exttest/ext']});
 const errs=[];const log=(...a)=>console.log(...a);
 const s=await b.target().createCDPSession();
 const id='aniopchmkabeloipmicldolcmpjmmggj';await W(1500);
 log('extension loaded, id =',id);
 // mock ESPN sign-in cookies (fake values) on .espn.com
 await s.send('Storage.setCookies',{cookies:[{name:'espn_s2',value:'MOCKS2'+'x'.repeat(150),domain:'.espn.com',path:'/',secure:true},{name:'SWID',value:'{11111111-2222-3333-4444-555555555555}',domain:'.espn.com',path:'/',secure:true}]});
 // an "open ESPN league tab" (served locally, never hits ESPN)
 const t=await b.newPage();await t.setRequestInterception(true);t.on('request',r=>r.url().includes('espn.com')?r.respond({status:200,contentType:'text/html',body:'<title>BBB Inc. - ESPN Fantasy Football</title>ok'}):r.continue());
 await t.goto('https://fantasy.espn.com/football/team?leagueId=1852378966&teamId=3&seasonId=2026');
 const p=await b.newPage();p.on('pageerror',e=>errs.push(e.message));p.on('console',m=>{if(m.type()==='error')errs.push(m.text())});
 await p.setViewport({width:360,height:640,deviceScaleFactor:2});
 await p.goto(`chrome-extension://${id}/popup.html`);await W(1500);
 log('signin:',await p.$eval('#siTxt',e=>e.innerText));log('tabs:',await p.$eval('#tabs',e=>e.innerText.replace(/\n/g,' | ')));
 const fit=async n=>{const h=await p.evaluate(()=>document.body.scrollHeight);await p.setViewport({width:360,height:h,deviceScaleFactor:2});await p.screenshot({path:SH+n})};
 await fit('friends-ext-popup.png');
 await p.type('#paste','1946');await p.click('#go');await p.waitForFunction(()=>!document.querySelector('#done').classList.contains('hide')||document.querySelector('#err').innerText,{timeout:60000});
 log('err:',await p.$eval('#err',e=>e.innerText)||'(none)');log('results:',await p.$eval('#res',e=>e.innerText.replace(/\n/g,' | ')));
 // 1946: this mock account isn't a member, so pick a team from the list
 await p.evaluate(()=>{const s=document.querySelector('.tp');if(s){s.value='3';s.dispatchEvent(new Event('change'))}});
 await fit('friends-ext-connected.png');
 // stored data check: only the server choice
 log('extension storage:',JSON.stringify(await p.evaluate(()=>chrome.storage.local.get(null))));
 const before=new Set((await b.pages()).map(x=>x.url()));
 await p.click('#open');await W(2500);
 const app=(await b.pages()).find(x=>x.url().startsWith('http://127.0.0.1:8812'));
 if(!app){log('FAIL: Playbook tab not opened');}else{
  await W(6000);
  log('app url after import (token removed from address bar?):',app.url(),'tab=',await app.evaluate(()=>tab+' '+window._fromExt));
  log('app saved leagues:',await app.evaluate(()=>JSON.stringify(JSON.parse(localStorage.lpLeagues||'[]').map(x=>({id:x.id,tid:x.tid,name:x.name,team:x.team,tok:!!x.tok})))));
  await app.screenshot({path:SH+'friends-ext-imported.png'});}
 await b.close();log(errs.length?'JS errors: '+errs.join(' / '):'no JS errors');
})();
