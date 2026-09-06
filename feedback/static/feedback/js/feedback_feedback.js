/* feedback_feedback.js — extracted from feedback.html */
/* ── Main form state ──────────────────────────────────────────── */
let step=0, ftype="", yr="";

function prog(n){
  for(let i=0;i<=5;i++){
    const d=document.getElementById("d"+i);
    if(!d)continue;
    d.className="pdot"+(i<n?" done":i===n?" active":"");
    const l=document.getElementById("l"+i+(i+1));
    if(l)l.className="pline"+(i<n?" done":"");
  }
}
function show(id){
  document.querySelectorAll(".step").forEach(s=>s.classList.remove("on"));
  const el=document.getElementById(id);
  if(el)el.classList.add("on");
}
function say(html,delay=0){
  const sp=document.getElementById("botSpeech");
  if(delay>0){sp.innerHTML='<div class="tdots"><div class="tdot"></div><div class="tdot"></div><div class="tdot"></div></div>';setTimeout(()=>{sp.innerHTML=html;},delay);}
  else{sp.innerHTML=html;}
}
function lockStep(sid,icon,value){
  const s=document.getElementById(sid);
  s.querySelectorAll("input,textarea,button,.chip-row,.hint").forEach(el=>el.style.display="none");
  if(!s.querySelector(".answered-chip")){
    const c=document.createElement("div");c.className="answered-chip";
    c.innerHTML=`<i class="${icon}"></i><span>${value}</span>`;s.appendChild(c);
  }
}
function hint(id,sh){const el=document.getElementById(id);if(el)el.classList.toggle("show",sh);}
function get(id){const el=document.getElementById(id);return el?(el.value.trim()):""; }
function focus(id){setTimeout(()=>{const el=document.getElementById(id);if(el)el.focus();},380);}
function setBusy(btn,txt){btn.disabled=true;btn.innerHTML=`<i class="fas fa-spinner fa-spin"></i> ${txt}`;}
function setReady(btn,html){btn.disabled=false;btn.innerHTML=html;}
function csrf(){const c=document.cookie.split(";").find(s=>s.trim().startsWith("csrftoken="));return c?c.split("=")[1]:"";}

function go0(){const v=get("fName");if(!v){hint("h0",true);return;}hint("h0",false);lockStep("s0","fas fa-user-graduate",v);say(`Nice to meet you, <strong>${v}</strong>! What's your <strong>email address</strong>?`,500);step=1;prog(1);show("s1");focus("fEmail");}
function go1(){const v=get("fEmail");if(!v||!v.includes("@")){hint("h1",true);return;}hint("h1",false);lockStep("s1","fas fa-envelope",v);say(`Got it — <strong>${v}</strong>. What's your <strong>admission number</strong>? You can skip this.`,500);step=2;prog(2);show("s2");focus("fAdm");}
function go2(){const v=get("fAdm");lockStep("s2","fas fa-id-card",v||"Skipped");toStep3();}
function skip2(){document.getElementById("fAdm").value="";lockStep("s2","fas fa-id-card","Skipped");toStep3();}
function toStep3(){say(`Thank you! Now — <strong>what would you like to do?</strong><br><em>I can verify clashes against the live timetable database.</em>`,500);step=3;prog(3);show("s3");}
function chooseType(t){ftype=t;const labels={general:"Share feedback / suggestion",regular:"Report a lecture clash",exam:"Report an exam clash"};lockStep("s3","fas fa-comment-dots",labels[t]);if(t==="general"){say(`Go ahead — type your <strong>message or suggestion</strong> below:`,400);step=4;prog(4);show("s4a");focus("fMsg");}else{const which=t==="exam"?"exam":"lecture";say(`I'll check the database for your <strong>${which} clash</strong>. Please fill in the details:`,400);step=4;prog(4);show("s4b");focus("fProg");}}
function pickYear(btn,n){document.querySelectorAll(".yc").forEach(c=>c.classList.remove("selected"));btn.classList.add("selected");yr=n.toString();hint("hYear",false);}

async function submitGeneral(){
  const msg=get("fMsg");if(!msg||msg.length<10){hint("h4a",true);return;}hint("h4a",false);
  const btn=document.querySelector("#s4a .btn-fp-main");setBusy(btn,"Sending…");
  const ok=await postFeedback({full_name:get("fName"),email:get("fEmail"),admission_number:get("fAdm"),message:msg});
  if(ok){done(`<strong>Thank you, ${get("fName")}!</strong><br>Your feedback has been received. The Timetabling team will review it and follow up if needed.`);}
  else{setReady(btn,'<i class="fas fa-paper-plane"></i> Send Feedback');}
}

async function doClash(){
  const prog_val=get("fProg");if(!prog_val){hint("hProg",true);return;}hint("hProg",false);
  if(!yr){hint("hYear",true);return;}
  const courses=get("fCourses"),desc=get("fDesc"),btn=document.getElementById("btnCheck");
  setBusy(btn,"Checking database…");document.getElementById("clashResult").innerHTML="";
  let verifyData=null;
  try{const r=await fetch("/api/bot-chat/",{method:"POST",headers:{"Content-Type":"application/json","X-CSRFToken":csrf()},body:JSON.stringify({message:JSON.stringify({_bot_verify:true,type:ftype,program:prog_val,year:yr,courses}),_direct_verify:true})});const j=await r.json();if(j.ok)verifyData=j;}catch(_){}
  const found=verifyData&&verifyData.action==="collision_found",collisions=verifyData&&verifyData.collisions?verifyData.collisions:[],total=verifyData&&verifyData.total?verifyData.total:0;
  const rEl=document.getElementById("clashResult");
  if(found){rEl.innerHTML=`<div class="coll-card bad"><div class="coll-title"><i class="fas fa-exclamation-triangle"></i>${total} Collision${total!==1?"s":""} confirmed</div>${collisions.map(c=>`<div class="coll-item"><i class="fas fa-times-circle" style="color:#c03030;"></i><span>${c.description||c.type}</span></div>`).join("")}</div>`;}
  else{rEl.innerHTML=`<div class="coll-card good"><div class="coll-title"><i class="fas fa-check-circle"></i>No clash detected in the current database</div><div class="coll-item"><i class="fas fa-info-circle" style="color:var(--cu-green);"></i><span>Your report will still be saved for manual staff review.</span></div></div>`;}
  const typeLabel=ftype==="exam"?"EXAM CLASH":"LECTURE CLASH";
  let fullMsg=`[${typeLabel}]\nProgram: ${prog_val} | Year: ${yr}\nCourses: ${courses||"Not specified"}\n`;
  if(desc)fullMsg+=`Description: ${desc}\n`;
  if(found){fullMsg+=`\n[BOT-VERIFIED: ${total} collision(s) found]\n`;collisions.forEach(c=>{fullMsg+=`- ${c.description||c.type}\n`;});}
  else{fullMsg+="\n[BOT-CHECKED: No collision found in DB at time of report]";}
  const ok=await postFeedback({full_name:get("fName"),email:get("fEmail"),admission_number:get("fAdm"),message:fullMsg});
  if(ok){setTimeout(()=>{if(found){done(`<strong>Clash report submitted, ${get("fName")}!</strong><br>We confirmed <strong>${total} conflict${total!==1?"s":""}</strong>. The Timetabling staff have been notified.`);}else{done(`<strong>Report saved, ${get("fName")}!</strong><br>No automatic clash was detected, but your report has been logged for manual review.`);}},1200);}
  else{setReady(btn,'<i class="fas fa-search"></i> Check &amp; Submit Report');}
}

function done(html){say(`All done! Here's what happened:`,0);document.getElementById("doneMsg").innerHTML=html;step=5;prog(5);show("s5");}

function restart(){
  ["fName","fEmail","fAdm","fMsg","fProg","fCourses","fDesc"].forEach(id=>{const el=document.getElementById(id);if(el)el.value="";});
  document.getElementById("clashResult").innerHTML="";
  document.querySelectorAll(".yc").forEach(c=>c.classList.remove("selected"));
  document.querySelectorAll(".answered-chip").forEach(c=>c.remove());
  document.querySelectorAll(".step").forEach(s=>{s.querySelectorAll("input,textarea,button,.chip-row,.hint").forEach(el=>el.style.display="");});
  ftype="";yr="";step=0;prog(0);
  say('<div class="tdots"><div class="tdot"></div><div class="tdot"></div><div class="tdot"></div></div>');
  setTimeout(()=>{say(`Hi again! I'm your <strong>Timetabling Assistant</strong>. Let's go step by step.<br>First — what's your <strong>full name</strong>?`);},700);
  show("s0");focus("fName");
}

async function postFeedback(payload){
  try{
    const r=await fetch("/api/submit_feedback/",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const d=await r.json();if(!d.ok){alert(d.error||"Could not save feedback. Please try again.");return false;}return true;
  }catch(_){alert("Network error. Please check your connection and try again.");return false;}
}

window.addEventListener("DOMContentLoaded",()=>{
  setTimeout(()=>{say(`Hi! I'm your <strong>Timetabling Assistant</strong>. I'll guide you through step by step.<br>First — what's your <strong>full name</strong>?`);},600);
  focus("fName");
  // Fade out the fab label after 5s
  setTimeout(()=>{const l=document.getElementById("botFabLabel");if(l)l.style.opacity="0";},5000);
});

/* ── Floating bot widget ─────────────────────────────────────── */
let botOpen=false;
function toggleBot(){
  botOpen=!botOpen;
  document.getElementById("botPanel").classList.toggle("open",botOpen);
  document.getElementById("botFabLabel").style.display=botOpen?"none":"block";
  if(botOpen)document.getElementById("botInput").focus();
}

function botQuick(msg){
  addBotUserMsg(msg);
  botRespond(msg);
}

function sendBot(){
  const inp=document.getElementById("botInput");
  const msg=inp.value.trim();if(!msg)return;
  inp.value="";
  addBotUserMsg(msg);
  botRespond(msg);
}

function addBotUserMsg(msg){
  const body=document.getElementById("botPanelBody");
  const d=document.createElement("div");d.className="bp-msg user";d.textContent=msg;body.appendChild(d);
  body.scrollTop=body.scrollHeight;
}

function botRespond(msg){
  const body=document.getElementById("botPanelBody");
  const typing=document.createElement("div");typing.className="bp-msg bot";
  typing.innerHTML='<div class="tdots"><div class="tdot"></div><div class="tdot"></div><div class="tdot"></div></div>';
  body.appendChild(typing);body.scrollTop=body.scrollHeight;

  // Simple keyword responses
  const m=msg.toLowerCase();
  let reply="";
  if(m.includes("clash")||m.includes("collision")){reply="To report a clash, use the feedback form on this page — just scroll up and follow the steps. I'll verify it against the live database for you.";}
  else if(m.includes("exam")||m.includes("examination")){reply="Exam timetables are available on the <a href='/student/portal/' style='color:var(--cu-green);'>Student Portal</a>. You can view or download them there.";}
  else if(m.includes("download")||m.includes("pdf")){reply="You can download the latest timetable PDFs from the <a href='/portal/' style='color:var(--cu-green);'>Student Portal</a> — look for the quick downloads bar.";}
  else if(m.includes("contact")||m.includes("office")){reply="The Timetabling &amp; Examinations office is located at Science Complex (S102), Main Campus. Office hours: Mon–Fri 8 AM – 5 PM.";}
  else if(m.includes("class rep")||m.includes("classrep")){reply="Class Representatives can log in at <a href='/classrep/login/' style='color:var(--cu-green);'>/classrep/login/</a> to manage timetable entries.";}
  else if(m.includes("staff")||m.includes("lecturer")){reply="Staff can access the timetabling dashboard via the <a href='/staff/portal/' style='color:var(--cu-green);'>Staff Portal</a>.";}
  else{reply="I'm not sure about that specific question. Please use the feedback form above to submit a query, or visit the <a href='/portal/' style='color:var(--cu-green);'>Student Portal</a> for more information.";}

  setTimeout(()=>{
    typing.innerHTML=reply;
    body.scrollTop=body.scrollHeight;
  },900);
}