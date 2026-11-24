(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
  const two = id => String(id ?? '').padStart(2, '0');
  const list = value => Array.isArray(value) ? value : [];
  const wording = values => [...new Set(list(values).map(text => String(text ?? '').trim()).filter(Boolean))];

  function slotState(slot) {
    if (slot.skip || slot.raw?.skip || slot.skipReason || ['skip', 'skipped'].includes(slot.status)) {
      return ['skipped', slot.skipReason || slot.raw?.skip_reason || '缺少相关资料'];
    }
    if (slot.busy || ['generating', 'running', 'queued'].includes(slot.status)) {
      const queued = slot.status === 'queued' || slot.raw?.busy_phase === 'queued';
      return ['busy', queued ? '等待重出' : '生成中'];
    }
    if (slot.error || ['error', 'failed', 'fail'].includes(slot.status)) return ['error', '生成失败'];
    if (slot.status === 'approved') return ['approved', '已通过'];
    if (slot.flags?.length || slot.legacy) return ['warning', '有检查提示'];
    if (slot.image || slot.generated || slot.pasted) return ['ready', '待校样'];
    return ['idle', '待生成'];
  }

  function image(url, alt, className) {
    return url
      ? `<img class="${className}" src="${escape(url)}" alt="${escape(alt)}" draggable="false">`
      : `<span class="${className} proof-placeholder" aria-hidden="true">—</span>`;
  }

  function attemptImage(attempt) {
    return attempt?.pasted || attempt?.generated || '';
  }

  function compareImages(product, slot, viewed, previous, mode, originalOnly) {
    const original = product.proofOriginal || product.sources?.[0] || product.thumb;
    const right = viewed ? attemptImage(viewed) : slot.image || attemptImage(slot);
    const rightLabel = viewed ? `V${viewed.n} 历史图` : '当前图';
    if (originalOnly && original) {
      return {left: original, right: original, leftLabel: '包装原图', rightLabel: '包装原图'};
    }
    if (mode === 'previous') {
      return {left: attemptImage(previous) || slot.previous, right, leftLabel: '上一版', rightLabel};
    }
    if (mode === 'paste') {
      const pair = viewed || slot;
      return {left: pair.generated, right: pair.pasted, leftLabel: '生成版', rightLabel: '回贴版'};
    }
    return {left: original || right, right, leftLabel: original ? '包装原图' : rightLabel, rightLabel};
  }

  function renderFilmstrip(productSlots, selected) {
    return productSlots.map(entry => {
      const [state, label] = slotState(entry);
      const current = String(entry.id) === String(selected);
      const name = entry.name || entry.title || '图位';
      return `<button type="button" class="proof-film-item ${state}" data-proof-slot="${escape(entry.id)}" aria-pressed="${current}"
        aria-label="${two(entry.id)} ${escape(name)} · ${escape(label)}" title="${escape(name)} · ${escape(label)}">
        ${image(entry.image || attemptImage(entry), '', 'proof-film-art')}
        <span class="proof-film-caption"><span>${two(entry.id)}</span><i class="proof-status-dot" aria-hidden="true"></i></span>
      </button>`;
    }).join('');
  }

  function copySelect(label, name, current, choices) {
    const hasCurrent = choices.includes(current);
    return `<label class="proof-copy-field"><span>${label}</span>
      <select name="${name}" data-copy-start="${escape(current)}">
        <option value="" ${current ? '' : 'selected'}>不用文字</option>
        ${current && !hasCurrent ? `<option value="${escape(current)}" selected disabled>${escape(current)}（旧版）</option>` : ''}
        ${choices.map(text => `<option value="${escape(text)}" ${text === current ? 'selected' : ''}>${escape(text)}</option>`).join('')}
      </select>
    </label>`;
  }

  function copyFields(product, slot) {
    const label = product.detail?.label;
    if (!label) return '';
    const copy = list(slot.copy);
    const textFor = role => copy.find(item => item.role === role)?.text || '';
    const textsFor = role => copy.filter(item => item.role === role).map(item => item.text);
    const ingredientNames = wording(list(label.ingredient_names).length ? label.ingredient_names : [label.key_ingredient]);
    const claims = wording(label.allowed_claims);
    switch (Number(slot.id)) {
      case 2: {
        const headlineChoices = wording([label.product_name, ...claims]);
        const currentPoints = textsFor('point');
        const title = headlineChoices.length ? copySelect('标题', 'headline', textFor('headline'), headlineChoices) : '';
        const rows = claims.length ? Array.from({length: 3}, (_, index) =>
          copySelect(`要点 ${index + 1}`, 'bullets', currentPoints[index] || '', claims)
        ).join('') : '';
        return title + rows;
      }
      case 3: {
        if (!ingredientNames.length) return '';
        const current = textsFor('ingredient name');
        const confirmed = ingredientNames.map(text => `<label class="proof-copy-option">
          <input type="checkbox" name="redo-copy" data-copy-field="ingredient_labels" value="${escape(text)}" ${current.includes(text) ? 'checked' : ''}>
          <span>${escape(text)}</span>
        </label>`).join('');
        const old = current.filter(text => !ingredientNames.includes(text));
        return `<fieldset class="proof-ingredient-list" data-copy-original="${escape(JSON.stringify(current))}">
          <legend>成分 / 材质</legend>${confirmed}
          ${old.length ? `<small>旧版文字会在调整后移除 · ${escape(old.join('、'))}</small>` : ''}
        </fieldset>`;
      }
      case 4:
        return label.texture_caption ? copySelect('质地', 'texture_caption', textFor('caption'), [label.texture_caption]) : '';
      case 5: {
        const steps = list(label.usage_steps).slice(0, 3);
        const current = textsFor('step');
        // The frame actions are tied to these ordered, confirmed captions by the plan.
        const old = steps.length === 3 && steps.every(Boolean) && current.length === steps.length
          && steps.some((text, index) => text !== current[index]);
        return `<div class="proof-step-list">
          ${current.map((text, index) => `<span><b>${index + 1}</b> ${escape(text)}</span>`).join('')}
          ${old ? `<label class="proof-copy-option"><input type="checkbox" name="redo-copy"
            data-confirmed-steps="${escape(JSON.stringify(steps))}"><span>恢复已确认的三步用法</span></label>` : ''}
        </div>`;
      }
      case 6:
        return label.scene_caption ? copySelect('场景', 'scene_caption', textFor('caption'), [label.scene_caption]) : '';
      case 7:
        return label.capacity ? copySelect('容量', 'capacity', textFor('caption'), [label.capacity]) : '';
      default:
        return '';
    }
  }

  // Return a patch to the plan and preserve all fields the user didn't change.
  window.studioSlotCopy = function studioSlotCopy(container) {
    const editor = container?.matches?.('.proof-redo') ? container : container?.querySelector('.proof-redo');
    if (!editor) return {};
    const changes = {};
    for (const control of editor.querySelectorAll('select[data-copy-start]:not([name="bullets"])')) {
      if (control.value !== control.dataset.copyStart) changes[control.name] = control.value;
    }
    const bullets = [...editor.querySelectorAll('select[name="bullets"]')];
    if (bullets.some(control => control.value !== control.dataset.copyStart)) {
      changes.bullets = wording(bullets.filter(control => !control.selectedOptions[0]?.disabled).map(control => control.value));
    }
    const steps = editor.querySelector('input[data-confirmed-steps]:checked');
    if (steps) changes.steps = JSON.parse(steps.dataset.confirmedSteps);
    const ingredientList = editor.querySelector('.proof-ingredient-list');
    if (ingredientList) {
      const options = [...ingredientList.querySelectorAll('input[data-copy-field="ingredient_labels"]')];
      if (options.some(control => control.checked !== control.defaultChecked)) {
        const original = JSON.parse(ingredientList.dataset.copyOriginal);
        const selected = options.filter(control => control.checked).map(control => control.value);
        changes.ingredient_labels = [...original.filter(text => selected.includes(text)), ...selected.filter(text => !original.includes(text))];
      }
    }
    return changes;
  };

  function estimatePrice(slot, estimatedImageCost) {
    const price = Number(estimatedImageCost);
    if (estimatedImageCost == null || estimatedImageCost === '' || !Number.isFinite(price) || price < 0) return '';
    const images = Number(slot.id) === 5 ? 3 : 1;
    return `约 $${(price * images).toFixed(3)}`;
  }

  function redoEditor(product, slot, budgetExceeded, price) {
    const fields = copyFields(product, slot);
    return `<section class="proof-redo" aria-labelledby="proof-redo-title">
      <div class="proof-redo-heading"><strong id="proof-redo-title">修改并重出</strong>
        ${fields ? '<span>可选已确认文案</span>' : ''}
      </div>
      ${fields ? `<div class="proof-copy-fields" role="group" aria-label="已确认文案">${fields}</div>` : ''}
      <label class="proof-note"><span>修改说明</span><input id="redo-note" type="text" placeholder="如：保留包装正面的字号" autocomplete="off"></label>
      <div class="proof-redo-actions">
        <button type="button" class="proof-btn quiet" data-action="show-redo" aria-expanded="true">取消</button>
        <button type="button" class="proof-btn primary" data-action="submit-redo" ${budgetExceeded ? 'disabled' : ''}>重出这张 ${price ? `<span>${price}</span>` : ''}</button>
      </div>
      ${budgetExceeded ? '<p class="proof-budget-message">今日预算已用完。</p>' : ''}
    </section>`;
  }

  function checks(flags, legacy) {
    return flags.length || legacy
      ? `<div class="proof-warning">
          <span class="proof-warning-dot" aria-hidden="true">!</span>
          <div>${legacy ? '<p>旧版样式，建议重出</p>' : ''}
          ${flags.slice(0, 2).map(flag => `<p>${escape(flag)}</p>`).join('')}
          ${flags.length > 2 ? `<details><summary>另有 ${flags.length - 2} 项提示</summary>${flags.slice(2).map(flag => `<p>${escape(flag)}</p>`).join('')}</details>` : ''}</div>
        </div>`
      : '';
  }

  window.renderProofPanel = function renderProofPanel({
    product, slot, redo = false, compareMode = 'source', split = 50, versionIndex = 0,
    referenceOnly = false, budgetExceeded = false, estimatedImageCost
  }) {
    if (!product || !slot) return '';
    const [state, stateLabel] = slotState(slot);
    const versions = list(slot.versions).filter(entry => Number(entry.n) > 0 && attemptImage(entry))
      .sort((a, b) => Number(b.n) - Number(a.n));
    const current = Number(slot.raw?.current?.n || slot.attempt || versions[0]?.n || slot.version) || 0;
    const past = versions.filter(entry => Number(entry.n) !== current);
    const viewed = past.find(entry => Number(entry.n) === Number(versionIndex));
    const previous = past[0];
    const pair = viewed || slot;
    const canComparePaste = Boolean(pair.generated && pair.pasted);
    const canComparePrevious = !viewed && Boolean(attemptImage(previous) || slot.previous);
    const mode = (compareMode === 'paste' && canComparePaste) || (compareMode === 'previous' && canComparePrevious)
      ? compareMode : 'source';
    const parsedSplit = Number.parseFloat(split);
    const value = Number.isFinite(parsedSplit) ? Math.max(0, Math.min(100, parsedSplit)) : 50;
    const compared = compareImages(product, slot, viewed, previous, mode, referenceOnly);
    const flags = list(viewed ? viewed.flags : slot.flags).filter(Boolean);
    const legacy = viewed ? viewed.legacy_strip : slot.legacy;
    const hasImage = Boolean(slot.image || attemptImage(slot));
    const unavailable = !hasImage || ['skipped', 'busy', 'error'].includes(state);
    const historical = Number(versionIndex) !== 0;
    const showRedo = redo && !unavailable && !historical;
    const price = estimatePrice(slot, estimatedImageCost);
    const issue = viewed?.error || (!viewed && state === 'error' ? slot.error || '出图未完成。' : '');
    const status = issue
      ? `<div class="proof-warning" role="status"><span class="proof-warning-dot" aria-hidden="true">!</span>
          <p>${escape(issue)}</p>
          ${!historical ? `<button class="proof-btn quiet" type="button" data-action="retry-slot" ${budgetExceeded ? 'disabled' : ''}>重试 ${price}</button>` : ''}
        </div>`
      : state === 'busy' && !historical
        ? `<div class="proof-warning proof-in-progress" role="status"><span class="proof-status-dot" aria-hidden="true"></span><p>${stateLabel}${hasImage ? '，继续显示上一张' : ''}</p></div>`
        : checks(flags, legacy);
    const compareOptions = `<option value="source" ${mode === 'source' ? 'selected' : ''}>包装原图 vs ${viewed ? '历史图' : '当前图'}</option>
      <option value="previous" ${canComparePrevious ? '' : 'disabled'} ${mode === 'previous' ? 'selected' : ''}>上一版 vs 当前图</option>
      <option value="paste" ${canComparePaste ? '' : 'disabled'} ${mode === 'paste' ? 'selected' : ''}>生成版 vs 回贴版</option>`;
    const frame = !compared.right || (state === 'skipped' && !historical)
      ? `<div class="proof-blank" role="status"><strong>${state === 'skipped' ? '已跳过' : escape(stateLabel)}</strong>
          ${state === 'skipped' ? `<span>${escape(stateLabel)}</span>` : ''}</div>`
      : `<div class="proof-stage"><div class="proof-frame" data-proof-frame style="--split:${value}%">
          ${image(compared.right, compared.rightLabel, 'proof-reference-image proof-right')}
          ${image(compared.left, compared.leftLabel, 'proof-reference-image proof-left')}
          <span class="proof-picture-caption before">${escape(compared.leftLabel)}</span>
          ${referenceOnly ? '' : `<span class="proof-picture-caption after">${escape(compared.rightLabel)}</span>
          <div class="proof-divider" aria-hidden="true"><span class="proof-grip"><i></i><i></i></span></div>
          <input id="proof-split" class="proof-range" type="range" min="0" max="100" value="${value}" aria-label="图片对比滑杆">`}
        </div></div>`;
    return `<div class="proof-panel">
      <div class="proof-meta">
        <span class="proof-slot-title"><span class="proof-index">${two(slot.id)}</span>${escape(slot.name || slot.title)}</span>
        <div class="proof-selectors">
          <select id="proof-mode" aria-label="对比对象">${compareOptions}</select>
          <select id="proof-history" aria-label="历史版本">
            <option value="0" ${!viewed ? 'selected' : ''}>${current ? `V${current} 当前` : '当前图'}</option>
            ${past.map(entry => `<option value="${Number(entry.n)}" ${entry === viewed ? 'selected' : ''}>V${Number(entry.n)} 历史${entry.error ? ' · 异常' : ''}</option>`).join('')}
          </select>
        </div>
      </div>
      ${frame}
      <div class="proof-filmstrip" role="group" aria-label="套图图位">${renderFilmstrip(list(product.slots), slot.id)}</div>
      <div class="proof-review-line">
        ${status}
        <div class="proof-actions">
          <button type="button" class="proof-btn quiet proof-version" data-action="toggle-version" ${!canComparePaste || historical || unavailable ? 'disabled' : ''}>切换版本 <kbd>L</kbd></button>
          <button type="button" class="proof-btn" data-action="show-redo" aria-expanded="${showRedo}" ${unavailable || historical ? 'disabled' : ''}>重出 <kbd>R</kbd></button>
          <button type="button" class="proof-btn primary" data-action="approve" ${unavailable || historical || state === 'approved' ? 'disabled' : ''}>${state === 'approved' ? '已通过' : '通过'} <kbd>A</kbd></button>
        </div>
      </div>
      ${showRedo ? redoEditor(product, slot, budgetExceeded, price) : ''}
    </div>`;
  };
})();
