/* ---- import: drag & drop files or folders ---- */
let picked=[];
async function walk(entry, path, out){
  if(entry.isFile){await new Promise(r=>entry.file(f=>{out.push([f,path+f.name]);r()},()=>r()));}
  else if(entry.isDirectory){const rd=entry.createReader();let b;
    do{b=await new Promise(r=>rd.readEntries(r,()=>r([])));for(const e of b)await walk(e,path+entry.name+'/',out)}while(b.length)}
}
function setPicked(list){
  picked=list.filter(([f,p])=>/\.(jpe?g|png|webp)$/i.test(f.name)&&!p.split('/').some(x=>x.startsWith('.')));
  const box=document.getElementById('pick');
  if(!picked.length){box.innerHTML='<div class="sum err">没有找到图片（支持 jpg / png / webp）</div>';return}
  const groups=Object.create(null);let loose=0;
  for(const [f,p] of picked){const s=p.split('/');if(s.length>1){const g=s[s.length-2];groups[g]=(groups[g]||0)+1}else loose++}
  const names=Object.keys(groups);
  let h='<div class="sum">';
  if(names.length)h+=`<p><b>${names.length} 个文件夹 → ${names.length} 个产品：</b>${names.map(n=>esc(n)+'（'+groups[n]+' 张）').join('、')}</p>`;
  if(loose)h+=`<p><b>${loose} 张散图：</b>
    <label><input type="radio" name="mode" value="one" checked> 是同一个产品的几张图（不同角度/颜色）</label>
    <label style="margin-left:14px"><input type="radio" name="mode" value="each"> 每张图是一个产品</label></p>`;
  h+='<button class="p" id="go" onclick="doUpload()">开始导入</button> <button onclick="setPickedClear()">取消</button></div>';
  box.innerHTML=h;
}
function setPickedClear(){picked=[];document.getElementById('pick').innerHTML=''}
async function doUpload(){
  const fd=new FormData();
  const b=document.getElementById('go');b.disabled=true;b.textContent='上传中…';
  for(const [f,p] of picked){fd.append('files',new Blob([await f.arrayBuffer()],{type:f.type||'image/jpeg'}),f.name);fd.append('paths',p)}
  const m=document.querySelector('input[name=mode]:checked');fd.append('mode',m?m.value:'one');
  const r=await fetch('/upload',{method:'POST',body:fd});const j=await r.json();
  if(j.error){alert(j.error);b.disabled=false;b.textContent='开始导入';return}
  location.href=j.go;
}
document.addEventListener('DOMContentLoaded',()=>{
  const d=document.getElementById('drop');if(!d)return;
  d.addEventListener('dragover',e=>{e.preventDefault();d.classList.add('over')});
  d.addEventListener('dragleave',()=>d.classList.remove('over'));
  d.addEventListener('drop',async e=>{e.preventDefault();d.classList.remove('over');
    const out=[];const items=[...(e.dataTransfer.items||[])].map(i=>i.webkitGetAsEntry&&i.webkitGetAsEntry()).filter(Boolean);
    if(items.length){for(const it of items)await walk(it,'',out)}else{for(const f of e.dataTransfer.files)out.push([f,f.name])}
    setPicked(out)});
  document.getElementById('pickFiles').onchange=e=>setPicked([...e.target.files].map(f=>[f,f.name]));
  document.getElementById('pickDir').onchange=e=>setPicked([...e.target.files].map(f=>[f,f.webkitRelativePath||f.name]));
});

function esc(value) {
  const span = document.createElement('span');
  span.textContent = value ?? '';
  return span.innerHTML;
}

function post(url, data) {
  return fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data || {})
  }).then(response => {
    if (!response.ok) throw new Error('提交失败');
    return response.json();
  });
}

function jobError(message) {
  if (!message) return '处理失败，请重试。';
  if (/gpt[-\w.]*|Error code:|Traceback|https?:\/\//i.test(message)) {
    return '生成服务暂时无法处理，请检查设置后重试。';
  }
  return message;
}

const progressRegion = document.getElementById('production-progress');
const jobActions = {
  '生成套图': '正在制作套图',
  '出图': '正在生成图片',
  '重出': '正在重出',
  '识别标签': '正在识别包装文字',
  '卖点提炼': '正在提炼商品卖点',
  '做计划': '正在制定套图计划'
};
let progressMarkup = '';

function showProgress(entries) {
  const markup = entries.map(entry => {
    const {title, detail = '', done = 0, total = 0} = entry;
    const known = done > 0 && total >= done;
    const value = known
      ? ` aria-valuemax="${total}" aria-valuenow="${done}" aria-valuetext="已处理 ${done} / ${total} 张"`
      : '';
    const fill = known ? ` style="width:${100 * done / total}%"` : '';
    return `<div class="progress-job">
      <div class="progress-copy" role="status"><strong>${esc(title)}</strong>
      <span>${esc(known ? `已处理 ${done} / ${total} 张` : detail)}</span></div>
      <div class="progress-track" role="progressbar" aria-label="${esc(title)}"${value}><span${fill}></span></div>
      </div>`;
  }).join('');
  if (markup !== progressMarkup) {
    progressRegion.innerHTML = markup;
    progressMarkup = markup;
  }
  progressRegion.hidden = !entries.length;
}

async function pollJobs(sku) {
  const locationNow = new URL(location.href);
  const trackedId = locationNow.searchParams.get('job');
  const review = document.querySelector('[data-review-wall]');
  let seenRunning = false;
  if (trackedId) showProgress([{title: '正在读取任务状态'}]);

  async function tick() {
    let delay = 5000;
    try {
      const response = await fetch('/api/jobs' + (sku ? '?sku=' + encodeURIComponent(sku) : ''), {cache: 'no-store'});
      if (!response.ok) throw new Error('无法读取任务');
      const jobs = await response.json();
      const running = jobs.filter(job => job.state === 'running');
      const tracked = jobs.find(job => job.id === trackedId);
      if (tracked && tracked.state !== 'running') {
        locationNow.searchParams.delete('job');
        locationNow.searchParams.set('msg', tracked.state === 'error'
          ? jobError(tracked.error)
          : tracked.kind + '已完成。');
        location.replace(locationNow);
        return;
      }
      if (trackedId && !tracked) {
        locationNow.searchParams.delete('job');
        locationNow.searchParams.set('msg', '任务记录已失效，请查看当前制作结果。');
        location.replace(locationNow);
        return;
      }
      const visible = review ? running.filter(job => job.kind !== '重出') : running;
      const entries = visible.slice(0, 3).map(job => ({
        title: jobActions[job.kind] || '正在处理',
        detail: [!sku && job.sku, job.variant && '变体 ' + job.variant,
          job.slot && '图位 ' + String(job.slot).padStart(2, '0')].filter(Boolean).join(' · '),
        done: job.done,
        total: job.total
      }));
      if (visible.length > 3) entries.push({title: `另有 ${visible.length - 3} 项正在处理`});
      if (!progressRegion.dataset.submitting) showProgress(entries);
      document.getElementById('jobs').innerHTML = jobs.slice(0, 8).map(job => {
        const status = job.state === 'error' ? jobError(job.error)
          : job.state === 'done' ? '已完成' : '进行中';
        const task = [job.kind, job.sku, job.variant, job.slot && '图位 ' + job.slot].filter(Boolean).join(' · ');
        return `<li><span>${esc(task)}</span><span class="${job.state === 'error' ? 'err' : 'mute'}">${esc(status)}</span></li>`;
      }).join('') || '<li class="mute">暂无任务记录</li>';
      if (review) updateRedoCards(jobs);
      else if (sku && seenRunning && !running.length && !progressRegion.dataset.submitting) {
        location.reload();
        return;
      }
      seenRunning = running.length > 0;
      if (running.length || document.querySelector('[data-redo-id]')) delay = 1500;
    } catch {
      delay = 3500;
    }
    setTimeout(tick, delay);
  }

  await tick();
}

function showBig(urls, captions) {
  const dialog = document.getElementById('modal');
  const figures = urls.map((url, index) => {
    const figure = document.createElement('figure');
    const caption = document.createElement('figcaption');
    const image = document.createElement('img');
    caption.textContent = captions[index];
    image.alt = captions[index];
    image.src = url;
    figure.append(caption, image);
    return figure;
  });
  dialog.querySelector('.imgs').replaceChildren(...figures);
  dialog.showModal();
}

function redoFeedback(card, message, state) {
  const running = state === 'running';
  const feedback = card.querySelector('.review-feedback');
  feedback.textContent = message;
  feedback.classList.toggle('err', state === 'error');
  card.querySelector('.progress-track').hidden = !running;
  card.querySelectorAll('[data-review-control]').forEach(button => button.disabled = running);
}

async function refreshReviewCard(card) {
  const {sku, v, slot} = card.dataset;
  const url = new URL('/review', location.origin);
  url.searchParams.set('show', 'all');
  url.searchParams.set('sku', sku);
  const response = await fetch(url);
  if (!response.ok) throw new Error('预览刷新失败');
  const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
  const replacement = [...doc.querySelectorAll('.cell[data-slot]')].find(
    element => element.dataset.sku === sku && element.dataset.v === v && element.dataset.slot === slot
  );
  if (!replacement) {
    delete card.dataset.redoId;
    redoFeedback(card, '已完成，刷新审核页查看。', 'done');
    return;
  }
  const group = card.closest('[data-review-group]');
  const tally = replacement.closest('[data-review-group]').querySelector('[data-review-count]');
  group.querySelector('[data-review-count]').textContent = tally.textContent;
  const keepFocus = card.contains(document.activeElement);
  card.replaceWith(replacement);
  if (keepFocus) replacement.focus({preventScroll: true});
}

function updateRedoCards(jobs) {
  document.querySelectorAll('.cell[data-redo-id]').forEach(card => {
    const job = jobs.find(task => task.id === card.dataset.redoId);
    if (!job || job.state === 'running' || card.dataset.refreshing) return;
    if (job.state === 'error') {
      delete card.dataset.redoId;
      redoFeedback(card, jobError(job.error), 'error');
      return;
    }
    card.dataset.refreshing = '1';
    refreshReviewCard(card).catch(() => { delete card.dataset.refreshing; });
  });
}

function closeRedo(button) {
  const editor = button.closest('.redo-editor');
  editor.hidden = true;
  editor.closest('.cell').querySelector('[title="R"]').focus();
}

function act(element, action) {
  const card = element.closest('.cell');
  if (card.dataset.redoId || card.dataset.submitting) return;
  if (action === 'redo') {
    const editor = card.querySelector('.redo-editor');
    editor.hidden = false;
    editor.elements.note.focus();
    return;
  }
  submitReview(card, action);
}

async function submitReview(card, action, note = '') {
  const data = card.dataset;
  if (data.redoId || data.submitting) return;
  data.submitting = '1';
  if (action === 'redo') redoFeedback(card, '正在提交重出', 'running');
  try {
    const result = await post('/api/review', {
      sku: data.sku, variant: data.v, slot: +data.slot, action, note
    });
    if (result.error) redoFeedback(card, jobError(result.error), 'error');
    else if (result.job_id) {
      data.redoId = result.job_id;
      redoFeedback(card, '正在重出', 'running');
    } else await refreshReviewCard(card);
  } catch {
    redoFeedback(card, '提交或刷新失败，请核对任务状态。', 'error');
  } finally {
    delete data.submitting;
  }
}

document.addEventListener('submit', event => {
  if (event.defaultPrevented) return;
  const form = event.target;
  if (form.matches('.redo-editor')) {
    event.preventDefault();
    form.hidden = true;
    const card = form.closest('.cell');
    card.focus({preventScroll: true});
    submitReview(card, 'redo', form.elements.note.value);
    return;
  }
  if (form.method !== 'post') return;
  const path = new URL(form.action).pathname;
  const title = /\/suite\/|\/generate\//.test(path) ? '正在提交套图'
    : path.endsWith('/transcribe') ? '正在提交标签识别'
    : path.endsWith('/product-copy') ? '正在提交商品资料'
    : /\/sku\/[^/]+\/plan\/[^/]+$/.test(path) ? '正在提交套图计划' : '';
  if (!title) return;
  if (progressRegion.dataset.submitting) {
    event.preventDefault();
    return;
  }
  progressRegion.dataset.submitting = '1';
  showProgress([{title}]);
  if (progressRegion.getBoundingClientRect().bottom < 0) {
    progressRegion.scrollIntoView({block: 'start'});
  }
});

document.addEventListener('keydown', event => {
  const card = document.activeElement;
  if (!card?.matches('.cell[data-slot]') || event.metaKey || event.ctrlKey || event.altKey) return;
  const key = event.key.toLowerCase();
  const action = {a: 'approve', r: 'redo', l: 'toggle'}[key];
  if (action && (action !== 'toggle' || card.querySelector('[title="L"]'))) act(card, action);
});

function revealCopy() {
  if (location.hash === '#product-copy') {
    const section = document.getElementById('product-copy');
    if (section) section.open = true;
  }
}

window.addEventListener('hashchange', revealCopy);
window.addEventListener('pageshow', event => {
  if (event.persisted && progressRegion.dataset.submitting) {
    location.reload();
  }
});
revealCopy();
pollJobs(document.body.dataset.sku);
