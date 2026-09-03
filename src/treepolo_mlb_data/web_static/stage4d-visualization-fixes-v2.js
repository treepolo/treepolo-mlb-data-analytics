(() => {
  "use strict";

  if (window.__treepoloStage4DFixupsV2) return;
  window.__treepoloStage4DFixupsV2 = true;

  const SVG_NS = "http://www.w3.org/2000/svg";
  const COLORS = ["#2f6fad","#c3543f","#3f8a5b","#8a5ba5","#c18a2d","#477f8e","#9a5966","#65728a","#6c8f36","#a76d34"];
  let lastPrepared = null;
  const upstreamFetch = window.fetch.bind(window);
  const $ = (selector, root=document) => root.querySelector(selector);
  const unique = values => Array.from(new Set(values.map(value => String(value ?? "—"))));
  const numeric = value => typeof value === "number" && Number.isFinite(value) ? value : (value != null && value !== "" && Number.isFinite(Number(value)) ? Number(value) : null);
  const fmt = value => {
    if (value == null) return "—";
    if (typeof value === "number" && Number.isFinite(value)) return Number.isInteger(value) ? value.toLocaleString("en-US") : value.toFixed(3).replace(/0+$/,"").replace(/\.$/,"");
    return String(value);
  };
  const svgEl = (name, attrs={}, text=null) => {
    const el=document.createElementNS(SVG_NS,name);
    Object.entries(attrs).forEach(([key,value])=>value!=null&&el.setAttribute(key,String(value)));
    if(text!=null)el.textContent=String(text);
    return el;
  };
  const color = (value,categories) => COLORS[Math.max(0,categories.indexOf(String(value ?? "—"))) % COLORS.length];
  const safeDomain = (min,max) => {
    if(!Number.isFinite(min)||!Number.isFinite(max)) return [0,1];
    if(min===max){const pad=Math.abs(min||1)*.08;return[min-pad,max+pad];}
    const pad=(max-min)*.06;return[min-pad,max+pad];
  };
  const scale = ([d0,d1],[r0,r1]) => value => r0+(Number(value)-d0)/(d1-d0)*(r1-r0);

  window.fetch = async function treepoloStage4DRenderFixFetch(input, init={}) {
    const url=typeof input==="string"?input:input?.url||"";
    const method=String(init?.method||"GET").toUpperCase();
    const response=await upstreamFetch(input,init);
    try{
      if(url.includes("/api/visualization/data")&&method==="POST"&&response.ok){
        const body=await response.clone().json();
        if(body?.result_available){lastPrepared=body;setTimeout(renderAdvancedBars,0);}
      }
    }catch{}
    return response;
  };

  function barNeedsFix() {
    const type=$("#viz-type")?.value;
    return (type==="bar"||type==="difference") && ($("#viz-bar-orientation")?.value==="horizontal" || $("#viz-stacked")?.checked);
  }

  function renderAdvancedBars() {
    if(!barNeedsFix()||!lastPrepared?.section?.rows?.length)return;
    const svg=$("#viz-canvas");if(!svg)return;
    const rows=lastPrepared.section.rows.filter(row=>row&&typeof row==="object");
    const xField=$("#viz-x")?.value||"",yField=$("#viz-y")?.value||"",seriesField=$("#viz-series")?.value||"";
    if(!yField)return;
    const values=rows.map(row=>numeric(row[yField])).filter(value=>value!=null);if(!values.length)return;
    const horizontal=$("#viz-bar-orientation")?.value==="horizontal",stacked=Boolean($("#viz-stacked")?.checked);
    const width=Math.max(480,Number($("#viz-width")?.value||1000)),height=Math.max(320,Number($("#viz-height")?.value||620));
    const opacity=Math.max(.05,Math.min(1,Number($("#viz-opacity")?.value||.75))),showLabels=Boolean($("#viz-data-labels")?.checked),showN=Boolean($("#viz-show-n")?.checked),showLegend=Boolean($("#viz-legend")?.checked);
    const title=$("#viz-title")?.value||lastPrepared.section.title||"Visualization",subtitle=$("#viz-subtitle")?.value||"";
    const categories=seriesField?unique(rows.map(row=>row[seriesField])):["All"];
    const xCategories=unique(rows.map((row,index)=>xField?row[xField]:index+1));
    const groups=xCategories.map(label=>({label,rows:rows.filter((row,index)=>String(xField?row[xField]:index+1)===label)}));
    svg.replaceChildren();svg.setAttribute("width",width);svg.setAttribute("height",height);svg.setAttribute("viewBox",`0 0 ${width} ${height}`);svg.append(svgEl("rect",{x:0,y:0,width,height,fill:"#fff"}));
    svg.append(svgEl("text",{x:22,y:28,class:"viz-title"},title));if(subtitle)svg.append(svgEl("text",{x:22,y:48,class:"viz-subtitle"},subtitle));
    if(showN){const s=lastPrepared.sampling||{};svg.append(svgEl("text",{x:width-20,y:27,"text-anchor":"end",class:"viz-subtitle"},s.sampled?`Sampled n=${fmt(s.returned_rows)} / ${fmt(s.source_rows)}`:`n=${fmt(s.returned_rows??rows.length)}`));}
    const margin={left:horizontal?150:75,right:showLegend?150:30,top:70,bottom:horizontal?45:75};const left=margin.left,right=width-margin.right,top=margin.top,bottom=height-margin.bottom;
    let positiveMax=0,negativeMin=0;
    if(stacked){groups.forEach(group=>{let positive=0,negative=0;group.rows.forEach(row=>{const value=numeric(row[yField]);if(value==null)return;if(value>=0)positive+=value;else negative+=value;});positiveMax=Math.max(positiveMax,positive);negativeMin=Math.min(negativeMin,negative);});}
    else{positiveMax=Math.max(0,...values);negativeMin=Math.min(0,...values);}
    let [vMin,vMax]=safeDomain(negativeMin,positiveMax);const userMin=$(horizontal?"#viz-x-min":"#viz-y-min")?.value,userMax=$(horizontal?"#viz-x-max":"#viz-y-max")?.value;if(userMin!=="")vMin=Number(userMin);if(userMax!=="")vMax=Number(userMax);
    if(horizontal)drawHorizontal(svg,groups,{left,right,top,bottom,vMin,vMax,yField,seriesField,categories,stacked,opacity,showLabels});
    else drawVertical(svg,groups,{left,right,top,bottom,vMin,vMax,yField,seriesField,categories,stacked,opacity,showLabels});
    if(showLegend&&seriesField)categories.slice(0,18).forEach((category,index)=>{const y=top+index*17;svg.append(svgEl("rect",{x:right+18,y:y-9,width:10,height:10,fill:color(category,categories)}),svgEl("text",{x:right+33,y,class:"viz-legend"},category.slice(0,24)));});
  }

  function drawVertical(svg,groups,opts){const {left,right,top,bottom,vMin,vMax,yField,seriesField,categories,stacked,opacity,showLabels}=opts;const sy=scale([vMin,vMax],[bottom,top]),groupWidth=(right-left)/Math.max(1,groups.length);for(let tick=0;tick<=5;tick++){const ratio=tick/5,y=bottom-ratio*(bottom-top),value=vMin+ratio*(vMax-vMin);svg.append(svgEl("line",{x1:left,y1:y,x2:right,y2:y,class:"viz-gridline"}),svgEl("text",{x:left-7,y:y+4,"text-anchor":"end",class:"viz-label"},fmt(value)));}svg.append(svgEl("line",{x1:left,y1:bottom,x2:right,y2:bottom,class:"viz-axis"}),svgEl("line",{x1:left,y1:top,x2:left,y2:bottom,class:"viz-axis"}));groups.forEach((group,gIndex)=>{const cx=left+(gIndex+.5)*groupWidth;svg.append(svgEl("text",{x:cx,y:bottom+18,"text-anchor":"middle",class:"viz-label"},group.label.slice(0,16)));let pos=0,neg=0;const width=stacked?Math.max(4,groupWidth*.62):Math.max(3,groupWidth*.68/Math.max(1,group.rows.length));group.rows.forEach((row,rIndex)=>{const value=numeric(row[yField]);if(value==null)return;const category=seriesField?String(row[seriesField]??"—"):"All";let start=0,end=value,x=cx;if(stacked){if(value>=0){start=pos;pos+=value;end=pos;}else{start=neg;neg+=value;end=neg;}}else{x=cx+(rIndex-(group.rows.length-1)/2)*width;}const y1=sy(start),y2=sy(end);const rect=svgEl("rect",{x:x-width/2,y:Math.min(y1,y2),width:Math.max(2,width-1),height:Math.max(1,Math.abs(y2-y1)),fill:color(category,categories),opacity});rect.append(svgEl("title",{},`${group.label} · ${category} · ${fmt(value)}`));svg.append(rect);if(showLabels&&!stacked)svg.append(svgEl("text",{x,y:y2-4,"text-anchor":"middle",class:"viz-label"},fmt(value)));});});}

  function drawHorizontal(svg,groups,opts){const {left,right,top,bottom,vMin,vMax,yField,seriesField,categories,stacked,opacity,showLabels}=opts;const sx=scale([vMin,vMax],[left,right]),groupHeight=(bottom-top)/Math.max(1,groups.length);for(let tick=0;tick<=5;tick++){const ratio=tick/5,x=left+ratio*(right-left),value=vMin+ratio*(vMax-vMin);svg.append(svgEl("line",{x1:x,y1:top,x2:x,y2:bottom,class:"viz-gridline"}),svgEl("text",{x,y:bottom+18,"text-anchor":"middle",class:"viz-label"},fmt(value)));}svg.append(svgEl("line",{x1:left,y1:bottom,x2:right,y2:bottom,class:"viz-axis"}),svgEl("line",{x1:left,y1:top,x2:left,y2:bottom,class:"viz-axis"}));groups.forEach((group,gIndex)=>{const cy=top+(gIndex+.5)*groupHeight;svg.append(svgEl("text",{x:left-8,y:cy+4,"text-anchor":"end",class:"viz-label"},group.label.slice(0,20)));let pos=0,neg=0;const barHeight=stacked?Math.max(4,groupHeight*.62):Math.max(3,groupHeight*.7/Math.max(1,group.rows.length));group.rows.forEach((row,rIndex)=>{const value=numeric(row[yField]);if(value==null)return;const category=seriesField?String(row[seriesField]??"—"):"All";let start=0,end=value,y=cy;if(stacked){if(value>=0){start=pos;pos+=value;end=pos;}else{start=neg;neg+=value;end=neg;}}else{y=cy+(rIndex-(group.rows.length-1)/2)*barHeight;}const x1=sx(start),x2=sx(end);const rect=svgEl("rect",{x:Math.min(x1,x2),y:y-barHeight/2,width:Math.max(1,Math.abs(x2-x1)),height:Math.max(2,barHeight-1),fill:color(category,categories),opacity});rect.append(svgEl("title",{},`${group.label} · ${category} · ${fmt(value)}`));svg.append(rect);if(showLabels&&!stacked)svg.append(svgEl("text",{x:x2+(value>=0?4:-4),y:y+4,"text-anchor":value>=0?"start":"end",class:"viz-label"},fmt(value)));});});}

  function restoreNavigationOrder(){
    const nav=$(".navigation-pane");if(!nav)return;
    const groups=Array.from(nav.querySelectorAll(":scope > .task-group"));
    const dataGroup=groups.find(group=>group.querySelector(".task-group-title")?.textContent.includes("資料 Data"));
    const output=$("#stage4d-output-nav",nav);
    if(dataGroup&&nav.firstElementChild!==dataGroup)nav.prepend(dataGroup);
    if(output&&nav.lastElementChild!==output)nav.append(output);
  }

  function scheduleIfNeeded(){setTimeout(renderAdvancedBars,0);}
  document.addEventListener("DOMContentLoaded",()=>{
    restoreNavigationOrder();setTimeout(restoreNavigationOrder,0);
    const panel=$("#visualization-panel")||document.body;
    panel.addEventListener("change",event=>{if(event.target?.closest?.("#viz-type,#viz-bar-orientation,#viz-stacked,#viz-x,#viz-y,#viz-series,#viz-data-labels,#viz-title,#viz-subtitle,#viz-width,#viz-height,#viz-opacity,#viz-x-min,#viz-x-max,#viz-y-min,#viz-y-max,#viz-legend,#viz-show-n"))scheduleIfNeeded();});
    panel.addEventListener("click",event=>{if(event.target?.closest?.("#viz-render"))scheduleIfNeeded();});
  },{once:true});
})();
