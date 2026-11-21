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
  function esc(t){return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
  function one(el){return (el?el.textContent:'').replace(/\s+/g,' ').trim()}
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
      if(el.classList.contains('tools2')||el.hidden)return;
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
    all('.look[hidden]').forEach(function(s){if(s.querySelector('li:not([hidden])'))s.hidden=false});
    all('.area').forEach(function(a){var n=a.querySelectorAll('.rr.bad,.rr[data-late]').length;if(!n)return;
      var b=a.querySelector('.ah.bad'),g=a.querySelector('.ah.good'),k=a.querySelector('.ahn'),v=a.querySelector('.ahv');
      if(k)k.textContent=n;if(v)v.textContent=n===1?'needs':'need';if(b)b.hidden=false;if(g)g.hidden=true;
      all('label[for="t'+a.getAttribute('data-tab')+'"] [hidden]').forEach(function(d){d.hidden=false})})}
  all('.scopy').forEach(function(c){c.hidden=false;c.addEventListener('click',function(e){
    e.preventDefault();e.stopPropagation();var g=grab(c.closest('details').querySelector('.body'));put(c,g[0],g[1])})});
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
        rows.forEach(function(r){r._f=!!q&&r.textContent.toLowerCase().indexOf(q)<0;vis(r)});capApply();count();if(tb._expand)fitRows(tb)});
      /* one choice at a time per list (1.67.0): a row stays when the chosen value is one of its own (data-f<n>: |a|b|) */
      dd.forEach(function(s){s.parentNode.hidden=false;s.addEventListener('change',function(){if(tb._expand)tb._expand();tb._cf=dd.some(function(x){return !!x.value});
        rows.forEach(function(r){var off=false;dd.forEach(function(x){var v=x.value;
          if(v&&(r.getAttribute('data-f'+x.getAttribute('data-f'))||'').indexOf('|'+v+'|')<0)off=true});r._d=off;vis(r)});capApply();count();if(tb._expand)fitRows(tb)})})}
  }
  all('.tw table').forEach(bindTable);
  capAll();
  window.addEventListener('beforeprint',function(){printing=true;capAll()});
  window.addEventListener('afterprint',function(){printing=false;capAll()});
  /* 1.86.0: for report_pop.js (the team picker, the severity chips) - a row's flags applied, then the caps, the counts and the fit */
  window.siteReport.vis=vis;window.siteReport.rowsChanged=function(){capAll();counts.forEach(function(c){c()});fit()};
  /* a link to a row past the cap (a dashboard's drill link, Open item, a timeline line) shows every row of that table first */
  function liftFor(h){if(!h||h.length<2)return;var id=decodeURIComponent(h.slice(1)),pending=deferredIds[id];
    if(pending){pending._open=true;pending._capApply();fitRows(pending)}
    var t=document.getElementById(id);
    if(t){var sec=t.closest('details.sec');if(sec)sec.open=true;if(pending)t.scrollIntoView()}
    var tr=t&&t.closest&&t.closest('tr');if(tr&&tr._c){var tb=tr.closest('table');tb._open=true;if(tb._capApply)tb._capApply();fitRows(tb)}}
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
    try{var t0=location.hash.length>1&&document.getElementById(decodeURIComponent(location.hash.slice(1)));if(t0&&t0.tagName==='TR'){marked=t0;t0.classList.add('is-target')}}catch(x){}
    document.addEventListener('click',function(e){
      if(e.defaultPrevented||e.button||e.metaKey||e.ctrlKey||e.shiftKey||e.altKey)return;
      var a=e.target.closest&&e.target.closest('a[href^="#"]');if(!a||(a.target&&a.target!=='_self')||a.hasAttribute('download'))return;
      var h=a.getAttribute('href'),id;try{id=decodeURIComponent(h.slice(1))}catch(x){return}
      var t=id?document.getElementById(id):null;if(id&&!t)return;
      e.preventDefault();
      for(var d=t;d&&d!==document;d=d.parentNode)if(d.tagName==='DETAILS'&&!d.open)d.open=true;   /* the place itself too: a closed section opens */
      try{history.replaceState(history.state,'',id?h:location.pathname+location.search)}catch(x){}
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
    var low=function(el){if(el._t===undefined){var detail=el._detail;var s=(el.textContent||'')+(detail?' '+detailText(detail):el._x?' '+el._x.textContent:'');   /* a row: what opens under it too */
        if(el.tagName!=='TR'){s='';var w=document.createTreeWalker(el,NodeFilter.SHOW_TEXT,null),t;
          while((t=w.nextNode()))if(!t.parentNode.closest('button'))s+=t.nodeValue+' '}
        el._t=s.toLowerCase()}return el._t};
    /* a section's title, without its row count */
    var title=function(d){var h=d.querySelector('summary h2'),f=h&&h.firstChild;
      return (f&&f.nodeType===3?f.textContent:(h?h.textContent:'')).toLowerCase()};
    var mark=function(q){if(!HL)return;CSS.highlights['delete']('site-find');if(!q)return;var h=new Highlight(),n=0;
      secs.forEach(function(d){if(d.hidden||n>=3000)return;var w=document.createTreeWalker(d.querySelector('.body')||d,NodeFilter.SHOW_TEXT,null),t;
        while(n<3000&&(t=w.nextNode())){var pe=t.parentNode;if(pe.closest('[hidden],button,svg,script,summary,.tools2'))continue;
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
        var away=!!q&&!k&&!km&&!whole;if(d.hidden!==away)d.hidden=away;
        if(q&&!d.hidden){d.open=true;total+=k;others+=km;ns++}
        (links[d.id]||[]).forEach(function(a){var m=a.querySelector('.m');a.classList.toggle('off',d.hidden);
          if(m)m.textContent=q&&!d.hidden?String(k):''})});
      capAll();                                                  /* the folded rows again, from the rows the search left */
      found.textContent=q?(ns?total+' found in '+ns+' section'+(ns===1?'':'s')+(others?', '+others+' more in other teams':''):'Nothing found'):'';
      if(window.reportPopFold)window.reportPopFold();               /* the fold buttons count the other teams' matches */
      box.classList.toggle('none-found',!!q&&!ns);
      counts.forEach(function(c){c()});mark(q);
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
  var otPass=function(e){return !(OTF.h&&e.h!==OTF.h&&e.p!==OTF.h)&&!(OTF.v&&e.v!==OTF.v)&&!(OTF.vm&&String(e.vm||'').toLowerCase().indexOf(OTF.vm.toLowerCase())<0)};
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
    otMarks.forEach(function(m){if(!m._ev)return;var v=otVisible(m._ev);m.classList.toggle('ot-off',!v.length);if(v.length)otMark(m,v)});
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
    var oh=otU(function(e){return e.h}),ov=otU(function(e){return e.v}),om=otU(function(e){return e.vm});
    if(oh.length>1||ov.length>1||om.length>1){      /* one host, one vCenter, one VM: nothing to choose - no row */
      var fr=document.createElement('div');fr.className='otf';fr.setAttribute('role','group');fr.setAttribute('aria-label','Filter the time line by host, vCenter or VM');
      var otSel=function(cls,name,list,word,key){if(list.length<2)return null;var l=document.createElement('label');l.className=cls;l.appendChild(document.createTextNode(name));
        var x=document.createElement('select');x.appendChild(new Option(word+' ('+list.length+')',''));list.forEach(function(v){x.appendChild(new Option(v,v))});
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
        el.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();otOpen(el)}})}};
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
      add('Where',e.p);add('Host',o(e.h));add('VM',o(e.vm));add('Cluster',o(e.cl));add('vCenter',o(e.v));add('Alarm',e.a||'',String(e.a||'').length>22);add('Kind',otKn[e.c]||'');
      if(otSubj)add('About the outage',e.r?'Yes: about '+otSubj+' or its host, cluster, datastores':'No',true);
      if(e.lu)add('Lead-up',e.lu===2?'Ends here: the first critical event':'Yes: in the '+(otLs?otLs+' ':'')+'before #'+otLn,true);
      if(e.k&&e.u&&okUrl(e.u)){var inc=document.createElement('a');inc.className='tt-inc';inc.href=e.u;inc.target='_blank';inc.rel='noopener noreferrer';
        inc.textContent=e.k;add('Incident','',false,inc)}
      var b=document.createElement('button');b.type='button';b.textContent='Row '+e.n+' in The events';
      b.addEventListener('click',function(){otGo(e.n,function(t){var p=dl.parentNode,x=p.querySelector('.otx-note')||document.createElement('p');x.className='otx-note';
        x.setAttribute('role','status');x.textContent=t;p.appendChild(x)})});
      add('Go to','',false,b)};
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
            var w=otWhat(e,'w'),sm=document.createElement('small');sm.textContent=[e.p,e.h&&e.h!==e.p?e.h:'',e.v].filter(function(q){return q}).join(' \u00b7 ');w.appendChild(sm);li.appendChild(w);
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
  });
})();
