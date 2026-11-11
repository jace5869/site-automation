/* site-automation - the report website's script (the atlas look; roles/site_email/templates/site_report.js). 0.63.0:
   site_report_web writes it beside the index (site-report.js) and each page loads it from there, in its head - a web
   server whose Content-Security-Policy allows no inline script (script-src 'self') still runs it; the page carries it
   inline too, for where the file is not there (a page saved or copied by itself), and it runs once. Without it the
   page reads the same: Light / Auto / Dark, the row height, the section rail (and putting it away, 0.66.0) and
   Columns are HTML and CSS; this adds keeping those choices, Print, sorting, filtering, the search, CSV and the Copy
   buttons. It looks for each part of
   the page before it uses it - a page from an older release loads the newest script too. ASCII only (the Windows
   writer writes it as ASCII). */
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
    all('details.sec').forEach(function(d){d.open=true});window.print()})}
  /* a table wider than its card first takes the CSS's steps (1.21.0: f1 short values wrap, f2 tighter cells and
     headings, f3 numbers wrap at spaces and a smaller type, f4 long chips narrower; a compact table's sq, 1.11.0,
     before them), one by one while it does not fit; one still too wide scrolls sideways inside its card (its headings
     then stay in it) - a date, an address, a short chip or a heading's word is never broken to fit. Before f4, the
     tables get the rail's room: the rail is put away for this page only (not kept); its button brings it back. Each table keeps its card's width (data-fw)
     and its steps (data-fs): it is fitted again only when that width changed (a narrower card goes on from the steps
     it has) or when it must be (its Columns, the rows' height) - opening a section costs the others nothing */
  var railAuto=0,railUser=0,lastW=0;
  /* mode: true = from no step again (its Columns); 'grow' = its values may be wider now (the rows' height): on from
     the steps it has. A table of over 400 rows finds its steps on its first 300 rows, then checks once with all of
     them (and goes on if it must): the same steps, far fewer layouts of a big table */
  function fit1(w,mode){
    var t=w.querySelector('table');if(!t||!w.offsetParent)return -1;
    var cw=w.clientWidth,fw=+(w.getAttribute('data-fw')||-1),fs=w.getAttribute('data-fs');
    var redo=mode===true||fs===null||cw>fw,more=!redo&&(mode==='grow'||cw<fw);
    if(redo||more){
      var st=(t.classList.contains('cc')?['sq']:[]).concat(['f1','f2','f3','f4']),i=0,b=t.tBodies[0],big=b&&b.rows.length>400;
      var over=function(){return t.scrollWidth>w.clientWidth+1};
      if(more)i=fs==='9'?st.length:+fs;
      else{w.classList.remove('scroll');t.classList.remove('sq','f1','f2','f3','f4')}
      if(big){t.classList.add('fsample');for(;i<st.length&&over();i++)t.classList.add(st[i]);t.classList.remove('fsample')}
      for(;i<st.length&&over();i++)t.classList.add(st[i]);
      var sc=over();w.classList.toggle('scroll',sc);
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
    function ok(){b.classList.add('done');if(s)s.textContent='Copied';else if(!b.querySelector('.i1'))b.textContent='Copied';
      setTimeout(function(){b.classList.remove('done');if(s)s.textContent='Copy';else if(!b.querySelector('.i1'))b.textContent='Copy'},1500)}
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
  function head(th){var b=th.querySelector('button'),f=b&&b.firstChild;return f&&f.nodeType===3?f.textContent.trim():one(th)}
  function tr(r,t){return '<tr>'+r.map(function(x){return '<'+t+' style="border:1px solid #999;padding:3px 6px;text-align:left">'+esc(x)+'</'+t+'>'}).join('')+'</tr>'}
  /* a table as shown: the columns not taken off (Columns), the rows not filtered out or searched away, in their order */
  function view(tb,f){var hs=[].slice.call(tb.tHead.rows[0].cells),on=[];
    hs.forEach(function(h,i){if(getComputedStyle(h).display!=='none')on.push(i)});
    return [on.map(function(i){return head(hs[i])}),
      [].filter.call(tb.tBodies[0].rows,function(r){return !r.hidden}).map(function(r){return on.map(function(i){return f(r.cells[i])})})]}
  /* a section as text and HTML: its lines as a list, its table (as shown) as rows with tabs between the columns, its
     cards one after the other; its words (what the section is) only when it has nothing else */
  function grab(body){var P=[],H=[],W=[],WH=[];
    [].forEach.call(body.children,function(el){
      if(el.classList.contains('tools2')||el.hidden)return;
      if(el.tagName==='UL'){var li=[].filter.call(el.children,function(x){return !x.hidden}).map(one).filter(Boolean);
        P.push(li.map(function(x){return '- '+x}).join('\n'));H.push('<ul>'+li.map(function(x){return '<li>'+esc(x)+'</li>'}).join('')+'</ul>');return}
      if(el.classList.contains('tw')){var v=view(el.querySelector('table'),one),hd=v[0],rs=v[1];
        P.push([hd].concat(rs).map(function(r){return r.join('\t')}).join('\n'));
        H.push('<table style="border-collapse:collapse">'+tr(hd,'th')+rs.map(function(r){return tr(r,'td')}).join('')+'</table>');return}
      if(el.classList.contains('cards')){[].forEach.call(el.querySelectorAll('.todo'),function(a){if(a.hidden)return;
          var l=[one(a.querySelector('.row1')),one(a.querySelector('h3')),one(a.querySelector('.subj')),one(a.querySelector('.next'))].filter(Boolean),
              pre=a.querySelector('pre'),code=pre?pre.textContent:'';
          P.push(l.join('\n')+(code?'\n'+code:''));H.push(l.map(function(x){return '<p>'+esc(x)+'</p>'}).join('')+(code?'<pre>'+esc(code)+'</pre>':''))});return}
      var t=one(el);if(t){W.push(t);WH.push('<p>'+esc(t)+'</p>')}});
    return P.length?[P.join('\n\n'),H.join('')]:[W.join('\n\n'),WH.join('')]}
  all('.code .copy').forEach(function(c){c.hidden=false;c.addEventListener('click',function(){
    put(c,c.previousElementSibling.textContent,'')})});
  /* a command in a table (Look further, Command, Hint, Next step): its Copy button, the command alone */
  all('.ccopy').forEach(function(c){c.hidden=false;c.addEventListener('click',function(){put(c,c.previousElementSibling.textContent.trim(),'')})});
  all('.tcopy').forEach(function(c){c.hidden=false;c.addEventListener('click',function(){put(c,c.getAttribute('data-copy'),'')})});
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
  fit();
  document.addEventListener('click',function(e){all('details.cols[open]').forEach(function(d){if(!d.contains(e.target))d.open=false})});
  /* a row is shown unless the table's filter or the search takes it away */
  function vis(r){var h=!!(r._f||r._s);if(r.hidden!==h)r.hidden=h}
  var counts=[];
  all('.tw table').forEach(function(tb){
    var body=tb.tBodies[0],rows=[].slice.call(body.rows);
    /* a header's click sorts by its column, the next one the other way; the arrows as characters (textContent: a page
       under Trusted Types sorts too). A first click on a column whose values are already in order as the rows stand
       now (the VM tables come sorted by VM), not all the same (empty cells aside), sorts it the other way, so the click
       always shows. The
       whole header cell is the target, not only its button */
    tb.querySelectorAll('th button').forEach(function(btn,i){var th=btn.parentNode;
      th.addEventListener('click',function(e){if(!btn.contains(e.target))btn.click()});
      btn.addEventListener('click',function(){
      var by=rows.map(function(r){return [r,skey(cval(r.cells[i]))]}),
          order=function(asc){return by.slice().sort(function(a,b){return scmp(a[1],b[1],asc)}).map(function(x){return x[0]})},
          s=th.getAttribute('aria-sort'),asc=s!=='ascending',
          now=[].map.call(body.rows,function(r){return skey(cval(r.cells[i]))}),inc=true,diff=false;
      for(var j=1;j<now.length;j++){var c=scmp(now[j-1],now[j],true);if(c>0)inc=false;if(c<0&&now[j]!==null)diff=true}
      if(!s&&inc&&diff)asc=false;
      tb.querySelectorAll('th').forEach(function(h){var a=h.querySelector('.sort');h.removeAttribute('aria-sort');if(a)a.textContent='\u2195'});
      th.setAttribute('aria-sort',asc?'ascending':'descending');btn.querySelector('.sort').textContent=asc?'\u25b2':'\u25bc';
      order(asc).forEach(function(r){body.appendChild(r)})})});
    var tools=tb.parentNode.previousElementSibling;
    if(tools&&tools.classList.contains('tools2')){var f=tools.querySelector('.filter'),n=tools.querySelector('.shown');
      if(!f)return;
      /* an older page: the whole row of tools was hidden */
      tools.hidden=false;f.hidden=false;
      var count=function(){var k=0;rows.forEach(function(r){if(!r.hidden)k++});n.textContent=k<rows.length?k+' of '+rows.length:''};
      counts.push(count);
      f.addEventListener('input',function(){var q=f.value.trim().toLowerCase();
        rows.forEach(function(r){r._f=!!q&&r.textContent.toLowerCase().indexOf(q)<0;vis(r)});count()})}
  });
  /* the search: every section's rows, lines and cards that have the words, the others put away; a section without
     any, away too (all of it when its title has them); the matches marked where the browser can (CSS highlights) */
  var find=document.getElementById('find');
  if(find){var box=find.closest('.find'),found=box.querySelector('.found'),secs=all('details.sec'),tm,
      HL=window.CSS&&CSS.highlights&&window.Highlight;
    box.hidden=false;
    /* what an item says, in lower case, once: a row its cells; a line or a card its words but its buttons' (Copy) */
    var low=function(el){if(el._t===undefined){var s=el.textContent||'';
        if(el.tagName!=='TR'){s='';var w=document.createTreeWalker(el,NodeFilter.SHOW_TEXT,null),t;
          while((t=w.nextNode()))if(!t.parentNode.closest('button'))s+=t.nodeValue+' '}
        el._t=s.toLowerCase()}return el._t};
    /* a section's title, without its row count */
    var title=function(d){var h=d.querySelector('summary h2'),f=h&&h.firstChild;
      return (f&&f.nodeType===3?f.textContent:(h?h.textContent:'')).toLowerCase()};
    var mark=function(q){if(!HL)return;CSS.highlights['delete']('site-find');if(!q)return;var h=new Highlight(),n=0;
      secs.forEach(function(d){if(d.hidden||n>=3000)return;var w=document.createTreeWalker(d.querySelector('.body')||d,NodeFilter.SHOW_TEXT,null),t;
        while(n<3000&&(t=w.nextNode())){var pe=t.parentNode;if(pe.closest('[hidden],button,svg,summary,.tools2'))continue;
          var s=t.nodeValue.toLowerCase(),i=s.indexOf(q);
          while(i>=0&&n<3000){var r=document.createRange();r.setStart(t,i);r.setEnd(t,i+q.length);h.add(r);n++;i=s.indexOf(q,i+q.length)}}});
      CSS.highlights.set('site-find',h)};
    var run=function(){var q=find.value.trim().toLowerCase(),total=0,ns=0;
      secs.forEach(function(d){var body=d.querySelector('.body'),k=0;if(!body)return;
        var whole=!q||title(d).indexOf(q)>=0;
        all('.tw tbody tr',body).forEach(function(r){var on=whole||low(r).indexOf(q)>=0;r._s=!on;vis(r);if(on&&!r.hidden)k++});
        all('ul.lines>li,ul.passed>li,.todo',body).forEach(function(el){var on=whole||low(el).indexOf(q)>=0;if(el.hidden===on)el.hidden=!on;if(on)k++});
        [].forEach.call(body.children,function(el){if(el.tagName==='P'&&low(el).indexOf(q)>=0)k++});
        var away=!!q&&!k&&!whole;if(d.hidden!==away)d.hidden=away;
        if(q&&!d.hidden){d.open=true;total+=k;ns++}
        (links[d.id]||[]).forEach(function(a){var m=a.querySelector('.m');a.classList.toggle('off',d.hidden);
          if(m)m.textContent=q&&!d.hidden?String(k):''})});
      found.textContent=q?(ns?total+' found in '+ns+' section'+(ns===1?'':'s'):'Nothing found'):'';
      box.classList.toggle('none-found',!!q&&!ns);
      counts.forEach(function(c){c()});mark(q);
      if(window.requestAnimationFrame)requestAnimationFrame(function(){fit()});else fit()};
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
  });
})();
