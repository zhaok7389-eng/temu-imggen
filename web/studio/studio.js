(() => {
  'use strict';

  const {request, skuPath, normalizeRows, toProduct, getCosts} = window.StudioData;
  const $ = selector => document.querySelector(selector);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
  const shortId = value => String(value).padStart(2, '0');
  const money = value => Number.isFinite(value) ? '$' + value.toFixed(2) : '—';
  const post = (url, data) => request(url, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
  });

  const stateNames = {
    new: '待识别', reading: '识别中', confirm: '待确认标签', ready: '待开始',
    queued: '排队中', generating: '生成中', proof: '待校样', done: '已完成',
    error: '识别失败', interrupted: '已中断'
  };
  const slotStates = {
    ready: '待校样', approved: '已通过', skipped: '已跳过', queued: '排队',
    generating: '生成中', empty: '待生成', flagged: '有提示', error: '失败'
  };
  const stageSets = [
    ['资料', ['new', 'reading', 'confirm', 'error']],
    ['构图', ['ready', 'queued', 'generating', 'interrupted']],
    ['校样', ['proof']],
    ['成品', ['done']]
  ];
  const isReviewable = slot => slot?.image && !slot.busy && ['ready', 'flagged'].includes(slot.status);
  const details = new Map();
  const fingerprints = new Map();
  const drafts = new Map();
  const labelModes = new Set();
  const expandedCopy = new Set();
  const busyActions = new Set();
  const changedImages = new Map();
  let rows = [];
  let jobs = [];
  let products = [];
  let capacity = {};
  let generationMode = '';
  let costs = null;
  let selected = null;
  let variant = '';
  let activeSlot = 1;
  let split = 52;
  let filter = 'all';
  let compareMode = 'source';
  let versionIndex = 0;
  let referenceOnly = false;
  let showRedo = false;
  let reviewArchived = false;
  let boardLoaded = false;
  let focusSignature = '';
  let renderedSlot = '';
  let syncInFlight;
  let costSyncInFlight;
  let lastCostRead = 0;
  let pendingFocusRefresh = false;
  let noteDraft = '';
  let redoFields = [];
  let toastTimer;
  const copyErrors = new Map();

  const productFor = id => products.find(product => product.id === id);
  const item = () => productFor(selected);
  const currentSlot = () => item()?.slots.find(slot => slot.id === activeSlot);
  const canTrialFirst = product => product.hasConfirmedLabel && !product.activeJobs.length &&
    product.slots.some(slot => slot.id === 1 && slot.raw && !slot.skip && !slot.busy);
  const budget = () => Number(capacity.budget_usd_per_day);
  const spent = () => Number(capacity.spent_today_usd);
  const budgetExceeded = () => Number.isFinite(budget()) && spent() >= budget();
  const canShowProof = product => product?.stage === 'proof' || (reviewArchived && product?.stage === 'done');
  const isLabelMode = product => product?.stage === 'confirm' ||
    (product?.hasConfirmedLabel && ['ready', 'error', 'interrupted'].includes(product.stage) && labelModes.has(product.id));
  const sortTodo = () => products.filter(product => ['confirm', 'proof'].includes(product.stage))
    .sort((a, b) => Number(a.stage !== 'confirm') - Number(b.stage !== 'confirm')
      || String(a.created || '').localeCompare(String(b.created || '')) || a.id.localeCompare(b.id, 'zh'));

  function toast(message) {
    const box = $('#feedback');
    box.textContent = message;
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.hidden = true; }, 4200);
  }

  function stageLabel(product) {
    const {stage} = product;
    const label = stage === 'error' && !product.canRetryLabel ? '生成失败' : stateNames[stage];
    return `<span class="status ${escape(stage)}">${escape(label || stage)}</span>`;
  }

  function meter(done, total, alert = false) {
    const percent = total ? Math.max(0, Math.min(1, done / total)) : 0;
    return `<span class="meter${alert ? ' alert' : ''}" aria-hidden="true" style="--amount:${percent}"><i></i></span>`;
  }

  function tally(product) {
    const included = product.slots.filter(slot => slot.status !== 'skipped');
    const plan = product.detail?.variants?.[product.variant]?.slots || [];
    const counts = product.variantSummary || {};
    const made = plan.length ? included.filter(slot => slot.image).length : counts.made || 0;
    const approved = plan.length ? included.filter(slot => slot.status === 'approved').length : counts.approved || 0;
    const total = plan.length ? included.length : counts.slots || product.progress?.total || included.length;
    return {total, made, approved, remaining: Math.max(0, made - approved)};
  }

  function renderTop() {
    const service = $('#service-mode');
    const serviceLabel = {live: '真实出图', demo: '演示模式', unconfigured: '设置出图服务'}[generationMode];
    service.textContent = serviceLabel || '';
    service.dataset.mode = generationMode;
    service.hidden = !serviceLabel;
    const {
      running_sets: running, queued_sets: queued, images_last_minute: minute, images_per_minute: limit,
      use_ratio: ratio
    } = capacity;
    const show = value => Number.isFinite(value) ? value : '—';
    const approaching = Number.isFinite(budget()) && spent() >= budget() * .9;
    const slow = Number.isFinite(limit) && minute >= Math.ceil(limit * (ratio ?? .8));
    const locked = budgetExceeded();
    $('#capacity').innerHTML = `<div class="capacity-group">
      <span class="capacity-mark capacity-stack" aria-hidden="true"><i></i></span>
      <div><small>生成中 / 排队</small><div><strong>${show(running)}</strong><span class="quiet"> 套 / </span><strong>${show(queued)}</strong><span class="quiet"> 套</span></div></div></div>
      <div class="capacity-group"><span class="capacity-mark capacity-pace" aria-hidden="true"><i></i></span>
      <div><small>每分钟已用</small><div><strong>${show(minute)}</strong><span class="quiet"> / ${show(limit)} 张</span>${meter(minute || 0, limit)}</div></div></div>
      <div class="capacity-group"><span class="capacity-mark capacity-budget" aria-hidden="true"><i></i></span>
      <div><small>今日费用 / 预算</small><div><strong>${money(spent())}</strong><span class="quiet"> / ${money(budget())}</span>${meter(spent() || 0, budget(), approaching)}</div></div></div>`;
    const alert = $('#budget-alert');
    alert.hidden = !approaching && !slow;
    alert.textContent = locked ? '今日预算已用完。新生成和重出暂停，已有图片仍可继续校样和归档。'
      : approaching ? '今日费用接近预算，新任务仍可提交。' : slow ? '接近每分钟上限，整套任务自动放慢。' : '';
    $('#choose').disabled = locked;
  }

  function renderWorkflow() {
    const current = item()?.stage;
    const count = stage => products.filter(product => product.stage === stage).length;
    const summaries = [`${count('confirm')} 待确认`, `${count('generating')} 生成 · ${count('queued')} 排队`,
      `${count('proof')} 待校样`, `${count('done')} 已归档`];
    const artwork = [
      ['material', 'proof-source'], ['composition', 'slot-03'],
      ['comparison', 'proof-current', 'proof-previous'], ['finished', 'slot-06']
    ];
    const workflow = $('#workflow');
    const focused = workflow.contains(document.activeElement) ? document.activeElement.dataset.flow : null;
    workflow.innerHTML = stageSets.map(([name, stages], index) => {
      const [style, ...photos] = artwork[index];
      const images = photos.map(photo => `<img src="/studio/assets/${photo}.webp" alt="">`).join('');
      return `<button type="button" class="workflow-phase" data-flow="${index}"
        ${stages.includes(current) ? 'aria-current="step"' : ''}
        ${products.some(product => stages.includes(product.stage)) ? '' : 'disabled'}>
        <span class="phase-art ${style}" aria-hidden="true">${images}</span>
        <span class="phase-caption"><strong>${name}</strong><small>${summaries[index]}</small></span></button>`;
    }).join('');
    if (focused != null) workflow.querySelector(`[data-flow="${focused}"]`)?.focus({preventScroll: true});
  }

  function thumb(url, alt = '') {
    return url ? `<img src="${escape(url)}" alt="${escape(alt)}">` : '<span class="tile-placeholder" aria-hidden="true">—</span>';
  }

  function renderTodo() {
    const todos = sortTodo();
    const list = $('#todo-list');
    const focused = list.contains(document.activeElement) ? document.activeElement.dataset.select : null;
    $('#todo-count').textContent = todos.length;
    list.innerHTML = todos.map(product => `<button class="todo-item" type="button" data-select="${escape(product.id)}"
      ${selected === product.id ? 'aria-current="true"' : ''}>
      ${thumb(product.thumb)}<span><strong>${escape(product.name)}</strong>
      <small>${product.stage === 'confirm' ? '确认标签' : tally(product).remaining ? `校样 ${tally(product).remaining} 张` : '查看校样'}</small></span></button>`).join('')
      || '<p class="todo-empty">没有需要你处理的商品。</p>';
    if (focused) [...list.querySelectorAll('[data-select]')].find(button => button.dataset.select === focused)?.focus({preventScroll: true});
  }

  const filters = [
    ['all', '全部进行中', product => product.stage !== 'done'],
    ['manual', '待处理', product => ['confirm', 'proof'].includes(product.stage)],
    ['production', '自动生产', product => ['new', 'reading', 'ready', 'queued', 'generating'].includes(product.stage)],
    ['error', '异常', product => product.hasProblem || ['error', 'interrupted'].includes(product.stage)],
    ['done', '成品', product => product.stage === 'done']
  ];

  function tile(slot, product) {
    const status = slotStates[slot.status] || '待生成';
    const detail = slot.skipReason || slot.error || slot.flags?.[0] || status;
    const recent = changedImages.get(`${product.id}:${slot.id}`);
    const arrival = recent && performance.now() - recent < 500 ? ' arrived' : '';
    return `<span class="mini-tile ${escape(slot.status)}${arrival}" title="${shortId(slot.id)} ${escape(slot.name)} · ${escape(detail)}">${slot.image
      ? thumb(slot.image, status) : slot.status === 'skipped' ? '—' : shortId(slot.id)}</span>`;
  }

  function renderBoard() {
    const content = $('#board-content');
    const scrollTop = content.dataset.filter === filter ? content.querySelector('.board-scroll')?.scrollTop : 0;
    const focusedProduct = content.contains(document.activeElement) && document.activeElement.closest('.product-button')?.dataset.select;
    const focusedFilter = $('#filters').contains(document.activeElement) && document.activeElement.dataset.filter;
    content.dataset.filter = filter;
    $('#board-count').textContent = `${products.length} 个商品`;
    $('#filters').innerHTML = filters.map(([key, label, test]) => `<button class="filter" type="button" data-filter="${key}"
      aria-pressed="${filter === key}">${label}<span>${products.filter(test).length}</span></button>`).join('');
    if (focusedFilter) $('#filters').querySelector(`[data-filter="${focusedFilter}"]`)?.focus({preventScroll: true});
    const filtered = products.filter(filters.find(([key]) => key === filter)[2]);
    if (!filtered.length) {
      content.innerHTML = `<div class="board-empty"><h2>${products.length ? '当前筛选没有商品' : '上传第一个商品'}</h2>
        <p>${products.length ? '切换筛选继续查看生产记录。' : '拖入一组同一商品的图片，识别完成后确认标签。'}</p>
        ${products.length ? '<button class="button" type="button" data-action="reset-filter">返回全部进行中</button>' : ''}</div>`;
      return;
    }
    const tableRows = filtered.map(product => {
      const {total, made, approved} = tally(product);
      const amount = ['done', 'proof'].includes(product.stage) ? approved : made;
      return `<tr data-product="${escape(product.id)}" aria-selected="${selected === product.id}">
        <td><button type="button" class="product-button" data-select="${escape(product.id)}">${thumb(product.thumb)}
          <span><b title="${escape(product.name)}">${escape(product.name)}</b><small>${escape(product.added || product.id)}</small></span></button></td>
        <td>${stageLabel(product)}</td>
        <td><div class="tile-strip" aria-label="${escape(product.name)}的八个图位">${product.slots.map(slot => tile(slot, product)).join('')}</div></td>
        <td><div class="row-progress">${amount}/${total}${meter(amount, total)}</div></td>
        <td><span class="money"${Number.isFinite(product.cost) ? '' : ' title="尚无该商品的计费记录"'}>${money(product.cost)}</span></td></tr>`;
    }).join('');
    content.innerHTML = `<div class="board-scroll"><table class="board-table">
      <colgroup><col class="product-col"><col class="stage-col"><col class="tiles-col"><col class="progress-col"><col class="cost-col"></colgroup>
      <thead><tr><th>商品</th><th>阶段</th><th>图位 <small>01–08</small></th><th>进度</th><th>费用</th></tr></thead><tbody>${tableRows}</tbody></table></div>
      <div class="board-foot" aria-label="图位状态图例">
        <span class="legend" style="--legend:var(--accent)"><i></i>生成中</span><span class="legend" style="--legend:#aa681f"><i></i>有提示</span>
        <span class="legend" style="--legend:var(--success)"><i></i>已通过</span><span class="legend" style="--legend:#b1a99b"><i></i>跳过</span>
        <span class="quiet">跳过图位不计入进度</span>
      </div>`;
    content.querySelector('.board-scroll').scrollTop = scrollTop || 0;
    if (focusedProduct) [...content.querySelectorAll('.product-button')].find(button => button.dataset.select === focusedProduct)?.focus({preventScroll: true});
  }

  function focusProgress(product) {
    const stats = tally(product);
    const queue = jobs.filter(job => job.kind === '生成套图' && job.state === 'running' && job.phase === 'queued').reverse();
    const ahead = queue.findIndex(entry => entry.sku === product.id && entry.variant === product.variant);
    const waiting = product.stage === 'queued' ? (ahead < 0 || queue.length >= 30 ? '正在排队' : `前面还有 ${ahead} 套等待`)
      : product.stage === 'generating' ? '出一张更新一张' : product.stage === 'reading' ? '正在读取包装标签' : '';
    const latest = product.slots.find(slot => slot.image && !slot.busy) || product.slots.find(slot => slot.image);
    const available = product.stage !== 'reading' && product.stage !== 'generating' && product.stage !== 'queued';
    const hasPlanIssues = product.detail?.variants?.[product.variant]?.issues?.length > 0;
    return `<div class="focus-summary"><h3>${product.stage === 'reading' ? '资料' : '套图进度'}</h3><span class="quiet">${waiting}</span></div>
      ${product.error ? `<div class="focus-alert"><p>${escape(product.error)}</p></div>` : ''}
      ${available && product.canRetryLabel ? `<button class="button primary focus-start" type="button" data-action="retry-label"
        ${budgetExceeded() ? 'disabled' : ''}>${product.stage === 'new' ? '读取包装标签' : '重新识别'}</button>` : ''}
      ${available && product.canResume ? `<div class="focus-start">
        ${hasPlanIssues ? `<a class="button" target="_blank" rel="noopener" href="/sku/${encodeURIComponent(product.id)}?variant=${encodeURIComponent(product.variant)}">查看构图问题</a>` : `<button class="button primary" type="button" data-action="continue-suite" ${budgetExceeded() ? 'disabled' : ''}>
          ${product.generationError ? '继续生成缺少图位' : '开始生成'}</button>`}
        ${product.hasConfirmedLabel && !product.slots.some(slot => slot.image) ? '<button type="button" class="button" data-action="open-label">查看确认标签</button>' : ''}
      </div>` : ''}
      ${latest ? `<div class="progress-preview"><figure class="preview-print">${thumb(latest.image, latest.name)}</figure>
        <div class="preview-caption"><span>${shortId(latest.id)} · ${escape(latest.name)}</span><span><strong>${stats.made}</strong> / ${stats.total} 张</span></div></div>` : ''}
      <div class="focus-progress">${product.slots.map(slot => `<div class="focus-progress-row">${tile(slot, product)}
        <span><strong>${escape(slot.name)}</strong>${slot.skipReason || slot.error ? `<span class="why">${escape(slot.skipReason || slot.error)}</span>` : ''}</span>
        <span class="status ${escape(slot.status)}">${slotStates[slot.status]}</span></div>`).join('')}</div>
      <p class="focus-note">模板待开放</p>
      ${sortTodo().length ? '<div class="needs-next"><span>下一条待办</span><button type="button" class="button slim" data-action="next-todo">继续处理</button></div>' : ''}`;
  }

  function focusDone(product) {
    const {approved} = tally(product);
    return `<div class="focus-summary"><h3>套图 ${escape(product.variant)} · ${approved} 张已通过</h3>
        ${product.archive ? '<span class="status done">自动归档</span>' : ''}</div>
      <div class="archived-grid">${product.slots.map(slot => `<figure class="archived-frame">
        ${slot.image ? thumb(slot.image, slot.name) : `<span class="absent">${escape(slot.skipReason || '无成品')}</span>`}
        <figcaption>${shortId(slot.id)} ${escape(slot.name)}</figcaption></figure>`).join('')}</div>
      ${product.archive ? `<h3>归档文件夹</h3><p class="archive-path" id="archive-path">${escape(product.archive)}</p>` : ''}
      <div class="archive-tools">
        ${product.archive ? '<button class="button primary" data-action="copy-folder" type="button">复制文件夹路径</button>' : ''}
        <button class="button slim" data-action="open-history" type="button">查看校样版本</button>
        <a href="/export" class="button slim" target="_blank" rel="noopener">可选导出</a>
      </div>`;
  }

  function labelDraft(product = item()) {
    if (!isLabelMode(product) || !$('#focus .lc-panel')) return;
    drafts.set(product.id, window.StudioLabel.capture($('#focus'), product));
  }

  function editingFocus() {
    return $('#focus').contains(document.activeElement) &&
      document.activeElement.matches('input, textarea, select, [contenteditable=true]');
  }

  function renderFocus(force = false) {
    const product = item();
    const focus = $('#focus');
    focus.hidden = !product;
    $('#workspace').classList.toggle('no-focus', !product);
    if (!product) {
      focus.innerHTML = '';
      focusSignature = '';
      return;
    }
    const currentDetails = product.detail;
    const copyJob = jobs.find(job => job.sku === product.id && job.kind === '卖点提炼');
    const copyBusy = busyActions.has(product.id) || product.activeJobs.some(job => job.kind === '卖点提炼');
    const copyError = copyErrors.get(product.id) || (copyJob?.state === 'error' ? displayError(copyJob.error) : '');
    const key = JSON.stringify([product.id, product.variant, product.stage, product.error, product.archive,
      activeSlot, split, compareMode, versionIndex, referenceOnly, showRedo, reviewArchived, budgetExceeded(), generationMode,
      product.slots.map(slot => [slot.status, slot.versionKey, slot.image, slot.busy, slot.error, slot.flags]),
      currentDetails?.label?.confirmed_at, currentDetails?.label_draft?.transcribed_at,
      currentDetails?.product_copy?.id, currentDetails?.product_copy?.state, copyError, copyBusy,
      busyActions.has(`${product.id}:${product.variant}:${activeSlot}`), canTrialFirst(product)]);
    if (!force && key === focusSignature) return;
    if (!force && (editingFocus() || showRedo && focus.querySelector('.proof-redo'))) {
      if (budgetExceeded()) focus.querySelectorAll('[data-action="submit-redo"], [data-action="confirm-label"], [data-action="extract-copy"]')
        .forEach(button => { button.disabled = true; });
      pendingFocusRefresh = true;
      return;
    }
    const oldScroll = force ? 0 : focus.querySelector('.focus-inner')?.scrollTop;
    const action = focus.contains(document.activeElement) ? document.activeElement.dataset.action : '';
    const slotKey = `${product.id}:${product.variant}:${activeSlot}`;
    const redoInputs = '.proof-redo input, .proof-redo select';
    const previousRedo = renderedSlot === slotKey && showRedo ? [...focus.querySelectorAll(redoInputs)] : [];
    if (previousRedo.length) redoFields = previousRedo;
    pendingFocusRefresh = false;
    focusSignature = key;
    let inside;
    if (!currentDetails && ['confirm', 'proof', 'done'].includes(product.stage)) {
      inside = '<p class="focus-note" role="status">正在读取商品资料…</p>';
    } else if (isLabelMode(product)) {
      inside = window.StudioLabel.render({
        product, draft: drafts.get(product.id), expandedCopy: expandedCopy.has(product.id),
        budgetExhausted: budgetExceeded(),
        busy: copyBusy, copyError
      });
    } else if (canShowProof(product)) {
      const slot = currentSlot();
      const slotLock = busyActions.has(`${product.id}:${product.variant}:${slot?.id}`);
      inside = window.renderProofPanel({
        product, slot: slotLock ? {...slot, busy: true} : slot, redo: showRedo,
        compareMode, split, versionIndex, referenceOnly, budgetExceeded: budgetExceeded(),
        estimatedImageCost: Number(capacity.est_usd_per_image)
      });
      if (product.canResume) inside += `<div class="focus-alert"><p>${escape(product.generationError || '还有图位未生成，可以只补缺少的图。')}</p>
        <button class="button slim" type="button" data-action="continue-suite" ${budgetExceeded() ? 'disabled' : ''}>补齐缺少图位</button></div>`;
    } else {
      inside = product.stage === 'done' ? focusDone(product) : focusProgress(product);
    }
    const variantOptions = product.variantNames.length > 1
      ? `<label class="focus-variant">套图 <select id="focus-variant">${product.variantNames.map(name =>
        `<option value="${escape(name)}"${name === product.variant ? ' selected' : ''}>${escape(name)}</option>`).join('')}</select></label>`
      : `套图 ${escape(product.variant)}`;
    const trial = canTrialFirst(product) && !isLabelMode(product);
    const estimate = Number(capacity.est_usd_per_image);
    const trialTitle = generationMode === 'demo' ? '生成一张演示首图' :
      Number.isFinite(estimate) ? `只生成首图 01，预计 $${estimate.toFixed(3)}` : '只生成首图 01';
    focus.innerHTML = `<header class="focus-head"><div><h2>${escape(product.name)}</h2>
      <small>${escape(product.id)} · ${variantOptions}</small></div>
      <div class="focus-tools">${stageLabel(product)}${trial ? `<button type="button" class="button slim trial-first"
        data-action="trial-first" title="${escape(trialTitle)}"
        ${budgetExceeded() || !['live', 'demo'].includes(generationMode) || busyActions.has(`${product.id}:${product.variant}:1`) ? 'disabled' : ''}>
        ${generationMode === 'demo' ? '演示首图 · 1 张' : '试出首图 · 1 张'}</button>` : ''}
      <button type="button" class="button slim" data-action="close-focus" aria-label="关闭焦点面板">关闭</button></div></header>
      <section class="focus-inner">${inside}</section>`;
    renderedSlot = slotKey;
    [...focus.querySelectorAll(redoInputs)].forEach((input, index) => {
      const previous = redoFields[index];
      if (!previous || previous.name !== input.name || previous.type !== input.type) return;
      if (input.type === 'checkbox' && previous.value === input.value) input.checked = previous.checked;
      else if (input.type !== 'checkbox') input.value = previous.value;
    });
    if (oldScroll) focus.querySelector('.focus-inner').scrollTop = oldScroll;
    if (!force && action) focus.querySelector(`[data-action="${action}"]:not(:disabled)`)?.focus({preventScroll: true});
  }

  function renderShell(focus = true) {
    renderTop();
    renderWorkflow();
    renderTodo();
    renderBoard();
    if (focus) renderFocus();
  }

  function resetProof() {
    compareMode = 'source';
    versionIndex = 0;
    referenceOnly = false;
    showRedo = false;
    noteDraft = '';
    redoFields = [];
  }

  function setSlot(id) {
    if (!item()?.slots.some(slot => slot.id === id)) return;
    activeSlot = id;
    resetProof();
    renderFocus(true);
  }

  function rebuildProducts() {
    products = rows.map(row => toProduct(row, details.get(row.id), jobs, costs?.get(row.id) ?? null,
      row.id === selected ? variant || 'A' : 'A'));
  }

  function select(id, preferred = '') {
    labelDraft();
    if (!productFor(id)) return;
    selected = id;
    variant = preferred;
    reviewArchived = false;
    rebuildProducts();
    activeSlot = item().slots.find(isReviewable)?.id || item().slots.find(slot => slot.status === 'error')?.id || 1;
    resetProof();
    renderShell(false);
    renderFocus(true);
    if (!item().detail) void refreshProduct(id);
  }

  function setConnection(message) {
    $('#board-connection').textContent = message;
  }

  function fingerprint(row) {
    const recent = jobs.filter(job => job.sku === row.id);
    return JSON.stringify([row.stage, row.variants, row.jobs, row.last_error, row.archived_to, recent]);
  }

  async function loadDetail(id) {
    const row = rows.find(entry => entry.id === id);
    if (!row) return;
    const key = fingerprint(row);
    const data = await request(skuPath(id));
    const before = details.get(id);
    for (const [group, plan] of Object.entries(data.variants || {})) {
      const previous = before?.variants?.[group]?.slots || [];
      for (const slot of plan.slots || []) {
        const oldFile = previous.find(item => item.slot === slot.slot)?.current?.file;
        if (before && slot.current?.file && slot.current.file !== oldFile) changedImages.set(`${id}:${slot.slot}`, performance.now());
      }
    }
    if (id === selected && !before?.variants?.[variant || 'A']?.slots?.some(slot => slot.current)) {
      const plan = data.variants?.[variant || 'A']?.slots || [];
      const first = plan.find(slot => slot.current && slot.status !== 'approved' && !slot.busy);
      if (first) activeSlot = Number(first.slot);
    }
    details.set(id, data);
    fingerprints.set(id, key);
  }

  function updateCosts(changed) {
    if (costSyncInFlight || (Date.now() - lastCostRead < (changed ? 5000 : 30000))) return;
    lastCostRead = Date.now();
    costSyncInFlight = getCosts().then(data => {
      if (data) {
        costs = data;
        rebuildProducts();
        renderShell(false);
      }
    }).finally(() => { costSyncInFlight = null; });
  }

  async function syncBoard() {
    try {
      const [board, recent] = await Promise.all([
        request('/api/board'),
        request('/api/jobs').catch(() => jobs)
      ]);
      jobs = recent;
      const oldSpend = spent();
      capacity = board.capacity || {};
      generationMode = board.generation_mode || '';
      rows = normalizeRows(board, jobs);
      rebuildProducts();
      if (!boardLoaded) {
        boardLoaded = true;
        const first = sortTodo()[0] || products.find(product => product.stage !== 'done') || products[0];
        if (first) {
          selected = first.id;
          if (first.stage === 'done') filter = 'done';
        }
      } else if (selected && !productFor(selected)) selected = null;
      setConnection('');
      renderShell();
      updateCosts(spent() !== oldSpend);
      const priority = rows.find(row => row.id === selected);
      const visible = filters.find(([key]) => key === filter)[2];
      const need = [priority, ...rows.filter(row => row.id !== selected && visible(row)), ...rows]
        .filter(Boolean).filter((row, index, list) => list.indexOf(row) === index)
        .filter(row => fingerprints.get(row.id) !== fingerprint(row));
      if (need.length) {
        await Promise.allSettled(need.slice(0, 10).map(row => loadDetail(row.id)));
        rebuildProducts();
        renderShell();
      }
    } catch (error) {
      setConnection('暂未连接');
      if (!boardLoaded) $('#board-content').innerHTML =
        '<div class="board-empty"><h2>暂时无法读取生产记录</h2><button class="button" type="button" data-action="refresh-board">重试</button></div>';
    }
  }

  function refreshBoard() {
    if (!syncInFlight) syncInFlight = syncBoard().finally(() => { syncInFlight = null; });
    return syncInFlight;
  }

  async function refreshProduct(id) {
    if (syncInFlight) await syncInFlight;
    fingerprints.delete(id);
    await refreshBoard();
    if (!fingerprints.has(id) && rows.some(row => row.id === id)) await loadDetail(id).catch(() => {});
    rebuildProducts();
    renderShell();
  }

  async function poll() {
    await refreshBoard();
    setTimeout(poll, 2000);
  }

  async function runAction(key, operation) {
    if (busyActions.has(key)) return;
    busyActions.add(key);
    try {
      await operation();
    } catch (error) {
      toast(displayError(error.message));
    } finally {
      busyActions.delete(key);
      renderFocus();
    }
  }

  function displayError(text) {
    const message = String(text || '');
    return /\bgpt[-\w.]*|\btoken\b|\bsk-[\w-]+|error code:|traceback/i.test(message)
      ? '任务处理失败，请检查设置后重试。' : message || '操作未完成，请重试。';
  }

  function cycleTodo(direction) {
    const pending = sortTodo();
    if (!pending.length) return;
    const index = pending.findIndex(product => product.id === selected);
    const target = index < 0 ? direction > 0 ? 0 : pending.length - 1
      : (index + direction + pending.length) % pending.length;
    if (filter === 'done') filter = 'all';
    select(pending[target].id);
  }

  function requireBudget() {
    if (budgetExceeded()) throw new Error('今日预算已用完，暂不能开始新任务。');
  }

  function checkLabel(start) {
    const product = item();
    if (!isLabelMode(product) || !product.detail || !document.querySelector('[data-label-line]')) return;
    return runAction(product.id, async () => {
      if (start) requireBudget();
      const label = window.StudioLabel.payload($('#focus'), product);
      const response = await post(skuPath(product.id, '/confirm'), {...label, start, variant: product.variant});
      if (selected === product.id && editingFocus()) document.activeElement.blur();
      drafts.delete(product.id);
      if (start) labelModes.delete(product.id);
      await refreshProduct(product.id);
      if (response.start_error) throw new Error(response.start_error);
      if (response.warnings?.length) toast(response.warnings.join(' · '));
      else if (start) toast('已进入生成队列，可以继续上传。');
      if (start && selected === product.id) {
        const next = sortTodo().find(candidate => candidate.id !== product.id);
        if (next) select(next.id);
      }
    });
  }

  async function extractCopy() {
    const product = item();
    if (!isLabelMode(product) || !product.detail) return;
    labelDraft(product);
    const source = $('#label-copy-source')?.value.trim();
    return runAction(product.id, async () => {
      requireBudget();
      if (!source || source.length > 6000) throw new Error('请输入 1–6000 字的商品资料。');
      const label = window.StudioLabel.payload($('#focus'), product);
      const confirmed = await post(skuPath(product.id, '/confirm'), {...label, start: false, variant: product.variant});
      labelModes.add(product.id);
      expandedCopy.add(product.id);
      copyErrors.delete(product.id);
      try {
        const result = await window.StudioLabel.startExtraction(product.id, source);
        toast(result.message || '已开始提炼商品文案。');
      } catch (error) {
        copyErrors.set(product.id, displayError(error.message));
        throw error;
      } finally {
        await refreshProduct(product.id);
      }
      if (confirmed.warnings?.length) toast(confirmed.warnings.join(' · '));
    });
  }

  function applyCopy(button) {
    const product = item();
    const draftId = button?.dataset.draftId;
    const checked = [...$('#focus').querySelectorAll('[data-label-candidate]:checked')].map(input => input.dataset.labelCandidate);
    if (!product || !draftId || !checked.length) return toast('请勾选要采用的文案。');
    labelDraft(product);
    const draft = drafts.get(product.id);
    return runAction(product.id, async () => {
      const result = await window.StudioLabel.applyExtraction(product.id, draftId, checked);
      if (draft) {
        const appliedFields = {
          benefit: ['allowed_claims'], ingredient: ['ingredient_names', 'ingredientNamesText'],
          usage: ['usage_steps', 'usageStepsText'], capacity: ['capacity'],
          texture: ['texture_caption'], scene: ['scene_caption']
        };
        const items = product.detail?.product_copy?.items || [];
        for (const index of checked) for (const field of appliedFields[items[index]?.kind] || []) delete draft[field];
        delete draft.selectedIndices;
        drafts.set(product.id, draft);
      }
      copyErrors.delete(product.id);
      await refreshProduct(product.id);
      toast(result.message || '已采用勾选的文案。');
    });
  }

  function startOrRetry(action) {
    const product = item();
    if (!product || !(action === 'read-label' ? product.canRetryLabel : product.canResume)) return;
    return runAction(product.id, async () => {
      requireBudget();
      await post(skuPath(product.id, '/' + action), {variant: product.variant});
      labelModes.delete(product.id);
      drafts.delete(product.id);
      await refreshProduct(product.id);
      toast(action === 'start' ? '任务已进入队列。' : '正在读取包装标签。');
    });
  }

  function reviewAction(action) {
    const product = item();
    const slot = currentSlot();
    if (!canShowProof(product) || !slot || slot.busy || versionIndex || slot.status === 'skipped') return;
    if (action === 'approve' && !isReviewable(slot)) return;
    if (action === 'toggle' && !(slot.generated && slot.pasted)) return;
    const key = `${product.id}:${product.variant}:${slot.id}`;
    return runAction(key, async () => {
      const isCurrent = () => selected === product.id && activeSlot === slot.id && item()?.variant === product.variant;
      const data = {sku: product.id, variant: product.variant, slot: slot.id, action};
      if (action === 'redo') {
        requireBudget();
        data.note = $('#redo-note')?.value.trim() || '';
        const edits = window.studioSlotCopy($('#focus'));
        if (Object.keys(edits).length) await post(skuPath(product.id, '/slot-copy'), {
          ...edits, variant: product.variant, slot: slot.id
        });
      }
      await post('/api/review', data);
      if (isCurrent() && action === 'redo') {
        showRedo = false;
        noteDraft = '';
        redoFields = [];
        versionIndex = 0;
        if (editingFocus()) document.activeElement.blur();
      }
      if (isCurrent() && action === 'toggle' && compareMode === 'paste') compareMode = 'source';
      await refreshProduct(product.id);
      if (action === 'redo') toast('单张重出已提交，可继续处理其他图位。');
      if (action === 'approve' && isCurrent()) {
        const latest = item();
        const next = latest.slots.find(candidate => candidate.id > slot.id && isReviewable(candidate))
          || latest.slots.find(isReviewable);
        if (next) setSlot(next.id);
        else if (latest.stage === 'done') {
          const upcoming = sortTodo().find(candidate => candidate.id !== product.id);
          if (upcoming) select(upcoming.id);
          else {
            filter = 'done';
            renderShell();
          }
        }
        toast('已通过并保存到成品文件夹。');
      }
    });
  }

  function trialFirst() {
    const product = item();
    if (!product || !canTrialFirst(product) || !['live', 'demo'].includes(generationMode)) return;
    return runAction(`${product.id}:${product.variant}:1`, async () => {
      requireBudget();
      await post('/api/review', {sku: product.id, variant: product.variant, slot: 1, action: 'trial-first'});
      if (selected === product.id && item()?.variant === product.variant) {
        activeSlot = 1;
        resetProof();
        if (filter === 'done') filter = 'all';
        if (editingFocus()) document.activeElement.blur();
      }
      await refreshProduct(product.id);
      toast('只提交了首图 01。完成后会更新到这个图位。');
    });
  }

  async function upload(images) {
    const files = [...images].filter(file => file?.type.startsWith('image/') ||
      /\.(?:png|jpe?g|webp|heic|heif|gif|bmp|tiff?)$/i.test(file?.name || ''));
    if (!files.length) return toast('请选择商品图片。');
    if (budgetExceeded()) return toast('今日预算已用完，暂不能开始识别。');
    if (files.length > 4) return toast('每次只导入同一商品的 1–4 张图片。');
    const data = new FormData();
    files.forEach(file => data.append('files', file, file.name));
    data.append('name', $('#sku-name').value.trim());
    $('#sku-name').value = '';
    $('#file-input').value = '';
    try {
      const result = await request('/api/intake', {method: 'POST', body: data});
      if (filter === 'done') filter = 'all';
      await refreshProduct(result.sku);
      if (!selected && productFor(result.sku)) select(result.sku);
      toast(result.warning ? displayError(result.warning) : '商品已导入，可以继续上传。');
    } catch (error) {
      toast(displayError(error.message));
    }
  }

  function reviseDraft(kind, button) {
    const product = item();
    labelDraft(product);
    const draft = drafts.get(product?.id);
    if (!draft) return;
    if (kind === 'line') draft.lines.push({text: '', size: 'small'});
    if (kind === 'remove') draft.lines.splice(Number(button.dataset.lineIndex) - 1, 1);
    if (kind === 'claim') {
      const claim = $('#focus [data-label-new-claim]')?.value.trim();
      if (!claim) return;
      draft.claim_candidates = [...new Set([...(draft.claim_candidates || []), claim])];
      draft.allowed_claims = [...new Set([...(draft.allowed_claims || []), claim])];
    }
    renderFocus(true);
    if (kind === 'line') [...$('#focus').querySelectorAll('[data-label-line]')].at(-1)?.focus();
    if (kind === 'claim') $('#focus [data-label-new-claim]')?.focus();
  }

  const actions = {
    'confirm-label': () => checkLabel(true),
    'extract-copy': extractCopy,
    'apply-copy': applyCopy,
    'continue-suite': () => startOrRetry('start'),
    'retry-label': () => startOrRetry('read-label'),
    'refresh-board': refreshBoard,
    'trial-first': trialFirst,
    'submit-redo': () => reviewAction('redo'),
    'retry-slot': () => reviewAction('redo'),
    'approve': () => reviewAction('approve'),
    'toggle-version': () => reviewAction('toggle'),
    'show-redo'() {
      showRedo = !showRedo;
      renderFocus(true);
      if (showRedo) {
        const note = $('#redo-note');
        if (note) {
          note.value = noteDraft;
          note.focus({preventScroll: true});
        }
      }
    },
    'open-label'() { labelModes.add(selected); renderFocus(true); },
    'open-history'() {
      reviewArchived = true;
      activeSlot = item()?.slots.find(slot => slot.image)?.id || 1;
      resetProof();
      renderFocus(true);
    },
    'close-focus'() {
      labelDraft();
      selected = null;
      resetProof();
      renderShell();
    },
    'reset-filter'() { filter = 'all'; renderBoard(); },
    'next-todo'() { cycleTodo(1); },
    'zoom-label'(button) {
      const url = button.dataset.url || item()?.proofOriginal;
      if (!url) return;
      const image = new Image();
      image.alt = '包装原图';
      image.src = url;
      $('#dialog-image').replaceChildren(image);
      $('#image-dialog').showModal();
    },
    'add-label-line': button => reviseDraft('line', button),
    'remove-label-line': button => reviseDraft('remove', button),
    'add-claim': button => reviseDraft('claim', button),
    async 'copy-folder'() {
      const path = item()?.archive;
      if (!path) return;
      try {
        await navigator.clipboard.writeText(path);
        toast('已复制。可在访达按 ⌘⇧G 前往该文件夹。');
      } catch {
        const text = $('#archive-path');
        if (text) {
          const range = document.createRange();
          range.selectNodeContents(text);
          window.getSelection().removeAllRanges();
          window.getSelection().addRange(range);
        }
        toast('路径已选中，按 ⌘C 复制。');
      }
    }
  };

  $('#choose').addEventListener('click', () => $('#file-input').click());
  $('#file-input').addEventListener('change', event => {
    if (event.target.files.length) upload(event.target.files);
  });
  const drop = $('#drop');
  drop.addEventListener('dragover', event => { event.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', event => {
    event.preventDefault();
    drop.classList.remove('over');
    if (event.dataTransfer.files.length) upload(event.dataTransfer.files);
  });
  document.addEventListener('paste', event => {
    const clipboard = event.clipboardData;
    const files = [...(clipboard?.files || [])].filter(file => file.type.startsWith('image/'));
    if (!files.length) files.push(...[...(clipboard?.items || [])].filter(entry => entry.kind === 'file' &&
      entry.type.startsWith('image/')).map(entry => entry.getAsFile()).filter(Boolean));
    if (!files.length) return;
    event.preventDefault();
    upload(files);
  });

  document.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (button?.disabled) return;
    if (button?.dataset.select) {
      if (filter === 'done' && productFor(button.dataset.select)?.stage !== 'done') filter = 'all';
      return select(button.dataset.select);
    }
    if (button?.hasAttribute('data-flow')) {
      const index = Number(button.dataset.flow);
      const stages = stageSets[index][1];
      const product = [...sortTodo(), ...products].find(entry => stages.includes(entry.stage));
      if (product) {
        filter = index === 3 ? 'done' : 'all';
        select(product.id);
        $('#workflow').querySelector(`[data-flow="${index}"]`)?.focus({preventScroll: true});
      }
      return;
    }
    if (button?.dataset.filter) {
      filter = button.dataset.filter;
      renderBoard();
      $('#filters').querySelector(`[data-filter="${filter}"]`)?.focus({preventScroll: true});
      return;
    }
    if (button?.dataset.proofSlot) return setSlot(Number(button.dataset.proofSlot));
    if (button?.dataset.action) return actions[button.dataset.action]?.(button);
    const row = event.target.closest('[data-product]');
    if (row) select(row.dataset.product);
  });

  function captureInput(event) {
    const target = event.target;
    if (target.id === 'proof-split') {
      split = Number(target.value);
      $('[data-proof-frame]')?.style.setProperty('--split', `${split}%`);
    } else if (target.id === 'redo-note') noteDraft = target.value;
    else if (target.closest('.lc-panel')) labelDraft();
  }

  document.addEventListener('input', captureInput);
  document.addEventListener('toggle', event => {
    if (event.target.matches('[data-label-extra]')) labelDraft();
    if (!event.target.matches('[data-label-copy]') || !selected) return;
    if (event.target.open) expandedCopy.add(selected);
    else expandedCopy.delete(selected);
  }, true);
  document.addEventListener('focusout', () => {
    if (pendingFocusRefresh) setTimeout(() => renderFocus(), 0);
  });
  document.addEventListener('change', async event => {
    const target = event.target;
    if (target.id === 'focus-variant') return select(selected, target.value);
    if (target.id === 'proof-mode' || target.id === 'proof-history') {
      if (target.id === 'proof-mode') compareMode = target.value;
      else versionIndex = Number(target.value);
      renderFocus(true);
      $(`#${target.id}`)?.focus({preventScroll: true});
      return;
    }
    if (target.matches('[data-label-source-file]')) {
      const file = target.files[0];
      const product = item();
      if (!file || !product) return;
      labelDraft(product);
      try {
        const text = await window.StudioLabel.readSource(file);
        const draft = drafts.get(product.id);
        if (draft) draft.copy_source = text;
        expandedCopy.add(product.id);
        if (selected === product.id) renderFocus(true);
        toast('商品资料已读入。');
      } catch (error) {
        toast(error.message);
      }
    }
  });

  document.addEventListener('keydown', event => {
    if ($('#image-dialog').open) return;
    const key = event.key.toLowerCase();
    if (event.metaKey && key === 'enter' && isLabelMode(item())) {
      event.preventDefault();
      const confirm = $('[data-action=confirm-label]');
      if (confirm && !confirm.disabled) confirm.click();
      return;
    }
    if (key === 'escape' && selected) {
      event.preventDefault();
      actions['close-focus']();
      return;
    }
    if (event.target.matches('input, textarea, select, [contenteditable=true]') || event.metaKey || event.ctrlKey || event.altKey) return;
    if (key === 'j' || key === 'k') return cycleTodo(key === 'j' ? 1 : -1);
    if (!canShowProof(item())) return;
    const shortcut = {a: 'approve', r: 'show-redo', l: 'toggle-version'}[key];
    if (shortcut && !event.repeat) {
      const button = $(`[data-action="${shortcut}"]:not(:disabled)`);
      if (button) button.click();
    }
    if (key === 'arrowleft' || key === 'arrowright') {
      event.preventDefault();
      setSlot(Math.max(1, Math.min(8, activeSlot + (key === 'arrowleft' ? -1 : 1))));
    }
    if (key === ' ' && !event.repeat && !event.target.closest('button')) {
      event.preventDefault();
      referenceOnly = true;
      renderFocus(true);
    }
  });
  document.addEventListener('keyup', event => {
    if (event.key === ' ' && referenceOnly) {
      referenceOnly = false;
      renderFocus(true);
    }
  });
  window.addEventListener('blur', () => {
    if (referenceOnly) {
      referenceOnly = false;
      renderFocus(true);
    }
  });

  renderTop();
  renderWorkflow();
  poll();
})();
