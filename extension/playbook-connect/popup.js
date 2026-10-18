"use strict";
// Playbook Connect popup. ESPN cookies are read only inside connect(), sent once to the chosen Playbook server,
// and then dropped. Nothing but the chosen server is ever saved (chrome.storage.local).
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let server = PB_SERVERS[0].origin, results = [];

function b64u(s){return btoa(unescape(encodeURIComponent(s))).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,"")}
function parseIds(text){const out=[];String(text||"").split(/[\s,]+/).forEach(t=>{const m=t.match(/leagueId=(\d+)/i)||t.match(/^(\d{3,12})$/);if(m){const tm=t.match(/teamId=(\d+)/i);out.push({id:+m[1],tid:tm?+tm[1]:undefined})}});return out}
function getCookie(name){return new Promise(r=>chrome.cookies.get({url:"https://fantasy.espn.com/",name},c=>r(c?c.value:null)))}

async function checkSignin(){
  // presence check only; values are not kept
  const has = !!(await getCookie("SWID")) && !!(await getCookie("espn_s2"));
  $("siTxt").innerHTML = has ? '<span class="ok">✓ Signed in to ESPN in this Chrome</span>'
    : '<span class="bad">Not signed in to ESPN here.</span> <a href="https://fantasy.espn.com/football/" target="_blank">Sign in at fantasy.espn.com</a>, then open this again.';
  $("go").disabled = !has; return has}

async function findTabs(){
  let tabs=[];try{tabs=await chrome.tabs.query({url:["https://fantasy.espn.com/*"]})}catch(e){}
  const seen=new Map();tabs.forEach(t=>parseIds(t.url||"").forEach(x=>{if(!seen.has(x.id)||x.tid!=null)seen.set(x.id,{...x,title:(t.title||"").replace(/\s*-\s*ESPN.*$/,"")})}));
  const list=[...seen.values()];
  $("tabs").innerHTML = list.length ? list.map(x=>`<label class="row"><input type="checkbox" class="tl" data-id="${x.id}" data-tid="${x.tid??""}" checked><span><b>League ${x.id}</b>${x.tid!=null?` · team ${x.tid}`:""}<small>${esc(x.title)} (open tab)</small></span></label>`).join("")
    : '<div class="xs">No ESPN league tabs open. That\'s fine: we can look up your leagues, or paste one below.</div>';
}

async function connect(){
  $("err").textContent="";const want=[...document.querySelectorAll(".tl:checked")].map(e=>({id:+e.dataset.id,tid:e.dataset.tid?+e.dataset.tid:undefined})).concat(parseIds($("paste").value));
  const discover=$("disc").checked;
  if(!want.length&&!discover){$("err").textContent="Pick a league, paste one, or tick “find my leagues”.";return}
  const b=$("go");b.disabled=true;b.textContent="Checking with ESPN…";
  let s2=await getCookie("espn_s2"), swid=await getCookie("SWID");
  try{
    if(!s2||!swid)throw new Error("Couldn't find your ESPN sign-in. Sign in at fantasy.espn.com in this Chrome, then try again.");
    const r=await fetch(server+"/api/ext/connect",{method:"POST",credentials:"omit",cache:"no-store",headers:{"Content-Type":"application/json"},body:JSON.stringify({espn_s2:s2,swid,leagues:want,discover})});
    s2=swid=null;   // drop the cookies right after the one request
    const j=await r.json().catch(()=>({}));
    if(!r.ok||!j.ok)throw new Error(j.msg||"Playbook couldn't connect (HTTP "+r.status+"). Try again in a minute; the free server may be waking up.");
    results=j.leagues||[];draw(j)}
  catch(e){$("err").textContent=e.message||"No connection. Try again."}
  finally{s2=swid=null;b.disabled=false;b.textContent="Connect to Playbook"}}

function draw(j){
  const ok=results.filter(x=>x.ok);
  $("res").innerHTML=(results.length?results.map((x,i)=>x.ok?`<div class="lr"><input type="checkbox" class="pk" data-i="${i}" checked><div style="flex:1"><b>${esc(x.name)}</b><div class="ok">✓ ${x.tid!=null?esc(x.team||"Team "+x.tid):"Connected"}</div>${x.tid==null?`<select class="tp" data-i="${i}"><option value="">Which team is yours?</option>${(x.teams||[]).map(t=>`<option value="${t.id}">${esc(t.name)}</option>`).join("")}</select>`:""}</div></div>`
    :`<div class="lr"><div><b>League ${x.id}</b><div class="bad">✕ ${esc(x.msg)}</div></div></div>`).join("")
    :'<div class="xs">No football leagues found for this ESPN account this season. Paste your league link above and try again.</div>')
    +(j.searched_espn&&!j.found_on_espn?'<div class="xs" style="margin-top:6px">ESPN didn\'t list any leagues automatically, so only the ones you picked were checked.</div>':"");
  $("done").classList.toggle("hide",!results.length);$("open").disabled=$("phone").disabled=!ok.length;
  document.querySelectorAll(".tp").forEach(s=>s.onchange=()=>{const x=results[+s.dataset.i];x.tid=s.value===""?null:+s.value;const t=(x.teams||[]).find(t=>t.id===x.tid);x.team=t?t.name:null})}

function link(){
  const pick=[...document.querySelectorAll(".pk:checked")].map(e=>results[+e.dataset.i]).filter(x=>x&&x.ok);
  const miss=pick.filter(x=>x.tid==null);if(miss.length){$("err").textContent="Pick your team for "+miss.map(x=>x.name).join(", ")+".";return null}
  if(!pick.length){$("err").textContent="Tick at least one league.";return null}
  $("err").textContent="";
  return server+"/#join="+b64u(JSON.stringify(pick.map(x=>({id:x.id,tid:x.tid,season:x.season,tok:x.tok,name:x.name,team:x.team}))))+"&src=ext"}

async function init(){
  const st=await chrome.storage.local.get("server");if(st.server&&PB_SERVERS.some(s=>s.origin===st.server))server=st.server;
  $("srv").innerHTML=PB_SERVERS.map(s=>`<option value="${s.origin}" ${s.origin===server?"selected":""}>${esc(s.label)} · ${esc(s.origin.replace("https://",""))}</option>`).join("");
  $("srv").onchange=()=>{server=$("srv").value;chrome.storage.local.set({server})};
  $("go").onclick=connect;
  $("open").onclick=()=>{const l=link();if(l)chrome.tabs.create({url:l})};
  $("phone").onclick=async()=>{const l=link();if(!l)return;await navigator.clipboard.writeText(l);$("phone").textContent="Copied ✓ Text it to yourself"};
  await checkSignin();await findTabs()}
init();
