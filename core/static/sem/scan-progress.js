'use strict';
document.querySelectorAll('[data-scan-live]').forEach(card => {
  const nf = new Intl.NumberFormat('nl-NL', {maximumFractionDigits: 0});
  const rateFormatter = new Intl.NumberFormat('nl-NL', {maximumFractionDigits: 2});
  const duration = value => {
    const seconds=Math.max(0,Math.ceil(value));
    if(seconds>=86400) return `${nf.format(Math.floor(seconds/86400))} d ${Math.floor(seconds%86400/3600)} u`;
    if(seconds>=3600) return `${nf.format(Math.floor(seconds/3600))} u ${Math.floor(seconds%3600/60)} min`;
    if(seconds>=60) return `${nf.format(Math.floor(seconds/60))} min ${seconds%60} s`;
    return `${nf.format(seconds)} s`;
  };
  const tf = new Intl.DateTimeFormat('nl-NL', {timeZone: 'Europe/Amsterdam', hour: '2-digit', minute: '2-digit', second: '2-digit'});
  const svg = card.querySelector('svg'), chart = card.querySelector('[data-live-chart]');
  const tooltip = card.querySelector('[data-live-tooltip]');
  const state = card.querySelector('[data-live-state]');
  let W=960,H=270,L=55,R=18,T=14,B=32,plotW=W-L-R,plotH=H-T-B;
  const ns='http://www.w3.org/2000/svg';
  let timer, busy=false, active=true, latest=null, samples=[], keys=[];
  const el=(tag,attrs={},text) => {
    const node=document.createElementNS(ns,tag);
    Object.entries(attrs).forEach(([name,value])=>node.setAttribute(name,value));
    if (text!==undefined) node.textContent=text;
    return node;
  };
  const outcomeLabels={success:'Succesvol',http_error:'HTTP-fout',blocked:'Geblokkeerd',retrying:'Opnieuw proberen',unknown:'Onbekend',download_error:'Downloadfout'};
  const phaseStates={queued:'In wachtrij',running:'Bezig',complete:'Afgerond',partial:'Gedeeltelijk',incomplete:'Gedeeltelijk',failed:'Mislukt',cancelled:'Gestopt',skipped:'Overgeslagen'};
  const label=key=>outcomeLabels[key] || `HTTP ${key}`;
  const color=key=> {
    const outcomes={success:'#21966c',http_error:'#e26955',blocked:'#c64e8c',retrying:'#db960c',unknown:'#667580',download_error:'#667580'};
    if(outcomes[key]) return outcomes[key];
    const fixed={'200':'#21966c','201':'#16b2a3','204':'#59c4b4','301':'#3387d5','302':'#6b75d9','304':'#74a9d8','403':'#c64e8c','404':'#e26955','429':'#db960c','500':'#b23b47','502':'#a35891','503':'#d18332'};
    return fixed[key] || `hsl(${(Number(key)*37)%360} 48% 48%)`;
  };
  const render=original => {
    latest=original;
    let data=original;
    const workflow=data.workflow;
    let current=null;
    if(workflow) {
      data={...data,active:workflow.active};
      current=workflow.phases.find(p=>p.name===workflow.current_phase);
      const heading=card.querySelector('[data-live-phase]');
      heading.textContent=current ? `${current.label}${current.detail ? ' · '+current.detail : ''}${current.status==='queued'?' · in wachtrij':''}` : workflow.status_label || 'Onderzoek afgerond';
      const container=card.querySelector('[data-live-phases]');container.replaceChildren();
      workflow.phases.forEach(phase=>{
        const column=document.createElement('div');column.className='col-6 col-lg-4';
        const panel=document.createElement('div');panel.className='border rounded p-2 h-100';
        const title=document.createElement('strong');title.className='d-block small';title.textContent=phase.label;
        const counts=document.createElement('div');counts.textContent=`${nf.format(phase.done||0)}${phase.total===null || phase.total===undefined?' / ?':' / '+nf.format(phase.total)} · ${phaseStates[phase.status]||phase.status}`;
        panel.append(title,counts);
        if(phase.total>0){const meter=document.createElement('progress');meter.className='w-100';meter.max=phase.total;meter.value=phase.done||0;meter.setAttribute('aria-label',phase.label);panel.append(meter);}
        if(phase.outcomes){const outcomes=document.createElement('div');outcomes.className='small text-muted';outcomes.textContent=Object.entries(phase.outcomes).filter(([,n])=>n).map(([k,n])=>`${label(k)} ${nf.format(n)}`).join(' · ');panel.append(outcomes);}
        column.append(panel);container.append(column);
      });
      const activity=card.querySelector('[data-live-activity]');activity.replaceChildren();
      workflow.requests.slice(0,5).forEach(request=>{
        const row=document.createElement('div');row.className='text-break';
        row.textContent=request.state==='retrying'?`Opnieuw proberen: ${request.host} · over ${duration(request.retry_in_seconds||0)}`:`${request.state==='rendering'?'Pagina renderen':'Controleren'}: ${request.host}${request.waiting_seconds===null?'':' · wachten '+duration(request.waiting_seconds||0)}`;
        activity.append(row);
      });
      if(!workflow.requests.length)activity.textContent=current ? (current.name.includes('analysis')?'Controles en paginarelatieonderzoek worden uitgevoerd.':'Wachten op de volgende werkeenheid…') : 'Alle beschikbare onderzoeksfasen zijn afgerond.';
      if(workflow.stale_seconds>180){const warning=document.createElement('div');warning.className='text-warning-emphasis';warning.textContent=`Geen nieuwe activiteit gemeld gedurende ${duration(workflow.stale_seconds)}. De worker kan wachten of onderbroken zijn.`;activity.append(warning);}
      card.querySelectorAll('[data-live-filters] select').forEach(filter=>filter.disabled=!workflow.telemetry_available);
      if(!workflow.telemetry_available)card.querySelector('[data-live-chart-caption]').textContent='Deze meting heeft nog geen uitgebreide netwerkregistratie. Beschikbare paginagegevens en fasevoortgang blijven zichtbaar.';
      if(workflow.telemetry_available){
        const phase=card.querySelector('[data-live-phase-filter]').value,outcome=card.querySelector('[data-live-outcome-filter]').value,grouping=card.querySelector('[data-live-group-filter]').value;
        const accepts=row=>(phase==='all'||row.phase===phase)&&(outcome==='all'||row.outcome===outcome);
        const totals={};workflow.totals.filter(accepts).forEach(row=>{const key=grouping==='outcome'?row.outcome:row.status;totals[key]=(totals[key]||0)+row.n;});
        data={...data,totals,series:workflow.series.filter(accepts).map(row=>({time:row.bucket,status:grouping==='outcome'?row.outcome:row.status,count:row.n})),interval_seconds:1,end:workflow.end,updated_at:workflow.updated_at};
        card.querySelector('[data-live-chart-caption]').textContent='Activiteit per seconde · laatste 5 minuten · antwoorden, blokkades en herpogingen in de geselecteerde fasen. Legenda: totalen over het hele onderzoek.';
        svg.querySelector('title').textContent='Onderzoeksactiviteit per seconde';
      }
    }
    const mobile=chart.clientWidth<600;
    W=mobile?600:960;H=mobile?300:270;L=mobile?70:55;plotW=W-L-R;plotH=H-T-B;
    svg.setAttribute("viewBox",`0 0 ${W} ${H}`);
    for(const name of ['records','documents','pending','errors']) card.querySelector(`[data-live-${name}]`).textContent=nf.format(data[name] || 0);
    const eta=card.querySelector('[data-live-eta]');
    if(!data.active) eta.textContent=workflow?(workflow.status_label || 'Onderzoek afgerond'):data.status_label;
    else if(workflow) eta.textContent=current && Number.isFinite(current.eta_seconds) && current.pending>0 ? `≈ ${duration(current.eta_seconds)} voor deze fase` : 'Nog onbekend voor deze fase';
    else if(data.scan_kind==='sitemap') eta.textContent='Niet beschikbaar';
    else if(data.eta_seconds===0) eta.textContent='Wordt afgerond…';
    else if(Number.isFinite(data.eta_seconds)) eta.textContent=`≈ ${duration(data.eta_seconds)}`;
    else eta.textContent='Nog onbekend';
    const timing=[];
    if(workflow && current && Number.isFinite(current.elapsed_seconds)) timing.push(`Huidige fase ${duration(current.elapsed_seconds)}`);
    if(workflow && current && Number.isFinite(current.units_per_second)) timing.push(`${rateFormatter.format(current.units_per_second)} verwerkt/s in deze fase`);
    if(Number.isFinite(data.elapsed_seconds)) timing.push(`Verstreken ${duration(data.elapsed_seconds)}`);
    if(!workflow && Number.isFinite(data.urls_per_second)) timing.push(`${rateFormatter.format(data.urls_per_second)} URL/s`);
    card.querySelector('[data-live-timing]').textContent=timing.join(' · ');
    keys=[...new Set([...Object.keys(data.totals || {}),...(data.series || []).map(point=>point.status)])].sort((a,b)=>{const order=['success','http_error','blocked','retrying','unknown','download_error'];return outcomeLabels[a]&&outcomeLabels[b]?order.indexOf(a)-order.indexOf(b):Number(a)-Number(b);});
    const step=data.interval_seconds || 3,end=data.end,start=end-(data.window_seconds || 300);
    samples=[];
    for(let t=start;t<=end;t+=step) samples.push({time:t,values:{}});
    for(const point of data.series || []) {
      const index=Math.round((point.time-start)/step);
      if(samples[index]) samples[index].values[point.status]=(samples[index].values[point.status] || 0)+point.count;
    }
    const max=Math.max(1,...samples.map(sample=>keys.reduce((sum,key)=>sum+(sample.values[key]||0),0)));
    const ceiling=Math.max(4,Math.ceil(max/4)*4);
    const x=i=>L+i*plotW/(samples.length-1),y=v=>T+plotH-v*plotH/ceiling;
    svg.querySelectorAll('[data-drawing]').forEach(node=>node.remove());
    const group=el('g',{'data-drawing':''}); svg.append(group);
    for(let i=0;i<=4;i++) {
      const value=ceiling*i/4,pos=y(value);
      group.append(el('line',{x1:L,x2:W-R,y1:pos,y2:pos,stroke:'#e0e7e8'}));
      group.append(el('text',{x:L-9,y:pos+4,'text-anchor':'end',class:'scan-ticker-axis'},nf.format(value)));
    }
    let baseline=samples.map(()=>0);
    keys.forEach(key=> {
      const upper=baseline.map((value,i)=>value+(samples[i].values[key]||0));
      const top=upper.map((value,i)=>`${i?'L':'M'}${x(i).toFixed(2)},${y(value).toFixed(2)}`).join(' ');
      const bottom=baseline.map((value,i)=>[x(i),y(value)]).reverse().map(([a,b])=>`L${a.toFixed(2)},${b.toFixed(2)}`).join(' ');
      const path=el('path',{d:`${top} ${bottom} Z`,fill:color(key),'fill-opacity':'.8','data-status':key});
      path.append(el('title',{},`${label(key)} · ${nf.format(data.totals[key]||0)} tijdens de hele scan`));group.append(path);
      baseline=upper;
    });
    const ticks=mobile?2:5;
    for(let i=0;i<=ticks;i++) {
      const index=Math.round(i*(samples.length-1)/ticks);
      group.append(el('text',{x:x(index),y:H-8,'text-anchor':i===0?'start':i===ticks?'end':'middle',class:'scan-ticker-axis'},tf.format(new Date(samples[index].time*1000))));
    }
    const hasRecentActivity=samples.some(sample=>Object.values(sample.values).some(value=>value>0));
    if(!hasRecentActivity) group.append(el('text',{x:L+plotW/2,y:T+plotH/2,'text-anchor':'middle',class:'scan-ticker-axis'},data.active?'Geen afgeronde aanvragen in dit tijdvenster; zie de actieve fase hierboven.':'Geen netwerkactiviteit in dit tijdvenster.'));
    const legend=card.querySelector('[data-live-legend]');legend.replaceChildren();
    keys.forEach(key=> {
      const item=document.createElement('span'),swatch=document.createElement('span');
      swatch.className='scan-ticker-swatch';swatch.style.backgroundColor=color(key);
      item.append(swatch,document.createTextNode(`${label(key)} · ${nf.format(data.totals[key]||0)}`));
      item.title='Totaal tijdens de hele scan';legend.append(item);
    });
    state.textContent=data.active?((data.available || workflow?.telemetry_available)?'Live · iedere seconde':'Wachten op de scan-worker…'):(workflow?.status_label || data.status_label);
    state.classList.remove('text-danger');
    const last=data.updated_at?tf.format(new Date(data.updated_at*1000)):null;
    card.querySelector('[data-live-footnote]').textContent=`${last?`Laatste activiteit ${last}. `:''}${workflow ? (data.active?'Elke fase publiceert eigen voortgang; de tijdschatting betreft de huidige bekende wachtrij.':'Onderzoek afgerond. Gedeeltelijke of onbekende metingen blijven afzonderlijk herkenbaar.') : (data.active?'Resultaten worden na afronding gepubliceerd.':'Legenda toont de totalen van de hele scan.')}`;
    const badge=document.querySelector('[data-batch-status]');
    if(badge) {badge.textContent=data.status_label;badge.className=`status-badge status-${data.status}`;}
    card.querySelector('[data-live-finished]').classList.toggle('d-none',data.active);
    if(!data.active) {
      document.querySelector('[data-scan-processing-notice]')?.classList.add('d-none');
      const published=document.querySelector('[data-scan-published-records]');
      if(published && ['complete','incomplete'].includes(data.status)) published.textContent=nf.format(data.records);
    }
    active=data.active;
  };
  card.querySelectorAll('[data-live-filters] select').forEach(filter=>filter.addEventListener('change',()=>{if(latest)render(latest);}));
  chart.addEventListener('pointermove',event=> {
    if(!latest || !samples.length) return;
    const rect=svg.getBoundingClientRect(),pos=(event.clientX-rect.left)*W/rect.width;
    const index=Math.max(0,Math.min(samples.length-1,Math.round((pos-L)/plotW*(samples.length-1))));
    const sample=samples[index];
    tooltip.replaceChildren();
    const title=document.createElement('strong');title.textContent=tf.format(new Date(sample.time*1000));tooltip.append(title);
    keys.forEach(key=>{const row=document.createElement('div');row.textContent=`${label(key)}: ${nf.format(sample.values[key]||0)}`;tooltip.append(row);});
    tooltip.classList.remove('d-none');
    tooltip.style.left=`${Math.min(rect.width-180,Math.max(0,event.clientX-rect.left+12))}px`;
  });
  chart.addEventListener('pointerleave',()=>tooltip.classList.add('d-none'));
  const poll=async()=> {
    if(busy) return;
    if(document.hidden) {timer=setTimeout(poll,1000);return;}
    busy=true;
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),5000);
    try {
      const response=await fetch(card.dataset.progressUrl,{credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'},signal:controller.signal});
      if(response.status===403 || response.status===404) {active=false;throw new Error('Geen toegang meer tot deze scan.');}
      if(response.redirected) {active=false;throw new Error('Je sessie is verlopen. Log opnieuw in.');}
      if(!response.ok) throw new Error('Live-verbinding onderbroken; opnieuw proberen…');
      render(await response.json());
    } catch(error) {
      state.textContent=error.message==='Geen toegang meer tot deze scan.' || error.message==='Je sessie is verlopen. Log opnieuw in.'?error.message:'Live-verbinding onderbroken; opnieuw proberen…';
      state.classList.add('text-danger');
    } finally {
      clearTimeout(timeout);busy=false;
      if(active) timer=setTimeout(poll,1000);
    }
  };
  document.addEventListener('visibilitychange',()=>{if(!document.hidden && active){clearTimeout(timer);poll();}});
  window.addEventListener('pagehide',()=>clearTimeout(timer),{once:true});
  new ResizeObserver(()=>{if(latest)render(latest);}).observe(chart);
  poll();
});
