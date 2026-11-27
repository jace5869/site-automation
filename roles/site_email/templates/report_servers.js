/* 1.138.0: Controls for Servers, Cert Rollup, Chrome-only bars, and Glass web view.
   ASCII-only, strict ES5, idempotent, no HTML sinks. */
(function(){
  if(window.siteReportServers)return;
  window.siteReportServers=true;

  function toList(nl){return Array.prototype.slice.call(nl||[])}

  function applyVis(row,api){
    if(api&&api.vis){api.vis(row);return}
    row.hidden=!!(row._f||row._sv||row._hr||row._cf);
    if(row._x)row._x.hidden=row.hidden||!row._o;
  }

  function notifyChanged(api){if(api&&api.rowsChanged)api.rowsChanged()}

  function setup(){
    var api=window.siteReport||null;

    /* Narrow-screen section jump select (800px and below) */
    var select=document.querySelector('.rail-sel');
    if(select){
      var mob=select.closest('.rail-mob');
      if(mob)mob.hidden=false;
      select.addEventListener('change',function(){
        var link=document.querySelector('.rail a[href="#'+select.value+'"],.nav a[href="#'+select.value+'"]');
        if(link){link.click();return}
        if(select.value==='top'){window.scrollTo(0,0);return}
        var target=document.getElementById(select.value);
        if(target){if(target.tagName==='DETAILS')target.open=true;target.scrollIntoView()}
      });
      var rail=document.querySelector('.rail');
      if(rail&&window.MutationObserver)new MutationObserver(function(){
        var current=rail.querySelector('a.on');
        if(current)select.value=current.hash.slice(1);
      }).observe(rail,{subtree:true,attributes:true,attributeFilter:['class']});
    }

    /* Standalone fallback for Glass web view (when site_report.js is not loaded) */
    if(!api){
      document.documentElement.classList.add('js');
      document.addEventListener('click',function(ev){
        var xb=ev.target.closest('button.xb');
        if(xb){
          var tr=xb.closest('tr');
          var xr=tr&&tr.nextElementSibling;
          if(xr&&xr.classList.contains('xr')){
            var open=xb.getAttribute('aria-expanded')!=='true';
            xb.setAttribute('aria-expanded',String(open));
            tr._x=xr;tr._o=open;
            xr.hidden=!open;
            var det=xr.querySelector('.srv-det');
            if(det&&open)det.open=true;
          }
          return;
        }
        var cp=ev.target.closest('button.copy');
        if(cp){
          var code=cp.getAttribute('data-copy');
          if(!code){
            var box=cp.closest('.code');
            var pre=box&&box.querySelector('pre');
            code=pre?pre.textContent:'';
          }
          if(code&&navigator.clipboard&&navigator.clipboard.writeText){
            navigator.clipboard.writeText(code).then(function(){
              var old=cp.textContent;cp.textContent='Copied';
              setTimeout(function(){cp.textContent=old},1400);
            },function(){});
          }
        }
      });
      toList(document.querySelectorAll('button.copy')).forEach(function(b){b.hidden=false});
      toList(document.querySelectorAll('input.filter')).forEach(function(inp){inp.hidden=false});
      var pr=document.getElementById('print');
      if(pr){pr.hidden=false;pr.addEventListener('click',function(){window.print()})}
      toList(document.querySelectorAll('input[name="site-report-theme"]')).forEach(function(r){
        var cur=document.documentElement.getAttribute('data-theme')||'auto';
        if(r.value===cur)r.checked=true;
        r.addEventListener('change',function(){
          if(r.value==='auto')document.documentElement.removeAttribute('data-theme');
          else document.documentElement.setAttribute('data-theme',r.value);
          try{localStorage.setItem('site-report-theme',r.value)}catch(e){}
        });
      });
    }

    /* Server-first tables (standard & list-only) */
    toList(document.querySelectorAll('table.srv-tbl')).forEach(function(table){
      var sec=table.closest('.srv-sec');
      var tools=sec?sec.querySelector('.srv-tools'):null;
      function rows(){return toList(table.querySelectorAll('tbody>tr[data-sev]'))}
      function expand(){if(table._expand)table._expand()}
      function toggle(row,open){
        var button=row.querySelector('.xb');
        if(button&&open!==(button.getAttribute('aria-expanded')==='true'))button.click();
      }
      table.addEventListener('click',function(event){
        var row=event.target.closest('tr[data-sev]');
        if(!row)return;
        if(event.target.closest('.xb')){
          setTimeout(function(){
            var xr=row._x||row.nextElementSibling;
            var detail=xr&&xr.querySelector('.srv-det');
            if(detail&&row._o)detail.open=true;
          },0);
        }else if(!event.target.closest('a,button,input,select,label,summary')){
          var xb=row.querySelector('.xb');
          if(xb)xb.click();
        }
      });
      if(tools){
        toList(tools.querySelectorAll('.srv-exp,.srv-col')).forEach(function(button){
          button.hidden=false;
          button.addEventListener('click',function(){
            var open=button.classList.contains('srv-exp');
            if(open)expand();
            rows().forEach(function(row){if(!open||!row.hidden)toggle(row,open)});
          });
        });
        function nodeText(node){return typeof node==='string'?node:node.slice(1).map(nodeText).join('')}
        var inp=tools.querySelector('.filter');
        if(inp){
          inp.hidden=false;
          inp.addEventListener('input',function(){
            var query=this.value.trim().toLowerCase();
            if(!api){
              rows().forEach(function(row){
                if(!query){row._f=false;applyVis(row,null);return}
                var xr=row._x||row.nextElementSibling;
                var txt=(row.textContent+' '+(xr?xr.textContent:'')).toLowerCase();
                row._f=txt.indexOf(query)<0;
                applyVis(row,null);
              });
              return;
            }
            if(!query)return;
            rows().forEach(function(row){
              if(!row._f)return;
              if(row._srvText===undefined){
                var detail=row._detail?JSON.parse(row._detail.textContent)[1].map(nodeText).join(''):
                  row._x?row._x.textContent:'';
                row._srvText=detail.toLowerCase();
              }
              if(row._srvText.indexOf(query)>=0){row._f=false;api.vis(row)}
            });
            api.rowsChanged();
          });
        }
        var group=tools.parentNode.querySelector('.pt-sev:not(.srv-severity)');
        if(group&&group.parentNode)group.parentNode.removeChild(group);
        var picker=tools.parentNode.querySelector('.srv-severity');
        if(picker){
          picker.hidden=false;
          picker.addEventListener('click',function(event){
            var button=event.target.closest('button');
            if(!button)return;
            expand();
            var severity=button.getAttribute('data-sev')||'';
            var wantChg=button.getAttribute('data-changed')==='1';
            table._cf=!!(severity||wantChg);
            toList(picker.querySelectorAll('button')).forEach(function(b){b.setAttribute('aria-pressed',String(b===button))});
            rows().forEach(function(row){
              if(wantChg)row._sv=row.getAttribute('data-changed')!=='1';
              else row._sv=!!severity&&row.getAttribute('data-sev')!==severity;
              applyVis(row,api);
            });
            notifyChanged(api);
          });
        }
      }
    });

    /* Step 12: Certificate host rollup -> click host to filter certificate tables below */
    toList(document.querySelectorAll('[data-cert-rollup]')).forEach(function(sec){
      var items=toList(sec.querySelectorAll('.srv-rollup-item'));
      var qInp=sec.querySelector('.srv-rollup-q');
      var clr=sec.querySelector('.srv-rollup-clear');
      var sevBar=sec.querySelector('.srv-rollup-sev');
      var selectedHost='';
      var selectedSev='';
      var query='';

      function certRows(){return toList(document.querySelectorAll('table:not(.srv-tbl) tbody>tr[data-host]'))}

      function filterRollup(){
        items.forEach(function(it){
          var okSev=!selectedSev||it.getAttribute('data-sev')===selectedSev;
          var okQ=!query||it.textContent.toLowerCase().indexOf(query)>=0;
          it.hidden=!(okSev&&okQ);
        });
      }

      function applyHostFilter(host){
        selectedHost=host;
        items.forEach(function(it){
          var on=!!host&&it.getAttribute('data-host')===host;
          it.setAttribute('aria-pressed',String(on));
          var act=it.querySelector('.srv-rollup-act');
          if(act)act.textContent=on?'Showing (click to clear)':'Filter table ->';
        });
        if(clr)clr.hidden=!host;
        certRows().forEach(function(row){
          var tbl=row.closest('table');
          if(tbl&&tbl._expand)tbl._expand();
          row._hr=!!host&&row.getAttribute('data-host')!==host;
          if(api&&api.vis){
            row._sv=row._hr;
            api.vis(row);
          }else{
            applyVis(row,null);
          }
        });
        notifyChanged(api);
      }

      items.forEach(function(it){
        it.addEventListener('click',function(){
          var h=it.getAttribute('data-host')||'';
          applyHostFilter(selectedHost===h?'':h);
        });
      });
      if(clr){
        clr.addEventListener('click',function(){applyHostFilter('')});
      }
      if(qInp){
        qInp.hidden=false;
        qInp.addEventListener('input',function(){query=this.value.trim().toLowerCase();filterRollup()});
      }
      if(sevBar){
        sevBar.hidden=false;
        sevBar.addEventListener('click',function(ev){
          var b=ev.target.closest('button');
          if(!b)return;
          selectedSev=b.getAttribute('data-sev')||'';
          toList(sevBar.querySelectorAll('button')).forEach(function(x){x.setAttribute('aria-pressed',String(x===b))});
          filterRollup();
        });
      }
    });

    /* Chrome-only unified search, severity chips & expand/collapse (ONTAP, UCS, Netmap) */
    toList(document.querySelectorAll('[data-chrome-bar]')).forEach(function(bar){
      bar.hidden=false;
      var qInp=bar.querySelector('.srv-chrome-q');
      var shownEl=bar.querySelector('.srv-chrome-shown');
      var sevBar=bar.querySelector('.srv-chrome-sev');
      var curQ='';
      var curSev='';

      function allRows(){return toList(document.querySelectorAll('details.sec table tbody>tr[data-sev]'))}

      function applyChrome(){
        var total=0,vis=0;
        allRows().forEach(function(row){
          var tbl=row.closest('table');
          if(tbl&&tbl._expand)tbl._expand();
          total++;
          var matchSev=!curSev||row.getAttribute('data-sev')===curSev;
          var matchQ=!curQ||row.textContent.toLowerCase().indexOf(curQ)>=0;
          row._sv=!matchSev;
          row._f=!matchQ;
          applyVis(row,api);
          if(matchSev&&matchQ){
            vis++;
            var d=row.closest('details.sec');
            if(d&&(curQ||curSev))d.open=true;
          }
        });
        if(shownEl)shownEl.textContent=(curQ||curSev)?(vis+' of '+total+' shown'):'';
        notifyChanged(api);
      }

      if(qInp){
        qInp.addEventListener('input',function(){curQ=this.value.trim().toLowerCase();applyChrome()});
      }
      if(sevBar){
        sevBar.addEventListener('click',function(ev){
          var b=ev.target.closest('button');
          if(!b)return;
          curSev=b.getAttribute('data-sev')||'';
          toList(sevBar.querySelectorAll('button')).forEach(function(x){x.setAttribute('aria-pressed',String(x===b))});
          applyChrome();
        });
      }
      toList(bar.querySelectorAll('.srv-chrome-exp,.srv-chrome-col')).forEach(function(btn){
        btn.addEventListener('click',function(){
          var open=btn.classList.contains('srv-chrome-exp');
          toList(document.querySelectorAll('details.sec')).forEach(function(d){d.open=open});
        });
      });
    });
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);
  else setup();
})();
