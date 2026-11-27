/* site-automation - the report website's motion and team layer (1.86.0; roles/site_email/templates/report_pop.js). The
   atlas, brief and ops pages carry it after site_report.js (in site-report.js), the ticket insights' pages in
   site-insights.js. No inline script, nothing from elsewhere (a strict Content-Security-Policy, offline), no HTML from
   strings; ASCII only. Every piece works on the markup the pages already have:
     count-up      a tile's number (.tile .v and the team view's): from 0 to its value, its format and unit kept
     reveal        cards, tiles and sections fade and slide in as they scroll into view
     draw-in       chart lines draw, bars grow (svg[role=img], svg.spark, the team view's charts)
     crosshair     a line chart (one line path, value labels on its grid): a line and a dot at the nearest point; its
                   value and week in a tip when the chart has no hover of its own
     rows          a row, tbody or opened part that becomes visible (a filter, paging, an opened row) fades in
     slim header   the title, the status and the first tiles in a slim bar under the page's bar once the top has
                   scrolled away
     team picker   the health reports' team view (report_teams): a team's view, its rows; the other teams' rows folded
                   behind one button - and the ticket insights' teams, which are pages (links)
   Motion: report_motion - <html class="motion-off"> still, "motion-always" moving even when the system asks for less;
   else the system's reduced-motion wish. Still, the slim header and the picker work; nothing moves. Print is still. */
(function(){
  if(window.reportPop)return;window.reportPop=1;
  var root=document.documentElement,cl=root.classList;
  var reduce=!!(window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  var still=cl.contains('motion-off')||(reduce&&!cl.contains('motion-always'));
  cl.add('pop');cl.add(still?'pop-still':'pop-move');
  function all(s,el){return [].slice.call((el||document).querySelectorAll(s))}
  function keep(k,v){try{if(v===undefined)return localStorage.getItem(k);if(v)localStorage.setItem(k,v);else localStorage.removeItem(k)}catch(e){}return null}
  function el(tag,cls,txt){var e=document.createElement(tag);if(cls)e.className=cls;if(txt!==undefined&&txt!==null)e.textContent=String(txt);return e}
  var raf=window.requestAnimationFrame||function(f){return setTimeout(function(){f(Date.now())},16)};

  /* count-up: the number of a tile; a value that is not one number with words around it (a date, two numbers) stays.
     1.86.0 review: the number it ends on is read once (v._popEnd) and kept; a count still running is stopped (its number
     put back) before another starts - a team picked twice within the count never ends on a number in between, nor on 0 */
  /* no count running: the number on the page is the one (the page may have written it); a count running: the one it ends on */
  function popEnd(v){if(v._popStop)return v._popEnd;var tn=[].filter.call(v.childNodes,function(n){return n.nodeType===3&&/\d/.test(n.nodeValue)})[0];
    if(!tn)return null;v._popTn=tn;v._popEnd=tn.nodeValue;return v._popEnd}
  function countUp(v){
    if(v.getAttribute('data-pop-n'))return;v.setAttribute('data-pop-n','1');
    if(v._popStop)v._popStop();
    var end=popEnd(v),tn=v._popTn;if(end===null||!tn)return;
    var m=end.match(/^(\D*)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\D*)$/);if(!m)return;
    var to=parseFloat((m[2]+(m[3]||'')).replace(/,/g,'')),dec=m[3]?m[3].length-1:0,comma=m[2].indexOf(',')>=0;
    /* still, a hidden value (another period's, another team's) or a page nobody looks at: the number as it is */
    if(still||!to||to>1e9||v.hidden||v.closest('[hidden]')||document.visibilityState==='hidden'){tn.nodeValue=end;v.classList.add('pop-done');return}
    function fmt(x){var s=x.toFixed(dec);if(comma)s=s.replace(/\B(?=(\d{3})+(?!\d))/g,',');return m[1]+s+m[4]}
    var t0=null,dur=900,last=fmt(0),over=false,timer=0;
    function stop(){over=true;clearTimeout(timer);v._popStop=null}
    function done(){if(over)return;stop();if(tn.nodeValue===last)tn.nodeValue=end;v.classList.add('pop-done');if(window.reportPopSlim)window.reportPopSlim()}
    v._popStop=function(){if(over)return;stop();if(tn.nodeValue===last)tn.nodeValue=end};
    /* the page's own script may write the value meanwhile (a period button): then it is its number from now on */
    function step(t){if(over)return;if(tn.nodeValue!==last){stop();v._popEnd=tn.nodeValue;v.classList.add('pop-done');return}
      if(t0===null)t0=t;var p=Math.min(1,(t-t0)/dur),k=1-Math.pow(1-p,3);
      if(p<1){last=fmt(to*k);tn.nodeValue=last;raf(step)}else done()}
    tn.nodeValue=last;raf(step);timer=setTimeout(done,dur+400)}

  /* draw-in: a chart's lines draw, its bars grow from their base */
  function drawIn(svg){
    if(svg.getAttribute('data-pop-d'))return;svg.setAttribute('data-pop-d','1');if(still)return;
    all('path,polyline',svg).forEach(function(p){
      if((p.getAttribute('fill')||'')!=='none'&&getComputedStyle(p).fill!=='none')return;
      var L=p.getTotalLength?p.getTotalLength():0;if(!L||L>20000)return;
      p.style.strokeDasharray=L;p.style.strokeDashoffset=L;p.getBoundingClientRect();
      p.style.transition='stroke-dashoffset 1.1s cubic-bezier(.3,.7,.2,1)';p.style.strokeDashoffset='0';
      p.addEventListener('transitionend',function(){p.style.strokeDasharray='';p.style.strokeDashoffset='';p.style.transition=''},{once:true})});
    all('rect',svg).forEach(function(r,i){
      if(/pop-x|hit|bg/.test(r.getAttribute('class')||'')||r.getAttribute('fill')==='transparent'||r.getAttribute('fill')==='none'||+(r.getAttribute('height')||0)<1)return;
      r.style.transformBox='fill-box';r.style.transformOrigin='bottom';r.style.transform='scaleY(0)';r.getBoundingClientRect();
      r.style.transition='transform .7s cubic-bezier(.3,.7,.2,1) '+Math.min(i*25,400)+'ms';r.style.transform='scaleY(1)'})}

  /* reveal on scroll: once each */
  var REVEAL='.card.sec,section.card,.card.tile,.tiles>.tile,.pt-card,.cz,.who';
  var io=('IntersectionObserver' in window&&!still)?new IntersectionObserver(function(es){es.forEach(function(e){
    if(!e.isIntersecting)return;var t=e.target;io.unobserve(t);t.classList.add('pop-in');light(t)})},{rootMargin:'0px 0px -6% 0px'}):null;
  function light(t){all('.v',t).concat(t.matches&&t.matches('.v')?[t]:[]).forEach(function(v){if(v.closest('.tile,.pt-tile'))countUp(v)});
    all('svg[role=img],svg.spark,svg.pt-runs',t).forEach(drawIn)}
  function watch(){var i=0;all(REVEAL).forEach(function(t){
    if(t.getAttribute('data-pop-w'))return;t.setAttribute('data-pop-w','1');
    var r=t.getBoundingClientRect(),seen=r.top<window.innerHeight&&r.bottom>0;
    if(!io||t.closest('[hidden]')){light(t);return}
    t.style.setProperty('--pop-i',String(seen?(i++)%8:0));t.classList.add('pop-wait');io.observe(t)})}

  /* rows and parts that become visible fade in - never the hover tip (site_report.js #site-tip, role tooltip): its place is
     its transform, which the fade's own transform took over, so for its first .38 s each time it showed it sat at the
     window's top left corner, not by the cell (1.122.1: the AD matrix, cell after cell) */
  function rowsIn(){if(still||!('MutationObserver' in window)||!document.body)return;
    new MutationObserver(function(ms){ms.forEach(function(m){var t=m.target;
      if(m.attributeName!=='hidden'||t.hidden||!/^(TR|TBODY|DIV|LI|SECTION)$/.test(t.tagName))return;
      if(t.id==='site-tip'||t.getAttribute('role')==='tooltip')return;
      t.classList.remove('pop-show');void t.offsetWidth;t.classList.add('pop-show')})}).observe(document.body,{subtree:true,attributes:true,attributeFilter:['hidden']})}

  /* the crosshair: a line chart drawn as one path of 4 or more points, with its grid's values written at the left */
  var MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  function crosshair(svg){
    if(svg.getAttribute('data-pop-x'))return;svg.setAttribute('data-pop-x','1');
    var line=all('path',svg).filter(function(p){return /^M-?[\d.]+[ ,]-?[\d.]+(\s*L-?[\d.]+[ ,]-?[\d.]+){3,}$/.test((p.getAttribute('d')||'').trim())});
    if(line.length!==1||!svg.viewBox||!svg.viewBox.baseVal||!svg.viewBox.baseVal.width)return;
    var nums=line[0].getAttribute('d').replace(/[ML,]/g,' ').trim().split(/\s+/).map(Number),xy=[];
    for(var i=0;i+1<nums.length;i+=2)xy.push([nums[i],nums[i+1]]);
    var grid=all('text[text-anchor=end]',svg).map(function(t){return [+t.getAttribute('y')-4,parseFloat(t.textContent.replace(/,/g,''))]}).filter(function(g){return !isNaN(g[1])&&!isNaN(g[0])});
    if(grid.length<2||grid[0][0]===grid[grid.length-1][0])return;
    var g0=grid[0],g1=grid[grid.length-1];
    /* the dates under it ("Jun 1"): a week's date by its place between them, the year carried over December */
    var yr=2001,lastM=-1,ticks=all('text',svg).filter(function(t){return /^[A-Z][a-z]{2} \d{1,2}$/.test(t.textContent.trim())&&MON.indexOf(t.textContent.trim().slice(0,3))>=0}).map(function(t){
      var p=t.textContent.trim().split(' '),mo=MON.indexOf(p[0]);if(mo<lastM)yr++;lastM=mo;return [+t.getAttribute('x'),Date.UTC(yr,mo,+p[1])]});
    var vb=svg.viewBox.baseVal,NS='http://www.w3.org/2000/svg',vl=document.createElementNS(NS,'line'),dot=document.createElementNS(NS,'circle');
    vl.setAttribute('class','pop-x pop-xl');dot.setAttribute('class','pop-x pop-xd');dot.setAttribute('r','4.5');
    vl.setAttribute('y1',String(vb.y+4));vl.setAttribute('y2',String(Math.max(g0[0],g1[0])));
    svg.appendChild(vl);svg.appendChild(dot);
    var own=!!svg.querySelector('rect.hit,[data-t],[data-tip],title'),box=svg.parentNode,tip=null;
    if(!own){tip=el('div','pop-tip');tip.hidden=true;tip.setAttribute('aria-hidden','true');if(getComputedStyle(box).position==='static')box.classList.add('pop-rel');box.appendChild(tip)}
    function val(y){return g0[1]+(y-g0[0])*(g1[1]-g0[1])/(g1[0]-g0[0])}
    function when(x){if(ticks.length<2)return '';var a=ticks[0],b=ticks[ticks.length-1];if(b[0]===a[0])return '';
      var t=a[1]+(x-a[0])*(b[1]-a[1])/(b[0]-a[0]);t=a[1]+Math.round((t-a[1])/6048e5)*6048e5;var d=new Date(t);
      return 'Week of '+MON[d.getUTCMonth()]+' '+d.getUTCDate()}
    function move(ev){var r=svg.getBoundingClientRect();if(!r.width)return;
      var x=vb.x+(ev.clientX-r.left)*vb.width/r.width,k=0;
      xy.forEach(function(p,j){if(Math.abs(p[0]-x)<Math.abs(xy[k][0]-x))k=j});
      var p=xy[k];vl.setAttribute('x1',p[0]);vl.setAttribute('x2',p[0]);dot.setAttribute('cx',p[0]);dot.setAttribute('cy',p[1]);
      svg.classList.add('pop-xon');
      if(tip){var v=Math.round(val(p[1])*10)/10;tip.textContent=(when(p[0])||('Point '+(k+1)))+': '+v;tip.hidden=false;
        var br=box.getBoundingClientRect();tip.style.left=(r.left-br.left+(p[0]-vb.x)/vb.width*r.width)+'px';tip.style.top=(r.top-br.top+(p[1]-vb.y)/vb.height*r.height)+'px'}}
    svg.addEventListener('pointermove',move);
    svg.addEventListener('pointerleave',function(){svg.classList.remove('pop-xon');if(tip)tip.hidden=true})}

  /* the slim header: under the page's bar once the top has scrolled away (a wide screen; the bar is not sticky on a
     narrow one) */
  function slim(){
    var bar=document.querySelector('.appbar,header.bar');if(!bar||bar.querySelector('.pop-mini'))return;
    var h1=document.querySelector('.hero h1,main h1,h1');if(!h1)return;
    var mini=el('div','pop-mini');mini.setAttribute('aria-hidden','true');
    var t=el('b','pm-t',(h1.textContent||'').split(':')[0].trim()),c=el('span','pm-c'),k=el('span','pm-k');
    var chip=document.querySelector('.hero .state .chip');if(chip)c.appendChild(chip.cloneNode(true));
    mini.appendChild(t);mini.appendChild(c);mini.appendChild(k);
    function fill(){while(k.firstChild)k.removeChild(k.firstChild);
      var on=document.querySelector('.pop-team .pt-b[aria-pressed=true]'),team=on&&on.getAttribute('data-team');
      if(team){k.appendChild(el('span','pm-team',team))}
      var ts=all('.pop-team-view:not([hidden]) .pt-tile');if(!ts.length)ts=all('.tiles .tile,#glance .tile,.kpis .kpi');
      ts.slice(0,3).forEach(function(x){var l=x.querySelector('.pt-l,.k,.lbl,.eyebrow'),v=x.querySelector('.v');if(!l||!v)return;
        var lc=l.cloneNode(true);all('[aria-hidden],.pt-ic,.vh,svg,button',lc).forEach(function(z){z.parentNode.removeChild(z)});
        var s=el('span');s.appendChild(el('i','',(lc.textContent||'').replace(/\s+/g,' ').trim()));s.appendChild(document.createTextNode(' '));
        var e=popEnd(v);s.appendChild(el('b','',(e!==null?e:(v.firstChild&&v.firstChild.nodeType===3?v.firstChild.nodeValue:v.textContent)||'').trim()));k.appendChild(s)})}
    bar.appendChild(mini);fill();window.reportPopSlim=fill;
    var top=document.querySelector('.hero,#glance')||h1;
    if('IntersectionObserver' in window)new IntersectionObserver(function(es){var on=!es[0].isIntersecting;
      mini.classList.toggle('on',on);cl.toggle('pop-mini-on',on);if(on)fill()}).observe(top)}

  /* the team picker of a health report: the views, the rows, the fold */
  function teams(){
    var box=document.querySelector('.pop-teams');if(!box)return;
    var pick=box.querySelector('.pop-team'),btns=all('.pt-b',box),sel=box.querySelector('select.pt-sel'),SR=window.siteReport||{};
    if(pick)pick.hidden=false;if(sel)sel.hidden=false;
    var rows=all('tr[data-team]'),tables=[];rows.forEach(function(r){var t=r.closest('table');if(t&&tables.indexOf(t)<0)tables.push(t)});
    /* each table: one row at its end, "Show the other teams' findings (N)" - one button, the rows of the others back */
    tables.forEach(function(tb){var b=tb.tBodies[0],n=tb.tHead?tb.tHead.rows[0].cells.length:1,tr=el('tr','pt-fold'),td=el('td'),bt=el('button','pt-fb');
      td.colSpan=n;bt.type='button';bt.setAttribute('aria-expanded','false');td.appendChild(bt);tr.appendChild(td);tr.hidden=true;b.appendChild(tr);tb._fold=tr;tb._fb=bt;
      bt.addEventListener('click',function(){tb._show=!tb._show;apply(cur,false)});
      /* a sort puts the rows back in a new order: the fold row stays last */
      if(tb.tHead)tb.tHead.addEventListener('click',function(){setTimeout(function(){b.appendChild(tr)},0)})});
    var cur='';
    /* a row's teams: a JSON list (1.86.0 review: a team's name may hold any character) */
    function has(r,t){if(!r._tl){try{r._tl=JSON.parse(r.getAttribute('data-team')||'[]')}catch(e){r._tl=[]}}return r._tl.indexOf(t)>=0}
    function apply(name,save){cur=name;
      btns.forEach(function(b){b.setAttribute('aria-pressed',String((b.getAttribute('data-team')||'')===name))});
      if(sel&&sel.value!==name)sel.value=name;
      all('.pop-team-view',box).forEach(function(v){v.hidden=(v.getAttribute('data-team')||'')!==name});
      tables.forEach(function(tb){var k=0;all('tr[data-team]',tb).forEach(function(r){var other=!!name&&!has(r,name);if(other)k++;
          r.classList.toggle('pt-other',other&&!!tb._show);r._tm=other&&!tb._show;if(SR.vis)SR.vis(r);else r.hidden=r._tm});
        tb._fold.hidden=!name||!k;tb._fb.setAttribute('aria-expanded',String(!!tb._show));
        tb._fb.textContent=(tb._show?'Hide':'Show')+' the other teams\u2019 findings ('+k+')';
        if(!name)tb._show=false});
      if(SR.rowsChanged)SR.rowsChanged();
      if(save)keep('site-report-team',name);
      all('.pop-team-view:not([hidden]) .v',box).forEach(function(v){v.removeAttribute('data-pop-n');if(v.closest('.pt-tile'))countUp(v)});
      all('.pop-team-view:not([hidden]) svg',box).forEach(function(s){s.removeAttribute('data-pop-d');drawIn(s)});
      if(window.reportPopSlim)window.reportPopSlim()}
    btns.forEach(function(b){b.addEventListener('click',function(){apply(b.getAttribute('data-team')||'',true)})});
    if(sel)sel.addEventListener('change',function(){apply(sel.value,true)});
    var saved=keep('site-report-team')||'',known=btns.some(function(b){return saved&&b.getAttribute('data-team')===saved});
    apply(known?saved:'',false)}

  /* the severity chips over a table whose rows say their severity (data-sev) */
  function sevChips(){var SR=window.siteReport||{};
    all('table').forEach(function(tb){if(!tb.querySelector('tbody tr[data-sev]')||!tb.closest('.tw'))return;
      var w=tb.closest('.tw'),seen={};all('tbody tr[data-sev]',tb).forEach(function(r){seen[r.getAttribute('data-sev')]=1});
      var keys=['critical','warning','low'].filter(function(k){return seen[k]});if(keys.length<2)return;
      var g=el('div','pt-sev');g.setAttribute('role','group');g.setAttribute('aria-label','Severity');
      [['','All']].concat(keys.map(function(k){return [k,k.charAt(0).toUpperCase()+k.slice(1)]})).forEach(function(x){
        var b=el('button','pt-sb'+(x[0]?' s-'+x[0]:''),x[1]);b.type='button';b.setAttribute('data-sev',x[0]);b.setAttribute('aria-pressed',String(!x[0]));
        b.addEventListener('click',function(){all('button',g).forEach(function(y){y.setAttribute('aria-pressed',String(y===b))});
          all('tbody tr[data-sev]',tb).forEach(function(r){r._sv=!!x[0]&&r.getAttribute('data-sev')!==x[0];if(SR.vis)SR.vis(r);else r.hidden=r._sv});
          if(SR.rowsChanged)SR.rowsChanged()});g.appendChild(b)});
      w.parentNode.insertBefore(g,w)})}

  /* the ticket insights: teams are pages - the chips are links; the one last chosen is "Your team" on the others */
  function pageTeams(){
    var box=document.querySelector('.pop-team-bar[data-pages]');if(!box)return;
    var cur=box.getAttribute('data-current')||'',sel=box.querySelector('select.pt-sel');
    all('a.pt-b',box).forEach(function(a){a.addEventListener('click',function(){keep('site-insights-team',a.getAttribute('data-team')||'')})});
    if(sel){sel.hidden=false;sel.addEventListener('change',function(){var o=sel.options[sel.selectedIndex];if(!o||!o.getAttribute('data-href'))return;
      keep('site-insights-team',o.value);location.href=o.getAttribute('data-href')})}
    var mine=keep('site-insights-team'),hint=box.querySelector('.pt-mine');
    if(mine&&mine!==cur&&hint){var a=all('a.pt-b',box).filter(function(x){return x.getAttribute('data-team')===mine})[0]||
        (sel?[].filter.call(sel.options,function(o){return o.value===mine&&o.getAttribute('data-href')})[0]:null);
      if(a){var l=hint.querySelector('a');l.href=a.getAttribute('href')||a.getAttribute('data-href');l.textContent=mine;hint.hidden=false}}}

  function start(){
    try{teams()}catch(e){}try{sevChips()}catch(e){}try{pageTeams()}catch(e){}try{slim()}catch(e){}
    try{watch()}catch(e){}try{rowsIn()}catch(e){}
    all('svg[role=img]').forEach(function(s){try{crosshair(s)}catch(e){}});
    if(!io)all('svg[role=img],svg.spark,svg.pt-runs').forEach(function(s){try{drawIn(s)}catch(e){}});
    /* a section opened later (a closed details): its parts come in then */
    document.addEventListener('toggle',function(e){var d=e.target;if(d&&d.open&&d.tagName==='DETAILS')watch()},true)}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
