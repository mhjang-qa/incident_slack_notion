(() => {
  const state = { data: window.DASHBOARD_DATA || {incidents:[]}, period:"year", sort:{key:"date",dir:-1}, charts:{} };
  const colors = {blue:"#4f8cff",purple:"#8b6cff",green:"#2ed3a3",red:"#ff5e6c",orange:"#ff9d55",yellow:"#f4ca55",muted:"#7388a3"};
  const $ = id => document.getElementById(id);
  const fmt = value => new Intl.NumberFormat("ko-KR").format(value || 0);
  const parseDate = value => value ? new Date(value) : null;
  const monthNow = () => new Date().toISOString().slice(0,7);
  const chartDefaults = () => ({
    responsive:true, maintainAspectRatio:false,
    plugins:{legend:{labels:{color:getComputedStyle(document.body).getPropertyValue("--muted"),boxWidth:10,usePointStyle:true}}},
    scales:{x:{ticks:{color:"#8296af"},grid:{color:"rgba(120,145,175,.09)"}},y:{beginAtZero:true,ticks:{color:"#8296af",precision:0},grid:{color:"rgba(120,145,175,.09)"}}}
  });

  function filtered() {
    const category = $("categoryFilter").value, impact = $("impactFilter").value;
    const now = new Date(); let start = null, end = now;
    if (state.period === "year") start = new Date(now.getFullYear(),0,1);
    else if (state.period !== "custom") start = new Date(now.getFullYear(),now.getMonth()-Number(state.period)+1,1);
    else { start = parseDate($("startDate").value); end = parseDate($("endDate").value) || now; if(end) end.setHours(23,59,59,999); }
    return state.data.incidents.filter(item => {
      const date = parseDate(item.registered_at || item.started_at);
      return (!start || (date && date >= start)) && (!end || (date && date <= end))
        && (!category || item.category === category) && (!impact || item.impact === impact);
    });
  }

  function countBy(items,key,order=[]) {
    const counts = items.reduce((acc,item)=>{ const value=item[key]||"미지정"; acc[value]=(acc[value]||0)+1; return acc; },{});
    const labels = order.length ? order.filter(label => counts[label] !== undefined) : Object.keys(counts).sort((a,b)=>counts[b]-counts[a]);
    return {labels, values:labels.map(label=>counts[label]||0)};
  }

  function aggregateMonthly(items,key,average=false) {
    const groups={};
    items.forEach(item=>{ if(!item.month || item[key] == null) return; (groups[item.month] ||= []).push(Number(item[key])); });
    const labels=Object.keys(groups).sort();
    return {labels,values:labels.map(label=>average?Math.round(groups[label].reduce((a,b)=>a+b,0)/groups[label].length):groups[label].length)};
  }

  function renderKpis(items) {
    const completed=items.filter(i=>i.mttr_minutes!=null);
    $("totalKpi").textContent=fmt(items.length);
    $("monthKpi").textContent=fmt(items.filter(i=>i.month===monthNow()).length);
    $("mttrKpi").textContent=fmt(completed.length?Math.round(completed.reduce((a,b)=>a+b.mttr_minutes,0)/completed.length):0);
    $("criticalKpi").textContent=fmt(items.filter(i=>i.impact==="Critical").length);
    $("openKpi").textContent=fmt(items.filter(i=>!["정상화","완료"].includes(i.status)).length);
  }

  function setChart(id, config) {
    if(state.charts[id]) state.charts[id].destroy();
    state.charts[id]=new Chart($(id),config);
  }

  function renderCharts(items) {
    const monthly=aggregateMonthly(items,"title");
    setChart("monthlyChart",{type:"line",data:{labels:monthly.labels,datasets:[{label:"장애 건수",data:monthly.values,borderColor:colors.blue,backgroundColor:"#4f8cff22",fill:true,tension:.38,pointRadius:4,pointBackgroundColor:colors.blue}]},options:chartDefaults()});
    const category=countBy(items,"category");
    setChart("categoryChart",{type:"pie",data:{labels:category.labels,datasets:[{data:category.values,backgroundColor:[colors.blue,colors.purple,colors.green,colors.orange,colors.red,colors.yellow]}]},options:{...chartDefaults(),scales:{}}});
    const impact=countBy(items,"impact",["Critical","High","Medium","Low","Unknown"]);
    setChart("impactChart",{type:"bar",data:{labels:impact.labels,datasets:[{label:"건수",data:impact.values,backgroundColor:[colors.red,colors.orange,colors.yellow,colors.blue,colors.muted],borderRadius:6}]},options:chartDefaults()});
    const grade=countBy(items,"grade",["P1","P2","P3","P4","미지정"]);
    setChart("gradeChart",{type:"doughnut",data:{labels:grade.labels,datasets:[{data:grade.values,backgroundColor:[colors.red,colors.orange,colors.yellow,colors.blue,colors.muted],borderWidth:0}]},options:{...chartDefaults(),scales:{},cutout:"66%"}});
    const owners=countBy(items,"assignee");
    setChart("assigneeChart",{type:"bar",data:{labels:owners.labels.slice(0,10),datasets:[{label:"처리 건수",data:owners.values.slice(0,10),backgroundColor:colors.purple,borderRadius:6}]},options:{...chartDefaults(),indexAxis:"y"}});
    const mttr=aggregateMonthly(items,"mttr_minutes",true);
    setChart("mttrChart",{type:"line",data:{labels:mttr.labels,datasets:[{label:"평균 MTTR(분)",data:mttr.values,borderColor:colors.green,backgroundColor:"#2ed3a31a",fill:true,tension:.35}]},options:chartDefaults()});
    renderFunnel(items);
  }

  function renderFunnel(items) {
    const stages=["발생","분석중","조치중","정상화","완료"], palette=[colors.red,colors.orange,colors.yellow,colors.blue,colors.green];
    const counts=countBy(items,"status",stages); const max=Math.max(...counts.values,1);
    $("funnelChart").innerHTML=stages.map((stage,index)=>{
      const value=(counts.values[index]||0), width=55+45*(value/max);
      return `<div class="funnel-row" style="width:${width}%;background:${palette[index]}cc"><span>${stage}</span><span>${value}</span></div>`;
    }).join("");
  }

  function renderTable(items) {
    const search=$("tableSearch").value.trim().toLowerCase();
    let rows=items.filter(item=>!search || [item.title,item.assignee,item.status,item.category,item.impact].join(" ").toLowerCase().includes(search));
    const {key,dir}=state.sort;
    rows.sort((a,b)=>String(a[key]??"").localeCompare(String(b[key]??""), "ko",{numeric:true})*dir);
    rows=rows.slice(0,20);
    $("incidentTable").innerHTML=rows.length?rows.map(item=>`<tr>
      <td>${esc(item.date||"-")}</td><td class="title-cell">${item.url?`<a href="${esc(item.url)}" target="_blank" style="color:inherit">${esc(item.title)}</a>`:esc(item.title)}</td>
      <td>${esc(item.category)}</td><td><span class="badge ${esc(item.impact)}">${esc(item.impact)}</span></td>
      <td><span class="badge ${esc(item.grade)}">${esc(item.grade)}</span></td><td>${esc(item.assignee)}</td>
      <td><span class="badge status-badge">${esc(item.status)}</span></td><td>${item.mttr_minutes==null?"진행중":fmt(item.mttr_minutes)+"분"}</td>
    </tr>`).join(""):`<tr><td colspan="8" class="empty">조건에 맞는 장애가 없습니다.</td></tr>`;
    $("tableCount").textContent=`${rows.length}건 표시`;
  }

  function esc(value){ const div=document.createElement("div"); div.textContent=value??""; return div.innerHTML; }
  function render(){ const items=filtered(); renderKpis(items); renderCharts(items); renderTable(items); }
  function syncLabels(){ const date=new Date(state.data.synced_at); const text=isNaN(date)?"-":date.toLocaleString("ko-KR"); $("syncTime").textContent=`마지막 동기화 ${text}`; $("sideSync").textContent=text; }
  function options(id,key){ const values=[...new Set(state.data.incidents.map(i=>i[key]).filter(Boolean))].sort(); $(id).innerHTML=`<option value="">전체 ${key==="category"?"구분":"영향도"}</option>`+values.map(v=>`<option>${esc(v)}</option>`).join(""); }
  function toast(message,error=false){ const el=$("toast"); el.textContent=message; el.className=`toast show${error?" error":""}`; setTimeout(()=>el.className="toast",3200); }

  async function refresh(){
    if(window.STATIC_MODE){ toast("정적 HTML은 생성 시점의 데이터입니다. 서버 또는 Pages 워크플로에서 재생성하세요.",true); return; }
    const button=$("refreshButton"); button.disabled=true;
    try{ const response=await fetch("/api/dashboard?refresh=true"); if(!response.ok) throw new Error(await response.text()); state.data=await response.json(); syncLabels(); options("categoryFilter","category"); options("impactFilter","impact"); render(); toast("Notion 데이터 동기화 완료"); }
    catch(error){ toast(`동기화 실패: ${error.message}`,true); } finally{ button.disabled=false; }
  }

  $("periodButtons").addEventListener("click",event=>{ if(!event.target.dataset.period)return; [...event.currentTarget.children].forEach(b=>b.classList.remove("active")); event.target.classList.add("active"); state.period=event.target.dataset.period; $("customDates").classList.toggle("visible",state.period==="custom"); render(); });
  ["startDate","endDate","categoryFilter","impactFilter"].forEach(id=>$(id).addEventListener("change",render));
  $("tableSearch").addEventListener("input",()=>renderTable(filtered()));
  document.querySelectorAll("th[data-sort]").forEach(th=>th.addEventListener("click",()=>{ const key=th.dataset.sort; state.sort={key,dir:state.sort.key===key?-state.sort.dir:-1}; renderTable(filtered()); }));
  $("refreshButton").addEventListener("click",refresh);
  $("themeButton").addEventListener("click",()=>{ document.body.classList.toggle("light"); localStorage.setItem("dashboard-theme",document.body.classList.contains("light")?"light":"dark"); renderCharts(filtered()); });
  if(localStorage.getItem("dashboard-theme")==="light") document.body.classList.add("light");
  options("categoryFilter","category"); options("impactFilter","impact"); syncLabels(); render();
  if(!window.STATIC_MODE) setInterval(refresh,300000);
})();

