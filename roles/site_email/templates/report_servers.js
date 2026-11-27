/* Extra controls for the Servers section, in the same published script as the table controls. */
(function(){
  if(window.siteReportServers)return;
  window.siteReportServers=true;

  function setup(){
    var select=document.querySelector('.rail-sel');
    if(select){
      select.closest('.rail-mob').hidden=false;
      select.addEventListener('change',function(){
        var link=document.querySelector('.rail a[href="#'+select.value+'"],.nav a[href="#'+select.value+'"]');
        if(link)link.click();
        else if(select.value==='top')window.scrollTo(0,0);
      });
      var rail=document.querySelector('.rail');
      if(rail&&window.MutationObserver)new MutationObserver(function(){
        var current=rail.querySelector('a.on');
        if(current)select.value=current.hash.slice(1);
      }).observe(rail,{subtree:true,attributes:true,attributeFilter:['class']});
    }

    document.querySelectorAll('table.srv-tbl').forEach(function(table){
      var tools=table.closest('.srv-sec').querySelector('.srv-tools');
      var api=window.siteReport;
      function rows(){return table.querySelectorAll('tbody>tr[data-sev]')}
      function expand(){if(table._expand)table._expand()}
      function toggle(row,open){
        var button=row.querySelector('.xb');
        if(button&&open!==(button.getAttribute('aria-expanded')==='true'))button.click();
      }
      table.addEventListener('click',function(event){
        var row=event.target.closest('tr[data-sev]');
        if(!row)return;
        if(event.target.closest('.xb')){
          /* The shared expander handles the click on document and unpacks the detail. */
          Promise.resolve().then(function(){
            var detail=row._x&&row._x.querySelector('.srv-det');
            if(detail&&row._o)detail.open=true;
          });
        }else if(!event.target.closest('a,button,input,select,label,summary')){
          row.querySelector('.xb').click();
        }
      });
      tools.querySelectorAll('.srv-exp,.srv-col').forEach(function(button){
        button.hidden=false;
        button.addEventListener('click',function(){
          var open=button.classList.contains('srv-exp');
          if(open)expand();
          rows().forEach(function(row){if(!open||!row.hidden)toggle(row,open)});
        });
      });
      /* A published detail stores its text nodes after the shape index in each node array. */
      function nodeText(node){return typeof node==='string'?node:node.slice(1).map(nodeText).join('')}
      tools.querySelector('.filter').addEventListener('input',function(){
        var query=this.value.trim().toLowerCase();
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
      /* Replace the generic three-severity picker with the statuses of the entire fleet. */
      var group=tools.parentNode.querySelector('.pt-sev:not(.srv-severity)');
      if(group)group.remove();
      var picker=tools.parentNode.querySelector('.srv-severity');
      if(!picker)return;
      picker.hidden=false;
      picker.addEventListener('click',function(event){
        var button=event.target.closest('button');
        if(!button)return;
        expand();
        var severity=button.getAttribute('data-sev');
        table._cf=!!severity;
        picker.querySelectorAll('button').forEach(function(b){b.setAttribute('aria-pressed',String(b===button))});
        rows().forEach(function(row){row._sv=!!severity&&row.getAttribute('data-sev')!==severity;api.vis(row)});
        api.rowsChanged();
      });
    });
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);
  else setup();
})();
