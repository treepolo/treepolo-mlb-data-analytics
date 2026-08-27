(() => {
  "use strict";

  if (window.treepoloClassicFieldControls) return;
  window.treepoloClassicFieldControls = true;

  const PITCH_TYPES = [
    ["FF", "四縫線速球 Four-Seam Fastball"], ["SI", "伸卡球 Sinker"], ["FC", "卡特球 Cutter"],
    ["SL", "滑球 Slider"], ["ST", "Sweeper"], ["CU", "曲球 Curveball"],
    ["KC", "彈指曲球 Knuckle Curve"], ["CH", "變速球 Changeup"], ["FS", "指叉球 Split-Finger"],
    ["FO", "Forkball"], ["KN", "蝴蝶球 Knuckleball"], ["EP", "Eephus"],
    ["SC", "螺旋球 Screwball"], ["SV", "Slurve"],
  ];

  const VALUE_DOMAINS = {
    pitch_type: PITCH_TYPES,
    stand: [["R", "右打 Right"], ["L", "左打 Left"]],
    p_throws: [["R", "右投 Right"], ["L", "左投 Left"]],
    inning_topbot: [["Top", "上半局 Top"], ["Bot", "下半局 Bottom"]],
    type: [["B", "壞球 Ball"], ["S", "好球／擦棒 Strike"], ["X", "擊球進場 In Play"]],
    bb_type: [["ground_ball", "滾地球 Ground Ball"], ["line_drive", "平飛球 Line Drive"], ["fly_ball", "飛球 Fly Ball"], ["popup", "高飛球 Popup"]],
    description: [
      ["ball", "壞球 Ball"], ["blocked_ball", "擋球 Blocked Ball"], ["called_strike", "主審判好球 Called Strike"],
      ["swinging_strike", "揮空 Swinging Strike"], ["swinging_strike_blocked", "揮空未接捕 Swinging Strike Blocked"],
      ["foul", "界外 Foul"], ["foul_tip", "擦棒被捕 Foul Tip"], ["foul_bunt", "觸擊界外 Foul Bunt"],
      ["missed_bunt", "觸擊揮空 Missed Bunt"], ["hit_into_play", "擊球進場 In Play"],
      ["hit_by_pitch", "觸身球 Hit By Pitch"], ["pitchout", "Pitchout"],
    ],
    events: [
      ["single", "一壘安打 Single"], ["double", "二壘安打 Double"], ["triple", "三壘安打 Triple"], ["home_run", "全壘打 Home Run"],
      ["walk", "四壞 Walk"], ["intent_walk", "故意四壞 Intentional Walk"], ["hit_by_pitch", "觸身球 Hit By Pitch"],
      ["strikeout", "三振 Strikeout"], ["strikeout_double_play", "三振雙殺 Strikeout Double Play"], ["field_out", "一般出局 Field Out"],
      ["force_out", "封殺 Force Out"], ["grounded_into_double_play", "滾地雙殺 Grounded Into Double Play"], ["double_play", "雙殺 Double Play"],
      ["triple_play", "三殺 Triple Play"], ["field_error", "失誤 Field Error"], ["fielders_choice", "野手選擇 Fielder's Choice"],
      ["fielders_choice_out", "野手選擇出局 Fielder's Choice Out"], ["sac_fly", "高飛犧牲打 Sac Fly"], ["sac_bunt", "犧牲觸擊 Sac Bunt"],
      ["sac_fly_double_play", "高飛犧牲雙殺 Sac Fly Double Play"], ["sac_bunt_double_play", "犧牲觸擊雙殺 Sac Bunt Double Play"],
      ["catcher_interf", "捕手妨礙 Catcher Interference"], ["other_out", "其他出局 Other Out"],
    ],
    game_type: [["R", "例行賽 Regular Season"], ["S", "春訓 Spring Training"], ["F", "外卡 Wild Card"], ["D", "分區系列賽 Division Series"], ["L", "聯盟冠軍賽 League Championship"], ["W", "世界大賽 World Series"], ["A", "明星賽 All-Star"], ["E", "表演賽 Exhibition"]],
    if_fielding_alignment: [["Standard", "標準 Standard"], ["Infield shift", "內野佈陣 Infield Shift"], ["Strategic", "策略性 Strategic"], ["4th outfielder", "第四外野手 4th Outfielder"]],
    of_fielding_alignment: [["Standard", "標準 Standard"], ["Strategic", "策略性 Strategic"], ["4th outfielder", "第四外野手 4th Outfielder"]],
    __arrangement: [["consecutive", "全部連續 All Consecutive"], ["none_adjacent", "完全不相鄰 None Adjacent"], ["any", "不限 Any"]],
  };

  const MLB_TEAMS = ["ARI","ATH","ATL","BAL","BOS","CHC","CWS","CIN","CLE","COL","DET","HOU","KC","LAA","LAD","MIA","MIL","MIN","NYM","NYY","PHI","PIT","SD","SEA","SF","STL","TB","TEX","TOR","WSH"];
  VALUE_DOMAINS.home_team = MLB_TEAMS.map(v => [v, `球隊 Team · ${v}`]);
  VALUE_DOMAINS.away_team = VALUE_DOMAINS.home_team;
  VALUE_DOMAINS.pitch_name = [["4-Seam Fastball","四縫線速球 4-Seam Fastball"],["Sinker","伸卡球 Sinker"],["Cutter","卡特球 Cutter"],["Slider","滑球 Slider"],["Sweeper","Sweeper"],["Curveball","曲球 Curveball"],["Knuckle Curve","彈指曲球 Knuckle Curve"],["Changeup","變速球 Changeup"],["Split-Finger","指叉球 Split-Finger"],["Forkball","Forkball"],["Knuckleball","蝴蝶球 Knuckleball"],["Eephus","Eephus"],["Screwball","螺旋球 Screwball"],["Slurve","Slurve"]];
  VALUE_DOMAINS.zone = Array.from({length:14}, (_,i) => [String(i+1), `好球區 Zone ${i+1}`]);
  VALUE_DOMAINS.balls = Array.from({length:4}, (_,i) => [String(i), `壞球數 Balls ${i}`]);
  VALUE_DOMAINS.strikes = Array.from({length:3}, (_,i) => [String(i), `好球數 Strikes ${i}`]);
  VALUE_DOMAINS.outs_when_up = Array.from({length:3}, (_,i) => [String(i), `出局數 Outs ${i}`]);
  VALUE_DOMAINS.inning = Array.from({length:20}, (_,i) => [String(i+1), `第 ${i+1} 局 Inning ${i+1}`]);
  VALUE_DOMAINS.game_year = Array.from({length:Math.max(1,new Date().getFullYear()-2014)}, (_,i) => { const v=String(new Date().getFullYear()-i); return [v,`球季 Season ${v}`]; });

  const FIELD_INPUT_RULES = [
    [".s4-groups",true],[".s4-metric-field",false],[".s4-metric-cond-field",false],[".s4-left",false],[".s4-right-field",false],
    [".s4-field",false],[".s4-value-field",false],[".s4-partition",true],[".s4-order",true],[".s4-fields",true],
    ["#s4-cluster-features",true],["#s4-cluster-ids",true],["#s4-cluster-partitions",true],["#s4-reg-dependent",false],["#s4-reg-independent",true],
    ["#s4-boot-value",false],["#s4-boot-units",true],["#s4-boot-group",false],["#cc-entities",true],["#cc-features",true],
    [".ta-entity-fields",true],[".ta-pitch-field",false],[".ta-value-field",false],[".ta-percentile-field",false],[".ta-percentile-partition",true],
    [".ta-event-field",false],[".ta-metric-cond-value-field",false],
  ];
  const SINGLE_FIELD_SELECTORS = ['select[data-field-select]:not([multiple])','.s4-filter-field','.s4-field-select','.cc-field','.cc-filter-field','.result-sort-field'].join(',');
  const norm = v => String(v ?? '').toLowerCase().replace(/\s+/g,' ').trim();

  function injectStyles() {
    if (document.getElementById('classic-field-control-styles')) return;
    const s=document.createElement('style'); s.id='classic-field-control-styles'; s.textContent=`
      .xp-select-shell,.xp-edit-shell,.xp-semantic-shell{position:relative;display:flex;align-items:stretch;width:100%;min-width:0}
      .xp-select-shell>select,.xp-edit-shell>input,.xp-semantic-shell>input{flex:1 1 auto;min-width:0;width:100%}
      .xp-field-search-button,.xp-combo-button{flex:0 0 auto;min-height:24px;width:38px;padding:1px 4px;margin-left:2px;border-radius:2px;font-size:11px}
      .xp-combo-button{width:23px;margin-left:-1px;padding:0;font-size:10px}.xp-combo-button[hidden]{display:none}
      .xp-popup{position:absolute;left:0;top:calc(100% + 1px);z-index:10020;width:100%;min-width:270px;max-width:min(520px,90vw);max-height:245px;overflow:auto;border:1px solid #1f4e79;background:#fff;box-shadow:2px 2px 0 rgba(0,0,0,.22);padding:2px;color:#111}
      .xp-popup-search{display:block;width:100%;margin:0 0 2px;min-height:23px;border:1px solid #7f9db9;border-radius:0;background:#fff;padding:2px 4px;box-shadow:inset 1px 1px 1px rgba(0,0,0,.08)}
      .xp-popup-list{max-height:205px;overflow:auto;border-top:1px solid #d6d2c2;background:#fff}
      .xp-popup-item{display:block;width:100%;min-height:21px;padding:2px 5px;border:0;border-radius:0;background:#fff;box-shadow:none;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#111}
      .xp-popup-item:hover,.xp-popup-item:focus{background:#316ac5;color:#fff;outline:0;box-shadow:none}.xp-popup-item.xp-selected::before{content:'✓ ';font-weight:700}.xp-popup-empty{padding:5px;color:#666}
    `; document.head.append(s);
  }

  function closeAll(){ document.querySelectorAll('.xp-popup').forEach(n=>n.remove()); }
  function removeNativeDatalist(input){
    if(!input)return; const id=input.getAttribute('list');
    if(id && /^ta-(fields|pipeline)-/.test(id)){input.removeAttribute('list');document.getElementById(id)?.remove();}
    const sib=input.nextElementSibling; if(sib?.tagName==='DATALIST' && /^ta-(fields|pipeline)-/.test(sib.id||''))sib.remove();
  }
  function baseFieldOptions(){ const s=document.querySelector('#basic-group'); return s?Array.from(s.options).filter(o=>o.value).map(o=>({value:o.value,label:o.textContent||o.value})):[]; }
  function pipelineFieldOptions(input){
    const map=new Map(baseFieldOptions().map(o=>[o.value,o.label])); const stage=input.closest('.s4-stage'), list=stage?.parentElement;
    if(stage&&list){for(const sib of Array.from(list.children)){if(sib===stage)break;if(!sib.classList?.contains('s4-stage'))continue;
      sib.querySelectorAll('.s4-metric-alias,.s4-alias,.ta-custom-alias,.ta-cohort-alias').forEach(a=>{const v=a.value?.trim();if(v)map.set(v,`前一步輸出 Prior-stage alias · ${v}`);});}}
    return Array.from(map,([value,label])=>({value,label}));
  }
  function popup(shell, optionsProvider, choose, current, multi=false, initial=''){
    closeAll(); const p=document.createElement('div');p.className='xp-popup'; const q=document.createElement('input');q.type='text';q.className='xp-popup-search';q.placeholder='搜尋 Search';q.value=initial;
    const list=document.createElement('div');list.className='xp-popup-list';p.append(q,list);shell.append(p);
    const render=()=>{const query=norm(q.value), selected=new Set(String(current?.()??'').split(',').map(x=>x.trim()).filter(Boolean)); const opts=(optionsProvider?.()||[]).filter(o=>!query||norm(`${o.label} ${o.value}`).includes(query));list.innerHTML='';
      if(!opts.length){const e=document.createElement('div');e.className='xp-popup-empty';e.textContent='沒有符合項目 No matches';list.append(e);return;}
      opts.forEach(o=>{const b=document.createElement('button');b.type='button';b.className='xp-popup-item'+(selected.has(o.value)?' xp-selected':'');b.textContent=o.label.includes(o.value)?o.label:`${o.label} (${o.value})`;b.addEventListener('mousedown',e=>e.preventDefault());b.addEventListener('click',()=>{choose(o.value);if(multi)render();else closeAll();});list.append(b);});};
    q.addEventListener('input',render);q.addEventListener('keydown',e=>{if(e.key==='Escape')closeAll();if(e.key==='Enter'){const b=list.querySelector('.xp-popup-item');if(b){e.preventDefault();b.click();}}});render();q.focus();return p;
  }

  function restoreSingleSelect(select){
    if(!select||select.multiple)return; select.dataset.taCombo='1';select.hidden=false;select.removeAttribute('hidden');select.style.display='';
    const prev=select.previousElementSibling;if(prev?.classList?.contains('ta-field-combo')){removeNativeDatalist(prev);prev.remove();}
    const next=select.nextElementSibling;if(next?.tagName==='DATALIST'&&/^ta-fields-/.test(next.id||''))next.remove();
    if(select.dataset.xpFieldSearch==='1')return;select.dataset.xpFieldSearch='1';const shell=document.createElement('span');shell.className='xp-select-shell';select.parentNode.insertBefore(shell,select);shell.append(select);
    const b=document.createElement('button');b.type='button';b.className='xp-field-search-button';b.textContent='搜尋';b.title='搜尋欄位 Search field';shell.append(b);
    b.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();if(shell.querySelector('.xp-popup')){closeAll();return;}popup(shell,()=>Array.from(select.options).filter(o=>o.value).map(o=>({value:o.value,label:o.textContent||o.value})),v=>{select.value=v;select.dispatchEvent(new Event('change',{bubbles:true}));},()=>select.value);});
  }

  function token(input,multi){const raw=String(input.value||'');if(!multi)return raw.trim();return(raw.split(',').pop()||'').trim().replace(/^[+-]/,'');}
  function setToken(input,value,multi,replaceTail=false){
    if(!multi)input.value=value;else{const raw=String(input.value||'').split(','), tail=raw.at(-1)?.trim()||'', prefix=replaceTail?((tail.match(/^[+-]/)||[''])[0]):'';let parts=raw.map(x=>x.trim()).filter(Boolean);if(replaceTail&&tail)parts=parts.slice(0,-1);if(!new Set(parts.map(x=>x.replace(/^[+-]/,''))).has(value))parts.push(`${prefix}${value}`);input.value=parts.join(',');}
    input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));
  }
  function decorateFieldInput(input,multi){
    if(!input)return;input.dataset.taFieldAssist='1';removeNativeDatalist(input);if(input.dataset.xpFieldCombo==='1')return;input.dataset.xpFieldCombo='1';
    const shell=document.createElement('span');shell.className='xp-edit-shell';input.parentNode.insertBefore(shell,input);shell.append(input);const b=document.createElement('button');b.type='button';b.className='xp-combo-button';b.textContent='▼';b.title='顯示欄位清單 Show field list';shell.append(b);
    const open=fromTyping=>popup(shell,()=>pipelineFieldOptions(input),v=>setToken(input,v,multi,fromTyping),()=>input.value,multi,fromTyping?token(input,multi):'');
    b.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();if(shell.querySelector('.xp-popup'))closeAll();else open(false);});
    input.addEventListener('input',()=>{const t=token(input,multi);if(!t)return;let p=shell.querySelector('.xp-popup');if(!p)p=open(true);const q=p?.querySelector('.xp-popup-search');if(q&&q.value!==t){q.value=t;q.dispatchEvent(new Event('input'));}});
    input.addEventListener('keydown',e=>{if(e.key==='ArrowDown'&&!shell.querySelector('.xp-popup')){e.preventDefault();open(false);}if(e.key==='Escape')closeAll();});
  }

  function domain(field){return(VALUE_DOMAINS[String(field||'').trim()]||[]).map(([value,label])=>({value:String(value),label}));}
  function valueField(input){
    const row=input.closest('.condition-row,.s4-filter-row,.cc-filter-row,.s4-metric-row,.s4-stage');
    if(row?.classList.contains('condition-row'))return row.querySelector('.condition-field')?.value||'';
    if(row?.classList.contains('s4-filter-row'))return row.querySelector('.s4-filter-field')?.value||'';
    if(row?.classList.contains('cc-filter-row'))return row.querySelector('.cc-filter-field')?.value||'';
    if(row?.classList.contains('s4-metric-row'))return row.querySelector('.s4-metric-cond-field')?.value?.trim()||'';
    if(row?.classList.contains('s4-stage')){const kind=row.querySelector('.s4-stage-kind')?.value;if(kind==='filter'&&input.classList.contains('s4-value'))return row.querySelector('.s4-field')?.value?.trim()||'';if(kind==='event_pattern_cohorts'&&input.classList.contains('ta-event-value'))return row.querySelector('.ta-event-field')?.value?.trim()||'';}
    if(input.id==='s4-boot-a'||input.id==='s4-boot-b')return document.getElementById('s4-boot-group')?.value?.trim()||'';
    if(input.id==='s4-boot-success')return document.getElementById('s4-boot-value')?.value?.trim()||'';return input.dataset.semanticField||'';
  }
  function isMultiValue(input){const row=input.closest('.condition-row,.s4-filter-row,.cc-filter-row,.s4-metric-row,.s4-stage');const op=row?.querySelector('.condition-op,.s4-filter-op,.cc-filter-op,.s4-metric-cond-op,.s4-op,.ta-event-op')?.value||'eq';return op==='in'||op==='not_in'||input.dataset.semanticMulti==='1';}
  function refreshValue(input){const shell=input?.closest('.xp-semantic-shell');if(!shell)return;const f=input.dataset.semanticField||valueField(input),b=shell.querySelector('.xp-semantic-button'),available=domain(f).length>0;if(b)b.hidden=!available||input.disabled;if(!available)shell.querySelector('.xp-popup')?.remove();}
  function decorateValue(input,forcedField='',forcedMulti=false){
    if(!input)return;if(forcedField)input.dataset.semanticField=forcedField;if(forcedMulti)input.dataset.semanticMulti='1';if(input.dataset.xpSemanticCombo==='1'){refreshValue(input);return;}input.dataset.xpSemanticCombo='1';
    const shell=document.createElement('span');shell.className='xp-semantic-shell';input.parentNode.insertBefore(shell,input);shell.append(input);const b=document.createElement('button');b.type='button';b.className='xp-combo-button xp-semantic-button';b.textContent='▼';b.title='顯示合理值 Show valid values';shell.append(b);
    const open=fromTyping=>{const f=forcedField||valueField(input),opts=domain(f);if(!opts.length)return null;const multi=forcedMulti||isMultiValue(input);return popup(shell,()=>opts,v=>setToken(input,v,multi,fromTyping),()=>input.value,multi,fromTyping?token(input,multi):'');};
    b.addEventListener('click',e=>{e.preventDefault();e.stopPropagation();if(shell.querySelector('.xp-popup'))closeAll();else open(false);});
    input.addEventListener('input',()=>{const f=forcedField||valueField(input);if(!domain(f).length||!input.value.trim())return;let p=shell.querySelector('.xp-popup');if(!p)p=open(true);const q=p?.querySelector('.xp-popup-search'),t=token(input,forcedMulti||isMultiValue(input));if(q&&q.value!==t){q.value=t;q.dispatchEvent(new Event('input'));}});refreshValue(input);
  }

  function semanticControls(root=document){
    root.querySelectorAll?.('.condition-value,.s4-filter-value,.cc-filter-value,.s4-metric-cond-value,.s4-value,.ta-event-value,#s4-boot-a,#s4-boot-b,#s4-boot-success').forEach(i=>decorateValue(i));
    root.querySelectorAll?.('#role-exclude,.ta-role-exclude').forEach(i=>decorateValue(i,'pitch_type',true));
    root.querySelectorAll?.('#cc-reference').forEach(i=>decorateValue(i,'pitch_type',false));
    root.querySelectorAll?.('.ta-event-arrangements').forEach(i=>decorateValue(i,'__arrangement',true));
  }
  function scan(root=document){
    root.querySelectorAll?.(SINGLE_FIELD_SELECTORS).forEach(restoreSingleSelect);FIELD_INPUT_RULES.forEach(([s,m])=>root.querySelectorAll?.(s).forEach(i=>decorateFieldInput(i,m)));semanticControls(root);
    root.querySelectorAll?.('input[list^="ta-fields-"],input[list^="ta-pipeline-"]').forEach(removeNativeDatalist);root.querySelectorAll?.('datalist[id^="ta-fields-"],datalist[id^="ta-pipeline-"]').forEach(n=>n.remove());
  }
  function refreshRelated(e){const t=e.target,row=t?.closest?.('.condition-row,.s4-filter-row,.cc-filter-row,.s4-metric-row,.s4-stage');row?.querySelectorAll?.('.condition-value,.s4-filter-value,.cc-filter-value,.s4-metric-cond-value,.s4-value,.ta-event-value').forEach(refreshValue);if(t?.matches?.('#s4-boot-group,#s4-boot-value'))['#s4-boot-a','#s4-boot-b','#s4-boot-success'].forEach(s=>refreshValue(document.querySelector(s)));}
  function init(){
    injectStyles();scan(document);document.addEventListener('click',e=>{if(!e.target.closest?.('.xp-select-shell,.xp-edit-shell,.xp-semantic-shell'))closeAll();});document.addEventListener('change',refreshRelated);document.addEventListener('input',refreshRelated);document.addEventListener('treepolo:fields-updated',()=>setTimeout(()=>scan(document),0));
    let queued=false;new MutationObserver(ms=>{if(!ms.some(m=>m.addedNodes.length)||queued)return;queued=true;setTimeout(()=>{queued=false;scan(document);},0);}).observe(document.body,{childList:true,subtree:true});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
