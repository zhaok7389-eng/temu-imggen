(() => {
  'use strict';

  const slotNames = ['首图', '卖点图', '成分图', '质地特写', '使用方法', '场景图', '尺寸图', '礼品场景'];
  const generationKinds = new Set(['生成套图', '出图', '重出']);
  const failureStates = new Set(['error', 'interrupted']);
  const isGeneration = job => generationKinds.has(job?.kind);
  const isRedo = job => job.kind === '重出' || Number(job.slot) > 0;
  const skuPath = (id, suffix = '') => `/api/sku/${encodeURIComponent(id)}${suffix}`;

  function readable(value) {
    if (typeof value !== 'string') return '';
    return value.replace(/\bsk-[a-z0-9_*\-]{8,}/gi, '[已隐藏]')
      .replace(/[\u0000-\u001f\u007f]+/g, ' ')
      .replace(/\bOpenAI\b/g, '生成服务')
      .replace(/API key/gi, '密钥')
      .trim().slice(0, 480);
  }

  async function request(url, options = {}) {
    let response;
    try {
      response = await fetch(url, { cache: 'no-store', ...options });
    } catch (error) {
      if (error?.name === 'AbortError') throw error;
      throw new Error('无法连接到本机工作台，请检查工具是否已启动。');
    }
    const data = await response.json().catch(() => null);
    if (!response.ok || data?.error) {
      const detail = data?.error || data?.detail;
      const message = Array.isArray(detail) ? detail.map(item => readable(item?.msg)).filter(Boolean).join('。')
        : readable(detail?.message ?? detail);
      throw new Error(message || `操作未完成（${response.status}），请重试。`);
    }
    if (data === null) throw new Error('工作台没有返回可读取的数据，请重试。');
    return data;
  }

  function jobsFor(row, jobs) {
    return (jobs || []).filter(job => job.sku === row.id);
  }

  function activeFor(row, recent) {
    // Board rows include every current job for the product, even beyond the history limit.
    return Array.isArray(row.jobs) ? row.jobs : recent.filter(job => job.state === 'running');
  }

  function hasUnreviewed(summary) {
    return Number(summary.made) > Number(summary.approved);
  }

  function normalizeRow(row, jobs) {
    const recent = jobsFor(row, jobs);
    const activeJobs = activeFor(row, recent);
    const activeGeneration = activeJobs.filter(isGeneration);
    const activeRedos = activeGeneration.filter(isRedo);
    const summaries = row.variants || [];
    const serverStage = row.serverStage || row.stage;
    const latest = recent[0];
    const failed = !activeJobs.length && failureStates.has(latest?.state) ? latest : null;
    const failureMessage = !activeJobs.length ? readable(failed?.error || row.last_error) : '';
    const labelFailed = ['new', 'confirm'].includes(serverStage) &&
      (failed?.kind === '识别标签' || (!failed && serverStage === 'new' && !!failureMessage));
    const generationFailed = isGeneration(failed) ||
      (!failed && !!failureMessage && serverStage === 'ready');
    const incomplete = summaries.some(summary => Number(summary.made) < Number(summary.slots));
    let stage = serverStage;

    if (labelFailed) {
      stage = failed?.state === 'interrupted' ? 'interrupted' : 'error';
    } else if (activeRedos.length && activeRedos.length === activeGeneration.length &&
      summaries.some(summary => Number(summary.made) - Number(summary.approved) >
        activeRedos.filter(job => job.variant === summary.variant).length)) {
      stage = 'proof';
    } else if (serverStage === 'ready') {
      if (summaries.some(hasUnreviewed)) stage = 'proof';
      else if (generationFailed) stage = failed?.state === 'interrupted' ? 'interrupted' : 'error';
    }

    return {
      ...row,
      stage,
      serverStage,
      activeJobs,
      last_error: failureMessage,
      generationError: generationFailed ? failureMessage : '',
      hasProblem: !!failureMessage || ['error', 'interrupted'].includes(stage),
      canRetryLabel: labelFailed || serverStage === 'new',
      canResume: !activeGeneration.length && serverStage === 'ready' && (incomplete || !summaries.length),
    };
  }

  function normalizeRows(board, jobs) {
    return (board?.products || board?.rows || []).map(row => normalizeRow(row, jobs));
  }

  function historyFor(raw) {
    return (raw?.versions || []).map(entry => ({
      ...entry,
      flags: (entry.flags || []).map(readable).filter(Boolean),
      error: readable(entry.error),
      legacy: !!entry.legacy_strip,
      image: entry.pasted || entry.generated || '',
    })).filter(entry => entry.image).sort((a, b) => Number(b.n) - Number(a.n));
  }

  function readSlot(id, name, raw, activeJobs, lastGeneration, hasPlan) {
    const skip = !!raw?.skip;
    const current = raw?.current;
    const image = current?.file || '';
    const versions = historyFor(raw);
    const currentVersion = versions.find(entry => Number(entry.n) === Number(current?.n));
    const previous = versions.find(entry => Number(entry.n) < Number(current?.n))?.image || '';
    const flags = (current?.flags || currentVersion?.flags || []).map(readable).filter(Boolean);
    const redo = activeJobs.find(job => Number(job.slot) === id);
    const suite = activeJobs.find(job => !isRedo(job));
    const busy = !skip && (!!raw?.busy || !!redo || (!hasPlan && !!suite));
    const busyPhase = busy ? (raw?.busy_phase || redo?.phase || suite?.phase || 'running') : '';
    const isLatestFailure = failureStates.has(lastGeneration?.state);
    const exactFailure = isLatestFailure && Number(lastGeneration.slot) === id;
    const failedSet = isLatestFailure && !isRedo(lastGeneration) && !image && !!raw;
    let error = '';
    if (!skip && !busy && raw?.status !== 'approved') {
      error = exactFailure ? readable(lastGeneration.error) || '这张图未完成，可以重试。'
        : currentVersion?.error || (failedSet ? '整套任务未完成，这个图位还没有结果。' : '');
    }

    let status = 'empty';
    if (skip) status = 'skipped';
    else if (busy && (redo || !image || raw?.status === 'redo')) status = busyPhase === 'queued' ? 'queued' : 'generating';
    else if (error) status = 'error';
    else if (raw?.status === 'approved' && image) status = 'approved';
    else if (image) status = flags.length ? 'flagged' : 'ready';

    return {
      id, name: raw?.name || name, status, image: skip ? '' : image, flags,
      skipReason: readable(raw?.skip_reason), skip, error, previous,
      generated: currentVersion?.generated || (current?.which === 'generated' ? image : ''),
      pasted: currentVersion?.pasted || (current?.which === 'pasted' ? image : ''),
      version: current?.n ?? null,
      versionKey: current?.version || '',
      legacy: !!currentVersion?.legacy,
      copy: raw?.copy || [], versions, busy, busyPhase, raw: raw || null,
    };
  }

  function toProduct(row, detail, jobs, cost = null, preferredVariant = 'A') {
    const summary = normalizeRow(row || detail.row, jobs);
    const recent = jobsFor(summary, jobs);
    const variants = detail?.variants || {};
    const variantNames = [...new Set([
      ...Object.keys(variants),
      ...(summary.variants || []).map(entry => entry.variant),
      ...summary.activeJobs.map(job => job.variant).filter(Boolean),
    ])].sort();
    const variant = variantNames.includes(preferredVariant) ? preferredVariant : variantNames[0] || preferredVariant;
    const activeJobs = summary.activeJobs.filter(job => isGeneration(job) && job.variant === variant);
    const currentPlan = variants[variant];
    const planSlots = new Map((currentPlan?.slots || []).map(slot => [Number(slot.slot), slot]));
    const variantSummary = summary.variants?.find(entry => entry.variant === variant) || null;
    const slots = slotNames.map((name, index) => {
      const id = index + 1;
      const latest = recent.find(job => isGeneration(job) && job.variant === variant &&
        (!isRedo(job) || Number(job.slot) === id));
      return readSlot(id, name, planSlots.get(id), activeJobs, latest, !!currentPlan);
    });
    const unreviewed = slot => !slot.busy && ['ready', 'flagged'].includes(slot.status);
    const pending = slots.filter(unreviewed).length;
    const onlyRedos = summary.activeJobs.some(isRedo) && summary.activeJobs.every(job => !isGeneration(job) || isRedo(job));
    const stage = currentPlan && onlyRedos && pending ? 'proof' : summary.stage;
    const sources = detail?.sources?.length ? detail.sources : summary.thumb ? [summary.thumb] : [];
    const created = summary.created || '';
    const added = /[T ](\d\d:\d\d)/.exec(created)?.[1] || '';

    return {
      id: summary.id, name: detail?.name || summary.name || summary.id, stage,
      serverStage: summary.serverStage, thumb: summary.thumb || sources[0] || '',
      proofOriginal: sources[0] || '', sources, added, created, cost, slots,
      archive: summary.archived_to || '', error: summary.last_error, last_error: summary.last_error,
      variant, variantNames: variantNames.length ? variantNames : [variant], variantSummary,
      hasConfirmedLabel: detail ? !!detail.label : ['ready', 'queued', 'generating', 'proof', 'done'].includes(summary.serverStage),
      generationError: summary.generationError, hasProblem: summary.hasProblem || slots.some(slot => !!slot.error),
      canRetryLabel: summary.canRetryLabel, canResume: summary.canResume &&
        (!variantSummary || Number(variantSummary.made) < Number(variantSummary.slots) || !variantSummary.slots),
      progress: summary.progress || null, pending, activeJobs: summary.activeJobs, detail: detail || null,
    };
  }

  function parseLedger(csv) {
    const rows = [];
    let row = [];
    let field = '';
    let quoted = false;
    for (let i = 0; i < csv.length; i += 1) {
      const char = csv[i];
      if (char === '"') {
        if (quoted && csv[i + 1] === '"') {
          field += '"';
          i += 1;
        } else quoted = !quoted;
      } else if (quoted || (char !== ',' && char !== '\r' && char !== '\n')) {
        field += char;
      } else {
        row.push(field);
        field = '';
        if (char === ',') continue;
        rows.push(row);
        row = [];
        if (char === '\r' && csv[i + 1] === '\n') i += 1;
      }
    }
    if (quoted) return null;
    if (row.length || field) rows.push([...row, field]);

    const columns = rows.shift() || [];
    const skuColumn = columns.indexOf('sku_id');
    const costColumn = columns.indexOf('cost_usd');
    if (skuColumn < 0 || costColumn < 0) return null;
    const costs = new Map();
    for (const r of rows) {
      const id = r[skuColumn];
      const value = r[costColumn]?.trim();
      const cost = Number(value);
      if (!id || !value || !Number.isFinite(cost)) continue;
      costs.set(id, (costs.get(id) || 0) + cost);
    }
    for (const [id, cost] of costs) costs.set(id, Number(cost.toFixed(6)));
    return costs;
  }

  async function getCosts() {
    try {
      const response = await fetch('/files/ledger.csv', { cache: 'no-store' });
      return response.ok ? parseLedger((await response.text()).replace(/^\ufeff/, '')) : null;
    } catch {
      return null;
    }
  }

  window.StudioData = { request, skuPath, normalizeRows, toProduct, getCosts };
})();
