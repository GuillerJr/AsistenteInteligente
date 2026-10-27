import Foundation

/// Own fixed scripts, evaluated in a private JavaScript content world. The
/// caller supplies JSON data, never an expression or selector to execute.
enum BrowserAutomationScripts {
    static func expression(_ body: String, payload: [String: RPCValue]) throws -> String {
        let data = try JSONEncoder().encode(RPCValue.object(payload))
        guard data.count <= 20_000, let json = String(data: data, encoding: .utf8) else {
            throw BrowserAutomationError.oversized
        }
        return "(function(payload){'use strict';\n" + body + "\n})(" + json + ")"
    }

    static let observe = #"""
    if(location.origin!==payload.expected_origin)return JSON.stringify({error:'stale'});
    if (globalThis.__jarvisBrowserState?.observer) globalThis.__jarvisBrowserState.observer.disconnect();
    const state={snapshot:payload.snapshot,generation:0,elements:new Map(),fingerprints:new Map()};
    const clean=v=>String(v??'').replace(/\s+/g,' ').slice(0,512);
    const secret=e=>e.matches('input[type=password],input[type=hidden]') ||
      /password|one-time-code|cc-/.test(e.getAttribute('autocomplete')||'');
    const visible=e=>{const r=e.getBoundingClientRect();const c=getComputedStyle(e);
      return r.width>0&&r.height>0&&c.display!=='none'&&c.visibility!=='hidden'&&!e.closest('[inert],[aria-hidden=true]');};
    const describe=e=>({tag:e.tagName.toLowerCase(),role:clean(e.getAttribute('role')||e.tagName.toLowerCase()),
      label:clean(e.getAttribute('aria-label')||e.getAttribute('placeholder')||e.textContent||e.getAttribute('name')),
      value:secret(e)?null:typeof e.value==='string'?e.value.slice(0,2048):null,
      type:clean(e.getAttribute('type')),href:e.tagName==='A'?String(e.href).slice(0,4096):null,
      disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true'});
    state.describe=describe;state.visible=visible;state.secret=secret;
    const rows=[];let count=0;let rowBytes=0;const all=document.querySelectorAll('a,button,input,textarea,select,[role=button],[role=link],[contenteditable=true]');
    for(const e of all){if(rows.length>=64)break;if(secret(e)||!visible(e))continue;
      const ref='r'+(++count);const row=describe(e);const size=new TextEncoder().encode(JSON.stringify(row)).length;
      if(rowBytes+size>24000)break;rowBytes+=size;state.elements.set(ref,e);
      state.fingerprints.set(ref,JSON.stringify(row));rows.push({ref,...row});}
    state.observer=new MutationObserver(()=>{state.generation++});
    state.observer.observe(document,{subtree:true,childList:true,attributes:true,characterData:true});
    globalThis.__jarvisBrowserState=state;
    return JSON.stringify({snapshot_id:state.snapshot,url:location.href,origin:location.origin,title:document.title.slice(0,512),
      text:(document.body?.innerText||'').slice(0,8000),elements:rows,truncated:all.length>rows.length,
      frame_scope:'main',external_unverified:true});
    """#

    static let inspect = #"""
    const state=globalThis.__jarvisBrowserState;
    if(location.origin!==payload.expected_origin||!state||state.snapshot!==payload.snapshot_id||state.generation!==0)return JSON.stringify({error:'stale'});
    const e=state.elements.get(payload.ref);
    if(!e||!e.isConnected||!state.visible(e)||state.secret(e)||
      JSON.stringify(state.describe(e))!==state.fingerprints.get(payload.ref))return JSON.stringify({error:'stale'});
    const row=state.describe(e);if(row.disabled)return JSON.stringify({error:'disabled'});
    return JSON.stringify({element:row,form_action:e.form?.action||null});
    """#

    static let action = #"""
    const state=globalThis.__jarvisBrowserState;
    if(location.origin!==payload.expected_origin||!state||state.snapshot!==payload.snapshot_id||state.generation!==0)return JSON.stringify({error:'stale'});
    const e=state.elements.get(payload.ref);
    if(!e||!e.isConnected||!state.visible(e)||state.secret(e)||e.disabled||
      JSON.stringify(state.describe(e))!==state.fingerprints.get(payload.ref))return JSON.stringify({error:'stale'});
    state.generation++;
    if(payload.action==='click'){e.click();return JSON.stringify({dispatched:true,verified:false});}
    if(payload.action==='fill'){
      if(!(e instanceof HTMLInputElement||e instanceof HTMLTextAreaElement)||e.type==='file')return JSON.stringify({error:'unsupported'});
      const setter=Object.getOwnPropertyDescriptor(e instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set;
      setter.call(e,payload.text);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));
      return JSON.stringify({dispatched:true,verified:e.value===payload.text,value_matches:e.value===payload.text});}
    if(payload.action==='select'){
      if(!(e instanceof HTMLSelectElement)||!Array.from(e.options).some(o=>o.value===payload.value))return JSON.stringify({error:'unsupported'});
      e.value=payload.value;e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));
      return JSON.stringify({dispatched:true,verified:e.value===payload.value,value_matches:e.value===payload.value});}
    if(payload.action==='scroll'){e.scrollIntoView({block:'center',inline:'nearest'});return JSON.stringify({dispatched:true,verified:false});}
    if(payload.action==='press'){
      e.focus();e.dispatchEvent(new KeyboardEvent('keydown',{key:payload.key,bubbles:true}));
      e.dispatchEvent(new KeyboardEvent('keyup',{key:payload.key,bubbles:true}));
      if(payload.key==='Enter'&&e.form)e.form.requestSubmit();
      return JSON.stringify({dispatched:true,verified:false,synthetic_keyboard_events:true});}
    return JSON.stringify({error:'unsupported'});
    """#
}
