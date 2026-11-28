/* The atlas, brief and ops report pages load site-report.js from the website's own origin.
   The publisher extracts it from the rendered template and writes one shared ASCII file.
   Older pages load it too. Light / Auto / Dark, density, the section rail and Columns use
   HTML and CSS. This script remembers choices and handles row expansion, search and export. */
(function(){
  if(window.siteReport)return;
  /* sorting (1.20.0): a cell's value as a key. A lone number (a sign, thousands separators, a decimal point, a % after
     it) is that number; any other value is its text in parts, the numbers in it as numbers - an IPv4 address sorts by
     its four octets, 8.0.2 before 8.0.10, vmx-9 before vmx-21, host2 before host10. Numbers before text; an empty
     cell, or a lone dash (how a report writes a missing value), last, either way */
  var NUM=/^[-+]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)\s*%?$/;
  function skey(v){v=(v||'').replace(/\s+/g,' ').trim();if(!v||/^[-\u2013\u2014]$/.test(v))return null;
    if(NUM.test(v))return [parseFloat(v.replace(/[,%\s]/g,''))];
    var k=[],re=/\d+(?:[.,]\d+)*/g,m,at=0,low=v.toLowerCase();
    while((m=re.exec(low))){if(m.index>at)k.push(low.slice(at,m.index));var s=m[0];
      if(/^\d{1,3}(?:,\d{3})+(?:\.\d+)?$/.test(s)||/^\d+\.\d+$/.test(s))k.push(parseFloat(s.replace(/,/g,'')));
      else s.split(/([.,])/).forEach(function(x,j){k.push(j%2?x:parseInt(x,10))});
      at=m.index+s.length}
    if(at<low.length)k.push(low.slice(at));k.raw=low;return k}
  function kcmp(x,y){for(var j=0;j<x.length&&j<y.length;j++){var a=x[j],b=y[j],na=typeof a==='number',nb=typeof b==='number';
      if(na!==nb)return na?-1:1;if(a<b)return -1;if(a>b)return 1}
    return x.length-y.length||((x.raw||'')<(y.raw||'')?-1:(x.raw||'')>(y.raw||'')?1:0)}
  function scmp(x,y,asc){if(x===null||y===null)return (x===null)-(y===null);var r=kcmp(x,y);return asc?r:-r}
  /* what a cell sorts by: its whole value (data-v) where it has one, else its text */
  function cval(c){return c?(c.getAttribute('data-v')||c.textContent):''}
  window.siteReport={key:skey,compare:function(a,b,asc){return scmp(skey(a),skey(b),asc!==false)},cellValue:cval};
  var root=document.documentElement;
  /* the script runs: the headers' sort arrows and pointer show (CSS), not before */
  root.classList.add('js');
  /* the choices of the last page, before this one is drawn (no light page flashing before a dark one) */
  try{var t=localStorage.getItem('site-report-theme');if(t==='light'||t==='dark')root.setAttribute('data-theme',t);
    var d=localStorage.getItem('site-report-density');if(d==='compact')root.setAttribute('data-density',d);
    if(localStorage.getItem('site-report-rail')==='hidden')root.setAttribute('data-rail','hidden')}catch(e){}
  function ready(f){if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',f);else f()}
  ready(function(){
  function all(s,el){return [].slice.call((el||document).querySelectorAll(s))}
  /* 1.112.0: a saved copy of the page (file:) has no folder of files beside it - the head's file (the AAR) says where it is */
  if(location.protocol==='file:')all('.hfile a.hf-a').forEach(function(a){var s=document.createElement('span');s.className='hf-a off';
    [].slice.call(a.childNodes).forEach(function(n){s.appendChild(n)});var l=s.querySelector('.hf-l');if(l)l.textContent=l.textContent.replace(/^Download /,'');
    var m=a.nextElementSibling,w=document.createElement('span');w.className='hf-why';
    w.textContent='A saved copy of the page: the file is on the report website, and attached to the email.';
    a.parentNode.replaceChild(s,a);if(m&&m.classList.contains('hf-m')){m.textContent='';m.appendChild(w)}});
  // A block holds [element descriptors, nodes]. Strings are text, including strings that look like tags.
  function unpack(block){
    var data=JSON.parse(block.textContent),fragment=document.createDocumentFragment();
    function build(n,svg){
      if(typeof n==='string')return document.createTextNode(n);
      if(n[0]===-1){
        var files=document.querySelector('.sfiles'),w=files&&document.createTreeWalker(files,NodeFilter.SHOW_COMMENT),c;
        while(w&&(c=w.nextNode()))if(c.data===n[1]&&c.nextSibling){
          var copy=c.nextSibling.cloneNode(true);
          if(copy.tagName==='A'){copy.className='dl';copy.textContent='Download '+copy.download}
          return copy;
        }
        return document.createTextNode('');
      }
      var shape=data[0][n[0]],tag=shape[0];svg=svg||tag==='svg';
      var el=svg?document.createElementNS('http://www.w3.org/2000/svg',tag):document.createElement(tag);
      shape[1].forEach(function(a){el.setAttribute(a[0]==='viewbox'?'viewBox':a[0],a[1]||'')});
      for(var i=1;i<n.length;i++)el.appendChild(build(n[i],svg));
      return el;
    }
    data[1].forEach(function(n){fragment.appendChild(build(n,false))});
    block.replaceWith(fragment);
  }
  function detailText(block){
    function read(data){return data[1].map(function(n){return value(n,data[0])}).join('')}
    function value(n,shapes){
      if(typeof n==='string')return n;
      if(n[0]===-1)return n[1].split('/').pop();
      if(shapes[n[0]][0]==='script')return read(JSON.parse(n[1]));
      return n.slice(1).map(function(child){return value(child,shapes)}).join('');
    }
    return read(JSON.parse(block.textContent));
  }
  function bindNew(root){
    bindCopy(root);
    all('.tw table',root).forEach(function(tb){bindTable(tb);tb._capApply()});
    if(typeof bindServerCopy==='function')bindServerCopy(root);
    if(typeof bindTip==='function')all('[data-tip]',root).forEach(bindTip);
    if(typeof bindSpark==='function')all('svg.spark[data-points]',root).forEach(bindSpark);
  }
  /* a switch of the bar: its radio buttons work by themselves (CSS); here the choice is kept in this browser and set
     again on the next page */
  function seg(id,attr,key,def){var g=document.getElementById(id);if(!g)return;
    var c=g.querySelector('input[value="'+(root.getAttribute(attr)||def)+'"]');if(c)c.checked=true;
    g.addEventListener('change',function(ev){var n=ev.target.value;
      if(n===def)root.removeAttribute(attr);else root.setAttribute(attr,n);
      try{if(n===def)localStorage.removeItem(key);else localStorage.setItem(key,n)}catch(e){}
      fit(attr==='data-density'?'grow':false)})}
  seg('theme','data-theme','site-report-theme','auto');
  seg('density','data-density','site-report-density','comfortable');
  /* the rail put away (0.66.0): its check box works by itself (CSS); kept here like the switches */
  var rh=document.getElementById('rail-hide');if(rh){rh.checked=root.getAttribute('data-rail')==='hidden';
    rh.addEventListener('change',function(){railUser=1;railAuto=0;if(rh.checked)root.setAttribute('data-rail','hidden');else root.removeAttribute('data-rail');
      try{if(rh.checked)localStorage.setItem('site-report-rail','hidden');else localStorage.removeItem('site-report-rail')}catch(e){}
      fit()})}
  var p=document.getElementById('print');if(p){p.hidden=false;p.addEventListener('click',function(){
    all('details.sec,details.grp').forEach(function(d){d.open=true});window.print()})}
  /* a table wider than its card first takes the CSS's steps (1.21.0: f1 short values wrap, f2 tighter cells and
     headings, f3 numbers wrap at spaces and a smaller type, f4 long chips narrower; a compact table's sq, 1.11.0,
     before them), one by one while it does not fit; one still too wide scrolls sideways inside its card (its headings
     then stay in it) - a date, an address, a short chip or a heading's word is never broken to fit. Before f4, the
     tables get the rail's room: the rail is put away for this page only (not kept); its button brings it back. A
     table still too wide after f4 takes f4 off and breaks no word (fx) before it scrolls in its card. Each table keeps its card's width (data-fw)
     and its steps (data-fs): it is fitted again only when that width changed (a narrower card goes on from the steps
     it has) or when it must be (its Columns, the rows' height) - opening a section costs the others nothing */
  var railAuto=0,railUser=0,lastW=0;
  /* mode: true = from no step again (its Columns); 'grow' = its values may be wider now (the rows' height): on from
     the steps it has. A table of over 400 rows finds its steps on its first 300 rows, then checks once with all of
     them (and goes on if it must): the same steps, far fewer layouts of a big table */
  function fit1(w,mode){
    var t=w.querySelector('table');if(!t||!w.offsetParent)return -1;
    var cw=w.clientWidth,fw=+(w.getAttribute('data-fw')||-1),fs=w.getAttribute('data-fs');
    var redo=mode===true||fs===null||cw>fw||(fs!=='9'&&t.scrollWidth>cw+1),more=!redo&&(mode==='grow'||cw<fw);
    if(redo||more){
      var st=(t.classList.contains('cc')?['sq']:[]).concat(['f1','f2','f3','f4']),i=0,b=t.tBodies[0],big=b&&b.rows.length>400;
      var over=function(){return t.scrollWidth>w.clientWidth+1};
      if(more)i=fs==='9'?st.length:+fs;
      else{w.classList.remove('scroll');t.classList.remove('sq','f1','f2','f3','f4','fx')}
      if(big){t.classList.add('fsample');for(;i<st.length&&over();i++)t.classList.add(st[i]);t.classList.remove('fsample')}
      for(;i<st.length&&over();i++)t.classList.add(st[i]);
      /* still too wide after the last step: f4 off again and no word broken inside (fx) - a table that scrolls in its
         card is never also broken up (1.21.0) */
      var sc=over();if(sc){t.classList.remove('f4');t.classList.add('fx')}else t.classList.remove('fx');
      w.classList.toggle('scroll',sc);
      w.setAttribute('data-fw',cw);w.setAttribute('data-fs',sc?'9':String(i))}
    return w.classList.contains('scroll')?9:t.classList.contains('f4')?4:0}
  function fit(mode){if(mode!==true&&mode!=='grow')mode=false;
    if(railAuto&&(mode===true||window.innerWidth!==lastW)){railAuto=0;root.removeAttribute('data-rail');if(rh)rh.checked=false}
    lastW=window.innerWidth;
    var worst=0;all('.tw').forEach(function(w){worst=Math.max(worst,fit1(w,mode))});
    var rail=document.querySelector('.has-rail>.rail');
    if(worst>0&&rh&&!rh.checked&&!railUser&&rail&&getComputedStyle(rail).position==='sticky'){
      railAuto=1;rh.checked=true;root.setAttribute('data-rail','hidden');all('.tw').forEach(function(w){fit1(w)})}}
  fit();var rt;window.addEventListener('resize',function(){clearTimeout(rt);rt=setTimeout(function(){fit()},120)});
  all('details.sec').forEach(function(d){d.addEventListener('toggle',function(){fit()})});
  /* the section in view, marked in the bar and in the rail */
  var links={};all('.nav a,.rail a').forEach(function(a){var k=a.getAttribute('href').slice(1);(links[k]=links[k]||[]).push(a)});
  if(window.IntersectionObserver&&Object.keys(links).length){var seen={};
    var io=new IntersectionObserver(function(es){es.forEach(function(e){seen[e.target.id]=e.isIntersecting});
      var first=null;all('details.sec').forEach(function(d){if(!first&&!d.hidden&&seen[d.id])first=d.id});
      Object.keys(links).forEach(function(k){links[k].forEach(function(a){a.classList.toggle('on',k===first)})});
      (links[first]||[]).forEach(function(l){var n=l.parentNode;
        if(n.classList.contains('nav')&&n.scrollWidth>n.clientWidth)n.scrollLeft=l.offsetLeft-n.clientWidth/3})},{rootMargin:'-70px 0px -55% 0px'});
    all('details.sec').forEach(function(d){io.observe(d)})}
  /* Copy: the text, and with it the same as HTML where the browser allows it (a table pastes as a table in Word,
     Outlook and Excel, the lines as a list); over plain http or from a share the old way, text only */
  function put(b,plain,html){
    var c=navigator.clipboard,s=b.querySelector('.t');
    /* the button's own word comes back after Copied (1.68.0: Copy check, Copy fix) */
    if(!b.hasAttribute('data-was'))b.setAttribute('data-was',(s?s.textContent:b.textContent).trim()||'Copy');var w=b.getAttribute('data-was');
    function ok(){b.classList.add('done');if(s)s.textContent='Copied';else if(!b.querySelector('.i1'))b.textContent='Copied';
      setTimeout(function(){b.classList.remove('done');if(s)s.textContent=w;else if(!b.querySelector('.i1'))b.textContent=w},1500)}
    function old(){var a=document.createElement('textarea');a.value=plain;a.setAttribute('readonly','');a.style.position='fixed';a.style.opacity='0';
      document.body.appendChild(a);a.select();try{if(document.execCommand('copy'))ok()}catch(e){}a.remove()}
    function txt(){if(c&&c.writeText)c.writeText(plain).then(ok,old);else old()}
    if(html&&c&&c.write&&window.ClipboardItem){try{c.write([new ClipboardItem({'text/plain':new Blob([plain],{type:'text/plain'}),
      'text/html':new Blob([html],{type:'text/html'})})]).then(ok,txt)}catch(e){txt()}}
    else txt()}
  function esc(t){return String(t===undefined||t===null?'':t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')}
  function one(el){return (el?el.textContent:'').replace(/\s+/g,' ').trim()}
  /* 1.140.0: Copy controls (text / picture / both) on server rows, Copy all shown, and standard sections.
     Reuses window.SAWallFleet.copy / window.SAPortalCopy when loaded in the portal shell; keeps the same pure-ASCII
     fallback for standalone and saved report pages. */
  function safeUrl(u){return /^https?:\/\//i.test(String(u||''))?String(u):''}
  function dataUrlToBlob(dataUrl){
    if(!dataUrl||dataUrl.indexOf('data:image/png;base64,')!==0)return null;
    try{
      var raw=atob(dataUrl.slice(22)),n=raw.length,arr=new Uint8Array(n);
      for(var i=0;i<n;i++)arr[i]=raw.charCodeAt(i);
      return new Blob([arr],{type:'image/png'})}
    catch(e){return null}}
  var extCopy=(window.SAWallFleet&&window.SAWallFleet.copy)||(window.SAPortalCopy)||null;
  var copyHelp=extCopy||null;
  if(!copyHelp){
    copyHelp={
      canRichHtml:function(){
        return !!(navigator.clipboard&&navigator.clipboard.write&&window.ClipboardItem&&window.isSecureContext!==false
          &&(!window.ClipboardItem.supports||window.ClipboardItem.supports('text/html')))},
      canPicture:function(){
        return !!(navigator.clipboard&&navigator.clipboard.write&&window.ClipboardItem&&window.isSecureContext!==false
          &&(!window.ClipboardItem.supports||window.ClipboardItem.supports('image/png')))},
      isBlocked:function(e){return !!(e&&(e.name==='NotAllowedError'||e.name==='SecurityError'))},
      isUnsupported:function(e){return !!(e&&(e.name==='NotSupportedError'||e.name==='DataError'||e.name==='TypeError'))},
      pngDataUrl:function(cv){
        if(!cv||typeof cv.toDataURL!=='function')return '';
        try{var u=cv.toDataURL('image/png');return typeof u==='string'&&u.indexOf('data:image/png;base64,')===0?u:''}catch(e){return ''}},
      dataUrlToBlob:dataUrlToBlob,
      embedPictureHtml:function(html,dataUrl,alt,width){
        var b=html||'',w=width||640;if(!dataUrl||dataUrl.indexOf('data:image/png;base64,')!==0)return b;
        return b+'<p><img width="'+w+'" style="max-width:100%;height:auto" alt="'+esc(alt||'Report snapshot')+'" src="'+dataUrl+'"></p>'},
      _execCopy:function(text,focusEl){
        return new Promise(function(res,rej){
          var a=document.createElement('textarea');a.value=text;a.setAttribute('readonly','');a.style.position='fixed';a.style.opacity='0';
          document.body.appendChild(a);a.select();
          try{if(typeof document.execCommand==='function'&&document.execCommand('copy'))res();else rej(new Error('The browser blocked text copying.'))}
          catch(e){rej(e)}
          finally{if(a.parentNode)a.parentNode.removeChild(a);if(focusEl&&typeof focusEl.focus==='function')focusEl.focus()}})},
      copyTextFallback:function(plain,focusEl){
        var text=String(plain===undefined||plain===null?'':plain);
        if(navigator.clipboard&&navigator.clipboard.writeText&&window.isSecureContext!==false){
          return navigator.clipboard.writeText(text).then(function(){},function(e){
            if(copyHelp.isBlocked(e))throw e;return copyHelp._execCopy(text,focusEl)})}
        return copyHelp._execCopy(text,focusEl)},
      copyBothRich:function(opts){
        var notify=opts.onState||function(){},done=opts.onDone||function(){};
        var plain=String(opts.plain===undefined||opts.plain===null?'':opts.plain);
        var dataUrl=opts.dataUrl||'',html=copyHelp.embedPictureHtml(opts.html||'',dataUrl,opts.alt,opts.width);
        var failMsg=function(e){if(copyHelp.isBlocked(e))return 'Clipboard access denied. Allow clipboard access and retry.';
          return e&&e.message?String(e.message):'Clipboard write failed. Try copying again.'};
        var fallbackText=function(why){
          return copyHelp.copyTextFallback(plain,opts.focusEl).then(function(){
            notify('warn','Text only','Text copied. '+why)},function(e){notify('bad','Copy failed',failMsg(e))}).then(done,done)};
        notify('info','Copying\u2026','Writing text and picture\u2026');
        var canRich=copyHelp.canRichHtml();
        if(!canRich||!dataUrl){
          var why=window.isSecureContext===false?'Rich HTML and picture copying needs HTTPS.'
            :!canRich?'This browser does not support picture copying.':'Picture could not be drawn here.';
          return fallbackText(why)}
        var items={'text/plain':new Blob([plain],{type:'text/plain'}),'text/html':new Blob([html],{type:'text/html'})};
        try{
          return navigator.clipboard.write([new ClipboardItem(items)]).then(function(){
            notify('good','Copied','Copied text and picture. Ready to paste.');done()},function(e){
            if(copyHelp.isUnsupported(e))return fallbackText('This browser does not support picture copying.');
            notify('bad','Copy failed',failMsg(e));done()})}
        catch(e){
          if(copyHelp.isUnsupported(e))return fallbackText('This browser does not support picture copying.');
          notify('bad','Copy failed',failMsg(e));done();return Promise.resolve()}}};
  }
  function runCopyMode(mode,opts){
    if(mode==='both')return copyHelp.copyBothRich(opts);
    if(copyHelp.copyMode)return copyHelp.copyMode(mode,opts);
    var notify=opts.onState||function(){},done=opts.onDone||function(){};
    var plain=String(opts.plain===undefined||opts.plain===null?'':opts.plain),html=String(opts.html||'');
    var failMsg=function(e){if(copyHelp.isBlocked(e))return 'Clipboard access denied. Allow clipboard access and retry.';
      return e&&e.message?String(e.message):'Clipboard write failed. Try copying again.'};
    if(mode==='text'){
      notify('info','Copying\u2026','Writing text\u2026');
      var plainFallback=function(){
        return copyHelp.copyTextFallback(plain,opts.focusEl).then(function(){
          notify('good','Copied','Copied text. Ready to paste.')},function(e){notify('bad','Copy failed',failMsg(e))}).then(done,done)};
      if(html&&copyHelp.canRichHtml()){
        try{
          var titems={'text/plain':new Blob([plain],{type:'text/plain'}),'text/html':new Blob([html],{type:'text/html'})};
          return navigator.clipboard.write([new ClipboardItem(titems)]).then(function(){
            notify('good','Copied','Copied text. Ready to paste.');done()},function(e){
            if(copyHelp.isUnsupported(e))return plainFallback();
            notify('bad','Copy failed',failMsg(e));done()})}
        catch(e){if(copyHelp.isUnsupported(e))return plainFallback();notify('bad','Copy failed',failMsg(e));done();return Promise.resolve()}}
      return plainFallback()}
    /* mode === 'picture' */
    notify('info','Copying\u2026','Writing picture\u2026');
    var picFallback=function(why){
      return copyHelp.copyTextFallback(plain,opts.focusEl).then(function(){
        notify('warn','Text only','Text copied. '+why)},function(e){notify('bad','Copy failed',failMsg(e))}).then(done,done)};
    var canPic=typeof copyHelp.canPicture==='function'?copyHelp.canPicture():
      !!(navigator.clipboard&&navigator.clipboard.write&&window.ClipboardItem&&window.isSecureContext!==false
        &&(!window.ClipboardItem.supports||window.ClipboardItem.supports('image/png')));
    var blob=(typeof copyHelp.dataUrlToBlob==='function'?copyHelp.dataUrlToBlob:dataUrlToBlob)(opts.dataUrl||'');
    if(!canPic||!blob){
      var why=window.isSecureContext===false?'Picture copying needs HTTPS.'
        :!canPic?'This browser does not support picture copying.':'Picture could not be drawn here.';
      return picFallback(why)}
    try{
      return navigator.clipboard.write([new ClipboardItem({'image/png':blob})]).then(function(){
        notify('good','Copied','Copied picture. Ready to paste.');done()},function(e){
        if(copyHelp.isUnsupported(e))return picFallback('This browser does not support picture copying.');
        notify('bad','Copy failed',failMsg(e));done()})}
    catch(e){
      if(copyHelp.isUnsupported(e))return picFallback('This browser does not support picture copying.');
      notify('bad','Copy failed',failMsg(e));done();return Promise.resolve()}}
  function picFit(ctx,s,maxW){s=String(s||'');if(!s)return '';
    if(ctx.measureText(s).width<=maxW)return s;
    var lo=0,hi=s.length;while(lo<hi){var m=(lo+hi+1)>>1;if(ctx.measureText(s.slice(0,m)+'\u2026').width<=maxW)lo=m;else hi=m-1}
    return lo>0?s.slice(0,lo)+'\u2026':'\u2026'}
  function secHeading(sec){var h=sec&&sec.querySelector('summary h2'),f=h&&h.firstChild;
    return (f&&f.nodeType===3?f.textContent:(h?h.textContent:'')).replace(/\s+/g,' ').trim()||'Report section'}
  /* 1.140.0: per-server and Copy-all-shown formatters and ~700 px Teams card drawers (shared spec with portal lists) */
  var SEV_RANK={critical:0,unknown:1,warning:2,low:3,info:4,ok:5};
  var SEV_LABEL={critical:'Critical',unknown:'Not reached',warning:'Warning',low:'Low',info:'Info',ok:'OK'};
  var SEV_GLYPH={critical:'\u2715',unknown:'?',warning:'\u25b3',low:'-',info:'i',ok:'\u2713'};
  var SEV_COL={critical:'#f397a4',unknown:'#f397a4',warning:'#e8bb65',low:'#c9b3f5',info:'#85d8df',ok:'#72d4a2'};
  var SEV_INK={critical:'#3a0a13',unknown:'#3a0a13',warning:'#2d1a00',low:'#210c36',info:'#062630',ok:'#07281b'};
  var WARN_CAP=10,CRIT_PIC_MAX=40,ONE_PIC_MAX=14;
  function normSev(raw){
    var s=String(raw||'').toLowerCase().trim();
    if(s==='crit'||s==='critical'||s==='down'||s==='failed'||s==='fail'||s==='error'||s==='breached'||s==='expired')return 'critical';
    if(s==='unknown'||s==='not reached'||s==='not checked'||s==='unreachable'||s==='stale'||s==='not read'||s==='missing'||s==='lastdown')return 'unknown';
    if(s==='warn'||s==='warning'||s==='degraded'||s==='at risk'||s==='lastwarn'||s==='expiring')return 'warning';
    if(s==='low')return 'low';
    if(s==='info')return 'info';
    if(s==='ok'||s==='up'||s==='pass'||s==='passed'||s==='healthy'||s==='compliant'||s==='valid')return 'ok';
    return 'unknown'}
  function oneLine(s){return String(s===undefined||s===null?'':s).replace(/\s+/g,' ').trim()}
  function sortFindings(list){
    return (list||[]).map(function(f,i){return [f,i]}).sort(function(a,b){
      var ra=SEV_RANK[normSev(a[0].sev)]!==undefined?SEV_RANK[normSev(a[0].sev)]:3;
      var rb=SEV_RANK[normSev(b[0].sev)]!==undefined?SEV_RANK[normSev(b[0].sev)]:3;
      return ra-rb||a[1]-b[1]}).map(function(p){return p[0]})}
  function reportLinkHtml(url){
    var u=String(url||''),su=safeUrl(u);
    return su?'<a href="'+esc(su)+'">'+esc(u)+'</a>':esc(u)}
  function extractContext(sec){
    var rootEl=document.documentElement;
    var report=(sec&&sec.getAttribute('data-report'))||one(document.querySelector('.hero h1,main h1,h1'))||document.title||'Operations report';
    var site=(sec&&sec.getAttribute('data-site'))||(rootEl&&rootEl.getAttribute('data-site'))||one(document.querySelector('.site,[data-site]'))||'';
    var when=(sec&&sec.getAttribute('data-when'))||(rootEl&&rootEl.getAttribute('data-when'))||'';
    var scope=one(document.querySelector('.hscope,.desc,.hstrip .ln1,.hero .meta,footer'))||'';
    if(!site&&scope){
      var parts=scope.split(/\s*(?:\u00b7|\/|\|)\s*/);
      for(var i=0;i<parts.length;i++){
        var p=parts[i].trim();
        if(p&&!/^\d{4}-\d{2}-\d{2}|^\d{1,2}\s+[A-Z]|^AAP\s+job|^run\s+by/i.test(p)&&p.toLowerCase()!==report.toLowerCase()){
          site=p.split(/\s+/)[0];if(site.length>=2&&site.length<=24)break;site=''}}}
    if(!site&&document.title&&document.title.indexOf('\u00b7')>=0){
      var tp=document.title.split('\u00b7').map(function(x){return x.trim()}).filter(Boolean);
      if(tp.length>=2&&tp[tp.length-1].length<=24)site=tp[tp.length-1]}
    if(!when&&scope){
      var m=scope.match(/\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?:\s+[A-Z]{2,5})?|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}(?:\s*(?:\u00b7|,)?\s*\d{2}:\d{2}(?:\s+[A-Z]{2,5})?)?/);
      if(m)when=m[0].replace(/\s*\u00b7\s*/g,' ')}
    if(!when){var tm=document.querySelector('time[datetime],time');if(tm)when=one(tm)}
    var url=(sec&&sec.getAttribute('data-url'))||(location&&location.href)||'';
    return {site:site,report:report,dateTime:when,url:url}}
  function formatOneServer(server,ctx){
    ctx=ctx||{};
    var sev=normSev(server.sev),stWord=(server.statusLabel||SEV_LABEL[sev]||sev).toUpperCase();
    var titlePart=[ctx.site,ctx.report].filter(Boolean).join(' ');
    var headMeta=[titlePart,ctx.dateTime].filter(Boolean).join(' \u00b7 ');
    var hostBit=server.host+(server.ip?' ('+server.ip+')':'');
    var line1='['+stWord+'] '+hostBit+(headMeta?' \u00b7 '+headMeta:'');
    var findings=sortFindings(server.findings||[]);
    var plainLines=[line1];
    var htmlParts=['<p><strong>['+esc(stWord)+'] '+esc(server.host)+'</strong>'+(server.ip?' ('+esc(server.ip)+')':'')+(headMeta?' &middot; '+esc(headMeta):'')+'</p>'];
    if(findings.length){
      var lis=[];
      findings.forEach(function(f){
        var fs=normSev(f.sev),fl=(f.sevLabel||SEV_LABEL[fs]||fs).toUpperCase();
        var ttl=oneLine(f.title||f.summary||'Finding'),fix=oneLine(f.whatToDo||f.fix||'').replace(/^What to do:\s*/i,'');
        var ev=oneLine(f.evidence||'');
        plainLines.push('\u2022 ['+fl+'] '+ttl+(fix?' \u2014 What to do: '+fix:(ev?' \u2014 '+ev:'')));
        lis.push('<li><strong>['+esc(fl)+']</strong> '+esc(ttl)+(fix?' &mdash; <strong>What to do:</strong> '+esc(fix):(ev?' &mdash; '+esc(ev):''))+'</li>')});
      if(server.cappedCount>0){
        plainLines.push('\u2022 ... '+server.cappedCount+' more findings in full report');
        lis.push('<li>... '+esc(server.cappedCount)+' more findings in full report</li>')}
      htmlParts.push('<ul>'+lis.join('')+'</ul>');
    }else{
      var msg=oneLine(server.summary||server.worst||(sev==='ok'?'No findings recorded (all checks passed)':'No detail recorded'));
      plainLines.push('\u2022 ['+stWord+'] '+msg);
      htmlParts.push('<ul><li><strong>['+esc(stWord)+']</strong> '+esc(msg)+'</li></ul>')}
    if(ctx.url){
      plainLines.push('Full report: '+ctx.url);
      htmlParts.push('<p><strong>Full report:</strong> '+reportLinkHtml(ctx.url)+'</p>')}
    return {plain:plainLines.join('\n'),html:htmlParts.join('')}}
  function serverWorstLine(s){
    var fs=sortFindings(s.findings||[]);
    var worst=oneLine((fs[0]&&(fs[0].title||fs[0].summary))||s.worst||s.summary||s.statusLabel||'Issue');
    var total=Math.max(fs.length,s.totalFindings||0)+(s.cappedCount||0);
    var more=total>1?total-1:0;
    return {worst:worst,more:more,suffix:more>0?' (+'+more+' more '+(more===1?'finding':'findings')+')':''}}
  function formatAllShown(servers,ctx){
    ctx=ctx||{};
    var crit=[],unk=[],warn=[],low=[],info=[],ok=[];
    (servers||[]).forEach(function(s){
      var k=normSev(s.sev);
      if(k==='critical')crit.push(s);
      else if(k==='unknown')unk.push(s);
      else if(k==='warning')warn.push(s);
      else if(k==='low')low.push(s);
      else if(k==='info')info.push(s);
      else ok.push(s)});
    var titlePart=[ctx.site,ctx.report].filter(Boolean).join(' ')||'Operations report';
    var counts=crit.length+' critical, '+warn.length+' warning, '+ok.length+' OK'
      +(unk.length?', '+unk.length+' not reached':'')
      +(low.length?', '+low.length+' low':'')
      +(info.length?', '+info.length+' info':'');
    var line1=titlePart+(ctx.dateTime?' \u00b7 '+ctx.dateTime:'')+' \u00b7 '+counts;
    var plain=[line1];
    var html=['<p><strong>'+esc(titlePart)+'</strong>'+(ctx.dateTime?' &middot; '+esc(ctx.dateTime):'')+' &middot; <strong>'+crit.length+' critical</strong>, <strong>'+warn.length+' warning</strong>, '+ok.length+' OK'
      +(unk.length?', <strong>'+unk.length+' not reached</strong>':'')
      +(low.length?', '+low.length+' low':'')
      +(info.length?', '+info.length+' info':'')+'</p>'];
    function addGroup(heading,list,cap){
      if(!list.length)return;
      plain.push('');
      plain.push(heading+' ('+list.length+')');
      var shown=cap&&list.length>cap?list.slice(0,cap):list;
      var lis=[];
      shown.forEach(function(s){
        var w=serverWorstLine(s);
        plain.push('\u2022 '+s.host+' - '+w.worst+w.suffix);
        lis.push('<li><strong>'+esc(s.host)+'</strong> - '+esc(w.worst)+(w.suffix?' '+esc(w.suffix):'')+'</li>')});
      if(cap&&list.length>cap){
        var extra=list.length-cap;
        plain.push('\u2022 ... '+extra+' more');
        lis.push('<li>... '+extra+' more</li>')}
      html.push('<p><strong>'+esc(heading+' ('+list.length+')')+'</strong></p><ul>'+lis.join('')+'</ul>')}
    addGroup('Critical',crit,0);
    addGroup('Not reached',unk,0);
    addGroup('Warning',warn,WARN_CAP);
    if(low.length){
      var lt=low.length+' server'+(low.length===1?'':'s')+' low';
      plain.push('');plain.push(lt);html.push('<p>'+esc(lt)+'</p>')}
    if(info.length){
      var it=info.length+' server'+(info.length===1?'':'s')+' info';
      plain.push('');plain.push(it);html.push('<p>'+esc(it)+'</p>')}
    if(ok.length){
      var ot=ok.length+' server'+(ok.length===1?'':'s')+' OK';
      plain.push('');plain.push(ot);html.push('<p><strong>'+esc(ot)+'</strong></p>')}
    if(ctx.url){
      plain.push('');plain.push('Full report: '+ctx.url);
      html.push('<p><strong>Full report:</strong> '+reportLinkHtml(ctx.url)+'</p>')}
    return {plain:plain.join('\n'),html:html.join(''),counts:{critical:crit.length,unknown:unk.length,warning:warn.length,low:low.length,info:info.length,ok:ok.length,total:(servers||[]).length}}}
  function drawOneServerCard(server,ctx){
    ctx=ctx||{};
    try{
      var cv=document.createElement('canvas'),g=cv&&cv.getContext?cv.getContext('2d'):null;if(!g)return {dataUrl:'',width:0,height:0};
      var sev=normSev(server.sev),findings=sortFindings(server.findings||[]);
      var shown=findings.slice(0,ONE_PIC_MAX),extra=(findings.length-shown.length)+(server.cappedCount||0);
      var rowsN=Math.max(1,shown.length),W=700,H=134+rowsN*48+(extra>0?24:0)+26;
      cv.width=W*2;cv.height=H*2;g.scale(2,2);
      var grad=g.createLinearGradient(0,0,W,H);grad.addColorStop(0,'#14283e');grad.addColorStop(1,'#1b3950');
      g.fillStyle=grad;g.fillRect(0,0,W,H);
      var sc=SEV_COL[sev]||'#85d8df';
      g.fillStyle=sc;g.fillRect(0,0,7,H);
      var sans='Manrope,"Segoe UI",system-ui,Arial,sans-serif',mono='"IBM Plex Mono",Consolas,monospace';
      g.fillStyle='#b3cce1';g.font='600 10.5px '+mono;
      var topL=[ctx.site,ctx.report].filter(Boolean).join(' / ').toUpperCase()||'SERVER REPORT';
      g.fillText(picFit(g,topL,430),22,24);
      if(ctx.dateTime){g.textAlign='right';g.fillStyle='#aec6d8';g.fillText(picFit(g,ctx.dateTime,220),W-20,24);g.textAlign='left'}
      g.fillStyle='#ffffff';g.font='700 20px '+sans;
      g.fillText(picFit(g,server.host||'Server',470),22,54);
      g.fillStyle='#bad4e6';g.font='400 12px '+mono;
      var sub=(server.ip?server.ip+'  \u00b7  ':'')+(findings.length?findings.length+' finding'+(findings.length===1?'':'s'):(sev==='ok'?'All checks passed':(server.statusLabel||'Status')));
      g.fillText(picFit(g,sub,470),22,74);
      var badge=(SEV_GLYPH[sev]||'?')+'  '+(server.statusLabel||SEV_LABEL[sev]||sev).toUpperCase();
      g.font='700 11.5px '+mono;
      var bw=Math.min(176,g.measureText(badge).width+18);
      g.fillStyle=sc;g.fillRect(W-20-bw,34,bw,26);
      g.fillStyle=SEV_INK[sev]||'#111827';g.fillText(picFit(g,badge,bw-12),W-12-bw,51);
      g.fillStyle='#3c5669';g.fillRect(22,88,W-44,1);
      var y=100;
      if(shown.length){
        shown.forEach(function(f){
          var fs=normSev(f.sev),fc=SEV_COL[fs]||'#85d8df';
          g.fillStyle='rgba(255,255,255,0.06)';g.fillRect(22,y,W-44,42);
          g.fillStyle=fc;g.fillRect(22,y,4,42);
          g.fillStyle='#ffffff';g.font='700 12.5px '+sans;
          g.fillText(picFit(g,'['+(f.sevLabel||SEV_LABEL[fs]||fs).toUpperCase()+'] '+oneLine(f.title||'Finding'),W-70),34,y+17);
          var fix=oneLine(f.whatToDo||f.fix||'').replace(/^What to do:\s*/i,'');
          var line2=fix?'What to do: '+fix:oneLine(f.evidence||'');
          g.fillStyle='#c4d4e0';g.font='400 11px '+mono;
          g.fillText(picFit(g,line2,W-70),34,y+34);
          y+=48});
      }else{
        g.fillStyle='rgba(255,255,255,0.06)';g.fillRect(22,y,W-44,40);
        g.fillStyle=sc;g.fillRect(22,y,4,40);
        g.fillStyle='#e5eff7';g.font='600 13px '+sans;
        g.fillText(picFit(g,oneLine(server.summary||server.worst||'No findings recorded'),W-70),34,y+25);
        y+=48}
      if(extra>0){
        g.fillStyle='#e8bb65';g.font='600 11.5px '+mono;
        g.fillText('+'+extra+' more findings in full report',22,y+14);y+=24}
      g.fillStyle='#3c5669';g.fillRect(22,H-34,W-44,1);
      g.fillStyle='#aec6d8';g.font='400 10.5px '+mono;
      g.fillText(picFit(g,'Full report: '+(ctx.url||topL),W-44),22,H-14);
      return {dataUrl:copyHelp.pngDataUrl(cv),width:W,height:H}
    }catch(e){return {dataUrl:'',width:0,height:0}}}
  function drawAllShownCard(servers,ctx){
    ctx=ctx||{};
    try{
      var cv=document.createElement('canvas'),g=cv&&cv.getContext?cv.getContext('2d'):null;if(!g)return {dataUrl:'',width:0,height:0};
      var crit=[],unk=[],warn=[],low=[],info=[],ok=[];
      (servers||[]).forEach(function(s){
        var k=normSev(s.sev);
        if(k==='critical')crit.push(s);else if(k==='unknown')unk.push(s);else if(k==='warning')warn.push(s);
        else if(k==='low')low.push(s);else if(k==='info')info.push(s);else ok.push(s)});
      var critDraw=crit.slice(0,CRIT_PIC_MAX),critMore=crit.length-critDraw.length;
      var unkDraw=unk.slice(0,CRIT_PIC_MAX),unkMore=unk.length-unkDraw.length;
      var warnDraw=warn.slice(0,WARN_CAP),warnMore=warn.length-warnDraw.length;
      var total=(servers||[]).length;
      var W=700,H=124;
      if(critDraw.length)H+=22+critDraw.length*28+(critMore>0?22:0)+6;
      if(unkDraw.length)H+=22+unkDraw.length*26+(unkMore>0?22:0)+6;
      if(warnDraw.length)H+=22+warnDraw.length*26+(warnMore>0?22:0)+6;
      if(low.length||info.length)H+=22;
      if(ok.length)H+=26;
      if(!critDraw.length&&!unkDraw.length&&!warnDraw.length&&!ok.length)H+=36;
      H+=38;
      cv.width=W*2;cv.height=H*2;g.scale(2,2);
      var grad=g.createLinearGradient(0,0,W,H);grad.addColorStop(0,'#14283e');grad.addColorStop(1,'#1b3950');
      g.fillStyle=grad;g.fillRect(0,0,W,H);
      var worstSev=crit.length?'critical':unk.length?'unknown':warn.length?'warning':ok.length?'ok':'info';
      g.fillStyle=SEV_COL[worstSev]||'#85d8df';g.fillRect(0,0,7,H);
      var sans='Manrope,"Segoe UI",system-ui,Arial,sans-serif',mono='"IBM Plex Mono",Consolas,monospace';
      var topL=[ctx.site,ctx.report].filter(Boolean).join(' / ').toUpperCase()||'REPORT BY SERVER';
      g.fillStyle='#b3cce1';g.font='600 10.5px '+mono;
      g.fillText(picFit(g,topL,430),22,24);
      if(ctx.dateTime){g.textAlign='right';g.fillStyle='#aec6d8';g.fillText(picFit(g,ctx.dateTime,220),W-20,24);g.textAlign='left'}
      g.fillStyle='#ffffff';g.font='700 19px '+sans;
      var heading=(ctx.report||'Report by server')+' \u00b7 '+total+' server'+(total===1?'':'s');
      g.fillText(picFit(g,heading,W-44),22,51);
      /* Counts bar: pills + proportional stacked bar */
      var px=22,py=62;
      var pills=[
        [crit.length+' critical','#f397a4','rgba(243,151,164,0.18)',crit.length>0],
        [unk.length+' not reached','#f397a4','rgba(243,151,164,0.12)',unk.length>0],
        [warn.length+' warning','#e8bb65','rgba(232,187,101,0.18)',warn.length>0],
        [ok.length+' OK','#72d4a2','rgba(114,212,162,0.16)',true]
      ];
      g.font='700 11px '+mono;
      pills.forEach(function(p){
        if(!p[3]&&p[0].indexOf('not reached')>=0)return;
        var tw=g.measureText(p[0]).width+16;
        g.fillStyle=p[2];g.fillRect(px,py,tw,22);
        g.strokeStyle=p[1];g.lineWidth=1;g.strokeRect(px+0.5,py+0.5,tw-1,21);
        g.fillStyle=p[1];g.fillText(p[0],px+8,py+15);
        px+=tw+8});
      var barW=W-44,barY=92,bx=22;
      g.fillStyle='rgba(255,255,255,0.08)';g.fillRect(bx,barY,barW,6);
      if(total>0){
        [[crit.length,'#f397a4'],[unk.length,'#d19aa6'],[warn.length,'#e8bb65'],[low.length+info.length,'#85d8df'],[ok.length,'#72d4a2']].forEach(function(seg){
          if(!seg[0])return;var sw=Math.max(3,Math.round(barW*seg[0]/total));
          if(bx+sw>22+barW)sw=22+barW-bx;
          if(sw>0){g.fillStyle=seg[1];g.fillRect(bx,barY,sw,6);bx+=sw}})}
      g.fillStyle='#3c5669';g.fillRect(22,108,W-44,1);
      var y=120;
      function drawGroup(label,color,rowFill,list,rowH,moreCount,moreWord){
        if(!list.length)return;
        g.fillStyle=color;g.font='700 11px '+mono;
        g.fillText(label,22,y+12);y+=20;
        list.forEach(function(s){
          var w=serverWorstLine(s);
          g.fillStyle=rowFill;g.fillRect(22,y,W-44,rowH-4);
          g.fillStyle=color;g.fillRect(22,y,3,rowH-4);
          g.fillStyle='#ffffff';g.font='700 12px '+mono;
          g.fillText(picFit(g,s.host,215),32,y+16);
          var tagW=0;
          if(w.more>0){
            var tg='+'+w.more+' more';
            g.font='700 10.5px '+mono;tagW=g.measureText(tg).width+12;
            g.fillStyle='rgba(255,255,255,0.09)';g.fillRect(W-26-tagW,y+3,tagW,rowH-10);
            g.fillStyle=color;g.fillText(tg,W-20-tagW,y+15)}
          g.fillStyle='#dce8f2';g.font='400 12px '+sans;
          g.fillText(picFit(g,w.worst,W-286-tagW),254,y+16);
          y+=rowH});
        if(moreCount>0){
          g.fillStyle=color;g.font='700 11.5px '+mono;
          g.fillText('+'+moreCount+' more '+(moreWord||'servers')+' in full report',32,y+14);y+=22}
        y+=6}
      drawGroup('CRITICAL ('+crit.length+')','#f397a4','rgba(243,151,164,0.11)',critDraw,28,critMore,'critical servers');
      drawGroup('NOT REACHED ('+unk.length+')','#f397a4','rgba(243,151,164,0.08)',unkDraw,26,unkMore,'not-reached servers');
      drawGroup('WARNING ('+warn.length+')','#e8bb65','rgba(232,187,101,0.09)',warnDraw,26,warnMore,'warning servers');
      if(low.length||info.length){
        var liParts=[];if(low.length)liParts.push(low.length+' low');if(info.length)liParts.push(info.length+' info');
        g.fillStyle='#b3cce1';g.font='600 11.5px '+mono;
        g.fillText(liParts.join(' \u00b7 '),22,y+13);y+=22}
      if(ok.length){
        g.fillStyle='#72d4a2';g.font='700 12px '+mono;
        g.fillText('\u2713  '+ok.length+' server'+(ok.length===1?'':'s')+' OK',22,y+15);y+=26}
      g.fillStyle='#3c5669';g.fillRect(22,H-34,W-44,1);
      g.fillStyle='#aec6d8';g.font='400 10.5px '+mono;
      g.fillText(picFit(g,'Full report: '+(ctx.url||topL),W-44),22,H-14);
      return {dataUrl:copyHelp.pngDataUrl(cv),width:W,height:H}
    }catch(e){return {dataUrl:'',width:0,height:0}}}
  /* 1.140.0: draw a crisp 2x PNG snapshot of a report section (or reuse an existing section canvas) for Copy both */
  function sectionPicture(sec,plain){
    try{
      var body=sec&&sec.querySelector('.body');if(!body)return '';
      var existing=body.querySelector('canvas');
      if(existing&&existing.width>0&&existing.height>0){var u0=copyHelp.pngDataUrl(existing);if(u0)return u0}
      var cv=document.createElement('canvas'),ctx=cv&&cv.getContext?cv.getContext('2d'):null;if(!ctx)return '';
      var title=secHeading(sec),pageTitle=one(document.querySelector('h1'))||document.title||'Operations report';
      var meta=one(document.querySelector('.hero .meta,.meta,.sub,.when'))||'';
      var badgeEl=sec.querySelector('summary .chip,summary .stc'),badgeTxt=one(badgeEl),sev='info';
      if(badgeEl){var bc=badgeEl.className||'';
        if(/\b(s-critical|c-critical)\b/.test(bc))sev='critical';
        else if(/\b(s-warning|c-warning)\b/.test(bc))sev='warning';
        else if(/\b(s-ok|c-ok)\b/.test(bc))sev='ok'}
      var SEV={ok:['#8ee0bd','\u2713'],warning:['#ebc994','\u25b3'],critical:['#ff9ca8','!'],info:['#85d8df','\u25a3']}[sev];
      var tb=body.querySelector('.tw table'),spark=body.querySelector('svg.spark[data-points]'),
          cards=[].filter.call(body.querySelectorAll('.cards .todo'),function(x){return !x.hidden}),
          lines=[].filter.call(body.querySelectorAll('ul.lines>li,ul.passed>li'),function(x){return !x.hidden});
      var mode='text',tView=null,pts=[],rowItems=[];
      if(spark){try{pts=JSON.parse(spark.getAttribute('data-points')||'[]').map(function(p){return typeof p==='number'?p:+(p&&p.v!==undefined?p.v:p&&p[1])}).filter(function(v){return !isNaN(v)})}catch(e){pts=[]}}
      if(tb){tView=view(tb,one);if(tView[0].length||tView[1].length)mode='table'}
      else if(pts.length>=2)mode='spark';
      else if(cards.length){mode='cards';rowItems=cards.slice(0,6).map(function(a){return [one(a.querySelector('.row1')),one(a.querySelector('h3')),one(a.querySelector('.subj'))||one(a.querySelector('.next'))]})}
      else if(lines.length){mode='lines';rowItems=lines.slice(0,9).map(one).filter(Boolean)}
      else{rowItems=String(plain||'').split(/\r?\n/).map(function(l){return l.replace(/\s+/g,' ').trim()}).filter(Boolean).slice(0,8)}
      var W=750,H=260;
      if(mode==='table'){H=146+Math.min(tView[1].length,8)*26+(tView[1].length>8?24:0)}
      else if(mode==='cards'){H=118+rowItems.length*38+(cards.length>rowItems.length?24:0)}
      else if(mode==='lines'){H=118+rowItems.length*24+(lines.length>rowItems.length?24:0)}
      else if(mode==='text'){H=Math.max(210,124+rowItems.length*22)}
      cv.width=W*2;cv.height=H*2;ctx.scale(2,2);
      var grad=ctx.createLinearGradient(0,0,W,H);grad.addColorStop(0,'#14283e');grad.addColorStop(1,'#1b3950');
      ctx.fillStyle=grad;ctx.fillRect(0,0,W,H);
      ctx.fillStyle=SEV[0];ctx.fillRect(0,0,6,H);
      var sans='Manrope,"Segoe UI",system-ui,Arial,sans-serif',mono='"IBM Plex Mono",Consolas,monospace';
      ctx.fillStyle='#b3cce1';ctx.font='600 11px '+mono;
      ctx.fillText(picFit(ctx,pageTitle.toUpperCase(),460),24,26);
      if(meta){ctx.textAlign='right';ctx.fillStyle='#aec6d8';ctx.fillText(picFit(ctx,meta,230),W-22,26);ctx.textAlign='left'}
      ctx.fillStyle='#ffffff';ctx.font='700 22px '+sans;
      ctx.fillText(picFit(ctx,title,badgeTxt?500:W-48),24,56);
      if(badgeTxt){ctx.font='700 12px '+sans;var bt=SEV[1]+'  '+badgeTxt,bw=Math.min(200,ctx.measureText(bt).width+20);
        ctx.fillStyle='rgba(255,255,255,0.08)';ctx.fillRect(W-22-bw,36,bw,24);
        ctx.strokeStyle=SEV[0];ctx.strokeRect(W-22-bw+0.5,36.5,bw-1,23);
        ctx.fillStyle=SEV[0];ctx.fillText(picFit(ctx,bt,bw-12),W-22-bw+10,52)}
      ctx.fillStyle='#3c5669';ctx.fillRect(24,70,W-48,1);
      if(mode==='table'){
        var cols=tView[0].slice(0,5),rows=tView[1].slice(0,8),nc=Math.max(1,cols.length),cw=(W-48)/nc;
        ctx.fillStyle='rgba(255,255,255,0.08)';ctx.fillRect(24,82,W-48,26);
        ctx.fillStyle='#b3cce1';ctx.font='700 11px '+mono;
        cols.forEach(function(h,i){ctx.fillText(picFit(ctx,h.toUpperCase(),cw-12),30+i*cw,99)});
        ctx.font='500 12px '+sans;
        rows.forEach(function(r,ri){var ry=108+ri*26;
          if(ri%2){ctx.fillStyle='rgba(255,255,255,0.04)';ctx.fillRect(24,ry,W-48,26)}
          ctx.fillStyle='#3c5669';ctx.fillRect(24,ry+25,W-48,0.5);
          cols.forEach(function(_,i){ctx.fillStyle=i===0?'#ffffff':'#d0e1ec';
            ctx.fillText(picFit(ctx,r[i]||'',cw-12),30+i*cw,ry+17)})});
        if(tView[1].length>rows.length){ctx.fillStyle='#aec6d8';ctx.font='600 11px '+mono;
          ctx.fillText('+'+(tView[1].length-rows.length)+' more rows in report',24,108+rows.length*26+16)}
      }else if(mode==='spark'){
        var mn=Math.min.apply(null,pts),mx=Math.max.apply(null,pts),span=mx-mn||1;
        var left=210,right=W-44,top=92,base=210;
        ctx.fillStyle='#ffffff';ctx.font='700 38px '+sans;ctx.fillText(String(pts[pts.length-1]),24,136);
        ctx.fillStyle='#c4d4e0';ctx.font='500 12px '+sans;ctx.fillText('Latest sample',24,158);
        ctx.fillStyle='#d0e1ec';ctx.font='500 12px '+mono;ctx.fillText('Min '+mn+'  Max '+mx,24,182);
        for(var gi=0;gi<=3;gi++){var gy=base-(base-top)*gi/3;ctx.beginPath();ctx.moveTo(left,gy);ctx.lineTo(right,gy);
          ctx.strokeStyle='#3c5669';ctx.lineWidth=1;ctx.stroke()}
        ctx.beginPath();
        pts.forEach(function(v,i){var px=left+(right-left)*i/(pts.length-1),py=base-(v-mn)*(base-top)/span;
          if(i)ctx.lineTo(px,py);else ctx.moveTo(px,py)});
        ctx.strokeStyle='#85d8df';ctx.lineWidth=2.5;ctx.stroke();
        var lx=right,ly=base-(pts[pts.length-1]-mn)*(base-top)/span;
        ctx.beginPath();ctx.arc(lx,ly,4,0,Math.PI*2);ctx.fillStyle='#befaff';ctx.fill()
      }else if(mode==='cards'){
        rowItems.forEach(function(it,ri){var ry=84+ri*38;
          ctx.fillStyle='rgba(255,255,255,0.06)';ctx.fillRect(24,ry,W-48,32);
          ctx.fillStyle='#85d8df';ctx.fillRect(24,ry,4,32);
          ctx.fillStyle='#ffffff';ctx.font='700 12.5px '+sans;ctx.fillText(picFit(ctx,it[1]||it[0],W-70),36,ry+14);
          ctx.fillStyle='#c4d4e0';ctx.font='500 11px '+sans;ctx.fillText(picFit(ctx,it[2]||it[0],W-70),36,ry+27)});
        if(cards.length>rowItems.length){ctx.fillStyle='#aec6d8';ctx.font='600 11px '+mono;
          ctx.fillText('+'+(cards.length-rowItems.length)+' more items in report',24,84+rowItems.length*38+14)}
      }else if(mode==='lines'){
        ctx.font='500 13px '+sans;
        rowItems.forEach(function(it,ri){var ry=98+ri*24;
          ctx.fillStyle='#85d8df';ctx.fillText('\u2022',26,ry);
          ctx.fillStyle='#e5eff7';ctx.fillText(picFit(ctx,it,W-66),40,ry)});
        if(lines.length>rowItems.length){ctx.fillStyle='#aec6d8';ctx.font='600 11px '+mono;
          ctx.fillText('+'+(lines.length-rowItems.length)+' more items in report',24,98+rowItems.length*24+8)}
      }else{
        ctx.font='500 13px '+sans;ctx.fillStyle='#e5eff7';
        rowItems.forEach(function(it,ri){ctx.fillText(picFit(ctx,it,W-48),24,100+ri*22)})
      }
      ctx.fillStyle='#aec6d8';ctx.font='500 11px '+mono;
      ctx.fillText(picFit(ctx,pageTitle+(meta?'  /  '+meta:''),W-48),24,H-14);
      return copyHelp.pngDataUrl(cv);
    }catch(e){return ''}
  }
  function directKids(parent,cls){
    return [].filter.call((parent&&parent.children)||[],function(x){return x.classList&&x.classList.contains(cls)})}
  function copyBarOf(sec){
    var body=(sec&&sec.querySelector('.body'))||sec;if(!body)return null;
    var existing=directKids(body,'scopy-bar');
    if(!existing.length)existing=all('.scopy-bar',body);
    if(existing.length>1)existing.slice(1).forEach(function(x){if(x.parentNode)x.parentNode.removeChild(x)});
    if(existing.length)return existing[0];
    var bar=document.createElement('div');bar.className='scopy-bar';bar.setAttribute('role','status');bar.setAttribute('aria-live','polite');bar.hidden=true;
    var g=document.createElement('span');g.className='sg';g.setAttribute('aria-hidden','true');
    var tx=document.createElement('span');tx.className='st';
    bar.appendChild(g);bar.appendChild(tx);body.appendChild(bar);return bar}
  var STATE_GLYPH={info:'\u25f7',good:'\u2713',warn:'T',bad:'!'};
  var STATE_CLS=['done','st-info','st-good','st-warn','st-bad'];
  function setCopyState(btn,bar,state,label,msg){
    var sp=btn&&btn.closest?btn.closest('.copy-split'):null;
    STATE_CLS.forEach(function(k){if(btn)btn.classList.remove(k);if(sp)sp.classList.remove(k)});
    if(state){if(btn)btn.classList.add('st-'+state);if(sp)sp.classList.add('st-'+state)}
    if(state==='good'){if(btn)btn.classList.add('done');if(sp)sp.classList.add('done')}
    var t=btn&&(btn.querySelector('.t,.lbl'));if(t&&label!==undefined)t.textContent=label;
    if(btn&&msg)btn.title=msg;
    if(bar){
      ['st-info','st-good','st-warn','st-bad'].forEach(function(k){bar.classList.remove(k)});
      if(state)bar.classList.add('st-'+state);
      var g=bar.querySelector('.sg'),tx=bar.querySelector('.st');
      if(g)g.textContent=STATE_GLYPH[state]||'';
      if(tx)tx.textContent=msg||'';
      bar.hidden=!msg}}
  function closeCopyMenus(except){
    all('.copy-split.open').forEach(function(sp){
      if(sp===except)return;
      sp.classList.remove('open');sp.classList.remove('drop-up');
      var tg=sp.querySelector('.copy-toggle');if(tg)tg.setAttribute('aria-expanded','false')})}
  document.addEventListener('click',function(e){if(!e.target||!e.target.closest||!e.target.closest('.copy-split'))closeCopyMenus()});
  document.addEventListener('keydown',function(e){if(e.key==='Escape')closeCopyMenus()});
  function makeCopyMenu(headTitle,headTag,subtitles){
    var menu=document.createElement('div');menu.className='copy-menu';menu.setAttribute('role','menu');
    var hd=document.createElement('div');hd.className='copy-menu-head';
    var h1=document.createElement('span');h1.textContent=headTitle||'Copy';
    var h2=document.createElement('span');h2.className='copy-menu-tag';h2.textContent=headTag||'';
    hd.appendChild(h1);hd.appendChild(h2);menu.appendChild(hd);
    var opts=[
      ['both','\u25cf','Copy both',(subtitles&&subtitles.both)||'Text + inline PNG',true],
      ['text','T','Copy text',(subtitles&&subtitles.text)||'Plain + HTML',false],
      ['picture','\u25a3','Copy picture',(subtitles&&subtitles.picture)||'PNG card',false]
    ];
    opts.forEach(function(o){
      var b=document.createElement('button');b.type='button';b.className='copy-opt';b.setAttribute('role','menuitem');b.setAttribute('data-mode',o[0]);
      var mk=document.createElement('span');mk.className='omark';mk.textContent=o[1];
      var mid=document.createElement('span');
      var st=document.createElement('strong');st.textContent=o[2];
      var sm=document.createElement('small');sm.textContent=o[3];
      mid.appendChild(st);mid.appendChild(sm);
      var tag=document.createElement('span');
      if(o[4]){tag.className='def-tag';tag.textContent='Default'}
      b.appendChild(mk);b.appendChild(mid);b.appendChild(tag);
      menu.appendChild(b)});
    return menu}
  function makeCopyIcon(){
    var svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.setAttribute('class','ic i1');svg.setAttribute('viewBox','0 0 16 16');svg.setAttribute('aria-hidden','true');
    var r=document.createElementNS('http://www.w3.org/2000/svg','rect');
    r.setAttribute('x','5.2');r.setAttribute('y','5.2');r.setAttribute('width','8.6');r.setAttribute('height','8.6');r.setAttribute('rx','1.8');
    r.setAttribute('fill','none');r.setAttribute('stroke','currentColor');r.setAttribute('stroke-width','1.5');
    var p=document.createElementNS('http://www.w3.org/2000/svg','path');
    p.setAttribute('d','M10.8 3.2V3a1.6 1.6 0 00-1.6-1.6H3.4A1.6 1.6 0 001.8 3v5.8a1.6 1.6 0 001.6 1.6h.2');
    p.setAttribute('fill','none');p.setAttribute('stroke','currentColor');p.setAttribute('stroke-width','1.5');p.setAttribute('stroke-linecap','round');
    svg.appendChild(r);svg.appendChild(p);return svg}
  function wireSplitControl(sp,mainBtn,opts){
    if(sp._splitBound)return sp;
    sp._splitBound=true;
    sp._baseLabel=opts.baseLabel||'Copy';
    sp._baseTitle=opts.title||'Copy as text and picture';
    mainBtn.classList.add('copy-main');
    mainBtn.hidden=false;mainBtn.disabled=false;
    STATE_CLS.forEach(function(k){mainBtn.classList.remove(k);sp.classList.remove(k)});
    sp.classList.remove('open');sp.classList.remove('drop-up');
    var lbl=mainBtn.querySelector('.t,.lbl');if(lbl)lbl.textContent=sp._baseLabel;
    mainBtn.title=sp._baseTitle;
    /* Deduplicate toggle/menu on saved-after-run DOM */
    var togs=directKids(sp,'copy-toggle');if(!togs.length)togs=all('.copy-toggle',sp);
    if(togs.length>1)togs.slice(1).forEach(function(x){if(x.parentNode)x.parentNode.removeChild(x)});
    var tog=togs[0];
    if(!tog){
      tog=document.createElement('button');tog.type='button';tog.className='copy-toggle';
      tog.setAttribute('aria-haspopup','menu');tog.textContent='\u25be';
      sp.appendChild(tog)}
    tog.hidden=false;tog.disabled=false;tog.setAttribute('aria-expanded','false');
    tog.setAttribute('aria-label',opts.toggleLabel||(sp._baseLabel+' options'));
    var menus=directKids(sp,'copy-menu');if(!menus.length)menus=all('.copy-menu',sp);
    if(menus.length>1)menus.slice(1).forEach(function(x){if(x.parentNode)x.parentNode.removeChild(x)});
    var menu=menus[0];
    if(!menu){menu=makeCopyMenu(opts.menuTitle||sp._baseLabel,opts.menuTag||'',opts.subtitles);sp.appendChild(menu)}
    var resetTimer=null;
    function trigger(mode){
      closeCopyMenus();
      if(resetTimer){clearTimeout(resetTimer);resetTimer=null}
      mainBtn.disabled=true;tog.disabled=true;
      var finish=function(){
        mainBtn.disabled=!!(opts.isEmpty&&opts.isEmpty());
        tog.disabled=mainBtn.disabled;
        if(sp.classList.contains('st-good')){
          resetTimer=setTimeout(function(){
            if(sp.classList.contains('st-good')){
              STATE_CLS.forEach(function(k){mainBtn.classList.remove(k);sp.classList.remove(k)});
              if(lbl)lbl.textContent=sp._baseLabel;mainBtn.title=sp._baseTitle}},2200)}};
      return opts.onCopy(mode,mainBtn,finish)}
    function openMenu(focusFirst){
      closeCopyMenus(sp);
      sp.classList.add('open');tog.setAttribute('aria-expanded','true');
      if(typeof sp.getBoundingClientRect==='function'&&window.innerHeight){
        var r=sp.getBoundingClientRect();
        if(r&&r.bottom&&window.innerHeight-r.bottom<190)sp.classList.add('drop-up')}
      if(focusFirst){var f=menu.querySelector('.copy-opt');if(f&&f.focus)f.focus()}}
    mainBtn.addEventListener('click',function(e){e.preventDefault();e.stopPropagation();trigger('both')});
    tog.addEventListener('click',function(e){
      e.preventDefault();e.stopPropagation();
      if(sp.classList.contains('open')){sp.classList.remove('open');sp.classList.remove('drop-up');tog.setAttribute('aria-expanded','false')}
      else openMenu(false)});
    tog.addEventListener('keydown',function(e){
      if(e.key==='ArrowDown'||e.key==='Down'){e.preventDefault();e.stopPropagation();openMenu(true)}});
    menu.addEventListener('keydown',function(e){
      var items=all('.copy-opt',menu),idx=items.indexOf(document.activeElement);
      if(e.key==='ArrowDown'||e.key==='Down'){e.preventDefault();var nx=items[(idx+1+items.length)%items.length];if(nx&&nx.focus)nx.focus()}
      else if(e.key==='ArrowUp'||e.key==='Up'){e.preventDefault();var pv=items[(idx-1+items.length)%items.length];if(pv&&pv.focus)pv.focus()}
      else if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closeCopyMenus();if(tog.focus)tog.focus()}});
    all('.copy-opt',menu).forEach(function(opt){
      opt.addEventListener('click',function(e){
        e.preventDefault();e.stopPropagation();
        trigger(opt.getAttribute('data-mode')||'both')})});
    sp._setModeLabel=function(label,count,title,ariaLabel,menuTitle,menuTag,disabled){
      var changed=sp._baseLabel!==label;
      sp._baseLabel=label;sp._baseTitle=title;
      var copying=sp.classList.contains('st-info');
      if(!copying&&changed){
        if(resetTimer){clearTimeout(resetTimer);resetTimer=null}
        STATE_CLS.forEach(function(k){mainBtn.classList.remove(k);sp.classList.remove(k)})}
      var keepFeedback=copying||(!changed&&(sp.classList.contains('st-good')||sp.classList.contains('st-warn')||sp.classList.contains('st-bad')));
      if(lbl&&!keepFeedback)lbl.textContent=label;
      if(!keepFeedback)mainBtn.title=title;
      if(ariaLabel)mainBtn.setAttribute('aria-label',ariaLabel);
      var cEl=sp.querySelector('.cnt');if(cEl&&count!==undefined)cEl.textContent=String(count);
      var mh=menu.querySelector('.copy-menu-head span:first-child');if(mh&&menuTitle)mh.textContent=menuTitle;
      var mt=menu.querySelector('.copy-menu-tag');if(mt&&menuTag!==undefined)mt.textContent=menuTag;
      if(!copying){mainBtn.disabled=!!disabled;tog.disabled=!!disabled}};
    return sp}
  function createSplitButton(opts){
    var sp=document.createElement('div');
    sp.className='copy-split'+(opts.cls?' '+opts.cls:'');
    if(opts.scope)sp.setAttribute('data-scope',opts.scope);
    var mainBtn=document.createElement('button');mainBtn.type='button';mainBtn.className='copy-main';
    mainBtn.setAttribute('data-action','both');
    if(opts.ariaLabel)mainBtn.setAttribute('aria-label',opts.ariaLabel);
    mainBtn.appendChild(makeCopyIcon());
    var lbl=document.createElement('span');lbl.className='t';lbl.textContent=opts.baseLabel||'Copy';
    mainBtn.appendChild(lbl);
    if(opts.withCount){
      var cnt=document.createElement('span');cnt.className='cnt';cnt.textContent='0';
      mainBtn.appendChild(cnt)}
    sp.appendChild(mainBtn);
    wireSplitControl(sp,mainBtn,opts);
    return sp}
  function copySection(btn,mode){
    var sec=btn&&btn.closest('details'),body=sec&&sec.querySelector('.body');if(!body)return Promise.resolve();
    var bar=copyBarOf(sec),g=grab(body),title=secHeading(sec),m=mode||'both';
    var dataUrl=m==='text'?'':sectionPicture(sec,g[0]);
    btn.disabled=true;
    return runCopyMode(m,{
      plain:g[0],html:g[1],dataUrl:dataUrl,alt:title+' snapshot',focusEl:btn,
      onState:function(st,lab,msg){setCopyState(btn,bar,st,lab,msg)},
      onDone:function(){btn.disabled=false}})}
  function copyBothSection(btn){return copySection(btn,'both')}
  /* 1.140.0: extract structured server data from a report-by-server table row + paired detail row */
  function parseDetailNodes(rawJson){
    var out={findings:[],cappedCount:0,reason:''};
    try{
      var data=JSON.parse(rawJson),shapes=data[0]||[],nodes=data[1]||[];
      function txt(n){if(typeof n==='string')return n;if(!Array.isArray(n)||n[0]<0)return '';return n.slice(1).map(txt).join('')}
      function attr(n,name){var sh=shapes[n[0]];if(!sh||!sh[1])return '';for(var i=0;i<sh[1].length;i++)if(sh[1][i][0]===name)return sh[1][i][1]||'';return ''}
      function walk(n){
        if(!Array.isArray(n)||n[0]<0)return;
        var sh=shapes[n[0]]||['',[]],tag=sh[0],cls=attr(n,'class');
        if(tag==='article'&&/\bsrv-finding\b/.test(cls)){
          var f={sev:'warning',sevLabel:'',title:'',evidence:'',whatToDo:''};
          var sm=cls.match(/\bsf-([a-z]+)\b/);if(sm)f.sev=normSev(sm[1]);
          (function scan(c){
            if(!Array.isArray(c)||c[0]<0)return;
            var cs=shapes[c[0]]||['',[]],ct=cs[0],cc=attr(c,'class');
            if(ct==='h3'&&!f.title)f.title=oneLine(txt(c));
            else if(/\bchip\b/.test(cc)&&!f.sevLabel)f.sevLabel=oneLine(txt(c));
            else if(/\bsrv-ev\b/.test(cc)&&!f.evidence)f.evidence=oneLine(txt(c));
            else if(/\bsrv-fix\b/.test(cc)&&!f.whatToDo)f.whatToDo=oneLine(txt(c)).replace(/^What to do:\s*/i,'');
            else c.slice(1).forEach(scan)})(n);
          if(f.title)out.findings.push(f);return}
        if(/\bsrv-capped\b/.test(cls)){
          var cm=txt(n).match(/(\d+)\s+more/i);if(cm)out.cappedCount+=parseInt(cm[1],10)||0}
        n.slice(1).forEach(walk)}
      nodes.forEach(walk)
    }catch(e){}
    return out}
  function readTableServer(row){
    var sev=normSev(row.getAttribute('data-sev')||(row.className.match(/\br-([a-z]+)\b/)||[])[1]||'');
    var nameTd=row.querySelector('td.srv-name')||row.cells[1]||row.cells[0];
    var host=nameTd?(nameTd.getAttribute('data-v')||one(nameTd.querySelector('.srv-host'))||one(nameTd)):one(row);
    var ip='',ipEls=nameTd?all('.srv-ip',nameTd):[];
    for(var ii=0;ii<ipEls.length;ii++){var iv=one(ipEls[ii]);if(/^\d{1,3}(?:\.\d{1,3}){3}/.test(iv)||iv.indexOf(':')>=0){ip=iv;break}}
    var stTd=row.querySelector('td[data-l="Status"]')||row.cells[2];
    var statusLabel=stTd?(stTd.getAttribute('data-v')||one(stTd)):SEV_LABEL[sev]||sev;
    var worstTd=row.querySelector('td.srv-worst');
    var worst=worstTd?(worstTd.getAttribute('data-v')||one(worstTd)):'';
    var capPill=worstTd&&worstTd.querySelector('.cap-pill');
    var cappedCount=0;
    if(capPill){var pm=one(capPill).match(/(\d+)/);if(pm)cappedCount=parseInt(pm[1],10)||0}
    var findings=[];
    var xr=row._x||(row.nextElementSibling&&row.nextElementSibling.classList.contains('xr')?row.nextElementSibling:null);
    if(xr){
      all('.srv-finding',xr).forEach(function(art){
        var fm=(art.className||'').match(/\bsf-([a-z]+)\b/);
        var fsev=fm?normSev(fm[1]):sev;
        var fl=one(art.querySelector('.srv-fhead .chip'))||SEV_LABEL[fsev]||fsev;
        var ft=one(art.querySelector('.srv-fhead h3'));
        var fe=one(art.querySelector('.srv-ev'));
        var ff=one(art.querySelector('.srv-fix')).replace(/^What to do:\s*/i,'');
        if(ft||fe)findings.push({sev:fsev,sevLabel:fl,title:ft||fe,evidence:fe,whatToDo:ff})});
      if(!cappedCount){var cp=xr.querySelector('.srv-capped');if(cp){var cm=one(cp).match(/(\d+)\s+more/i);if(cm)cappedCount=parseInt(cm[1],10)||0}}
    }else if(row._detail){
      var parsed=parseDetailNodes(row._detail.textContent);
      findings=parsed.findings;if(!cappedCount)cappedCount=parsed.cappedCount}
    var totalFindings=findings.length;
    if(!findings.length&&sev!=='ok'){
      var sumCnt=0;
      all('td.srv-cnt',row).forEach(function(c){var v=parseInt(c.getAttribute('data-v')||one(c),10);if(!isNaN(v)&&v>0)sumCnt+=v});
      if(sumCnt>totalFindings)totalFindings=sumCnt}
    return {host:host,ip:ip,sev:sev,statusLabel:statusLabel,worst:worst,summary:worst,findings:sortFindings(findings),totalFindings:totalFindings,cappedCount:cappedCount}}
  function readListServer(li){
    var sev=normSev(li.getAttribute('data-sev')||(li.className.match(/\bsev-([a-z]+)\b/)||[])[1]||'');
    var host=li.getAttribute('data-host')||one(li.querySelector('.srv-name'))||one(li);
    var ip='',metaSpans=all('.srv-meta span',li);
    for(var i=0;i<metaSpans.length;i++){var mv=one(metaSpans[i]);if(/^\d{1,3}(?:\.\d{1,3}){3}/.test(mv)){ip=mv;break}}
    var stEl=li.querySelector('.row-chip,.chip');
    var statusLabel=stEl?one(stEl).replace(/^[\u2715\u25b3\u2713?\-!]\s*/,''):SEV_LABEL[sev]||sev;
    var summary=one(li.querySelector('.srv-summary'));
    var findings=[];
    all('.finding',li).forEach(function(f){
      var fch=f.querySelector('.chip');
      var fsev=normSev((fch&&(fch.className.match(/\b(crit|warn|ok|unknown|low|info|critical|warning)\b/)||[])[1])||sev);
      var fl=fch?one(fch).replace(/^[\u2715\u25b3\u2713?\-!]\s*/,''):SEV_LABEL[fsev]||fsev;
      var ft=one(f.querySelector('.finding-title'));
      var ff=one(f.querySelector('.finding-fix')).replace(/^What to do:\s*/i,'');
      var fd=one(f.querySelector('.finding-detail'));
      if(!ff&&/^What to do:/i.test(fd)){ff=fd.replace(/^What to do:\s*/i,'');fd=''}
      if(ft)findings.push({sev:fsev,sevLabel:fl,title:ft,evidence:fd,whatToDo:ff})});
    return {host:host,ip:ip,sev:sev,statusLabel:statusLabel,worst:summary,summary:summary,findings:sortFindings(findings),totalFindings:findings.length,cappedCount:0}}
  var copyAllUpdaters=[];
  function refreshCopyAllCounts(){copyAllUpdaters.forEach(function(fn){try{fn()}catch(e){}})}
  function ensureSelBox(container,host,refEl){
    var cbs=all('input.srv-sel-cb',container);
    if(cbs.length>1)cbs.slice(1).forEach(function(x){if(x.parentNode)x.parentNode.removeChild(x)});
    var cb=cbs[0];
    if(!cb){
      cb=document.createElement('input');cb.type='checkbox';cb.className='srv-sel-cb';
      if(refEl&&refEl.parentNode===container)container.insertBefore(cb,refEl);
      else if(container.firstChild)container.insertBefore(cb,container.firstChild);
      else container.appendChild(cb)}
    cb.setAttribute('aria-label','Select '+(host||'server'));
    cb.title='Select '+(host||'server')+' (Space to toggle, Shift+click for range)';
    return cb}
  function ensureSelTools(tools,afterEl){
    var cnts=all('.srv-sel-count',tools);
    if(cnts.length>1)cnts.slice(1).forEach(function(x){if(x.parentNode)x.parentNode.removeChild(x)});
    var cnt=cnts[0];
    if(!cnt){cnt=document.createElement('span');cnt.className='srv-sel-count';cnt.setAttribute('aria-live','polite');cnt.textContent='0 selected'}
    var clrs=all('.srv-sel-clear',tools);
    if(clrs.length>1)clrs.slice(1).forEach(function(x){if(x.parentNode)x.parentNode.removeChild(x)});
    var clr=clrs[0];
    if(!clr){clr=document.createElement('button');clr.type='button';clr.className='btn srv-sel-clear';clr.textContent='Clear';clr.title='Clear selected rows';clr.hidden=true}
    var cues=all('.srv-sel-cue',tools);
    if(cues.length>1)cues.slice(1).forEach(function(x){if(x.parentNode)x.parentNode.removeChild(x)});
    var cue=cues[0];
    if(!cue){cue=document.createElement('span');cue.className='srv-sel-cue';cue.textContent='Tick rows or Space to select \u00b7 Shift+click range'}
    var ref=afterEl&&afterEl.parentNode===tools?afterEl.nextSibling:null;
    if(cnt.parentNode!==tools)tools.insertBefore(cnt,ref);
    if(clr.parentNode!==tools)tools.insertBefore(clr,cnt.nextSibling);
    if(cue.parentNode!==tools)tools.insertBefore(cue,clr.nextSibling);
    return {cnt:cnt,clr:clr,cue:cue}}
  function bindServerCopy(root){
    root=root||document;
    /* 1. Report-by-server tables (table.srv-tbl inside .srv-sec) */
    var srvTables=all('table.srv-tbl',root);
    if(root&&root.closest){
      var ancTbl=root.closest('table.srv-tbl');
      if(ancTbl&&srvTables.indexOf(ancTbl)<0)srvTables.push(ancTbl)}
    srvTables.forEach(function(tb){
      var sec=tb.closest('.srv-sec')||tb.closest('details.sec')||tb.parentNode;
      var bar=copyBarOf(sec);
      /* Header cell (deduplicate on saved-after-run DOM) */
      var hrow=tb.tHead&&tb.tHead.rows[0];
      if(hrow){
        var hths=all('th.srv-copy-th',hrow);
        if(hths.length>1)hths.slice(1).forEach(function(x){if(x.parentNode)x.parentNode.removeChild(x)});
        if(!hths.length){
          var th=document.createElement('th');th.className='k-x srv-copy-th';th.setAttribute('scope','col');
          var vh=document.createElement('span');vh.className='vh';vh.textContent='Copy';
          th.appendChild(vh);hrow.appendChild(th)}}
      /* Per-server row checkbox + Copy control */
      var dataRows=function(){
        var b=tb.tBodies&&tb.tBodies[0];if(!b)return [];
        return [].filter.call(b.rows,function(r){return !r.classList.contains('xr')&&!r.classList.contains('pt-fold')})};
      var shownRows=function(){
        if(tb._expand)tb._expand();
        return dataRows().filter(function(r){return !r.hidden||r._c})};
      var selectedRows=function(){
        return dataRows().filter(function(r){var cb=r.querySelector('input.srv-sel-cb');return !!(cb&&cb.checked)})};
      var tbody=tb.tBodies&&tb.tBodies[0];
      if(tbody){
        [].slice.call(tbody.rows).forEach(function(r){
          if(r.classList.contains('xr')||r.classList.contains('pt-fold'))return;
          var nameTd=r.querySelector('td.srv-name')||r.cells[1]||r.cells[0];
          var host=(nameTd&&(nameTd.getAttribute('data-v')||one(nameTd.querySelector('.srv-host'))))||'Server';
          if(nameTd){
            var cb=ensureSelBox(nameTd,host,nameTd.firstChild);
            r.classList.toggle('srv-selected',!!cb.checked);
            if(!cb._selBound){
              cb._selBound=true;
              cb.addEventListener('click',function(e){
                e.stopPropagation();
                if(e.shiftKey&&tb._lastSelRow&&tb._lastSelRow!==r){
                  var vis=dataRows().filter(function(x){return !x.hidden});
                  var a=vis.indexOf(tb._lastSelRow),b=vis.indexOf(r);
                  if(a>=0&&b>=0){
                    var lo=Math.min(a,b),hi=Math.max(a,b),state=!!cb.checked;
                    for(var k=lo;k<=hi;k++){
                      var rcb=vis[k].querySelector('input.srv-sel-cb');
                      if(rcb){rcb.checked=state;vis[k].classList.toggle('srv-selected',state)}}}}
                tb._lastSelRow=r;
                r.classList.toggle('srv-selected',!!cb.checked);
                if(tb._srvSelUpdate)tb._srvSelUpdate()});
              cb.addEventListener('change',function(){
                r.classList.toggle('srv-selected',!!cb.checked);
                if(tb._srvSelUpdate)tb._srvSelUpdate()})}}
          var tds=directKids(r,'srv-copy-td');if(!tds.length)tds=all('td.srv-copy-td',r);
          if(tds.length>1)tds.slice(1).forEach(function(x){if(x.parentNode)x.parentNode.removeChild(x)});
          var td=tds[0];
          if(!td){
            td=document.createElement('td');td.className='k-x srv-copy-td';td.setAttribute('data-l','Copy');
            r.appendChild(td)}
          var xr=r._x||(r.nextElementSibling&&r.nextElementSibling.classList.contains('xr')?r.nextElementSibling:null);
          if(xr){
            var xd=xr.querySelector('td.srv-xd,td.xd');
            if(xd&&!xd.getAttribute('data-copy-colspan')){
              var curCs=parseInt(xd.getAttribute('colspan')||'1',10)||1;
              xd.setAttribute('colspan',String(curCs+1));xd.setAttribute('data-copy-colspan','1')}}
          var existingSp=td.querySelector('.copy-split');
          if(existingSp&&existingSp._splitBound)return;
          var shortHost=host.split('.')[0]||host;
          var copyOpts={
            cls:'sm',scope:'server',baseLabel:'Copy',
            title:'Copy '+host+' status and findings (text + picture)',
            ariaLabel:'Copy '+host,
            toggleLabel:'Copy options for '+host,
            menuTitle:shortHost,menuTag:'Server row',
            subtitles:{both:'Text + inline PNG',text:'Status + all findings',picture:'PNG server card'},
            onCopy:function(mode,btn,finish){
              var ctx=extractContext(sec),srv=readTableServer(r);
              var fmt=formatOneServer(srv,ctx);
              var pic=mode==='text'?{dataUrl:''}:drawOneServerCard(srv,ctx);
              return runCopyMode(mode,{
                plain:fmt.plain,html:fmt.html,dataUrl:pic.dataUrl,width:700,alt:srv.host+' status card',focusEl:btn,
                onState:function(st,lab,msg){setCopyState(btn,bar,st,lab,msg)},
                onDone:finish})}};
          if(existingSp){
            var mbtn=existingSp.querySelector('.copy-main');
            if(mbtn)wireSplitControl(existingSp,mbtn,copyOpts);
          }else{td.appendChild(createSplitButton(copyOpts))}})}
      /* Copy all shown / Copy selected + n selected + Clear beside Expand all in .srv-tools */
      var tools=sec&&sec.querySelector('.srv-tools');
      if(tools&&!tools._srvCopyAllBound){
        tools._srvCopyAllBound=true;
        var olds=all('.srv-copy-all',tools);
        if(olds.length>1)olds.slice(1).forEach(function(x){if(x.parentNode)x.parentNode.removeChild(x)});
        var targetRows=function(){var sel=selectedRows();return sel.length?sel:shownRows()};
        var allOpts={
          cls:'pri srv-copy-all',scope:'all',baseLabel:'Copy all shown',withCount:true,
          title:'Copy every server currently shown after filters (text + picture)',
          ariaLabel:'Copy all shown servers',
          toggleLabel:'Copy all shown or selected options',
          menuTitle:'Copy all shown',menuTag:'0 rows',
          subtitles:{both:'Text summary + PNG',text:'Plain + HTML summary',picture:'PNG summary card'},
          isEmpty:function(){return targetRows().length===0},
          onCopy:function(mode,btn,finish){
            var rs=targetRows();if(!rs.length){finish();return Promise.resolve()}
            var ctx=extractContext(sec),servers=rs.map(readTableServer);
            var fmt=formatAllShown(servers,ctx);
            var pic=mode==='text'?{dataUrl:''}:drawAllShownCard(servers,ctx);
            return runCopyMode(mode,{
              plain:fmt.plain,html:fmt.html,dataUrl:pic.dataUrl,width:700,alt:(ctx.report||'Report')+' summary card',focusEl:btn,
              onState:function(st,lab,msg){setCopyState(btn,bar,st,lab,msg)},
              onDone:finish})}};
        var sp=olds[0];
        if(sp){
          var mb=sp.querySelector('.copy-main');
          if(mb)wireSplitControl(sp,mb,allOpts);
        }else{
          sp=createSplitButton(allOpts);
          var anchor=tools.querySelector('.srv-col')||tools.querySelector('.srv-exp');
          if(anchor&&anchor.parentNode===tools)tools.insertBefore(sp,anchor.nextSibling);
          else tools.appendChild(sp)}
        var selUI=ensureSelTools(tools,sp);
        var upd=function(){
          var nsel=selectedRows().length,nshown=shownRows().length;
          selUI.cnt.textContent=nsel+' selected';
          selUI.cnt.classList.toggle('has-sel',nsel>0);
          selUI.clr.hidden=nsel===0;
          if(nsel>0){
            sp._setModeLabel('Copy selected',nsel,
              'Copy '+nsel+' selected server'+(nsel===1?'':'s')+' (text + picture)',
              'Copy selected servers','Copy selected',nsel+' selected',false);
          }else{
            sp._setModeLabel('Copy all shown',nshown,
              'Copy every server currently shown after filters (text + picture)',
              'Copy all shown servers','Copy all shown',nshown+' row'+(nshown===1?'':'s'),nshown===0)}};
        if(!selUI.clr._selClrBound){
          selUI.clr._selClrBound=true;
          selUI.clr.addEventListener('click',function(){
            dataRows().forEach(function(r){
              var cb=r.querySelector('input.srv-sel-cb');if(cb)cb.checked=false;
              r.classList.remove('srv-selected')});
            tb._lastSelRow=null;upd();
            var mb2=sp.querySelector('.copy-main');if(mb2&&mb2.focus)mb2.focus()})}
        tb._srvSelUpdate=upd;
        copyAllUpdaters.push(upd);upd();
        var sevBar=sec&&sec.querySelector('.srv-severity');
        if(sevBar)sevBar.addEventListener('click',function(){setTimeout(upd,0)});
        var fltIn=tools.querySelector('.filter');
        if(fltIn)fltIn.addEventListener('input',function(){setTimeout(upd,0)})}
      else if(tb._srvSelUpdate){tb._srvSelUpdate()}});
    /* 2. Certificate host rollup (details.srv-rollup-sec[data-cert-rollup]) */
    all('[data-cert-rollup]',root).forEach(function(sec){
      if(sec._rollupCopyBound)return;sec._rollupCopyBound=true;
      var bar=copyBarOf(sec);
      function readRollup(btn){
        var host=btn.getAttribute('data-host')||one(btn.querySelector('.srv-host'));
        var sev=normSev(btn.getAttribute('data-sev')||'');
        var st=one(btn.querySelector('.srv-rollup-st'))||SEV_LABEL[sev]||sev;
        var sum=one(btn.querySelector('.srv-rollup-sum'));
        return {host:host,ip:'',sev:sev,statusLabel:st,worst:sum,summary:sum,
          findings:sum&&sev!=='ok'?[{sev:sev,sevLabel:st,title:sum,evidence:'',whatToDo:'Review expiring or invalid certificates in the table below.'}]:[],
          totalFindings:sum&&sev!=='ok'?1:0,cappedCount:0}}
      var allItems=function(){return all('.srv-rollup-item',sec)};
      var shownRollup=function(){return allItems().filter(function(it){return !it.hidden})};
      var selectedRollup=function(){return allItems().filter(function(it){
        var rw=it.parentNode,cb=rw&&rw.querySelector('input.srv-sel-cb');return !!(cb&&cb.checked)})};
      var upd=function(){};
      var lastRollup=null;
      allItems().forEach(function(item){
        var wrap=item.parentNode&&item.parentNode.classList.contains('srv-rollup-row')?item.parentNode:null;
        if(!wrap){
          wrap=document.createElement('div');wrap.className='srv-rollup-row';
          item.parentNode.insertBefore(wrap,item);wrap.appendChild(item)}
        var host=item.getAttribute('data-host')||one(item.querySelector('.srv-host'))||'Host';
        var cb=ensureSelBox(wrap,host,item);
        wrap.classList.toggle('srv-selected',!!cb.checked);
        if(!cb._selBound){
          cb._selBound=true;
          cb.addEventListener('click',function(e){
            e.stopPropagation();
            if(e.shiftKey&&lastRollup&&lastRollup!==item){
              var vis=shownRollup(),a=vis.indexOf(lastRollup),b=vis.indexOf(item);
              if(a>=0&&b>=0){
                var lo=Math.min(a,b),hi=Math.max(a,b),st=!!cb.checked;
                for(var k=lo;k<=hi;k++){
                  var rw=vis[k].parentNode,rcb=rw&&rw.querySelector('input.srv-sel-cb');
                  if(rcb){rcb.checked=st;rw.classList.toggle('srv-selected',st)}}}}
            lastRollup=item;wrap.classList.toggle('srv-selected',!!cb.checked);upd()});
          cb.addEventListener('change',function(){wrap.classList.toggle('srv-selected',!!cb.checked);upd()})}
        var ex=wrap.querySelector('.copy-split');if(ex&&ex._splitBound)return;
        var opts={
          cls:'sm',scope:'server',baseLabel:'Copy',title:'Copy '+host+' certificate status',
          ariaLabel:'Copy '+host,toggleLabel:'Copy options for '+host,
          menuTitle:host.split('.')[0]||host,menuTag:'Server row',
          subtitles:{both:'Text + inline PNG',text:'Status + findings',picture:'PNG server card'},
          onCopy:function(mode,btn,finish){
            var ctx=extractContext(sec),srv=readRollup(item),fmt=formatOneServer(srv,ctx);
            var pic=mode==='text'?{dataUrl:''}:drawOneServerCard(srv,ctx);
            return runCopyMode(mode,{plain:fmt.plain,html:fmt.html,dataUrl:pic.dataUrl,width:700,alt:srv.host+' status card',focusEl:btn,
              onState:function(st,lab,msg){setCopyState(btn,bar,st,lab,msg)},onDone:finish})}};
        if(ex){var mb=ex.querySelector('.copy-main');if(mb)wireSplitControl(ex,mb,opts)}
        else wrap.appendChild(createSplitButton(opts))});
      var tools=sec.querySelector('.srv-tools');
      if(tools){
        var olds=all('.srv-copy-all',tools);
        if(olds.length>1)olds.slice(1).forEach(function(x){if(x.parentNode)x.parentNode.removeChild(x)});
        var targetRollup=function(){var sel=selectedRollup();return sel.length?sel:shownRollup()};
        var allOpts={
          cls:'pri srv-copy-all',scope:'all',baseLabel:'Copy all shown',withCount:true,
          title:'Copy every host currently shown (text + picture)',ariaLabel:'Copy all shown hosts',
          toggleLabel:'Copy all shown or selected options',menuTitle:'Copy all shown',menuTag:'0 rows',
          subtitles:{both:'Text summary + PNG',text:'Plain + HTML summary',picture:'PNG summary card'},
          isEmpty:function(){return targetRollup().length===0},
          onCopy:function(mode,btn,finish){
            var items=targetRollup();if(!items.length){finish();return Promise.resolve()}
            var ctx=extractContext(sec),servers=items.map(readRollup),fmt=formatAllShown(servers,ctx);
            var pic=mode==='text'?{dataUrl:''}:drawAllShownCard(servers,ctx);
            return runCopyMode(mode,{plain:fmt.plain,html:fmt.html,dataUrl:pic.dataUrl,width:700,alt:(ctx.report||'Certificate rollup')+' summary card',focusEl:btn,
              onState:function(st,lab,msg){setCopyState(btn,bar,st,lab,msg)},onDone:finish})}};
        var sp=olds[0];
        if(!sp){sp=createSplitButton(allOpts);tools.appendChild(sp)}
        else{var mb=sp.querySelector('.copy-main');if(mb)wireSplitControl(sp,mb,allOpts)}
        var selUI=ensureSelTools(tools,sp);
        upd=function(){
          all('.srv-rollup-row',sec).forEach(function(rw){var it=rw.querySelector('.srv-rollup-item');if(it)rw.hidden=!!it.hidden});
          var nsel=selectedRollup().length,nshown=shownRollup().length;
          selUI.cnt.textContent=nsel+' selected';selUI.cnt.classList.toggle('has-sel',nsel>0);
          selUI.clr.hidden=nsel===0;
          if(nsel>0){
            sp._setModeLabel('Copy selected',nsel,'Copy '+nsel+' selected host'+(nsel===1?'':'s')+' (text + picture)',
              'Copy selected hosts','Copy selected',nsel+' selected',false);
          }else{
            sp._setModeLabel('Copy all shown',nshown,'Copy every host currently shown (text + picture)',
              'Copy all shown hosts','Copy all shown',nshown+' row'+(nshown===1?'':'s'),nshown===0)}};
        if(!selUI.clr._selClrBound){
          selUI.clr._selClrBound=true;
          selUI.clr.addEventListener('click',function(){
            all('.srv-rollup-row',sec).forEach(function(rw){
              var cb=rw.querySelector('input.srv-sel-cb');if(cb)cb.checked=false;
              rw.classList.remove('srv-selected')});
            lastRollup=null;upd()})}
        copyAllUpdaters.push(upd);upd();
        var sb=sec.querySelector('.srv-rollup-sev'),qi=sec.querySelector('.srv-rollup-q');
        if(sb)sb.addEventListener('click',function(){setTimeout(upd,0)});
        if(qi)qi.addEventListener('input',function(){setTimeout(upd,0)})}});
    /* 3. Card/list server view (ul.srv-list > li.srv-row) */
    all('ul.srv-list',root).forEach(function(ul){
      var sec=ul.closest('details.sec,section,.panel')||ul.parentNode;
      var bar=copyBarOf(sec);
      var allLis=function(){return all('li.srv-row',ul)};
      var shownList=function(){return allLis().filter(function(li){return !li.hidden&&!li.classList.contains('is-hidden')})};
      var selectedList=function(){return allLis().filter(function(li){var cb=li.querySelector('input.srv-sel-cb');return !!(cb&&cb.checked)})};
      var upd=function(){};
      var lastLi=null;
      allLis().forEach(function(li){
        var head=li.querySelector('.srv-head')||li;
        var srv0=readListServer(li);
        var cb=ensureSelBox(head,srv0.host,head.firstChild);
        li.classList.toggle('srv-selected',!!cb.checked);
        if(!cb._selBound){
          cb._selBound=true;
          cb.addEventListener('click',function(e){
            e.stopPropagation();
            if(e.shiftKey&&lastLi&&lastLi!==li){
              var vis=shownList(),a=vis.indexOf(lastLi),b=vis.indexOf(li);
              if(a>=0&&b>=0){
                var lo=Math.min(a,b),hi=Math.max(a,b),st=!!cb.checked;
                for(var k=lo;k<=hi;k++){
                  var rcb=vis[k].querySelector('input.srv-sel-cb');
                  if(rcb){rcb.checked=st;vis[k].classList.toggle('srv-selected',st)}}}}
            lastLi=li;li.classList.toggle('srv-selected',!!cb.checked);upd()});
          cb.addEventListener('change',function(){li.classList.toggle('srv-selected',!!cb.checked);upd()})}
        var ex=head.querySelector('.copy-split');if(ex&&ex._splitBound)return;
        var opts={
          cls:'sm',scope:'server',baseLabel:'Copy',title:'Copy '+srv0.host+' status and findings (text + picture)',
          ariaLabel:'Copy '+srv0.host,toggleLabel:'Copy options for '+srv0.host,
          menuTitle:srv0.host.split('.')[0]||srv0.host,menuTag:'Server row',
          subtitles:{both:'Text + inline PNG',text:'Status + all findings',picture:'PNG server card'},
          onCopy:function(mode,btn,finish){
            var ctx=extractContext(sec),srv=readListServer(li),fmt=formatOneServer(srv,ctx);
            var pic=mode==='text'?{dataUrl:''}:drawOneServerCard(srv,ctx);
            return runCopyMode(mode,{plain:fmt.plain,html:fmt.html,dataUrl:pic.dataUrl,width:700,alt:srv.host+' status card',focusEl:btn,
              onState:function(st,lab,msg){setCopyState(btn,bar,st,lab,msg)},onDone:finish})}};
        if(ex){var mb=ex.querySelector('.copy-main');if(mb)wireSplitControl(ex,mb,opts)}
        else head.appendChild(createSplitButton(opts))});
      var tools=sec&&sec.querySelector('.toolbar-right,.srv-tools');
      if(tools&&!tools._listCopyAllBound){
        tools._listCopyAllBound=true;
        var olds=all('.copy-split[data-scope="all"],.srv-copy-all',tools);
        if(olds.length>1)olds.slice(1).forEach(function(x){if(x.parentNode)x.parentNode.removeChild(x)});
        var targetList=function(){var sel=selectedList();return sel.length?sel:shownList()};
        var allOpts={
          cls:'pri srv-copy-all',scope:'all',baseLabel:'Copy all shown',withCount:true,
          title:'Copy every server currently shown after filters (text + picture)',ariaLabel:'Copy all shown servers',
          toggleLabel:'Copy all shown or selected options',menuTitle:'Copy all shown',menuTag:'0 rows',
          subtitles:{both:'Text summary + PNG',text:'Plain + HTML summary',picture:'PNG summary card'},
          isEmpty:function(){return targetList().length===0},
          onCopy:function(mode,btn,finish){
            var items=targetList();if(!items.length){finish();return Promise.resolve()}
            var ctx=extractContext(sec),servers=items.map(readListServer),fmt=formatAllShown(servers,ctx);
            var pic=mode==='text'?{dataUrl:''}:drawAllShownCard(servers,ctx);
            return runCopyMode(mode,{plain:fmt.plain,html:fmt.html,dataUrl:pic.dataUrl,width:700,alt:(ctx.report||'Report')+' summary card',focusEl:btn,
              onState:function(st,lab,msg){setCopyState(btn,bar,st,lab,msg)},onDone:finish})}};
        var sp=olds[0];
        if(sp){var mb=sp.querySelector('.copy-main');if(mb)wireSplitControl(sp,mb,allOpts)}
        else{
          sp=createSplitButton(allOpts);
          var anchor=tools.querySelector('#expand-all,.srv-col,.srv-exp');
          if(anchor&&anchor.parentNode===tools)tools.insertBefore(sp,anchor.nextSibling);
          else tools.appendChild(sp)}
        var selUI=ensureSelTools(tools,sp);
        upd=function(){
          var nsel=selectedList().length,nshown=shownList().length;
          selUI.cnt.textContent=nsel+' selected';selUI.cnt.classList.toggle('has-sel',nsel>0);
          selUI.clr.hidden=nsel===0;
          if(nsel>0){
            sp._setModeLabel('Copy selected',nsel,'Copy '+nsel+' selected server'+(nsel===1?'':'s')+' (text + picture)',
              'Copy selected servers','Copy selected',nsel+' selected',false);
          }else{
            sp._setModeLabel('Copy all shown',nshown,'Copy every server currently shown after filters (text + picture)',
              'Copy all shown servers','Copy all shown',nshown+' row'+(nshown===1?'':'s'),nshown===0)}};
        if(!selUI.clr._selClrBound){
          selUI.clr._selClrBound=true;
          selUI.clr.addEventListener('click',function(){
            allLis().forEach(function(li){
              var cb=li.querySelector('input.srv-sel-cb');if(cb)cb.checked=false;
              li.classList.remove('srv-selected')});
            lastLi=null;upd()})}
        copyAllUpdaters.push(upd);upd()}})}
  window.siteReport.copyHelp=copyHelp;
  window.siteReport.sectionPicture=sectionPicture;
  window.siteReport.copyBoth=copyBothSection;
  window.siteReport.copySection=copySection;
  window.siteReport.serverCopy={
    normSev:normSev,extractContext:extractContext,formatOneServer:formatOneServer,formatAllShown:formatAllShown,
    drawOneServerCard:drawOneServerCard,drawAllShownCard:drawAllShownCard,readTableServer:readTableServer,
    readListServer:readListServer,bindServerCopy:bindServerCopy,refreshCounts:refreshCopyAllCounts};
  /* a cell's value for a file: its whole value (data-v) where the page shows less, its lines kept */
  function val(el){var v=el?el.getAttribute('data-v'):'';return (v!==null&&v!==''?v:(el?el.textContent:'')).replace(/[ \t\r\f\v]+/g,' ').replace(/ ?\n ?/g,'\n').trim()}
  function head(th){var b=th.querySelector('button'),f=b&&b.firstChild,u=b&&b.querySelector('.hsub');
    /* a two-line header ("CPU" over "used") is one name again: 'CPU: used', as the document wrote it */
    return f&&f.nodeType===3?f.textContent.trim()+(u?': '+u.textContent.trim():''):one(th)}
  function tr(r,t){return '<tr>'+r.map(function(x){return '<'+t+' style="border:1px solid #999;padding:3px 6px;text-align:left">'+esc(x)+'</'+t+'>'}).join('')+'</tr>'}
  /* a table as shown: the columns not taken off (Columns), the rows not filtered out or searched away, in their order */
  function view(tb,f){if(tb._expand)tb._expand();if(tb._capApply)tb._capApply();var hs=[].slice.call(tb.tHead.rows[0].cells),on=[];
    hs.forEach(function(h,i){if(getComputedStyle(h).display!=='none'&&!h.classList.contains('k-x'))on.push(i)});   /* not the open buttons' column */
    return [on.map(function(i){return head(hs[i])}),
      [].filter.call(tb.tBodies[0].rows,function(r){return (!r.hidden||r._c)&&!r.classList.contains('xr')&&!r.classList.contains('pt-fold')}).map(function(r){return on.map(function(i){return f(r.cells[i])})})]}
  /* a section as text and HTML: its lines as a list, its table (as shown) as rows with tabs between the columns, its
     cards one after the other; its words (what the section is) only when it has nothing else */
  function grab(body){var P=[],H=[],W=[],WH=[];
    function table(tb){var v=view(tb,one),hd=v[0],rs=v[1];
      P.push([hd].concat(rs).map(function(r){return r.join('\t')}).join('\n'));
      H.push('<table style="border-collapse:collapse">'+tr(hd,'th')+rs.map(function(r){return tr(r,'td')}).join('')+'</table>')}
    [].forEach.call(body.children,function(el){
      if(el.classList.contains('tools2')||el.classList.contains('scopy-bar')||el.hidden)return;
      if(el.tagName==='UL'){var li=[].filter.call(el.children,function(x){return !x.hidden}).map(one).filter(Boolean);
        P.push(li.map(function(x){return '- '+x}).join('\n'));H.push('<ul>'+li.map(function(x){return '<li>'+esc(x)+'</li>'}).join('')+'</ul>');return}
      if(el.classList.contains('tw')){table(el.querySelector('table'));return}
      if(el.classList.contains('grps')){all('.gt,summary,.gl,.gh,.tw table',el).forEach(function(part){
        if(part.closest('[hidden]'))return;
        if(part.tagName==='TABLE')table(part);else{var label=one(part);P.push(label);H.push('<p>'+esc(label)+'</p>')}
      });return}
      if(el.tagName==='SCRIPT')return;
      if(el.classList.contains('cards')){[].forEach.call(el.querySelectorAll('.todo'),function(a){if(a.hidden)return;
          var l=[one(a.querySelector('.row1')),one(a.querySelector('h3')),one(a.querySelector('.subj')),one(a.querySelector('.next'))].filter(Boolean),
              pre=a.querySelector('pre'),code=pre?pre.textContent:'';
          P.push(l.join('\n')+(code?'\n'+code:''));H.push(l.map(function(x){return '<p>'+esc(x)+'</p>'}).join('')+(code?'<pre>'+esc(code)+'</pre>':''))});return}
      if(el.querySelector('script')){
        el=el.cloneNode(true);all('script',el).forEach(function(script){script.remove()})
      }
      var t=one(el);if(t){W.push(t);WH.push('<p>'+esc(t)+'</p>')}});
    return P.length?[P.join('\n\n'),H.join('')]:[W.join('\n\n'),WH.join('')]}
  /* a rule's check or fix text: its command lines (data-copy) - 1.73.0: a text with no command has no button, never the
     whole text; a Start here card: its command (the pre before the button) */
  function bindCopy(root){
  all('.code .copy',root).forEach(function(c){if(c._copyBound)return;c._copyBound=true;c.hidden=false;c.addEventListener('click',function(){
    put(c,c.hasAttribute('data-copy')?c.getAttribute('data-copy'):c.previousElementSibling.textContent,'')})});
  /* a command in a table (Look further, Command, Hint, Next step): its Copy button, the command alone */
  all('.ccopy',root).forEach(function(c){if(c._copyBound)return;c._copyBound=true;c.hidden=false;c.addEventListener('click',function(){
    put(c,c.hasAttribute('data-copy')?c.getAttribute('data-copy'):c.previousElementSibling.textContent.trim(),'')})});
  }
  bindCopy(document);
  /* Copy link (1.72.0, the index): an address made from the page's own (data-rel) when report_web_url is not set; in a
     line's summary the click copies and does not open or close the line */
  all('.tcopy').forEach(function(c){c.hidden=false;c.addEventListener('click',function(e){var v=c.getAttribute('data-copy');
    if(c.hasAttribute('data-rel')){try{v=new URL(v,location.href).href}catch(x){}}
    if(c.closest('summary')){e.preventDefault();e.stopPropagation()}put(c,v,'')})});
  /* 1.72.0, the index: a report that did not run on time - its last run (data-when) older than data-due hours (twice
     its usual gap) - judged here, when the page is read; the page was written when a report ran. Its line gets
     data-late (its clock and word show), its entry in Look at these first shows; the tiles, the area's head and its
     tab's dot count it; Runs ok (data-n) is lowered by those that were checked. No script: no judgement, nothing said. */
  var late=[];all('[data-due][data-when]').forEach(function(r){var w=Date.parse(r.getAttribute('data-when')),h=+r.getAttribute('data-due');
    if(w&&h>0&&Date.now()-w>h*3600000){r.setAttribute('data-late','');late.push(r);var s=r.querySelector('.late-slot');if(s)s.hidden=false;
      var li=r.id&&document.querySelector('[data-late-li="'+r.id+'"]');if(li)li.hidden=false}});
  var lateOk=late.filter(function(r){return !r.classList.contains('nc')}).length;
  all('[data-tile=late]').forEach(function(t){t.textContent=late.length});
  all('[data-tile=ok]').forEach(function(t){var n=parseInt(t.getAttribute('data-n'),10);if(!isNaN(n))t.textContent=Math.max(n-lateOk,0)});
  if(late.length){
    all('[data-tile-box=late]').forEach(function(t){t.classList.remove('t-good');t.classList.add('t-bad')});
    var w0=late[0].querySelector('.when');
    all('[data-tile-note=late]').forEach(function(t){t.textContent=(late[0].getAttribute('data-name')||'')+(w0?': last ran '+w0.textContent:'')+
      (late.length>1?' and '+(late.length-1)+' more':'')});
    /* 1.118.0: Look at these first says the late reports in ONE line - "5 reports are late: A, B, C +2" - the list
       under it (Show list) */
    all('.look .lf-late').forEach(function(box){var li=[].slice.call(box.querySelectorAll('li[data-late-li]:not([hidden])'));if(!li.length)return;
      var names=li.map(function(x){return x.getAttribute('data-name')||''}),c=box.querySelector('.lf-lc'),n=box.querySelector('.lf-ln'),
        m=box.querySelector('.lf-lplus');
      if(c)c.textContent=li.length+(li.length===1?' report is late:':' reports are late:');
      if(n){n.textContent=names.slice(0,3).join(', ');n.title=names.join('\n')}
      if(m)m.textContent=names.length>3?'+'+(names.length-3):'';
      box.hidden=false;var s=box.closest('.look');if(!s)return;s.hidden=false;
      /* the late line is one of the box's lines: one row fewer above it, the rest under Show N more (never Show 1 more) */
      var head=s.querySelector('.lf-gh+.lf-rows'),more=s.querySelector('.lf-more'),ml=more&&more.querySelector('.lf-rows');
      if(head&&ml){var rows=[].slice.call(head.children).concat([].slice.call(ml.children)),keep=(parseInt(s.getAttribute('data-cap'),10)||5)-1;
        if(rows.length===keep+1)keep++;
        rows.forEach(function(r,i){(i<keep?head:ml).appendChild(r)});
        var m1=more.querySelector('.m1');if(m1)m1.textContent='Show '+(rows.length-keep)+' more';more.hidden=rows.length<=keep}});
    all('.area').forEach(function(a){var n=a.querySelectorAll('.rr.bad,.rr[data-late]').length;if(!n)return;
      var b=a.querySelector('.ah.bad'),g=a.querySelector('.ah.good'),k=a.querySelector('.ahn'),v=a.querySelector('.ahv');
      if(k)k.textContent=n;if(v)v.textContent=n===1?'needs':'need';if(b)b.hidden=false;if(g)g.hidden=true;
      all('label[for="t'+a.getAttribute('data-tab')+'"] [hidden]').forEach(function(d){d.hidden=false})})}
  all('.scopy').forEach(function(c){
    if(c._copyBound)return;c._copyBound=true;
    var sec=c.closest('details'),title=secHeading(sec),bar=sec&&copyBarOf(sec);
    if(bar)bar.hidden=true;
    var sp=c.closest('.copy-split');
    if(!sp&&c.parentNode){
      sp=document.createElement('div');sp.className='copy-split';sp.setAttribute('data-scope','section');
      c.parentNode.insertBefore(sp,c);sp.appendChild(c)}
    if(sp){
      wireSplitControl(sp,c,{
        baseLabel:'Copy',title:'Copy this section as text and picture',
        toggleLabel:title+' copy options',menuTitle:title,menuTag:'Section',
        subtitles:{both:'Text + inline PNG',text:'Plain + HTML text',picture:'PNG snapshot'},
        onCopy:function(mode,btn,finish){
          var body=sec&&sec.querySelector('.body');if(!body){finish();return Promise.resolve()}
          var g=grab(body),dataUrl=mode==='text'?'':sectionPicture(sec,g[0]);
          return runCopyMode(mode,{
            plain:g[0],html:g[1],dataUrl:dataUrl,alt:title+' snapshot',focusEl:btn,
            onState:function(st,lab,msg){setCopyState(btn,bar,st,lab,msg)},
            onDone:finish})}})}});
  /* CSV: the table as shown, for Excel - a byte order mark (its accents read right), every value in quotes, and a value
     that Excel would take for a formula (= + - @ at its start, not a number) made text with a ' before it */
  function csvf(v){if(/^[=+\-@\t\r]/.test(v)&&!/^[-+]?[\d.,]+%?$/.test(v))v="'"+v;return '"'+v.replace(/"/g,'""')+'"'}
  function fname(t){return t.replace(/\s*:\s*/g,' - ').replace(/[\\\/*?"<>|\u0000-\u001f]+/g,'-').replace(/\s+/g,' ').trim().slice(0,120)||'report'}
  function save(name,text){var b=new Blob([text],{type:'text/csv;charset=utf-8'});
    if(navigator.msSaveOrOpenBlob){navigator.msSaveOrOpenBlob(b,name);return}
    var u=URL.createObjectURL(b),a=document.createElement('a');a.href=u;a.download=name;a.style.display='none';
    document.body.appendChild(a);a.click();a.remove();setTimeout(function(){URL.revokeObjectURL(u)},4000)}
  all('.csv').forEach(function(c){var sec=c.closest('details'),tb=sec&&sec.querySelector('.tw table');if(!tb)return;c.hidden=false;
    c.addEventListener('click',function(){var v=view(tb,val),h2=sec.querySelector('summary h2'),
        title=h2&&h2.firstChild&&h2.firstChild.nodeType===3?h2.firstChild.textContent.trim():one(h2);
      save(fname(document.title+' - '+title)+'.csv','\ufeff'+[v[0]].concat(v[1]).map(function(r){return r.map(csvf).join(',')}).join('\r\n')+'\r\n')})});
  /* Columns: check boxes the CSS acts on (they work without this); here a table that now fits stops scrolling, the
     list closes on a click elsewhere, and Show all. 1.11.0: the choice is kept in the browser for every table with the
     same columns (the VM inventory's folder tables hide a column together, on this page and the next); until a choice is
     made, the columns the report starts hidden (hidden_columns) */
  function colsig(d){return 'site-report-cols:'+all('label',d).map(function(l){return l.textContent.trim()}).join('|')}
  function coloff(d){var off=[];all('label',d).forEach(function(l){var i=l.querySelector('input');if(i&&!i.checked)off.push(l.textContent.trim())});return off}
  function colput(d,off){all('label',d).forEach(function(l){var i=l.querySelector('input');if(i)i.checked=off.indexOf(l.textContent.trim())<0})}
  function colkeep(d){var k=colsig(d),off=coloff(d);
    try{localStorage.setItem(k,JSON.stringify(off))}catch(e){}
    all('details.cols').forEach(function(o){if(o!==d&&colsig(o)===k)colput(o,off);if(colsig(o)===k)refit(o)});fit()}
  function refit(d){var sec=d.closest('details.sec');if(sec)all('.tw',sec).forEach(function(w){w.removeAttribute('data-fs')})}
  all('details.cols').forEach(function(d){var b=d.querySelector('.call');
    try{var kept=JSON.parse(localStorage.getItem(colsig(d))||'null');if(kept&&kept.length!==undefined){colput(d,kept);refit(d)}}catch(e){}
    d.addEventListener('change',function(){colkeep(d)});
    if(b){b.hidden=false;b.addEventListener('click',function(){all('input',d).forEach(function(i){i.checked=true});colkeep(d)})}});
  /* 1.70.2: 'grow' - the Copy buttons beside the commands came after the first fit; a table they made wider goes on
     from its steps (the same width would not fit it again) */
  fit('grow');
  document.addEventListener('click',function(e){all('details.cols[open]').forEach(function(d){if(!d.contains(e.target))d.open=false})});
  /* a row is shown unless the table's filter, the search or the page's filter buttons (1.67.0) take it away */
  /* 1.86.0: _tm another team's row folded, _sv a severity chip not chosen (report_pop.js) */
  function vis(r){var o=!!(r._f||r._s||r._p||r._d||r._ot||r._tm||r._sv),h=o||!!r._c;if(r.hidden!==h)r.hidden=h;
    /* only the row cap hides it: Print shows it (a row a filter or the search hid stays out) */
    if(!!r._c&&!o){if(!r.hasAttribute('data-cap'))r.setAttribute('data-cap','')}else if(r.hasAttribute('data-cap'))r.removeAttribute('data-cap');
    /* 1.68.0: a row that opens (tr.xr after it): shown when its row is and it was opened */
    if(r._x){var xh=h||!r._o;if(r._x.hidden!==xh)r._x.hidden=xh}}
  /* the open button of such a row: its rules, or its hosts, under it; its small table fitted */
  document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('.xb');if(!b)return;var r=b.closest('tr');
    if(!r)return;
    if(r._detail){unpack(r._detail);r._detail=null;r._x=r.nextElementSibling;bindNew(r._x)}
    if(!r._x)return;
    r._o=!r._o;b.setAttribute('aria-expanded',String(r._o));vis(r);fit()});
  /* 1.76.5: a cell said in words keeps its raw key (a check id, a raw JSON message) in its title: the table's data-keys {n: columns, k: [[row, column, key]]} */
  function cellKeys(tb){if(!tb.matches('table[data-keys]'))return;try{var d=JSON.parse(tb.getAttribute('data-keys')),b=[].filter.call(tb.tBodies[0].rows,function(x){return !x.classList.contains('xr')}),off=tb.rows[0].cells.length-d.n;
    d.k.forEach(function(e){var r=b[e[0]],c=r&&r.cells[e[1]+off];if(c&&c.title==='')c.title=e[2]})}catch(x){}}
  /* Older pages have every row in their markup and keep their 25-row fold. Published row blocks start at row 101. */
  /* a table's cap is lifted by its own filter box or column filters, or by the page's search, the page's filter buttons or the time line's controls (each its own flag: one table's box does not open another's) */
  var CAP=25,printing=false,gQ=false,gF=false,counts=[];
  function capFree(tb){return printing||gQ||gF||!!tb._otFree||!!tb._cq||!!tb._cf}
  function capAll(){all('.tw table').forEach(function(tb){if(tb._capApply)tb._capApply()})}
  var deferredIds=Object.create(null);
  function bindTable(tb){
    if(tb._capApply)return;
    var body=tb.tBodies[0],rows=[],pending=body.querySelector(':scope > script.defer-main,:scope > script.defer-mini');
    function register(start){
      for(var i=start;i<body.rows.length;i++){
        var r=body.rows[i];if(r.classList.contains('xr')||r.classList.contains('pt-fold'))continue;
        rows.push(r);var n=r.nextElementSibling;if(n&&n.classList.contains('xr'))r._x=n;
        if(n&&n.classList.contains('defer-detail'))r._detail=n;
      }
      cellKeys(tb);
    }
    register(0);
    var modern=document.documentElement.hasAttribute('data-row-window'),limit=modern?100:CAP;
    var capOn=!!pending||(!modern&&rows.length>CAP+5);
    var capBtn=null,capTxt=null,capAll_=null;tb._capApply=capApply;
    if(pending){
      var countAll=pending.getAttribute('data-count'),ids=JSON.parse(pending.getAttribute('data-ids'));
      ids.forEach(function(id){deferredIds[id]=tb});
      tb._expand=function(){
        if(!pending)return;
        var start=body.rows.length;unpack(pending);pending=null;register(start);
        ids.forEach(function(id){delete deferredIds[id]});
        bindNew(body);
      };
    }
    /* The older fold always includes severe rows. Both limits count only rows that pass the active filters. */
    function capApply(){if(!capOn)return;
      if(pending){if(!(tb._open||capFree(tb)))return;tb._expand()}
      var k=0,fr=capFree(tb),open=!!tb._open||fr,folded=0,left=0;
      [].forEach.call(body.rows,function(r){if(r.classList.contains('xr')||r.classList.contains('pt-fold'))return;var off=!!(r._f||r._s||r._p||r._d||r._ot||r._tm||r._sv),
          sev=!modern&&(r.classList.contains('r-critical')||r.classList.contains('r-warning'));
        if(!off)k++;r._c=!open&&!off&&!sev&&k>limit;if(r._c)folded++;else if(!off)left++;r.classList.toggle('capped',!!r._c);vis(r)});
      if(capBtn){capBtn.hidden=open||!folded;if(capTxt)capTxt.nodeValue='The first '+left+' of '+(left+folded)+' rows are shown'+(modern?'. ':' (critical and warning rows always are). ');
        if(capAll_)capAll_.textContent='Show all '+(left+folded)+(modern?' rows':'')}
      counts.forEach(function(c){c()})}
    if(capOn&&tb.parentNode){capBtn=document.createElement('p');capBtn.className='capbar';capTxt=document.createTextNode('The first '+limit+' of '+(countAll||rows.length)+' rows are shown. ');capBtn.appendChild(capTxt);
      var cb=document.createElement('button');cb.type='button';cb.className='btn';cb.textContent='Show all '+(countAll||rows.length)+(modern?' rows':'');capAll_=cb;
      cb.addEventListener('click',function(){tb._open=true;capApply();fitRows(tb)});capBtn.appendChild(cb);tb.parentNode.parentNode.insertBefore(capBtn,tb.parentNode.nextSibling)}
    /* a header's click sorts by its column, the next one the other way; the arrows as characters (textContent: a page
       under Trusted Types sorts too). A first click on a column whose values are already in order as the rows stand
       now (the VM tables come sorted by VM), not all the same (empty cells aside), sorts it the other way, so the click
       always shows. The
       whole header cell is the target, not only its button */
    tb.querySelectorAll('th button').forEach(function(btn){var th=btn.parentNode,i=th.cellIndex;   /* 1.68.0: a column may have no button */
      th.addEventListener('click',function(e){if(!btn.contains(e.target))btn.click()});
      btn.addEventListener('click',function(){
      if(tb._expand)tb._expand();
      var by=rows.map(function(r){return [r,skey(cval(r.cells[i]))]}),
          order=function(asc){return by.slice().sort(function(a,b){return scmp(a[1],b[1],asc)}).map(function(x){return x[0]})},
          s=th.getAttribute('aria-sort'),asc=s!=='ascending',
          now=[].filter.call(body.rows,function(r){return !r.classList.contains('pt-fold')}).map(function(r){return skey(cval(r.cells[i]))}),inc=true,diff=false;
      for(var j=1;j<now.length;j++){var c=scmp(now[j-1],now[j],true);if(c>0)inc=false;if(c<0&&now[j]!==null)diff=true}
      if(!s&&inc&&diff)asc=false;
      tb.querySelectorAll('th').forEach(function(h){var a=h.querySelector('.sort');h.removeAttribute('aria-sort');if(a)a.textContent='\u2195'});
      th.setAttribute('aria-sort',asc?'ascending':'descending');btn.querySelector('.sort').textContent=asc?'\u25b2':'\u25bc';
      order(asc).forEach(function(r){body.appendChild(r);if(r._x||r._detail)body.appendChild(r._x||r._detail)});capApply();if(tb._expand)fitRows(tb)})});
    var tools=tb.parentNode.previousElementSibling;
    if(tools&&tools.classList.contains('tools2')){var f=tools.querySelector('.filter'),n=tools.querySelector('.shown'),
        dd=[].slice.call(tools.querySelectorAll('select[data-f]'));
      if(!f&&!dd.length)return;
      /* an older page: the whole row of tools was hidden */
      tools.hidden=false;if(f)f.hidden=false;
      var count=function(){if(!n)return;var total=pending?+countAll:rows.length,k=0;rows.forEach(function(r){if(!r.hidden)k++});n.textContent=k<total?k+' of '+total:''};
      counts.push(count);count();
      if(f)f.addEventListener('input',function(){if(tb._expand)tb._expand();var q=f.value.trim().toLowerCase();tb._cq=!!q;
        rows.forEach(function(r){var rt='';if(r.cells){for(var ci=0;ci<r.cells.length;ci++)if(!r.cells[ci].classList.contains('srv-copy-td'))rt+=' '+(r.cells[ci].textContent||'')}else rt=r.textContent||'';
          r._f=!!q&&rt.toLowerCase().indexOf(q)<0;vis(r)});capApply();count();refreshCopyAllCounts();if(tb._expand)fitRows(tb)});
      /* one choice at a time per list (1.67.0): a row stays when the chosen value is one of its own (data-f<n>: |a|b|) */
      dd.forEach(function(s){s.parentNode.hidden=false;s.addEventListener('change',function(){if(tb._expand)tb._expand();tb._cf=dd.some(function(x){return !!x.value});
        rows.forEach(function(r){var off=false;dd.forEach(function(x){var v=x.value;
          if(v&&(r.getAttribute('data-f'+x.getAttribute('data-f'))||'').indexOf('|'+v+'|')<0)off=true});r._d=off;vis(r)});capApply();count();refreshCopyAllCounts();if(tb._expand)fitRows(tb)})})}
  }
  all('.tw table').forEach(bindTable);
  capAll();
  bindServerCopy(document);
  window.addEventListener('beforeprint',function(){printing=true;capAll()});
  window.addEventListener('afterprint',function(){printing=false;capAll()});
  /* 1.86.0: for report_pop.js (the team picker, the severity chips) - a row's flags applied, then the caps, the counts and the fit */
  window.siteReport.vis=vis;window.siteReport.rowsChanged=function(){capAll();counts.forEach(function(c){c()});refreshCopyAllCounts();fit()};
  /* a link to a row past the cap (a dashboard's drill link, Open item, a timeline line) shows every row of that table first */
  function openDetails(t){for(var d=t;d&&d!==document;d=d.parentNode)if(d.tagName==='DETAILS'&&!d.open)d.open=true;}
  function liftFor(h){if(!h||h.length<2)return;var id=decodeURIComponent(h.slice(1).split('&tab=')[0]),pending=deferredIds[id];
    if(pending){pending._open=true;pending._capApply();fitRows(pending)}
    var t=document.getElementById(id);
    openDetails(t);
    var tr=t&&t.closest&&t.closest('tr');if(tr&&tr._c){var tb=tr.closest('table');tb._open=true;if(tb._capApply)tb._capApply();fitRows(tb)}
    if(t&&(pending||h.indexOf('&tab=')>=0))t.scrollIntoView({behavior:'instant',block:'start'})}
  document.addEventListener('click',function(e){if(e.button||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;   /* 1.87.2: a new tab - the report stays as it is */
    var a=e.target.closest&&e.target.closest('a[href^="#"]');if(a)try{liftFor(a.getAttribute('href'))}catch(x){}},true);
  window.addEventListener('hashchange',function(){try{liftFor(location.hash)}catch(x){}});try{liftFor(location.hash)}catch(x){}
  /* 1.87.2: a link to a place on this page (the rail, Open item, a drill link, a table of contents) goes there without a step in the browser's history:
     the address shows it (history.replaceState - no new step), so Back leaves the report in one click. What the browser does for a #link
     is done here: the closed section around it opens, a row is marked (is-target, as tr:target), and the page scrolls to it - as the page's own scroll-behavior says
     (smooth under no-preference), at once when motion is off (a motion-off page or ?motion=off, the system's request). A link with a modifier key, another target or a name not on the page: the browser's own. */
  (function(){var H=document.documentElement;
    function still(){if(H.classList.contains('motion-off')||H.getAttribute('data-motion')==='off'||/[?&]motion=off\b/.test(location.search))return true;
      if(H.classList.contains('motion-always'))return false;return !!(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches)}
    var marked=null;
    /* one highlight: a row the address names when the page opens is marked the same way (no tr:target in the CSS) */
    try{var t0=location.hash.length>1&&document.getElementById(decodeURIComponent(location.hash.slice(1).split('&tab=')[0]));if(t0&&t0.tagName==='TR'){marked=t0;t0.classList.add('is-target')}}catch(x){}
    document.addEventListener('click',function(e){
      if(e.defaultPrevented||e.button||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
      var a=e.target.closest&&e.target.closest('a[href^="#"]');if(!a||(a.target&&a.target!=='_self')||a.hasAttribute('download'))return;
      var h=a.getAttribute('href'),id;try{id=decodeURIComponent(h.slice(1).split('&tab=')[0])}catch(x){return}
      var t=id?document.getElementById(id):null;if(id&&!t)return;
      e.preventDefault();
      openDetails(t);
      var tab=/(?:^#|&)tab=([^&]*)/.exec(location.hash),url=id?h:location.pathname+location.search;
      if(tab&&h.indexOf('&tab=')<0)url+=(id?'&':'#')+'tab='+tab[1];
      try{history.replaceState(history.state,'',url)}catch(x){}
      if(marked)marked.classList.remove('is-target');marked=t&&t.tagName==='TR'?t:null;if(marked)marked.classList.add('is-target');
      var b=still()?'instant':'auto';
      try{if(t)t.scrollIntoView({behavior:b,block:'start'});else window.scrollTo({top:0,behavior:b})}catch(x){if(t)t.scrollIntoView(true);else window.scrollTo(0,0)}
      /* the keyboard goes on from there (the next Tab is in the place, not back in the rail): focus, without a second scroll */
      var f=t&&(t.tagName==='DETAILS'?t.querySelector('summary'):t);
      if(f){if(!/^(A|BUTTON|INPUT|SELECT|TEXTAREA|SUMMARY)$/.test(f.tagName)&&!f.hasAttribute('tabindex'))f.setAttribute('tabindex','-1');try{f.focus({preventScroll:true})}catch(x){}}})})();
  /* 1.76.5: a table cell of more than 4 lines shows 4 and "more" (CSS draws the word; the cell's text is as it was) - measured once its section is open */
  function fitRows(root){fit('grow');longCells(root)}
  function longCells(root){all('.tw td .cv',root).forEach(function(cv){var td=cv.parentNode;if(td._lc||!td.offsetParent)return;
    var lh=parseFloat(getComputedStyle(cv).lineHeight)||20;if(cv.offsetHeight>lh*4.6){td._lc=1;td.classList.add('cl')}else td._lc=0})}
  document.addEventListener('click',function(e){var td=e.target.closest&&e.target.closest('td.cl');if(td&&!e.target.closest('a,button,input,select'))td.classList.toggle('open')});
  longCells(document);all('details.sec').forEach(function(d){d.addEventListener('toggle',function(){if(d.open)longCells(d)})});
  /* 1.67.0: the page's filter buttons (.facets: the STIG scorecard's Platform and Collection). A row or a collection that
     opens (details.grp) tagged data-pf (its platforms) or data-col (its collections) is shown when it has the one chosen;
     what has no such tag stays. The buttons and their select (a narrow screen) say the same; the counts of the tables'
     filters follow. A collection opened: its small tables fitted */
  var has=function(el,k,v){var t=el.getAttribute(k);return t===null||!v||(' '+t+' ').indexOf(' '+v+' ')>=0};
  all('.facets').forEach(function(fx){var ch={pf:'',col:'',cat:'',h:''};fx.hidden=false;
    /* 1.68.0: CAT (a row's categories, tokens as the platforms) and Host (a part of a host's name, in data-h) */
    var hosted=function(el){var t=el.getAttribute('data-h');return !ch.h||t===null||t.indexOf(ch.h)>=0};
    var apply=function(){gF=!!(ch.pf||ch.col||ch.cat||ch.h);capAll();all('[data-pf],[data-col],[data-cat],[data-h]').forEach(function(el){if(el.closest('.facets'))return;
        var off=!(has(el,'data-pf',ch.pf)&&has(el,'data-col',ch.col)&&has(el,'data-cat',ch.cat)&&hosted(el));
        if(el.tagName==='TR'){el._p=off;vis(el)}else if(el.hidden!==off)el.hidden=off});capAll();
      /* each section with tagged rows: its title's (N) and its count say how many are shown; none left - its table
         gives way to one line; a list of collections with none shown goes away */
      all('details.sec').forEach(function(sec){var tb=sec.querySelector('.tw table');
        if(!tb||!tb.tBodies[0]||!tb.querySelector('tbody tr[data-pf],tbody tr[data-col]'))return;
        var rows=[].slice.call(tb.tBodies[0].rows).filter(function(r){return !r.classList.contains('xr')&&!r.classList.contains('pt-fold')}),k=rows.filter(function(r){return !r.hidden}).length,
            h2=sec.querySelector('summary h2'),c=sec.querySelector('summary .count'),t=h2&&h2.firstChild,w=tb.closest('.tw'),
            empty=!k&&rows.some(function(r){return r._p});
        var any=ch.pf||ch.col||ch.cat||ch.h;
        if(c){if(c._n===undefined)c._n=c.textContent;c.textContent=any?String(k):c._n}
        if(t&&t.nodeType===3){if(t._v===undefined)t._v=t.nodeValue;t.nodeValue=any?t._v.replace(/\(\d+\)/,'('+k+')'):t._v}
        if(empty&&!sec._none){var p=sec._none=document.createElement('p');p.className='fnone';p.textContent='Nothing matches this filter.';
          w.parentNode.insertBefore(p,w.nextSibling)}
        if(sec._none)sec._none.hidden=!empty;if(w.hidden!==empty)w.hidden=empty});
      all('.grps').forEach(function(g){var off=!g.querySelector('details.grp:not([hidden])');if(g.hidden!==off)g.hidden=off});
      counts.forEach(function(c){c()});fitRows(document);
      /* 1.71.0: a dashboard draws its panels again (site-facets) */
      var ev;try{ev=new CustomEvent('site-facets',{detail:{pf:ch.pf,col:ch.col,cat:ch.cat,h:ch.h}})}catch(e){ev=null}if(ev)document.dispatchEvent(ev)};
    var put=function(k,v){ch[k]=v;
      all('button[data-fk="'+k+'"]',fx).forEach(function(b){b.setAttribute('aria-pressed',String(b.getAttribute('data-fv')===v))});
      all('select[data-fk="'+k+'"]',fx).forEach(function(s){if(s.value!==v)s.value=v});apply()};
    all('button[data-fk]',fx).forEach(function(b){b.addEventListener('click',function(){put(b.getAttribute('data-fk'),b.getAttribute('data-fv'))})});
    all('select[data-fk]',fx).forEach(function(s){s.addEventListener('change',function(){put(s.getAttribute('data-fk'),s.value)})});
    all('input[data-fk]',fx).forEach(function(s){var tm;s.addEventListener('input',function(){clearTimeout(tm);
      tm=setTimeout(function(){put(s.getAttribute('data-fk'),s.value.trim().toLowerCase())},150)})})});
  all('details.grp').forEach(function(d){d.addEventListener('toggle',function(){if(d.open)fitRows(d)})});
  /* 1.71.0: dashboards ([data-dash]: report_dash.html.j2, its data in the script of type application/json; the rules in
     plugins/filter/report_dash_filters.py). The page's filters (the .facets above, keys col, pf, cat, h) draw every panel
     again from the facts: the tiles, the stacked bars, the weekly line (its hover and its table), the top lists; a panel
     whose facts cannot follow a filter says so. The engine (dash-engine to end-dash-engine) is the same as dash_view in
     Python, line for line; tests/test_report_dash.py holds the two to the same answers */
  /*dash-engine*/
  var dashE=(function(){
    var DIMS=['col','pf','cat','h'];
    function n(v){v=parseInt(v,10);return isNaN(v)?0:v}
    function rows(f,sel,ign){f=f||{};ign=ign||[];var cols=f.cols||[],dic=f.dict||{},out=[],notby=[],tests=[];
      DIMS.forEach(function(k){if(!sel[k]||ign.indexOf(k)>=0)return;var i=cols.indexOf(k);if(i<0)notby.push(k);else tests.push([i,k,String(sel[k])])});
      (f.rows||[]).forEach(function(r){var o={},ok=true;
        cols.forEach(function(c,i){var v=r[i];if(dic[c]&&typeof v==='number'&&v>=0&&v<dic[c].length)v=dic[c][v];o[c]=v});
        for(var t=0;t<tests.length;t++){var x=String(o[tests[t][1]]===null||o[tests[t][1]]===undefined?'':o[tests[t][1]]),v=tests[t][2];
          if(tests[t][1]==='h'?(!x||x.toLowerCase().indexOf(v.toLowerCase())<0):x!==v){ok=false;break}}
        if(ok)out.push(o)});
      return {rows:out,notby:notby}}
    function sum(rs,col,where){var s=0;where=where||{};rs.forEach(function(r){
      for(var k in where)if(String(r[k])!==String(where[k]))return;s+=n(r[col])});return s}
    function clash(where,sel){for(var k in (where||{}))if(sel[k]&&String(sel[k])!==String(where[k]))return true;return false}
    /* CORA: exact at the band edges (BigInt where the browser has it), tenths of a percent half up */
    function cora(a,d,o){if(!(n(a[0])||n(a[1])||n(a[2])))return null;
      var B=typeof BigInt==='function'?BigInt:function(x){return x},W=[10,4,1],num=[],den=[],wt=0,any=false;
      for(var k=0;k<3;k++){var ak=n(a[k]);num.push(ak>0?Math.min(ak,Math.max(0,ak-n(d[k]))+n(o[k])):0);den.push(ak>0?ak:1);
        if(ak>0)wt+=W[k];if(num[k])any=true}
      /* avg = N / D over the CATs that have rules */
      var N=B(0),D=B(wt);for(k=0;k<3;k++)if(n(a[k])>0)D=D*B(den[k]);
      for(k=0;k<3;k++){if(!(n(a[k])>0))continue;var t=B(W[k])*B(num[k]);for(var j=0;j<3;j++)if(j!==k&&n(a[j])>0)t=t*B(den[j]);N=N+t}
      var atl=function(p,q){return N*B(q)>=D*B(p)};          /* N/D >= p/q */
      var rating,lt20=function(k){return B(20)*B(num[k])<B(den[k])};
      if(!any)rating='Very Low';else if(num[0]===0&&lt20(1)&&lt20(2))rating='Low';else if(atl(1,5))rating='Very High';
      else if(atl(1,10))rating='High';else rating='Moderate';
      var p10=typeof BigInt==='function'?Number((B(2000)*N+D)/(B(2)*D)):Math.floor(1000*N/D+0.5);
      var needs=[];if(rating!=='Low'&&rating!=='Very Low'){if(num[0]>0)needs.push('CAT I 0');
        var rest=[];if(!lt20(1))rest.push('II');if(!lt20(2))rest.push('III');if(rest.length)needs.push('CAT '+rest.join(' and ')+' under 5%')}
      return {pct:p10/10,p10:p10,rating:rating,status:{'Very High':'critical','High':'critical','Moderate':'warning'}[rating]||'ok',
        needs:needs.length?'Low needs: '+needs.join(', '):''}}
    function coraRows(rs){var a=[0,0,0],d=[0,0,0],o=[0,0,0];rs.forEach(function(r){var k=n(String(r.cat||'').slice(1))-1;
      if(k>=0&&k<3){a[k]+=n(r.a);d[k]+=n(r.d);o[k]+=n(r.o)}});return cora(a,d,o)}
    function tile(t,D,sel){var F=D.facts||{},q,out;
      if(t.t==='fixed')return {v:t.value,notby:DIMS.filter(function(k){return sel[k]})};
      if(t.t==='cora'){q=rows(F[t.fact],sel,['cat']);var x=coraRows(q.rows);out={cora:x,notby:q.notby.concat(sel.cat?['cat']:[])};
        if(x&&t.was&&F[t.was]){var rs=q.rows,ws=rows(F[t.was],sel,['cat']).rows,ks=t.match||[];
          if(ks.length){var mk=function(r){return JSON.stringify(ks.map(function(k){return String(r[k]==null?'':r[k])}))},
            inW={},both={};ws.forEach(function(r){inW[mk(r)]=1});rs.forEach(function(r){if(inW[mk(r)])both[mk(r)]=1});
            rs=rs.filter(function(r){return both[mk(r)]});ws=ws.filter(function(r){return both[mk(r)]})}
          var xm=coraRows(rs),w=coraRows(ws);if(xm&&w)out.d=(xm.p10-w.p10)/10}return out}
      if(t.t==='pair'){q=rows(F[t.fact],sel);var c=t.cols||['a','b'],a=sum(q.rows,c[0]),b=sum(q.rows,c[1]);
        return {v:a,v2:b,notby:q.notby,status:a>=b?'ok':'warning'}}
      var where=t.where||{};if(clash(where,sel))return {off:true,v:0,notby:[]};
      q=rows(F[t.fact],sel);var v=sum(q.rows,t.col||'n',where);out={v:v,notby:q.notby};
      if(t.was&&F[t.was])out.d=v-sum(rows(F[t.was],sel).rows,t.col||'n',where);
      var st=t.status||'';out.status=v?(st==='any'?'critical':st==='any-warning'?'warning':''):(st?'ok':'');return out}
    function lc(s){return String(s||'').toLowerCase()}
    function cmp(a,b){for(var i=0;i<a.length;i++){if(a[i]<b[i])return -1;if(a[i]>b[i])return 1}return 0}
    function bars(p,D,sel){var q=rows((D.facts||{})[p.fact],sel),keys=p.stacks.map(function(s){return s[0]}),W=D.weights||{},sums={};
      q.rows.forEach(function(r){var g=String(r[p.group]),k=String(r[p.stack]),i=keys.indexOf(k);if(i<0)return;
        (sums[g]=sums[g]||keys.map(function(){return 0}))[i]+=n(r[p.col])});
      var out=[];p.groups.forEach(function(g,i){var v=sums[String(g.k)]||keys.map(function(){return 0}),t=0,s=0;
        v.forEach(function(x,j){t+=x;s+=x*(W[keys[j]]===undefined?1:n(W[keys[j]]))});if(t>0)out.push({i:i,v:v,t:t,s:s})});
      out.sort(function(a,b){return cmp([-a.s,-a.t,lc(p.groups[a.i].name)],[-b.s,-b.t,lc(p.groups[b.i].name)])});
      var mx=0;out.forEach(function(x){if(x.t>mx)mx=x.t});
      return {groups:out.slice(0,p.show||8),count:out.length,max:mx,notby:q.notby}}
    function ticks(top){top=Math.max(1,n(top));var st=[1,2,5,10,15,20,25,50,100,200,250,500,1000,2000,2500,5000,10000,20000,25000,50000,100000];
      for(var i=0;i<st.length;i++)if(st[i]*3>=top)return [0,st[i],st[i]*2,st[i]*3];var s=Math.ceil(top/3);return [0,s,2*s,3*s]}
    var SW=600,SH=150,SP=6;
    function r1(x){return Math.round(x*10)/10}
    function path(vals,top){var m=vals.length;top=Math.max(1,top);var pts=vals.map(function(v,i){return v===null?null:[r1(SP+(SW-2*SP)*(i/Math.max(1,m-1))),r1(SH-SH*v/top)]});
      var d=[],pen=false;pts.forEach(function(p){if(!p){pen=false;return}d.push((pen?'L':'M')+p[0]+' '+p[1]);pen=true});return {d:d.join(' '),pts:pts}}
    function series(p,D,sel){var f=(D.facts||{})[p.fact]||{},q=rows(f,sel),lines=[],seen=[];
      p.lines.forEach(function(ln,j){var where=ln.where||{};if(clash(where,sel))return;var eff={};
        (f.cols||[]).forEach(function(k){if(sel[k]&&k!=='h')eff[k]=String(sel[k])});for(var k in where)eff[k]=String(where[k]);
        var key=JSON.stringify(Object.keys(eff).sort().map(function(k){return [k,eff[k]]}));if(seen.indexOf(key)>=0)return;seen.push(key);
        lines.push({j:j,vals:p.have.map(function(h,i){var wr=q.rows.filter(function(r){return n(r[p.x])===i});return h&&wr.length?sum(wr,p.col,where):null})})});
      var top=0;lines.forEach(function(l){l.vals.forEach(function(v){if(v!==null&&v>top)top=v})});
      var tk=ticks(top);lines.forEach(function(l){var g=path(l.vals,tk[3]),ends=[];g.pts.forEach(function(pt,i){if(pt)ends.push([pt,l.vals[i]])});
        l.d=g.d;l.end=ends.length?[ends[ends.length-1][0][0],ends[ends.length-1][0][1],ends[ends.length-1][1]]:null;
        l.dots=ends.length<=2?ends.map(function(e){return e[0]}):[]});
      return {lines:lines,max:top,ticks:tk,ty:tk.map(function(t){return r1(SH-SH*t/tk[3])}),
        xs:p.have.map(function(h,i){return r1(SP+(SW-2*SP)*(i/Math.max(1,p.have.length-1)))}),notby:q.notby}}
    function top(p,D,sel){var q=rows((D.facts||{})[p.fact],sel),W=D.weights||{},acc={};
      q.rows.forEach(function(r){var i=n(r.i),x=acc[i]=acc[i]||{i:i,n:0,s:0,k1:0,k2:0,k3:0},m=n(r.n),k=String(r.cat);
        x.n+=m;x.s+=m*(p.score==='weight'?(W[k]===undefined?1:n(W[k])):1);if(k in x&&k!=='i'&&k!=='n'&&k!=='s')x[k]+=m});
      var out=[];for(var i in acc)if(acc[i].n>0)out.push(acc[i]);
      out.sort(function(a,b){var la=a.i<p.items.length?lc(p.items[a.i].label):'',lb=b.i<p.items.length?lc(p.items[b.i].label):'';
        return cmp([-a.s,-a.n,la],[-b.s,-b.n,lb])});
      var mx=0;out.forEach(function(x){if(x.s>mx)mx=x.s});return {rows:out.slice(0,p.show||5),count:out.length,max:mx,notby:q.notby}}
    function count(x,D,sel){var f=(D.facts||{})[x.fact]||{},keys=x.key||['h'];
      function c(rs){var seen={},k=0,m=0;rs.forEach(function(r){if(r.h){var id=JSON.stringify(keys.map(function(q){return String(r[q])}));
        if(!seen[id]){seen[id]=1;k++}}else m+=n(r.n)});return k+m}
      return {v:c(rows(f,sel).rows),all:c(rows(f,{}).rows)}}
    function view(D,sel){var s={};DIMS.forEach(function(k){if(sel&&sel[k])s[k]=String(sel[k])});
      return (D.panels||[]).map(function(p){return p.t==='meta'?{items:p.items.map(function(x){return x.t==='count'?count(x,D,s):{}})}:
        p.t==='tiles'?{tiles:p.tiles.map(function(t){return tile(t,D,s)})}:
        p.t==='bars'?bars(p,D,s):p.t==='series'?series(p,D,s):p.t==='top'?top(p,D,s):{}})}
    return {view:view,cora:cora,ticks:ticks}})();
  /*end-dash-engine*/
  all('[data-dash]').forEach(function(box){var D;try{D=JSON.parse(box.querySelector('script.dash-data').textContent)}catch(e){return}
    var CAT={1:'CAT I',2:'CAT II',3:'CAT III'},NB={col:'collection',pf:'platform',cat:'CAT',h:'host'},NS='http://www.w3.org/2000/svg';
    function el(tag,cls,txt){var e=document.createElement(tag);if(cls)e.className=cls;if(txt!==undefined&&txt!==null)e.textContent=String(txt);return e}
    function sv(tag,at){var e=document.createElementNS(NS,tag);for(var k in at)e.setAttribute(k,at[k]);return e}
    function notby(v){var p=el('p','dnb');var t=(v.notby||[]).map(function(k){return (D.notby||{})[k]||('Not by '+NB[k]+'.')});
      p.textContent=t.join(' ');p.hidden=!t.length;return p}
    function delta(box2,d,since){if(d===undefined||d===null)return;var s=el('span','dd '+(d>0?'up':d<0?'down':'same'),
      d>0?'\u25B2 '+fmt(d):d<0?'\u25BC '+fmt(-d):'same');box2.appendChild(s);if(d!==0&&since)box2.appendChild(document.createTextNode(' '+since))}
    function fmt(x){return Math.round(x)===x?String(x):x.toFixed(1)}
    function tileEl(t,v){var d,k,s;
      if(t.t==='cora'){var x=v.cora;d=el('div','dt dcora'+(x?' ds-'+x.status:''));d.setAttribute('data-t','cora');
        k=el('div','k',t.label);if(x)k.appendChild(el('span','dchip ds-'+x.status,x.rating));d.appendChild(k);
        if(x){var cr=el('div','dcr'),g=sv('svg',{'class':'ring',viewBox:'0 0 64 64',role:'img','aria-label':'CORA '+x.pct+' %'});
          g.appendChild(sv('circle',{'class':'rt',cx:32,cy:32,r:26}));
          g.appendChild(sv('circle',{'class':'rv',cx:32,cy:32,r:26,pathLength:100,'stroke-dasharray':Math.min(100,Math.max(x.pct,1.5))+' 100',transform:'rotate(-90 32 32)'}));
          var tx=sv('text',{x:32,y:36.5});tx.textContent=x.pct.toFixed(1)+'%';g.appendChild(tx);cr.appendChild(g);
          var ct=el('div','dct');ct.appendChild(el('div','dbig ds-'+x.status,x.rating));ct.appendChild(el('div','dsub','weighted open + unassessed'));
          if(v.d!==undefined&&v.d!==null){s=el('div','dsub');delta(s,v.d,'');if(v.d!==0)s.appendChild(document.createTextNode(' pts'+(t.since?' '+t.since:'')));ct.appendChild(s)}
          if(x.needs)ct.appendChild(el('div','dsub dneed',x.needs));cr.appendChild(ct);d.appendChild(cr)}
        else{d.appendChild(el('div','dbig','-'));d.appendChild(el('div','dsub','no counts for this choice'))}}
      else if(t.t==='pair'){d=el('div','dt dpair'+(v.status?' ds-'+v.status:''));d.setAttribute('data-t','pair');d.appendChild(el('div','k',t.label));
        k=el('div','dv');k.appendChild(el('span','dbig c-ok',v.v));k.appendChild(document.createTextNode(' '));k.appendChild(el('span','dw',t.words[0]));d.appendChild(k);
        s=el('div','dsub');s.appendChild(el('span',v.v2>v.v?'c-warning':'',v.v2));s.appendChild(document.createTextNode(' '+t.words[1]+(t.since?' '+t.since:'')));d.appendChild(s)}
      else if(t.t==='fixed'){d=el('div','dt dfix');d.setAttribute('data-t','fixed');k=el('div','k');k.appendChild(el('span','d c-'+(t.status||'unknown')));
        k.appendChild(document.createTextNode(t.label));d.appendChild(k);k=el('div','dv');k.appendChild(el('span','dbig',t.value));d.appendChild(k);
        d.appendChild(el('div','dsub',t.note||''))}
      else{d=el('div','dt dsum'+(t.cat?' k'+t.cat:'')+(v.off?' off':''));d.setAttribute('data-t','sum');k=el('div','k');
        if(t.cat)k.appendChild(el('span','cdot k'+t.cat));k.appendChild(document.createTextNode(t.label));d.appendChild(k);
        k=el('div','dv');k.appendChild(el('span','dbig'+(t.cat===1&&(v.d||0)>0?' kc1':''),v.off?'-':v.v));d.appendChild(k);
        s=el('div','dsub');if(v.off)s.textContent='not in the CAT chosen';else delta(s,v.d,t.since||'');d.appendChild(s)}
      d.appendChild(notby(v));return d}
    function link(h){if(String(h||'').slice(0,4)!=='sec:')return h||'';var want=h.slice(4),hit='';
      all('details.sec').forEach(function(s){var t=s.querySelector('summary h2');if(!hit&&t&&t.firstChild&&String(t.firstChild.textContent||'').trim().indexOf(want)===0)hit='#'+s.id});
      return hit}
    function more(m,cnt){var u=link(m.href||'');if(!u)return null;var a=el('a','dmore',m.label||'All');a.href=u;a.setAttribute('data-drill','');
      if(cnt!==null){a.appendChild(document.createTextNode(' '));a.appendChild(el('span','dn',cnt))}
      a.appendChild(arrow());return a}
    function arrow(){var g=sv('svg',{'class':'ic',viewBox:'0 0 16 16','aria-hidden':'true'});
      g.appendChild(sv('path',{d:'M6 3.5L10.5 8 6 12.5',fill:'none',stroke:'currentColor','stroke-width':'1.8','stroke-linecap':'round','stroke-linejoin':'round'}));return g}
    function head(p,cnt,note){var h=el('div','dhead');h.appendChild(el('h3','dh',p.title));
      if(note)h.appendChild(el('span','dhn',note));else{var m=more(p.more||{},cnt);if(m)h.appendChild(m)}return h}
    function legend(items){var l=el('div','dleg');items.forEach(function(x){var s=el('span');var i=el('i','sw '+x[0]);i.setAttribute('aria-hidden','true');
      s.appendChild(i);s.appendChild(document.createTextNode(x[1]));l.appendChild(s)});return l}
    function drawBars(sec,p,v){sec.textContent='';sec.appendChild(head(p,null));
      sec.appendChild(legend(p.stacks.map(function(s){return ['k'+s[2],s[1]]})));var bl=el('div','dbl');
      if(!v.groups.length)bl.appendChild(el('p','dnone','Nothing open for this choice.'));
      v.groups.forEach(function(g){var gr=p.groups[g.i],r=el('div','dbr'),a=el('a','dbn',gr.name);a.href=gr.href||'#';a.title=gr.name;a.setAttribute('data-drill','');
        r.appendChild(a);var t=el('span','dbt');t.setAttribute('role','img');
        t.setAttribute('aria-label',p.stacks.map(function(s,j){return s[1]+' '+g.v[j]}).join(', '));
        var b=el('span','dbb');b.style.width=(100*g.t/Math.max(v.max,1)).toFixed(1)+'%';
        p.stacks.forEach(function(s,j){if(g.v[j]){var i=el('i','k'+s[2]);i.style.flexGrow=String(g.v[j]);b.appendChild(i)}});
        t.appendChild(b);r.appendChild(t);r.appendChild(el('span','dbv',g.v.join(' \u00B7 ')));bl.appendChild(r)});
      sec.appendChild(bl);if(v.count>v.groups.length)sec.appendChild(el('p','dnb',(v.count-v.groups.length)+' more in the section below.'));
      sec.appendChild(notby(v))}
    function cls(l){return l.cat?' k'+l.cat:' all'}
    function drawSeries(sec,p,v){sec.textContent='';sec.appendChild(head(p,null,p.note));
      sec.appendChild(legend(v.lines.map(function(ln){var l=p.lines[ln.j];return [l.cat?'k'+l.cat:'all',l.name]})));
      var ch=el('div','dch'),g=sv('svg',{'class':'dsvg',viewBox:'-30 -8 666 182',role:'img',tabindex:'0',
        'aria-label':p.title+': '+v.lines.map(function(ln){var last=ln.vals.filter(function(x){return x!==null});return p.lines[ln.j].name+' '+(last.length?last[last.length-1]:'-')}).join('; ')});
      v.ty.forEach(function(y,i){g.appendChild(sv('line',{'class':'gl',x1:0,x2:600,y1:y,y2:y}));var t=sv('text',{'class':'yl',x:-8,y:y+4});t.textContent=v.ticks[i];g.appendChild(t)});
      v.lines.forEach(function(ln){var l=p.lines[ln.j];g.appendChild(sv('path',{'class':'ln'+cls(l),d:ln.d}));
        ln.dots.forEach(function(q){g.appendChild(sv('circle',{'class':'dot'+cls(l),cx:q[0],cy:q[1],r:3}))});
        if(ln.end){g.appendChild(sv('circle',{'class':'end'+cls(l),cx:ln.end[0],cy:ln.end[1],r:3.5}));
          var t=sv('text',{'class':'el',x:ln.end[0]-6,y:ln.end[1]-8});t.textContent=ln.end[2];g.appendChild(t)}});
      var m=p.heads.length,ix=[0,Math.floor((m-1)/2),m-1].filter(function(x,i,a){return a.indexOf(x)===i});
      ix.forEach(function(i,j){var t=sv('text',{'class':'xl'+(j===0?' a0':(j===ix.length-1?' a1':'')),x:v.xs[i],y:170});t.textContent=p.heads[i];g.appendChild(t)});
      var gd=sv('line',{'class':'gd',x1:0,x2:0,y1:0,y2:150});gd.setAttribute('visibility','hidden');g.appendChild(gd);
      ch.appendChild(g);var tip=el('div','dtip');tip.hidden=true;ch.appendChild(tip);sec.appendChild(ch);hover(g,gd,tip,p,v);
      var dt=el('details','dtab');dt.appendChild(el('summary','','Table'));var tb=el('table','dtt'),th=el('thead'),tr=el('tr');
      tr.appendChild(el('th','','Week'));v.lines.forEach(function(ln){tr.appendChild(el('th','k-num',p.lines[ln.j].name))});th.appendChild(tr);tb.appendChild(th);
      var bd=el('tbody');p.heads.forEach(function(h,i){var r=el('tr');r.appendChild(el('td','',h));
        v.lines.forEach(function(ln){r.appendChild(el('td','k-num',ln.vals[i]===null?'-':ln.vals[i]))});bd.appendChild(r)});
      tb.appendChild(bd);dt.appendChild(tb);var u=link(p.table_href||'');
      if(u){var a=el('a','dmore','Every collection, week by week');a.href=u;a.setAttribute('data-drill','');a.appendChild(arrow());dt.appendChild(a)}
      sec.appendChild(dt);sec.appendChild(notby(v))}
    /* the weekly line's hover: the nearest week with a run - a guide, and its values in a tip; the arrow keys too */
    function hover(g,gd,tip,p,v){var cur=-1,have=[];p.have.forEach(function(h,i){if(h)have.push(i)});if(!have.length)return;
      function at(i){cur=i;var x=v.xs[i];gd.setAttribute('x1',x);gd.setAttribute('x2',x);gd.setAttribute('visibility','visible');
        tip.textContent='';tip.appendChild(el('b','',p.heads[i]));
        v.lines.forEach(function(ln){tip.appendChild(el('div','',p.lines[ln.j].name+': '+(ln.vals[i]===null?'-':ln.vals[i])))});
        tip.hidden=false;var r=g.getBoundingClientRect(),w=tip.offsetWidth,px=(x+30)/666*r.width;
        tip.style.left=Math.max(0,Math.min(r.width-w,px-w/2))+'px'}
      function off(){cur=-1;gd.setAttribute('visibility','hidden');tip.hidden=true}
      function near(cx){var r=g.getBoundingClientRect(),vx=-30+(cx-r.left)*666/r.width,b=have[0];
        have.forEach(function(i){if(Math.abs(v.xs[i]-vx)<Math.abs(v.xs[b]-vx))b=i});return b}
      g.addEventListener('pointermove',function(e){at(near(e.clientX))});g.addEventListener('pointerleave',off);g.addEventListener('blur',off);
      g.addEventListener('focus',function(){at(have[have.length-1])});
      g.addEventListener('keydown',function(e){var j=have.indexOf(cur);if(e.key==='ArrowLeft'&&j>0){at(have[j-1]);e.preventDefault()}
        else if(e.key==='ArrowRight'&&j<have.length-1){at(have[j+1]);e.preventDefault()}else if(e.key==='Home'){at(have[0]);e.preventDefault()}
        else if(e.key==='End'){at(have[have.length-1]);e.preventDefault()}else if(e.key==='Escape')off()})}
    function drawTop(sec,p,v){sec.textContent='';sec.appendChild(head(p,v.count));var tb=el('table','dtl'),th=el('thead'),tr=el('tr');
      p.columns.forEach(function(c){var h=el('th',(c.num?'k-num':'')+(c.bar?' dwc':''),c.h);h.setAttribute('scope','col');tr.appendChild(h)});
      th.appendChild(tr);tb.appendChild(th);var bd=el('tbody');
      v.rows.forEach(function(x){var it=p.items[x.i],r=el('tr');p.columns.forEach(function(c){var td;
        if(c.v==='label'){td=el('td','dlab');var a=el('a','',it.label);a.href=it.href||'#';a.setAttribute('data-drill','');a.setAttribute('data-open','');
          a.title=(it.text||'')+' - its row below';td.appendChild(a)}
        else if(c.v==='cat'){td=el('td');var s=el('span','catc k'+it.cat);var i=el('i');i.setAttribute('aria-hidden','true');s.appendChild(i);
          s.appendChild(document.createTextNode(CAT[it.cat]));td.appendChild(s)}
        else if(c.v==='text'){td=el('td','dtx',it.text||'');td.title=it.text||''}
        else if(c.bar){td=el('td','k-num dwb');td.appendChild(el('span','dwv',x[c.v]));var w=el('span','dwt'),b=el('i','k'+(it.cat||2));
          b.style.width=(100*x[c.v]/Math.max(v.max,1)).toFixed(1)+'%';w.appendChild(b);td.appendChild(w)}
        else td=el('td','k-num'+(c.cat&&x[c.v]?' kc'+c.cat:''),x[c.v]);r.appendChild(td)});bd.appendChild(r)});
      if(!v.rows.length){var r=el('tr'),td=el('td','dnone','Nothing open for this choice.');td.colSpan=p.columns.length;r.appendChild(td);bd.appendChild(r)}
      tb.appendChild(bd);sec.appendChild(tb);sec.appendChild(notby(v))}
    function draw(sel){var vs=dashE.view(D,sel||{});
      (D.panels||[]).forEach(function(p,i){var sec=box.querySelector('[data-p="'+i+'"]');if(!sec)return;var v=vs[i];
        if(p.t==='meta'){sec.textContent='';p.items.forEach(function(x,j){var y=v.items[j];
          sec.appendChild(el('span','',x.t==='count'?(y.v!==y.all?y.v+' of ':'')+y.all+' '+(y.all===1?x.word:x.words):x.text))})}
        else if(p.t==='tiles'){sec.textContent='';p.tiles.forEach(function(t,j){sec.appendChild(tileEl(t,v.tiles[j]))})}
        else if(p.t==='bars')drawBars(sec,p,v);else if(p.t==='series')drawSeries(sec,p,v);else if(p.t==='top')drawTop(sec,p,v)})}
    draw({});
    document.addEventListener('site-facets',function(e){draw(e.detail||{})});
    /* a link to a row below (a rule, a host): its section opened, the row opened under it (its check, fix text and hosts) */
    box.addEventListener('click',function(e){if(e.button||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;var a=e.target.closest&&e.target.closest('a[data-drill]');if(!a)return;
      var id=(a.getAttribute('href')||'').slice(1),t=id&&document.getElementById(id);if(!t)return;
      var sec=t.closest('details.sec');if(sec&&!sec.open)sec.open=true;
      if(a.hasAttribute('data-open')&&t.tagName==='TR'&&(t._x||t._detail)&&!t._o){var b=t.querySelector('.xb');if(b)b.click()}})});
  /* the search: every section's rows, lines and cards that have the words, the others put away; a section without
     any, away too (all of it when its title has them); the matches marked where the browser can (CSS highlights) */
  var find=document.getElementById('find');
  if(find){var box=find.closest('.find'),found=box.querySelector('.found'),secs=all('details.sec'),tm,
      HL=window.CSS&&CSS.highlights&&window.Highlight;
    box.hidden=false;
    /* what an item says, in lower case, once: a row its cells; a line or a card its words but its buttons' (Copy) */
    var low=function(el){if(el._t===undefined){var detail=el._detail,base='';
        if(el.tagName==='TR'&&el.cells){for(var ci=0;ci<el.cells.length;ci++)if(!el.cells[ci].classList.contains('srv-copy-td'))base+=' '+(el.cells[ci].textContent||'')}
        else base=el.textContent||'';
        var s=base+(detail?' '+detailText(detail):el._x?' '+el._x.textContent:'');   /* a row: what opens under it too */
        if(el.tagName!=='TR'){s='';var w=document.createTreeWalker(el,NodeFilter.SHOW_TEXT,null),t;
          while((t=w.nextNode()))if(!t.parentNode.closest('button,.copy-split,.copy-menu'))s+=t.nodeValue+' '}
        el._t=s.toLowerCase()}return el._t};
    /* a section's title, without its row count */
    var title=function(d){var h=d.querySelector('summary h2'),f=h&&h.firstChild;
      return (f&&f.nodeType===3?f.textContent:(h?h.textContent:'')).toLowerCase()};
    var mark=function(q){if(!HL)return;CSS.highlights['delete']('site-find');if(!q)return;var h=new Highlight(),n=0;
      secs.forEach(function(d){if(d.hidden||n>=3000)return;var w=document.createTreeWalker(d.querySelector('.body')||d,NodeFilter.SHOW_TEXT,null),t;
        while(n<3000&&(t=w.nextNode())){var pe=t.parentNode;if(pe.closest('[hidden],button,svg,script,summary,.tools2,.scopy-bar,.copy-split,.copy-menu,.srv-copy-td'))continue;
          var s=t.nodeValue.toLowerCase(),i=s.indexOf(q);
          while(i>=0&&n<3000){var r=document.createRange();r.setStart(t,i);r.setEnd(t,i+q.length);h.add(r);n++;i=s.indexOf(q,i+q.length)}}});
      CSS.highlights.set('site-find',h)};
    var run=function(){var q=find.value.trim().toLowerCase(),total=0,ns=0,others=0;gQ=!!q;capAll();
      secs.forEach(function(d){var body=d.querySelector('.body'),k=0,km=0;if(!body)return;
        var whole=!q||title(d).indexOf(q)>=0;
        all('.tw tbody tr',body).forEach(function(r){if(r.classList.contains('xr')||r.closest('.xr')||r.classList.contains('pt-fold'))return;
          var on=whole||low(r).indexOf(q)>=0;r._s=!on;vis(r);if(on&&!r.hidden)k++;
          /* 1.86.0 review: a match folded away only as another team's - counted, so its section and the fold button stay */
          else if(on&&r._tm&&!(r._f||r._p||r._d||r._ot||r._sv))km++});
        all('ul.lines>li,ul.passed>li,.todo',body).forEach(function(el){var on=whole||low(el).indexOf(q)>=0;if(el.hidden===on)el.hidden=!on;if(on)k++});
        [].forEach.call(body.children,function(el){if((el.tagName==='P'||el.classList.contains('snote'))&&low(el).indexOf(q)>=0)k++});
        /* 1.104.0 (Neon): the cause cards - a card that matches stays (a folded one opens; it folds again when the search is
           cleared), the others step aside; the overall line counts as text */
        all('.causes .cz',body).forEach(function(c){var on=whole||low(c).indexOf(q)>=0;if(c.hidden===on)c.hidden=!on;if(!q||!on)return;k++;
          if(c.tagName==='DETAILS'&&!c.open&&!whole){c.open=true;c._fo=1}});
        all('.causes .cz',body).forEach(function(c){if(!q&&c._fo){c.open=false;c._fo=0}});
        all('.causes .cz-ov',body).forEach(function(el){if(q&&low(el).indexOf(q)>=0)k++});
        var away=!!q&&!k&&!km&&!whole;if(d.hidden!==away)d.hidden=away;
        if(q&&!d.hidden){d.open=true;total+=k;others+=km;ns++}
        (links[d.id]||[]).forEach(function(a){var m=a.querySelector('.m');a.classList.toggle('off',d.hidden);
          if(m)m.textContent=q&&!d.hidden?String(k):''})});
      capAll();                                                  /* the folded rows again, from the rows the search left */
      found.textContent=q?(ns?total+' found in '+ns+' section'+(ns===1?'':'s')+(others?', '+others+' more in other teams':''):'Nothing found'):'';
      if(window.reportPopFold)window.reportPopFold();               /* the fold buttons count the other teams' matches */
      box.classList.toggle('none-found',!!q&&!ns);
      counts.forEach(function(c){c()});refreshCopyAllCounts();mark(q);
      if(window.requestAnimationFrame)requestAnimationFrame(function(){fitRows(document)});else fitRows(document)};
    find.addEventListener('input',function(){clearTimeout(tm);tm=setTimeout(run,150)});
    find.addEventListener('search',run);
    find.addEventListener('keydown',function(e){
      if(e.key==='Escape'){find.value='';run();find.blur()}
      else if(e.key==='Enter'){e.preventDefault();clearTimeout(tm);run();
        var s=secs.filter(function(d){return !d.hidden})[0];if(s&&find.value.trim())s.scrollIntoView()}});
    /* / or Ctrl+K: to the search box */
    document.addEventListener('keydown',function(e){var a=document.activeElement,tag=a&&a.tagName||'';
      if(((e.key==='/'&&!e.ctrlKey&&!e.metaKey&&!e.altKey)||((e.ctrlKey||e.metaKey)&&(e.key==='k'||e.key==='K')))&&
         !/^(INPUT|TEXTAREA|SELECT)$/.test(tag)&&!(a&&a.isContentEditable)){e.preventDefault();find.focus();find.select()}})}
  /* the hover layer (1.57.0): one tip (.tt, a tooltip the elements name with aria-describedby) for every element with
     data-tip (its lines, the first in bold) and every sparkline (svg.spark with data-points: a guide and a dot on the
     nearest day, its value and day in the tip; the arrow keys, Home and End step through the days). It shows on hover,
     on keyboard focus and on a tap; Esc, a tap elsewhere, leaving it or scrolling hides it (the focused one's moves with
     it); it stays inside the window.
     An element's title is taken off (no second tip). Every tip says what the page has elsewhere - the line's numbers
     are in the table under it */
  /* 1.76.2, the outage review's time line: the controls above the chart (Show All / Related only, the kinds, Show every repeat) filter
     the marks and The events; the choice is kept in this browser (localStorage, in try/catch). Folded repeats: identical rows one after
     another show as one, "x4, 17:21-17:40", the times in its title. A mark's events (data-ev) feed its tip's list (below). */
  var OT={show:'all',off:{},rep:false},OTK='site-report-outage',otMarks=all('.ot-m[data-ev]'),otCtl=document.querySelector('[data-ot-ctl]');
  var otRows=all('tr[data-cls]');
  var OTLV=['critical','warning','low','info','ok'],OTR={critical:4,warning:3,low:2,info:1,ok:0},OTW={critical:'Critical',warning:'Warning',low:'Low',info:'Note',ok:'Back to normal'};
  otMarks.forEach(function(m){try{m._ev=JSON.parse(m.getAttribute('data-ev'))}catch(e){m._ev=null}
    var b=m.querySelector('b');m._b=b;m._lab0=b?b.textContent:'';m._svg0=m.querySelector('svg');m._tip0=m.getAttribute('data-tip')||'';m._aria0=m.getAttribute('aria-label')||'';
    m._lv0=OTLV.filter(function(l){return m.classList.contains('ot-'+l)})[0]||'info';m._sub=m.classList.contains('ot-rel')||m.classList.contains('ot-dim')});
  /* 1.90.1: the host / vCenter / VM row above the lanes - its choices only the page's own events (data-ev: h, v, vm); it narrows the marks,
     their lists, The events, the lanes' and the kinds' counts with the controls above. Kept nowhere: nothing in the address, nothing stored */
  var OTF={h:'',v:'',vm:''},otByN={},otEvs=[],otEl=document.querySelector('.ot'),otHook={},otShown=null,otClear=null;
  otMarks.forEach(function(m){(m._ev||[]).forEach(function(e){otByN[e.n]=e;otEvs.push(e)})});
  /* 1.90.2: a host by the event's own host (h: also a ticket's host or a host alarm's entity), never by where it happened; a vCenter by its
     full name (v), shown short when that is not ambiguous (otVn); a VM: any VM the event stands for (vm and, for a grouped row, vms).
     A grouped row with several hosts or VMs (mem: [host, VM] pairs) matches when one pair matches both. */
  var otVms=function(e){return (e.vm?[e.vm]:[]).concat(e.vms||[])};
  var otHs=function(e){var o=e.h?[e.h]:[];(e.mem||[]).forEach(function(m){if(m[0]&&o.indexOf(m[0])<0)o.push(m[0])});return o};
  var otPass=function(e){var q=OTF.vm.toLowerCase(),has=function(x){return String(x||'').toLowerCase().indexOf(q)>=0};
    if(OTF.v&&e.v!==OTF.v)return false;
    if(e.mem)return e.mem.some(function(m){return !(OTF.h&&m[0]!==OTF.h)&&!(q&&!has(m[1]))});    /* a grouped row: one restart's own host and VM */
    return !(OTF.h&&e.h!==OTF.h)&&!(q&&!otVms(e).some(has))};
  var otVn={};otEvs.forEach(function(e){if(e.v)otVn[e.v]=/^[\d.]+$|:/.test(e.v)?e.v:e.v.split('.')[0]});    /* an address stays whole */
  (function(){var c={};Object.keys(otVn).forEach(function(k){c[otVn[k]]=(c[otVn[k]]||0)+1});Object.keys(otVn).forEach(function(k){if(c[otVn[k]]>1)otVn[k]=k})})();
  var otV=function(v){return v?otVn[v]||v:''};
  var otVisible=function(list){return list.filter(function(e){return !(OT.show==='rel'&&!e.r)&&!OT.off[e.c]&&otPass(e)})};
  /* the counts: a lane "4 of 29 events" while something narrows it, the kinds' counts within the host / vCenter / VM, "5 of 32 events" */
  var otCount=function(){if(!otEl)return;var on=!!(OTF.h||OTF.v||OTF.vm),kinds={},n=0;
    otEvs.forEach(function(e){if(otPass(e))kinds[e.c]=(kinds[e.c]||0)+1;if(otVisible([e]).length)n++});
    all('.ot-row',otEl).forEach(function(r){var sub=r.querySelector('.ot-name small'),ms=all('.ot-m[data-ev]',r),a=0,v=0;if(!sub||!ms.length)return;
      ms.forEach(function(m){a+=(m._ev||[]).length;v+=otVisible(m._ev||[]).length});if(sub._t0===undefined)sub._t0=sub.textContent;
      sub.textContent=v<a?v+' of '+a+(a===1?' event':' events'):sub._t0;sub.classList.toggle('f-of',v<a)});
    if(otCtl)all('input[name=ot-kind]',otCtl).forEach(function(i){var k=i.parentNode.querySelector('small');if(!k)return;if(k._t0===undefined)k._t0=k.textContent;
      k.textContent=on?String(kinds[i.value]||0):k._t0});
    if(otShown){otShown.textContent='';var b=document.createElement('b');b.textContent=String(n);otShown.appendChild(b);
      otShown.appendChild(document.createTextNode(' of '+otEvs.length+(otEvs.length===1?' event':' events')));otClear.hidden=!on}
    if(otHook.re)otHook.re()};
  /* a mark that stands for several events shows what is left of them: its number (first+others), its worst level and icon, whether the
     rest are related (the outline), and its tip and aria text - as it was when all of them show */
  var otMark=function(m,v){
    var full=v.length===m._ev.length,vs=v.slice().sort(function(a,b){return a.n-b.n}),lv=m._lv0,lab=m._lab0,tip=m._tip0,aria=m._aria0,rel=null;
    if(!full){lv=vs.reduce(function(a,e){return (OTR[e.l]||0)>(OTR[a]||0)?e.l:a},vs[0].l);if(OTLV.indexOf(lv)<0)lv='info';
      lab=vs.length===1?String(vs[0].n):vs[0].n+'+'+(vs.length-1);
      tip=vs.map(function(e){return e.n+'  '+e.t+'  '+(OTW[e.l]||'Note')+'\n'+e.w+(e.p?' - '+e.p:'')}).join('\n');aria=tip.replace(/\n/g,', ')}
    OTLV.forEach(function(l){m.classList.toggle('ot-'+l,l===lv)});
    if(m._sub){rel=full?null:(vs.every(function(e){return e.r})?2:vs.some(function(e){return e.r})?1:0);
      if(rel===null){m.classList.toggle('ot-rel',m._rel0>0);m.classList.toggle('ot-dim',!(m._rel0>0))}
      else{m.classList.toggle('ot-rel',rel>0);m.classList.toggle('ot-dim',!(rel>0))}}
    if(m._b&&m._b.textContent!==lab)m._b.textContent=lab;
    var cur=m.querySelector('svg'),ic=full?m._svg0:(document.querySelector('.ot-legend .ot-'+lv+' svg')||cur);
    if(cur&&ic&&cur!==ic){var nw=full?ic:ic.cloneNode(true);m.replaceChild(nw,cur)}
    m.setAttribute('data-tip',tip);m.setAttribute('aria-label',aria)};
  otMarks.forEach(function(m){m._rel0=m.classList.contains('ot-rel')?1:0});
  var otApply=function(changed){
    var filtered=OT.show!=='all'||Object.keys(OT.off).length>0||!!(OTF.h||OTF.v||OTF.vm);      /* 1.90.1: the host / vCenter / VM row too */
    var tables=all('.tw table').filter(function(tb){return tb.querySelector('tr[data-cls]')});
    tables.forEach(function(tb){if(tb._expand&&(changed||filtered||!OT.rep))tb._expand();
      tb._otFree=filtered||(!OT.rep&&!!tb.querySelector('tr[data-rep]'))});
    otMarks.forEach(function(m){if(!m._ev)return;var v=otVisible(m._ev);m.classList.toggle('ot-off',!v.length);m.tabIndex=v.length?0:-1;      /* 1.90.2: out of the Tab order at once */
      if(v.length)otMark(m,v);else if(document.activeElement===m)m.blur()});
    otRows=all('tr[data-cls]');var wi=-1;otRows.forEach(function(tr){var t=tr.closest('table');if(t&&wi<0){var h=all('thead th',t).map(function(x){return x.textContent.trim()});wi=h.findIndex(function(x){return x.indexOf('What happened')===0})}
      var ev=otByN[tr.getAttribute('data-num')]||{r:tr.getAttribute('data-rel')==='1'?1:0,c:tr.getAttribute('data-cls')};
      var hide=!otVisible([ev]).length||(tr.hasAttribute('data-rep')&&!OT.rep);if(!!tr._ot!==hide){tr._ot=hide;vis(tr)}      /* one more input to vis(), beside the table's filter and the search */
      var f=tr.getAttribute('data-fold'),td=wi>=0?tr.cells[wi]:null;
      if(f&&td){var q=f.split('|'),sx=td._sx;if(!sx){sx=td._sx=document.createElement('span');sx.className='ot-fold';td.appendChild(sx)}      /* its own node: the cell's link stays */
        sx.textContent=OT.rep?'':' \u00d7'+q[0]+', '+q[1]+(q[2]!==q[1]?'-'+q[2]:'');tr._t=undefined;if(OT.rep)td.removeAttribute('title');else td.title=q[3]}});
    capAll();
    counts.forEach(function(c){c()});
    if(find&&find.value.trim()&&typeof run==='function')run();
    else if(tables.length)fitRows(document);
    if(otCtl){all('input[name=ot-show]',otCtl).forEach(function(i){i.checked=i.value===OT.show});
      all('input[name=ot-kind]',otCtl).forEach(function(i){i.checked=!OT.off[i.value]});var r=otCtl.querySelector('input[name=ot-rep]');if(r)r.checked=OT.rep}
    otCount()};
  var otSave=function(){try{localStorage.setItem(OTK,JSON.stringify({show:OT.show,off:OT.off,rep:OT.rep}))}catch(e){}};
  if(otEl&&otEvs.length){
    var otU=function(f){var o=[];otEvs.forEach(function(e){var x=f(e);if(x&&o.indexOf(x)<0)o.push(x)});return o.sort()};
    var oh=[],ov=otU(function(e){return e.v}),om=[];otEvs.forEach(function(e){otHs(e).forEach(function(x){if(oh.indexOf(x)<0)oh.push(x)})});oh.sort();
    otEvs.forEach(function(e){otVms(e).forEach(function(x){if(om.indexOf(x)<0)om.push(x)})});om.sort();
    if(oh.length>1||ov.length>1||om.length>1){      /* one host, one vCenter, one VM: nothing to choose - no row */
      var fr=document.createElement('div');fr.className='otf';fr.setAttribute('role','group');fr.setAttribute('aria-label','Filter the time line by host, vCenter or VM');
      var otSel=function(cls,name,list,word,key){if(list.length<2)return null;var l=document.createElement('label');l.className=cls;l.appendChild(document.createTextNode(name));
        var x=document.createElement('select');x.appendChild(new Option(word+' ('+list.length+')',''));list.forEach(function(v){x.appendChild(new Option(key==='v'?otV(v):v,v))});
        x.addEventListener('change',function(){OTF[key]=x.value;otApply()});l.appendChild(x);fr.appendChild(l);return x};
      var fh=otSel('f-h','Host',oh,'All hosts','h'),fv=otSel('f-v','vCenter',ov,'All vCenters','v'),fm=null;
      if(om.length>1){var lm=document.createElement('label');lm.className='f-vm';lm.appendChild(document.createTextNode('VM'));
        fm=document.createElement('input');fm.type='search';fm.placeholder='Any of '+om.length+' VMs';fm.autocomplete='off';fm.setAttribute('list','otf-vms');
        var dl=document.createElement('datalist');dl.id='otf-vms';om.forEach(function(v){dl.appendChild(new Option(v))});
        fm.addEventListener('input',function(){OTF.vm=fm.value.trim();otApply()});lm.appendChild(fm);lm.appendChild(dl);fr.appendChild(lm)}
      var fo=document.createElement('div');fo.className='f-out';otShown=document.createElement('span');otShown.setAttribute('aria-live','polite');
      otClear=document.createElement('button');otClear.type='button';otClear.textContent='Clear';otClear.hidden=true;
      otClear.addEventListener('click',function(){OTF={h:'',v:'',vm:''};[fh,fv,fm].forEach(function(x){if(x)x.value=''});otApply();var f0=fh||fv||fm;if(f0)f0.focus()});
      fo.appendChild(otShown);fo.appendChild(otClear);fr.appendChild(fo);
      var ob=document.querySelector('.ot-over')||otEl;ob.parentNode.insertBefore(fr,ob)}
    /* motion (html.pop-move: report_motion, the system's reduced-motion wish): the marks come in left to right once the time line is in view,
       the lead-up draws in back from its critical event, its tab shows, that event pings once. Still: all of it is simply there */
    if(document.documentElement.classList.contains('pop-move')&&'IntersectionObserver' in window){
      otEl.classList.add('ot-wait');otMarks.forEach(function(m){m.style.setProperty('--ot-d',Math.round((parseFloat(m.style.left)||0)*9)+'ms')});
      var otIo=new IntersectionObserver(function(es){if(!es.some(function(x){return x.isIntersecting}))return;otIo.disconnect();
        (window.requestAnimationFrame||setTimeout)(function(){otEl.classList.add('ot-go');otEl.classList.remove('ot-wait');
          setTimeout(function(){otEl.classList.remove('ot-go');otMarks.forEach(function(m){m.style.removeProperty('--ot-d')})},2700)})},{threshold:0.2});
      otIo.observe(otEl)}}
  if(otCtl){
    try{var sv=JSON.parse(localStorage.getItem(OTK)||'null');if(sv&&typeof sv==='object'){if(sv.show==='rel'&&otCtl.querySelector('input[value=rel]'))OT.show='rel';
      if(sv.off&&typeof sv.off==='object')OT.off=sv.off;OT.rep=!!sv.rep}}catch(e){}
    otCtl.hidden=false;otApply();
    otCtl.addEventListener('change',function(e){var i=e.target;if(i.name==='ot-show')OT.show=i.value;else if(i.name==='ot-kind'){if(i.checked)delete OT.off[i.value];else OT.off[i.value]=true}
      else if(i.name==='ot-rep')OT.rep=i.checked;otSave();otApply(true)})}
  else if(otRows.length){otApply()}
  var tips=all('[data-tip]'),sparks=all('svg.spark[data-points]');
  if(tips.length||sparks.length||document.querySelector('script.defer-main,script.defer-detail,script.defer-mini')){
    var tt=document.createElement('div'),owner=null,pinned=null,NS='http://www.w3.org/2000/svg';
    tt.className='tt';tt.id='site-tip';tt.setAttribute('role','tooltip');tt.hidden=true;document.body.appendChild(tt);
    var place=function(x,y){var w=tt.offsetWidth,h=tt.offsetHeight,vw=document.documentElement.clientWidth,vh=window.innerHeight,
        lo=8,hi=vw-w-8,box=owner&&owner.closest&&tt.classList.contains('tt-list')?owner.closest('.ot-plot'):null;
      if(box){var b=box.getBoundingClientRect();lo=Math.max(8,b.left);hi=Math.min(hi,b.right-w);if(hi<lo)hi=Math.max(8,vw-w-8),lo=8}
      var l=Math.min(Math.max(lo,x-w/2),hi),t=y-h-12;if(t<8)t=Math.min(y+16,vh-h-8);
      tt.style.transform='translate('+Math.round(Math.max(8,l))+'px,'+Math.round(Math.max(8,t))+'px)'};
    var hide=function(){var o=owner;if(owner&&owner._off)owner._off();owner=null;pinned=null;tt.hidden=true;tt.className='tt';tt.removeAttribute('aria-label');tt.removeAttribute('tabindex')};
    var show=function(el,lines,x,y){if(owner&&owner!==el&&owner._off)owner._off();owner=el;tt.className='tt';tt.textContent='';
      lines.forEach(function(l,i){var d=document.createElement('div');if(!i)d.className='tt-h';d.textContent=l;tt.appendChild(d)});
      tt.hidden=false;place(x,y)};
    /* a mark's events as a list: a header ("26 events . 17:20-17:24"), one line each - time, level icon, what, where - the critical first, then by
       time; each line links to its row in The events. Hover shows it; a click (or Enter) pins it so it can be scrolled; Esc or a click elsewhere closes */
    var RANKS={critical:4,warning:3,low:2,info:1,ok:0};
    var rowFor=function(n){liftFor('#ot-row-'+n);var tr=document.getElementById('ot-row-'+n);if(tr&&tr.hasAttribute('data-rep')&&!OT.rep){var h=document.getElementById(tr.getAttribute('data-head')||'');if(h)tr=h}return tr};      /* a folded repeat: its head's row (data-head), whatever the sort */
    /* an incident link (data-ev): parsed - https, a host, no user or password, no spaces or markup characters - as the report's safe_link asks */
    var okUrl=function(u){if(typeof u!=='string'||!u||u.length>2000||/[\s\x00-\x1f\x7f<>"'`\\]/.test(u))return false;
      if(!/^https:\/\/[^\/?#\\]/i.test(u))return false;      /* the host is written out: URL() would read https:///x as host x */
      try{var a=new URL(u);return a.protocol==='https:'&&!!a.hostname&&!a.username&&!a.password&&u.replace(/^https:\/\//i,'').split(/[\/?#]/)[0].indexOf('@')<0}catch(x){return false}};
    /* to row n of The events: the search that hides it is cleared, a table the 25-row cap folds opens; a row a filter hides is said (say) */
    var otGo=function(n,say){var tr=rowFor(n);if(!tr)return false;
      if(tr.hidden&&tr._s&&find&&typeof run==='function'){find.value='';run()}                   /* the search hides it: the search is cleared */
      if(tr._c&&!(tr._f||tr._d||tr._p||tr._ot||tr._s)){var tb0=tr.closest('table');if(tb0){tb0._open=true;if(tb0._capApply)tb0._capApply()}}                  /* a row only the 25-row cap folds: the table opens first (a row a filter hides is never cap-only: the filter's note wins) */
      if(tr.hidden){say('Row '+n+' is hidden by '+(tr._f?"the table's filter box":tr._d?"the table's column filters":tr._p?'the filter buttons at the top of the page':tr._ot?"the time line's controls (host, vCenter, VM, Show, kinds, repeats)":'a filter')+'.');return true}
      hide();var d=tr.closest('details');if(d)d.open=true;
      tr.scrollIntoView({block:'center'});tr.classList.add('ot-flash');setTimeout(function(){tr.classList.remove('ot-flash')},2200);return true};
    /* what happened, an incident of ServiceNow's number a link (a new tab) */
    var otWhat=function(e,cls){var w=document.createElement('span');w.className=cls;var at=e.u&&e.k?e.w.indexOf(e.k):-1;
      if(at>=0&&okUrl(e.u)){w.appendChild(document.createTextNode(e.w.slice(0,at)));var inc=document.createElement('a');inc.className='tt-inc';inc.href=e.u;inc.target='_blank';
        inc.rel='noopener noreferrer';inc.textContent=e.k;w.appendChild(inc);w.appendChild(document.createTextNode(e.w.slice(at+e.k.length)))}
      else w.appendChild(document.createTextNode(e.w));return w};
    var showList=function(el,x,y){var list=otVisible(el._ev||[]);if(!list.length){hide();return}
      if(owner&&owner!==el&&owner._off)owner._off();owner=el;tt.textContent='';tt.className='tt tt-list'+(pinned===el?' pin':'');
      var bytime=list.slice().sort(function(a,b){return a.n-b.n}),first=bytime[0].m,last=bytime[bytime.length-1].m;
      var hd=document.createElement('div');hd.className='tt-head';hd.textContent=list.length+(list.length===1?' event':' events')+' \u00b7 '+first+(last!==first?'-'+last:'');tt.appendChild(hd);
      var body=document.createElement('div');body.className='tt-body';
      list.slice().sort(function(a,b){return (RANKS[b.l]||0)-(RANKS[a.l]||0)||a.n-b.n}).forEach(function(e){
        var ln=document.createElement('div'),ic=document.querySelector('.ot-legend .ot-'+e.l+' svg');ln.className='tt-ln'+(e.r?' rel':'');
        var t=document.createElement('a');t.className='tt-t';t.href='#ot-row-'+e.n;t.textContent=e.t;t.title='Go to row '+e.n+' of The events';ln.appendChild(t);
        var i=document.createElement('span');i.className='tt-i ot-'+e.l;if(ic)i.appendChild(ic.cloneNode(true));i.setAttribute('aria-hidden','true');ln.appendChild(i);
        var w=otWhat(e,'tt-w');
        if(e.p){var p=document.createElement('small');p.textContent=e.p;w.appendChild(p)}ln.appendChild(w);
        var go=function(ev){if(otGo(e.n,function(t){var nt=tt.querySelector('.tt-note')||document.createElement('div');nt.className='tt-note';nt.setAttribute('role','status');
          nt.textContent=t;tt.appendChild(nt)}))ev.preventDefault()};
        t.addEventListener('click',go);
        ln.addEventListener('click',function(ev){if(ev.target.closest&&ev.target.closest('a'))return;go(ev)});          /* the line goes to its row; the incident link opens ServiceNow */
        body.appendChild(ln)});
      tt.appendChild(body);if(pinned===el){body.tabIndex=0;body.setAttribute('aria-label','Events of this mark')}
      tt.hidden=false;place(x,y);if(pinned===el)body.focus()};
    /* the outage review's chart (report_outage.html.j2): a dot (.mk with data-evs: [time, level, what happened, object] each) opens on a
       click, Enter or Space as a list of every event it stands for - a popover by the dot, or a panel at the window's right side when there
       is no room above or below it; the list scrolls inside it (at most 60% of the window). Close, Esc, a click outside it or Tab out of it
       closes it and the focus goes back to the dot; one open at a time; the chart's Show switch or a folded section closes it. Hover keeps
       the short tip. Placed by its left, top and max-height (the CSSOM: a strict style-src allows it) */
    var dlBox=null,dlDot=null,DLW={critical:'Critical',warning:'Warning',low:'Low',info:'Note',ok:'Back to normal'};
    var dlPlace=function(){if(!dlBox||!dlDot)return;var r=dlDot.getBoundingClientRect(),vw=document.documentElement.clientWidth,vh=window.innerHeight;
      if((!r.width&&!r.height)||r.bottom<0||r.top>vh){dlClose();return}                                 /* its view switched, its section folded, scrolled away */
      var w=Math.min(480,vw-16),below=vh-r.bottom-18,above=r.top-18,cap=Math.round(vh*.6),side=Math.max(below,above)<Math.min(cap,240);
      dlBox.classList.toggle('mkp-side',side);dlBox.style.width=w+'px';
      if(side){dlBox.style.maxHeight=(vh-16)+'px';dlBox.style.left=(vw-w-8)+'px';dlBox.style.top='8px';return}
      var down=below>=above;dlBox.style.maxHeight=Math.min(cap,down?below:above)+'px';var h=dlBox.offsetHeight;
      if(!down&&below>=h)down=true;
      dlBox.style.left=Math.round(Math.min(Math.max(8,r.left+r.width/2-w/2),vw-w-8))+'px';
      dlBox.style.top=Math.round(Math.min(Math.max(8,down?r.bottom+10:r.top-10-h),vh-h-8))+'px'};
    var dlClose=function(back){var d=dlDot;if(dlBox&&dlBox.parentNode)dlBox.parentNode.removeChild(dlBox);dlBox=dlDot=null;
      window.removeEventListener('scroll',dlPlace,true);window.removeEventListener('resize',dlPlace);
      if(d){d.setAttribute('aria-expanded','false');if(back){d._quiet=1;d.focus({preventScroll:true})}}};
    var dlOpen=function(el){if(dlDot===el){dlClose(true);return}dlClose();var ev;try{ev=JSON.parse(el.getAttribute('data-evs'))}catch(x){ev=null}
      if(!ev||!ev.length)return;hide();var vr=el.getBoundingClientRect();if(vr.top<0||vr.bottom>window.innerHeight)el.scrollIntoView({block:'center',behavior:'instant'});dlDot=el;el.setAttribute('aria-expanded','true');
      var b=dlBox=document.createElement('div');b.className='mkp';b.id='mkp-open';b.setAttribute('role','dialog');b.setAttribute('aria-labelledby','mkp-title');
      var hd=document.createElement('div');hd.className='mkp-h';var h4=document.createElement('h4');h4.id='mkp-title';
      h4.textContent=ev[0][0]+' - '+ev.length+(ev.length===1?' event':' events');hd.appendChild(h4);
      var x=document.createElement('button');x.type='button';x.className='mkp-x';x.textContent='Close';x.addEventListener('click',function(){dlClose(true)});hd.appendChild(x);
      var sub=document.createElement('div');sub.className='mkp-sub';var last=ev[ev.length-1][0],worst=ev.reduce(function(a,e){return (RANKS[e[1]]||0)>(RANKS[a]||0)?e[1]:a},ev[0][1]);
      sub.textContent=(el.getAttribute('data-tip')||'').split('\n')[0]+(last!==ev[0][0]?' to '+last:'')+' \u00b7 the worst: '+(DLW[worst]||'Note');hd.appendChild(sub);b.appendChild(hd);
      var ul=document.createElement('ul');ul.className='mkp-ev';ul.tabIndex=0;ul.setAttribute('aria-label','The events of this dot, by time');
      ev.forEach(function(e){var li=document.createElement('li'),lv=DLW[e[1]]?e[1]:'info';
        var t=document.createElement('span');t.className='mkp-t';t.textContent=e[0];li.appendChild(t);
        var l=document.createElement('span');l.className='mkp-lv lv-'+lv;l.textContent=DLW[lv];li.appendChild(l);
        var w=document.createElement('span');w.className='mkp-w';w.textContent=e[2];
        if(e[3]){var o=document.createElement('small');o.textContent=e[3];w.appendChild(o)}li.appendChild(w);ul.appendChild(li)});
      b.appendChild(ul);
      b.addEventListener('keydown',function(k){if(k.key==='Escape'){k.preventDefault();k.stopPropagation();dlClose(true)}});
      b.addEventListener('focusout',function(f){var to=f.relatedTarget;if(to&&dlBox&&!dlBox.contains(to)&&to!==dlDot)dlClose()});
      document.body.appendChild(b);dlPlace();window.addEventListener('scroll',dlPlace,true);window.addEventListener('resize',dlPlace);
      ul.focus({preventScroll:true})};
    document.addEventListener('keydown',function(e){if(e.key==='Escape'&&dlBox)dlClose(true)});
    document.addEventListener('click',function(e){var t=e.target;if(!dlBox||dlBox.contains(t)||(t.closest&&t.closest('.mk[data-evs]')))return;dlClose()});
    all('input[name=tlv]').forEach(function(i){i.addEventListener('change',function(){if(dlBox)dlClose()})});
    var keyed=function(el){try{return el.matches(':focus-visible')}catch(e){return true}};
    var bindTip=function(el){if(el._at)return;if(!el.hasAttribute('tabindex'))el.tabIndex=0;el.removeAttribute('title');el.setAttribute('aria-describedby','site-tip');
      var ln=function(){return el.getAttribute('data-tip').split('\n')};
      var emit=function(x,y){if(el._ev)showList(el,x,y);else show(el,ln(),x,y)};
      var at=el._at=function(){var r=el.getBoundingClientRect();emit(r.left+r.width/2,r.top)};
      el.addEventListener('pointerenter',function(e){if(e.pointerType==='mouse'&&!pinned)emit(e.clientX,e.clientY)});
      el.addEventListener('pointermove',function(e){if(e.pointerType==='mouse'&&owner===el&&!pinned)place(e.clientX,e.clientY)});
      el.addEventListener('pointerleave',function(e){if(e.pointerType==='mouse'&&owner===el&&!pinned)hide()});
      el.addEventListener('pointerup',function(e){if(e.pointerType!=='mouse'&&owner!==el)at()});
      el.addEventListener('focus',function(){if(el._quiet){el._quiet=0;return}if(keyed(el)&&!pinned)at()});
      el.addEventListener('blur',function(){if(owner===el&&!pinned)hide()});
      if(el._ev){el.setAttribute('aria-expanded','false');el.addEventListener('click',function(){otOpen(el)});
        el.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();otOpen(el)}})}
      else if(el.hasAttribute('data-evs')){el.setAttribute('role','button');el.setAttribute('aria-haspopup','dialog');el.setAttribute('aria-expanded','false');
        el.addEventListener('click',function(){dlOpen(el)});
        el.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();dlOpen(el)}})}};
    tips.forEach(bindTip);
    /* 1.90.1: a mark opened (a click, Enter, Space, a tap): its events under its lane, as an opened row of the pop look - the worst first
       picked, the facts of the picked one beside them (where, host, VM, cluster, vCenter, alarm, kind, about the outage, its place in the
       lead-up, its row in The events). Esc or Close closes it; one open at a time; hover keeps the quick tip */
    var otPanel=null,otOpenM=null,OTWD={critical:'Critical',warning:'Warning',low:'Low',info:'Note',ok:'Back to normal'},otKn={};
    var otSubj=otEl&&otEl.getAttribute('data-subject')||'',otLd=document.querySelector('.ot-lead[data-lead]'),otLn=otLd?otLd.getAttribute('data-lead'):'',
      otLs=otLd?otLd.getAttribute('data-lead-span')||'':'';
    if(otCtl)all('input[name=ot-kind]',otCtl).forEach(function(i){otKn[i.value]=[].filter.call(i.parentNode.childNodes,function(x){return x.nodeType===3}).map(function(x){return x.nodeValue}).join('').trim()});
    var otClose=function(back){var m=otOpenM;if(otPanel&&otPanel.parentNode)otPanel.parentNode.removeChild(otPanel);otPanel=otOpenM=null;otHook.re=null;
      if(m){m.setAttribute('aria-expanded','false');m.removeAttribute('aria-controls');if(back){m._quiet=1;m.focus()}}};
    var otFacts=function(dl,e){dl.textContent='';var nt=dl.parentNode&&dl.parentNode.querySelector('.otx-note');if(nt)nt.parentNode.removeChild(nt);
      var add=function(k,v,wide,node){if(!v&&!node)return;var g=document.createElement('div');if(wide)g.className='wide';var t=document.createElement('dt');t.textContent=k;
        var d=document.createElement('dd');if(node)d.appendChild(node);else d.textContent=v;g.appendChild(t);g.appendChild(d);dl.appendChild(g)};
      var o=function(x){return x&&x!==e.p?x:''};
      var vs=otVms(e);add('Where',e.p);var hs=otHs(e);add(hs.length>1?'Hosts':'Host',hs.length>1?hs.join(', '):o(e.h));add(vs.length>1?'VMs':'VM',vs.length>1?vs.slice(0,6).join(', ')+(vs.length>6?' and '+(vs.length-6)+' more':''):o(e.vm),vs.length>2);
      add('Cluster',o(e.cl));add('vCenter',o(otV(e.v)));add('Alarm',e.a||'',String(e.a||'').length>22);add('Kind',otKn[e.c]||'');
      if(otSubj)add('About the outage',e.r?'Yes: about '+otSubj+' or its host, cluster, datastores':'No',true);
      if(e.lu)add('Lead-up',e.lu===2?'Ends here: the first critical event':'Yes: in the '+(otLs?otLs+' ':'')+'before #'+otLn,true);
      if(e.k&&e.u&&okUrl(e.u)){var inc=document.createElement('a');inc.className='tt-inc';inc.href=e.u;inc.target='_blank';inc.rel='noopener noreferrer';
        inc.textContent=e.k;add('Incident','',false,inc)}
      var b=document.createElement('button');b.type='button';b.textContent='Row '+e.n+' in The events';
      b.addEventListener('click',function(){otGo(e.n,function(t){var p=dl.parentNode,x=p.querySelector('.otx-note')||document.createElement('p');x.className='otx-note';
        x.setAttribute('role','status');x.textContent=t;p.appendChild(x)})});
      add('Go to','',false,b)};
    /* 1.104.0: ACT's cards (folded) - an event's number goes to its row (otGo), "Show these on the time line" rings the marks it read
       and fades the others; pressed again, all back */
    /* the card's note (why a row or a mark cannot be shown): in its body, where it opens; gone again with the next click (Neon) */
    var czSay=function(el,t){var p=el.closest('.cz'),body=p&&(p.querySelector('.cz-body')||p),x=body&&body.querySelector('.cz-act-off');
      if(!t){if(x)x.parentNode.removeChild(x);return}if(!body)return;if(!x){x=document.createElement('p');x.className='cz-note cz-act-off';x.setAttribute('role','status');body.appendChild(x)}x.textContent=t};
    all('.causes a[data-ot]').forEach(function(a){a.addEventListener('click',function(ev){var n=+a.getAttribute('data-ot');czSay(a,'');
      if(otGo(n,function(t){czSay(a,t)}))ev.preventDefault()})});
    all('.causes .cz-show[data-ots]').forEach(function(b){b.hidden=false;b.addEventListener('click',function(){
      var on=b.getAttribute('aria-pressed')!=='true',want=b.getAttribute('data-ots').split(' ').map(Number);czSay(b,'');
      all('.causes .cz-show').forEach(function(o){o.setAttribute('aria-pressed','false')});all('.ot-m.ot-act').forEach(function(m){m.classList.remove('ot-act')});all('.ot.ot-acting').forEach(function(o){o.classList.remove('ot-acting')});
      if(!on)return;var first=null,off=0;
      /* only the marks the time line shows (a mark its controls put away is ot-off: never ringed, the rest never faded for it) */
      all('.ot-track .ot-m[data-ev]').forEach(function(m){if((m._ev||[]).some(function(e){return want.indexOf(e.n)>=0})){if(m.classList.contains('ot-off')){off++;return}
        m.classList.add('ot-act');first=first||m}});
      if(!first){czSay(b,off?"These events are hidden by the time line's controls (host, vCenter, VM, Show, kinds, repeats).":'These events are not on the time line.');return}
      b.setAttribute('aria-pressed','true');var ot=first.closest('.ot'),sec=ot.closest('details');if(sec)sec.open=true;      /* its section folded: opened */
      ot.classList.add('ot-acting');ot.scrollIntoView({block:'center'})})});
    /* the folded cards print open, and fold again after */
    var czOpened=[];window.addEventListener('beforeprint',function(){all('details.cz-act:not([open])').forEach(function(d){d.open=true;czOpened.push(d)})});
    window.addEventListener('afterprint',function(){czOpened.forEach(function(d){d.open=false});czOpened=[]});
    var otOpen=function(m){if(otOpenM===m){otClose(true);return}otClose();if(!otVisible(m._ev||[]).length)return;
      hide();otOpenM=m;m.setAttribute('aria-expanded','true');m.setAttribute('aria-controls','otx-open');
      var p=otPanel=document.createElement('div');p.className='otx';p.id='otx-open';p.setAttribute('role','region');p.setAttribute('aria-labelledby','otx-title');
      var build=function(){var ev=otVisible(m._ev||[]).sort(function(a,b){return a.n-b.n}),pick=null;p.textContent='';
        var first=ev.slice().sort(function(a,b){return (RANKS[b.l]||0)-(RANKS[a.l]||0)||a.n-b.n})[0],lv=OTLV.indexOf(first.l)<0?'info':first.l;
        var h=document.createElement('div');h.className='otx-h';var c=m.cloneNode(true);c.className='ot-m otx-chip ot-'+lv;
        ['style','data-ev','data-tip','title','role','tabindex','aria-label','aria-describedby','aria-expanded','aria-controls'].forEach(function(a){c.removeAttribute(a)});
        c.setAttribute('aria-hidden','true');h.appendChild(c);
        var h4=document.createElement('h4');h4.id='otx-title';var a0=ev[0].m,a1=ev[ev.length-1].m;
        h4.textContent=ev.length>1?ev.length+' events, '+a0+(a1!==a0?'\u2013'+a1:''):first.w;h.appendChild(h4);
        var wn=document.createElement('span');wn.className='otx-when';wn.textContent=ev.length>1?'the worst: '+OTWD[lv]:first.t+' \u00b7 '+OTWD[lv];h.appendChild(wn);
        var tag=function(t,cl){var x=document.createElement('span');x.className='otx-tag'+(cl?' '+cl:'');x.textContent=t;h.appendChild(x)};
        if(ev.some(function(e){return e.lu===2}))tag('Ends the lead-up');else if(ev.some(function(e){return e.lu}))tag('In the lead-up');
        if(otSubj&&ev.some(function(e){return e.r}))tag('About '+otSubj,'rel');
        var x=document.createElement('button');x.type='button';x.className='otx-x';x.textContent='Close';x.addEventListener('click',function(){otClose(true)});h.appendChild(x);
        p.appendChild(h);var dl=document.createElement('dl');dl.className='otx-f';
        if(ev.length>1){var ul=document.createElement('ul');ul.className='otx-ev';ul.setAttribute('aria-label','Events of this mark');
          ev.forEach(function(e){var li=document.createElement('li');if(otSubj&&!e.r)li.className='dim';li.tabIndex=0;li.setAttribute('data-n',String(e.n));li.setAttribute('aria-label',e.t+', '+OTWD[e.l]+', '+e.w);
            var ic=document.createElement('span'),sv=document.querySelector('.ot-legend .ot-'+e.l+' svg');ic.className='ic ot-'+e.l;ic.setAttribute('aria-hidden','true');if(sv)ic.appendChild(sv.cloneNode(true));li.appendChild(ic);
            var t=document.createElement('span');t.className='t';t.textContent=e.t;li.appendChild(t);
            var w=otWhat(e,'w'),sm=document.createElement('small');sm.textContent=[e.p,e.h&&e.h!==e.p?e.h:'',otV(e.v)].filter(function(q){return q}).join(' \u00b7 ');w.appendChild(sm);li.appendChild(w);
            var pk=function(){all('li',ul).forEach(function(q){q.removeAttribute('aria-current')});li.setAttribute('aria-current','true');otFacts(dl,e)};
            li.addEventListener('click',function(k){if(k.target.closest&&k.target.closest('a'))return;pk()});
            li.addEventListener('keydown',function(k){if(k.target===li&&(k.key==='Enter'||k.key===' ')){k.preventDefault();pk()}});
            if(e===first){li.setAttribute('aria-current','true');pick=e}ul.appendChild(li)});p.appendChild(ul)}
        p.appendChild(dl);otFacts(dl,pick||first)};
      build();
      otHook.re=function(){if(m.classList.contains('ot-off')||!otVisible(m._ev||[]).length){otClose();return}build()};
      var lane=m.closest('.ot-row');lane.parentNode.insertBefore(p,lane.nextSibling)};
    document.addEventListener('keydown',function(e){if(e.key==='Escape'&&otPanel)otClose(true)});
    document.addEventListener('click',function(e){var t=e.target;if(!otPanel||otPanel.contains(t)||!t.closest)return;
      if(t.closest('.ot-m[data-ev]')||t.closest('.otf')||t.closest('[data-ot-ctl]'))return;otClose()});
    var bindSpark=function(svg){if(svg._at)return;var d;try{d=JSON.parse(svg.getAttribute('data-points'))}catch(e){return}if(!d||!d.length)return;
      var name=svg.getAttribute('data-label')||'',vb=svg.viewBox.baseVal,cur=-1,g=document.createElementNS(NS,'line'),dot=document.createElementNS(NS,'circle');
      g.setAttribute('class','hg');g.setAttribute('y1','0');g.setAttribute('y2',String(vb.height));dot.setAttribute('class','hd');dot.setAttribute('r','4');
      svg.setAttribute('aria-describedby','site-tip');
      svg._off=function(){cur=-1;if(g.parentNode){svg.removeChild(g);svg.removeChild(dot)}};
      svg._at=function(){at(cur<0?d.length-1:cur)};
      var at=function(i){i=Math.max(0,Math.min(d.length-1,i));cur=i;var p=d[i],r;
        g.setAttribute('x1',p[0]);g.setAttribute('x2',p[0]);dot.setAttribute('cx',p[0]);dot.setAttribute('cy',p[1]);
        if(!g.parentNode){svg.appendChild(g);svg.appendChild(dot)}
        r=svg.getBoundingClientRect();show(svg,[p[3],(name?name+', ':'')+p[2]],r.left+p[0]*r.width/vb.width,r.top+p[1]*r.height/vb.height)};
      var near=function(e){var r=svg.getBoundingClientRect(),sx=(e.clientX-r.left)*vb.width/r.width,b=0;
        d.forEach(function(p,i){if(Math.abs(p[0]-sx)<Math.abs(d[b][0]-sx))b=i});return b};
      svg.addEventListener('pointermove',function(e){at(near(e))});
      svg.addEventListener('pointerdown',function(e){at(near(e))});
      svg.addEventListener('pointerleave',function(e){if(e.pointerType==='mouse'&&owner===svg)hide()});
      svg.addEventListener('focus',function(){if(keyed(svg))at(d.length-1)});
      svg.addEventListener('blur',function(){if(owner===svg)hide()});
      svg.addEventListener('keydown',function(e){var k={ArrowLeft:cur-1,ArrowRight:cur+1,Home:0,End:d.length-1}[e.key];
        if(k!==undefined){e.preventDefault();at(k)}})};
    sparks.forEach(bindSpark);
    document.addEventListener('keydown',function(e){if(e.key==='Escape'&&!tt.hidden){var o=owner;hide();if(o&&o.focus&&o._ev){o._quiet=1;o.focus()}}});
    document.addEventListener('pointerdown',function(e){if(owner&&!owner.contains(e.target)&&!(pinned&&tt.contains(e.target)))hide()});
    /* a scroll hides it - but the element with the keyboard's focus keeps it, moved with it (Tab scrolls to it) */
    window.addEventListener('scroll',function(){if(tt.hidden)return;
      if(pinned&&owner&&owner._at)owner._at();else if(owner&&owner===document.activeElement&&owner._at)owner._at();else hide()},{passive:true})}
  /* 1.122.0: Export selected (a section with data-sel-export: the local groups' By server and By member). A box in each
     row's first cell, "Select all shown" (the rows the Group buttons and the search leave), and the ticked rows as CSV,
     JSON or the approved list's YAML, made here (Blob, a download link) - nothing leaves the page. Each row's data is its
     data-sel (JSON: g group, e approved-list entry, m member, i SID, k kind, n servers, s server). */
  /* sel-pure-start */
  var SelX={
    /* a cell Excel never runs as a formula: = + - @ (a tab, a return) first get a ' in front */
    q:function(v){if(typeof v==='number')return String(v);v=String(v==null?'':v);if(/^[=+\-@\t\r]/.test(v))v="'"+v;
      return /[",\r\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v},
    /* a By member row (x: {server: SID}) is one line per server: its own SID, never one for all */
    flat:function(rows){var out=[];rows.forEach(function(r){if(r.x&&typeof r.x==='object'&&!r.s){Object.keys(r.x).sort().forEach(function(h){
        out.push({s:h,g:r.g,m:r.m,i:r.x[h],k:r.k,e:r.e,n:r.n})})}else out.push(r)});return out},
    csv:function(rows){var cols=['server','group','member','sid','kind','entry','servers'],k={server:'s',group:'g',member:'m',sid:'i',kind:'k',entry:'e',servers:'n'};
      return [cols.join(',')].concat(SelX.flat(rows).map(function(r){return cols.map(function(c){return SelX.q(r[k[c]])}).join(',')})).join('\r\n')+'\r\n'},
    json:function(rows){return JSON.stringify(SelX.flat(rows).map(function(r){var o={group:r.g,member:r.m,sid:r.i,kind:r.k,entry:r.e,servers:r.n};
      if(r.s)o.server=r.s;return o}),null,1)+'\n'},
    /* an entry Ansible would run as a template (a Jinja mark: two braces, a brace and a percent or a hash) never goes into
       the YAML: its SID instead, else left out. The marks are made from char codes: this file is read through Jinja too */
    marks:(function(){var o=String.fromCharCode(123),c=String.fromCharCode(125);return [o+o,o+'%',o+'#',c+c,'%'+c,'#'+c]})(),
    tpl:function(v){v=String(v==null?'':v);return SelX.marks.some(function(m){return v.indexOf(m)>=0})},
    lit:function(e,sid){if(!SelX.tpl(e))return e;return sid&&!SelX.tpl(sid)?sid:''},
    yq:function(v){return "'"+String(v).replace(/'/g,"''")+"'"},
    yaml:function(rows,setting,kind){var by={},order=[];
      rows.forEach(function(r){if(!(r.g in by)){by[r.g]=[];order.push(r.g)}var e=SelX.lit(r.e,r.i);
        if(e&&by[r.g].map(function(x){return x.toLowerCase()}).indexOf(String(e).toLowerCase())<0)by[r.g].push(e)});
      var out=['# '+setting+' - the rows ticked on the report page. Paste it into all.yml (or the Variables of the template).',setting+':','  '+kind+':'];
      order.forEach(function(g){if(!by[g].length){out.push('    '+SelX.yq(g)+': []');return}
        out.push('    '+SelX.yq(g)+':');by[g].forEach(function(e){out.push('      - '+SelX.yq(e))})});
      return out.join('\n')+'\n'},
    /* the checks from the ticked Administrators rows, the same text as the job's (plugins/filter/win_local_groups_filters.py
       check_data, check_code, answer_file, check_script): the fixed parts come from the section (c), the entries are data */
    cq:function(v){return '"'+String(v).replace(/\r/g,' ').replace(/\n/g,' ').replace(/"/g,'""')+'"'},
    data:function(entries,c){var seen={},lines=[c.data_head];entries.forEach(function(x){if(SelX.bad.test(String(x[0]))||SelX.bad.test(String(x[1]||''))||SelX.bad.test(String(x[2]||'')))return;
        var e=String(x[0]).replace(/\r/g,' ').replace(/\n/g,' ').replace(/^ +| +$/g,''),m=String(x[1]||''),h=String(x[2]||''),
          k=e.toLowerCase()+'\u0000'+m.toLowerCase()+'\u0000'+h.toLowerCase();if(!e||seen[k])return;seen[k]=1;lines.push(SelX.cq(e)+','+SelX.cq(m)+','+SelX.cq(h))});
      lines.push(c.data_tail);return lines.join('\n')},
    /* a character XML 1.0 does not allow: the entry is left out (as check_data does) */
    /* control characters, U+FFFE/FFFF, and a surrogate without its pair (a pair is one character: kept) */
    bad:{test:function(v){return /[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/.test(v)||/[\ud800-\udbff](?![\udc00-\udfff])|(^|[^\ud800-\udbff])[\udc00-\udfff]/.test(v)}},
    xesc:function(v){return String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')},
    /* 1.122.2: a By server row (s) is approved on that server only: hosts = its full name (f), else match = its computer
       name (c, ASCII, no [ ]); neither: left out (counted in left) - never approved on every server */
    admins:function(rows){var out=[],left=0;rows.forEach(function(r){if(r.g!=='Administrators'||!r.e)return;
        if(!r.s){out.push([r.e,'','']);return}
        if(r.f){out.push([r.e,'',r.f]);return}
        var c=String(r.c||'');if(/^[\x21-\x7e]+$/.test(c)&&!/[\[\];*?]/.test(c)){out.push([r.e,c,''])}else left++});
      out.left=left;return out},
    code:function(entries,c){return [SelX.data(entries,c),c.body,c.vc_tail].join('\n')},
    xml:function(entries,c,st){var code=SelX.code(entries,c).split('\n').map(SelX.xesc).join('\n');return st.xml.split(c.mark).join(code)},
    ps1:function(entries,c){return '\ufeff'+[c.ps1_head,SelX.data(entries,c),c.body,c.ps1_tail].join('\n')+'\n'}
  };
  /* sel-pure-end */
  all('details.sec[data-sel-export]').forEach(function(sec){var cfg={};try{cfg=JSON.parse(sec.getAttribute('data-sel-export'))}catch(e){}
    var tb=sec.querySelector('.tw table');if(!tb||!tb.tBodies[0])return;
    /* the rows now (a table of more than 100 rows adds the rest later: Show all, a filter) - each gets its box when it comes */
    var rows=[];var take=function(){rows=[].slice.call(tb.tBodies[0].rows).filter(function(r){return r.hasAttribute('data-sel')});
      rows.forEach(function(r){if(r._sel)return;var c=r.cells[0];if(!c)return;var b=document.createElement('input');b.type='checkbox';b.className='selc';
        b.setAttribute('aria-label','Select this row');b.addEventListener('change',function(){upd()});r._sel=b;c.insertBefore(b,c.firstChild)})};
    take();if(!rows.length&&!tb.tBodies[0].querySelector('script.defer-main'))return;
    var bar=document.createElement('div');bar.className='selx';
    var lab=document.createElement('label');lab.className='sela';var allb=document.createElement('input');allb.type='checkbox';
    lab.appendChild(allb);lab.appendChild(document.createTextNode(' Select all shown'));bar.appendChild(lab);
    var cnt=document.createElement('span');cnt.className='seln';cnt.setAttribute('aria-live','polite');bar.appendChild(cnt);
    var lx=document.createElement('span');lx.className='sell';lx.textContent='Export selected:';bar.appendChild(lx);
    var name=cfg.name||'selected';
    var picked=function(){take();return rows.filter(function(r){return r._sel&&r._sel.checked}).map(function(r){try{return JSON.parse(r.getAttribute('data-sel'))}catch(e){return null}}).filter(Boolean)};
    var save=function(text,file,type){var b=new Blob([text],{type:type}),u=URL.createObjectURL(b),a=document.createElement('a');
      a.href=u;a.download=file;document.body.appendChild(a);a.click();setTimeout(function(){URL.revokeObjectURL(u);a.remove()},0)};
    var btns=[];
    var acts=[['CSV',function(p){save(SelX.csv(p),name+'-selected.csv','text/csv')}],['JSON',function(p){save(SelX.json(p),name+'-selected.json','application/json')}],
     ['Approved list (YAML)',function(p){save(SelX.yaml(p,cfg.setting||'approved',cfg.kind||'groups'),'approved-list-selected.yml','text/yaml')}]];
    /* 1.122.0: the checks from the ticked Administrators rows - an Evaluate-STIG answer file per STIG, and the PowerShell check */
    var ck=cfg.check;
    if(ck&&ck.stigs){var fit=function(f){return function(p){var e=SelX.admins(p);if(!e.length){cnt.textContent=e.left?'No server name to approve the ticked rows on':'Tick an Administrators row first';return}
      f(e);if(e.left)cnt.textContent=e.left+(e.left===1?' row':' rows')+' left out: no server name to approve it on'}};
      ck.stigs.forEach(function(st){acts.push(['Answer file '+st.name+' (XML)',fit(function(e){save(SelX.xml(e,ck,st),st.file,'application/xml')})])});
      acts.push(['PowerShell check (.ps1)',fit(function(e){save(SelX.ps1(e,ck),'check-local-admins.ps1','text/plain')})])}
    acts.forEach(function(x){
      var b=document.createElement('button');b.type='button';b.className='copy selb';b.textContent=x[0];
      b.addEventListener('click',function(){var p=picked();if(p.length)x[1](p)});btns.push(b);bar.appendChild(b)});
    if(cfg.note){var n=document.createElement('span');n.className='selnote';n.textContent=cfg.note;bar.appendChild(n)}
    var shown=function(){take();return rows.filter(function(r){return !r.hidden})};
    var upd=function(){take();var k=rows.filter(function(r){return r._sel.checked}).length,sh=shown(),ks=sh.filter(function(r){return r._sel.checked}).length;
      cnt.textContent=k+' selected';btns.forEach(function(b){b.disabled=!k});
      allb.checked=sh.length>0&&ks===sh.length;allb.indeterminate=ks>0&&ks<sh.length;
      rows.forEach(function(r){r.classList.toggle('sel-on',r._sel.checked)})};
    if(window.MutationObserver)new MutationObserver(function(){upd()}).observe(tb.tBodies[0],{childList:true});
    /* 1.122.2: ticking it shows every row first (the ones past the first 100 too, as Show all does): all of them go in the export */
    allb.addEventListener('change',function(){var on=allb.checked;if(on&&tb._capApply){tb._open=true;tb._capApply()}shown().forEach(function(r){r._sel.checked=on});upd()});
    var w=tb.closest('.tw');w.parentNode.insertBefore(bar,w);upd();
    document.addEventListener('site-facets',upd);var f=document.getElementById('fhost');if(f)f.addEventListener('input',function(){setTimeout(upd,200)})});
  });
})();
