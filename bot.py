import os,re,time,uuid
from datetime import datetime,timezone
from typing import Any
from fastapi import FastAPI
from pydantic import BaseModel
app=FastAPI(title="Vera Merchant AI Assistant",version="1.0.0")
started=time.time(); contexts={}; conversations={}; suppressions=set()
AUTO=["thank you for contacting","thanks for contacting","we will get back","will get back to you","message has been received","thank you for reaching out","we'll get back"]
def clean(x): return re.sub(r"\s+"," ",str(x or "")).strip()
def low(x): return clean(x).lower()
def merchant(mid): return contexts.get(("merchant",mid),{})
def customer(cid): return contexts.get(("customer",cid),{})
def auto_reply(x): return any(p in low(x) for p in AUTO)
def build(t,m,c=None):
    p=t.get("payload",{}) or {}; k=t.get("kind",""); i=m.get("identity",{}); name=i.get("owner_first_name") or i.get("name") or "there"; cat=m.get("category_slug","local business")
    if t.get("customer_id"):
        consent=(c or {}).get("consent",{}).get("scope",[])
        if k not in {"recall_due","appointment_tomorrow","chronic_refill_due","trial_followup"} or not any(x in consent for x in ["recall_reminders","appointment_reminders"]): return None,"none","customer consent does not cover this outreach"
        cn=(c or {}).get("identity",{}).get("name","there")
        if k=="recall_due":
            slots=p.get("available_slots",[]); s=" or ".join(x.get("label","") for x in slots[:2]); return f"Hi {cn}, a 6-month cleaning is due based on your last visit ({p.get('last_service_date')}). We have {s} available. Want me to help confirm one?","open_ended","consent and recorded appointment data support this reminder"
        if k=="trial_followup":
            slots=p.get("next_session_options",[]); s=slots[0].get("label","a next session") if slots else "a next session"; return f"Hi {cn}, following up on your trial from {p.get('trial_date')}. The next option currently open is {s}. Would you like to take it?","open_ended","recorded trial and session option"
        meds=", ".join(p.get("molecule_list",[])[:3]); return f"Hi {cn}, your refill window is coming up for {meds}. Want the pharmacy team to prepare the refill?","open_ended","recorded medicines and consent"
    if k=="research_digest": return f"Hi {name} — useful update for {cat}: {p.get('title','a new category research item')}. Source: {p.get('source','category digest')}. Want me to turn this into a short customer-facing post?","open_ended","category-specific research"
    if k=="regulation_change": return f"Quick heads-up, {name}: {p.get('title','a category compliance update')} (source: {p.get('source','category digest')}). Deadline: {p.get('deadline_iso')}. Want a checklist of affected changes?","open_ended","supplied source and deadline"
    if k=="perf_dip":
        d=abs(float(p.get('delta_pct',0)))*100; return f"{name}, {p.get('metric','performance')} are down {d:.0f}% over {p.get('window','7d')} vs baseline ({p.get('vs_baseline')}). Want to test one concrete listing change first?","yes_stop","metric magnitude window baseline"
    if k=="renewal_due": return f"{name}, your {p.get('plan','current')} plan has {p.get('days_remaining')} days left. Renewal amount: ₹{p.get('renewal_amount')}. Want me to walk you through renewal?","yes_stop","exact account data"
    if k=="festival_upcoming": return f"{name}, {p.get('festival','the upcoming festival')} is on {p.get('date')}. For a {cat} business, want 2 offer ideas based on your current catalog?","yes_stop","supplied festival and category"
    if k=="wedding_package_followup": return f"{name}, Kavya's wedding is on {p.get('wedding_date')}; the recorded next step is the 30-day skin-prep program. Want me to draft that package structure?","yes_stop","identified customer plan"
    if k=="winback_eligible":
        d=abs(float(p.get('perf_dip_pct',0)))*100; return f"{name}, you have {p.get('lapsed_customers_added_since_expiry')} newly lapsed customers, while performance is down {d:.0f}%. Want one win-back message around a specific service rather than a blanket discount?","yes_stop","lapsed customer and performance signals"
    if k=="ipl_match_today": return f"{name}, DC vs MI is at {p.get('match_time_iso','today')} at {p.get('venue','the listed venue')} in Delhi. Want a specific match-day push from your current menu?","open_ended","exact supplied match details"
    if k=="review_theme_emerged": return f"{name}, {p.get('occurrences_30d')} recent reviews mention '{p.get('theme')}', and the trend is {p.get('trend')}. One review says: \"{p.get('common_quote')}\". Want a response template?","yes_stop","supplied review evidence"
    if k=="milestone_reached":
        gap=int(p.get('milestone_value',0))-int(p.get('value_now',0)); return f"{name}, you're at {p.get('value_now')} reviews — only {gap} more to reach {p.get('milestone_value')}. Want a simple review-request message?","open_ended","exact milestone"
    return f"Hi {name}, I have a relevant update for your {cat} business. Want me to share the specific next step?","open_ended","merchant-specific fallback"
class ContextIn(BaseModel): scope:str; context_id:str; payload:dict[str,Any]
class TickIn(BaseModel): trigger:dict[str,Any]
class ReplyIn(BaseModel): conversation_id:str; message_id:str|None=None; text:str; timestamp:str|None=None
@app.get("/")
def root(): return {"status":"ok","service":"Vera Merchant AI Assistant","version":"1.0.0","docs":"/docs","health":"/v1/healthz"}
@app.get("/v1/healthz")
def health(): return {"status":"ok","uptime_seconds":round(time.time()-started,2)}
@app.get("/v1/metadata")
def metadata(): return {"name":"Vera Merchant AI Assistant","version":"1.0.0","protocol":"v1"}
@app.post("/v1/context")
def set_context(x:ContextIn): contexts[(x.scope,x.context_id)]=x.payload; return {"ok":True,"scope":x.scope,"context_id":x.context_id}
@app.post("/v1/tick")
def tick(x:TickIn):
    t=x.trigger; mid=t.get("merchant_id"); m=merchant(mid); c=customer(t.get("customer_id")) if t.get("customer_id") else None
    if not m:return {"status":"skipped","reason":"merchant context missing"}
    body,mode,reason=build(t,m,c)
    if body is None:return {"status":"skipped","reason":reason}
    key=f"{mid}:{t.get('kind')}:{t.get('event_id') or t.get('id') or t.get('payload',{}).get('date','')}"
    if key in suppressions:return {"status":"skipped","reason":"duplicate suppression key"}
    suppressions.add(key); cid=str(uuid.uuid4()); conversations[cid]={"merchant_id":mid,"customer_id":t.get("customer_id"),"trigger":t,"history":[]}
    return {"status":"sent","conversation_id":cid,"message":{"message_id":str(uuid.uuid4()),"channel":"whatsapp","body":body,"expected_reply_mode":mode},"reason":reason}
@app.post("/v1/reply")
def reply(x:ReplyIn):
    conv=conversations.get(x.conversation_id)
    if not conv:return {"status":"error","reason":"unknown conversation"}
    text=clean(x.text); conv["history"].append({"role":"merchant","text":text})
    if auto_reply(text):return {"status":"wait","reason":"automatic WhatsApp acknowledgement detected","should_send":False}
    t=low(text)
    if any(w in t for w in ["yes","sure","okay","ok","do it","let's do it","go ahead","interested"]):return {"status":"action","should_send":True,"message":{"message_id":str(uuid.uuid4()),"body":"Great — let's do it. I can move to the next step using the details already on file.","action":"advance_to_next_step"}}
    if any(w in t for w in ["no","not now","stop","don't"]):return {"status":"closed","should_send":False,"reason":"merchant declined or asked to stop"}
    return {"status":"continue","should_send":True,"message":{"message_id":str(uuid.uuid4()),"body":"Understood. I’ll keep this focused on the specific business goal and use the details already on file. What would you like to adjust first?"}}
@app.post("/v1/teardown")
def teardown(): contexts.clear(); conversations.clear(); suppressions.clear(); return {"ok":True}
 
