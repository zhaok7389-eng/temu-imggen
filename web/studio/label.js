(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
  const strings = value => Array.isArray(value) ? value.filter(item => typeof item === 'string' && item.trim()) : [];
  const newlines = value => String(value ?? '').split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const sizes = new Set(['small', 'medium', 'big']);
  const categories = {
    serum: '精华', cream: '面霜', mask: '面膜', cleanser: '清洁', toner: '爽肤水',
    lip: '唇部护理', eye: '眼部护理', body: '身体护理', other: '其它'
  };
  const copyKinds = {
    benefit: '功能卖点', ingredient: '成分或材质', texture: '质地说明',
    usage: '用法步骤', scene: '使用场景', capacity: '容量规格'
  };

  function readback(line, evidence, typos) {
    const result = [];
    if (typos.has(line.text)) result.push('疑似原标签错字，请照原图保留');
    if (evidence?.hidden) result.push('原图有遮挡，请核对可见文字');
    if (typeof evidence?.ocr_sim === 'number' && evidence.ocr_sim < .85) {
      result.push(evidence.ocr_found ? '另一路读到「' + evidence.ocr_found + '」' : '另一路未匹配，请对照原图');
    }
    return result;
  }

  function lineRow(line, index, evidence, typos) {
    const number = index + 1;
    const notes = readback(line, evidence, typos);
    const size = sizes.has(line.size) ? line.size : 'small';
    return '<div class="lc-label-line' + (notes.length ? ' lc-mismatch' : '') + '">' +
      '<div class="lc-line-fields">' +
        '<span class="lc-line-index" aria-hidden="true">' + String(number).padStart(2, '0') + '</span>' +
        '<input spellcheck="false" aria-label="包装标签第 ' + number + ' 行" data-label-line="' + number +
          '" data-label-size="' + size + '" value="' + escape(line.text) + '"' +
          (notes.length ? ' aria-describedby="lc-note-' + number + '"' : '') + '>' +
        '<input type="checkbox" aria-label="第 ' + number + ' 行是包装大字" data-label-big="' + number + '"' +
          (size === 'big' ? ' checked' : '') + '>' +
        '<button type="button" class="lc-remove" data-action="remove-label-line" data-line-index="' +
          number + '" aria-label="删除第 ' + number + ' 行">×</button>' +
      '</div>' +
      (notes.length ? '<small class="lc-readback" id="lc-note-' + number + '">' + notes.map(escape).join(' · ') + '</small>' : '') +
    '</div>';
  }

  function inputField(label, name, value, type = 'text') {
    const range = type === 'number' ? ' min="1" step="1"' : '';
    return '<label class="lc-field"><span>' + escape(label) + '</span><input type="' + type +
      '" data-label-field="' + name + '" value="' + escape(value) + '"' + range + '></label>';
  }

  function areaField(label, name, value, placeholder, extra = '') {
    return '<label class="lc-field lc-wide"><span>' + escape(label) + '</span>' +
      '<textarea rows="2" data-label-field="' + name + '" placeholder="' + escape(placeholder) + '"' + extra +
      '>\n' + escape(value) + '</textarea></label>';
  }

  function categoryField(value) {
    const options = Object.hasOwn(categories, value) ? categories : { ...categories, [value]: value };
    return '<label class="lc-field"><span>类目</span><select data-label-field="category">' +
      Object.entries(options).map(([key, name]) =>
        '<option value="' + escape(key) + '"' + (key === value ? ' selected' : '') + '>' + escape(name) + '</option>'
      ).join('') + '</select></label>';
  }

  function renderClaims(all, adopted) {
    return '<fieldset class="lc-claims"><legend>可用卖点</legend><div>' +
      all.map((claim, index) =>
        '<label class="lc-claim"><input type="checkbox" data-label-claim="' + (index + 1) +
        '" value="' + escape(claim) + '"' + (adopted.has(claim) ? ' checked' : '') + '><span>' +
        escape(claim) + '</span></label>'
      ).join('') + '</div><div class="lc-claim-entry">' +
      '<input type="text" data-label-new-claim aria-label="新的可用卖点" placeholder="补充已核实的卖点">' +
      '<button type="button" class="lc-control" data-action="add-claim">添加</button></div></fieldset>';
  }

  function renderCandidates(copy, selected, locked) {
    if (copy.state === 'applied') {
      const slots = (copy.applied_slots ?? []).map(slot => String(slot).padStart(2, '0')).join('、');
      return '<p class="lc-copy-note">已采用商品文案' + (slots ? '，适用图位 ' + escape(slots) : '') + '。</p>';
    }
    if (copy.state !== 'ready') return '';
    const items = Array.isArray(copy.items) ? copy.items : [];
    if (!items.length) return '<p class="lc-copy-note">没有找到符合规则且有原文依据的文案。可补充资料后再提炼。</p>';
    const ordered = items.map((item, index) => ({ item, index })).sort((a, b) => a.item.slot - b.item.slot);
    return '<div class="lc-candidates">' + ordered.map(({ item, index }) =>
      '<label class="lc-candidate"><input type="checkbox" data-label-candidate="' + index + '" value="' + index +
      '"' + (selected.has(index) ? ' checked' : '') + '><span><small class="lc-copy-kind">' +
      escape(String(item.slot).padStart(2, '0')) + ' · ' + escape(copyKinds[item.kind] ?? '副图文案') +
      '</small><b>' + escape(item.text) + '</b><small>原文依据 · ' + escape(item.source_quote) + '</small></span></label>'
    ).join('') + '<p class="lc-copy-note">功能最多三条，成分或材质最多五条，用法选齐三步，其它图位各选一条。</p>' +
      '<button type="button" class="lc-control lc-adopt" data-action="apply-copy" data-draft-id="' + escape(copy.id) +
      '"' + (locked ? ' disabled' : '') + '>采用勾选文案</button></div>';
  }

  function renderCopy(copy, draft, expanded, budgetExhausted, busy, error, confirmed) {
    const count = Array.isArray(copy.items) ? copy.items.length : 0;
    const summary = error ? '需检查' : copy.state === 'ready' ? count ? count + ' 条待选' : '暂无建议' :
      copy.state === 'applied' ? '已采用' : busy && copy.state === 'pending' ? '提炼中' : '可选';
    const selection = draft.selectedDraftId === copy.id ? draft.selectedIndices : [];
    const note = error ? '<p class="lc-copy-error" role="alert">' + escape(error) + '</p>' :
      busy && copy.state === 'pending' ? '<p class="lc-copy-note" role="status">正在提炼商品资料…</p>' : '';
    return '<details class="lc-copy" data-label-copy' + (expanded ? ' open' : '') + '>' +
      '<summary><span>补充商品文案</span><small>' + summary + '</small></summary><div class="lc-copy-body">' +
      areaField('商品资料', 'copy_source', draft.copy_source ?? copy.source,
        '粘贴可核实的卖点、配方或使用说明', ' id="label-copy-source" maxlength="6000"') +
      '<div class="lc-copy-actions"><label class="lc-file">读取文案' +
      '<input type="file" data-label-source-file accept=".txt,.md,.csv,.docx" aria-label="读取文案文件"></label>' +
      '<small>TXT / MD / CSV / DOCX</small><button type="button" class="lc-control" data-action="extract-copy"' +
      (busy || budgetExhausted ? ' disabled' : '') + '>' + (confirmed ? '提炼卖点' : '确认标签并提炼') +
      '</button></div>' + note +
      renderCandidates(copy, new Set(selection ?? []), busy) + '</div></details>';
  }

  function render({ product, draft = {}, expandedCopy = false, budgetExhausted = false, busy = false, copyError = '' }) {
    draft = draft ?? {};
    const detail = product.detail ?? {};
    const recognized = detail.label_draft ?? {};
    const confirmed = detail.label?.confirmed_at;
    const data = { ...(detail.label ?? recognized), ...draft };
    const copy = detail.product_copy ?? {};
    const reference = product.proofOriginal || detail.sources?.[0] || '';
    const lines = Array.isArray(data.lines) && data.lines.length ? data.lines : [{ text: '', size: 'small' }];
    const storedLines = new Map((recognized.lines ?? []).map(line => [line.text, line]));
    const typos = new Set(strings(recognized.suspected_typos));
    const pending = lines.filter(line => readback(line, storedLines.get(line.text), typos).length).length;
    const allowed = new Set(strings(data.allowed_claims));
    const claims = [...new Set([...allowed, ...strings(recognized.claim_candidates), ...strings(data.claim_candidates)])];
    const category = data.category || detail.category || 'other';
    const warnings = strings(data.warnings);
    return '<section class="lc-panel" aria-label="标签确认表单">' +
      '<div class="lc-section-head"><h3>核对包装文字</h3>' +
      '<span class="lc-counter' + (pending ? ' lc-warning-text' : '') + '">' + lines.filter(line => line.text.trim()).length +
      ' 行' + (pending ? ' · ' + pending + ' 处待核' : '') + '</span></div>' +
      '<div class="lc-recognition">' +
        '<figure class="lc-reference">' +
          (reference ? '<button type="button" class="lc-reference-button" data-action="zoom-label" data-url="' +
            escape(reference) + '" aria-label="放大包装原图"><img src="' + escape(reference) +
            '" alt="商品包装原图" draggable="false"></button>' :
            '<div class="lc-reference-button lc-reference-empty">暂未找到包装原图</div>') +
          '<figcaption><span>包装原图</span>' +
            (reference ? '<button type="button" data-action="zoom-label" data-url="' + escape(reference) + '">放大</button>' : '') +
          '</figcaption>' +
        '</figure>' +
        '<div class="lc-label-editor"><div class="lc-lines" role="group" aria-label="逐行核对">' +
          '<div class="lc-lines-head" aria-hidden="true"><span>行</span><span>识别文字</span><span>大字</span><span></span></div>' +
          lines.map((line, index) => lineRow(line, index, storedLines.get(line.text), typos)).join('') +
        '</div><button type="button" class="lc-control lc-add-line" data-action="add-label-line">＋ 添加一行</button></div>' +
      '</div>' +
      (warnings.length ? '<div class="lc-warning" role="status">' + warnings.map(warning => '<p>' + escape(warning) + '</p>').join('') + '</div>' : '') +
      renderClaims(claims, allowed) +
      '<section class="lc-specs" aria-labelledby="lc-spec-title"><h3 id="lc-spec-title">规格与用法</h3>' +
        '<div class="lc-facts">' +
          inputField('商品名称', 'product_name', data.product_name) +
          categoryField(category) +
          inputField('容量', 'capacity', data.capacity) +
          inputField('件数', 'pieces', data.pieces ?? '', 'number') +
          inputField('主成分', 'key_ingredient', data.key_ingredient) +
          inputField('质地', 'texture', data.texture) +
          areaField('用法', 'usage_steps', draft.usageStepsText ?? strings(data.usage_steps).join('\n'), '每行一个步骤。填齐三步可出使用方法图') +
        '</div>' +
        '<details class="lc-copy lc-extra" data-label-extra' + (draft.expandedExtra ? ' open' : '') +
        '><summary><span>更多规格与副图文案</span></summary><div class="lc-facts">' +
          inputField('件数原文', 'pieces_text', data.pieces_text) +
          inputField('质地短句', 'texture_caption', data.texture_caption) +
          inputField('场景短句', 'scene_caption', data.scene_caption) +
          areaField('成分或材质短名', 'ingredient_names', draft.ingredientNamesText ?? strings(data.ingredient_names).join('\n'), '每行一项') +
        '</div></details>' +
      '</section>' +
      renderCopy(copy, draft, expandedCopy, budgetExhausted, busy, copyError, confirmed) +
      '<div class="lc-footer">' +
        (budgetExhausted ? '<p class="lc-warning-text">今日预算已用完，暂不能开始生成。</p>' : '') +
        '<button type="button" class="lc-confirm" data-action="confirm-label"' +
          (busy || budgetExhausted ? ' disabled' : '') + '><span>' +
          (confirmed ? '开始生成' : '确认并开始生成') +
          '</span><span class="lc-quote">约 $0.35</span></button><kbd>⌘ Enter</kbd>' +
      '</div></section>';
  }

  function payload(container, product, { validate = true } = {}) {
    const value = name => container.querySelector('[data-label-field="' + name + '"]')?.value ?? '';
    const lines = Array.from(container.querySelectorAll('[data-label-line]'), input => {
      const id = input.dataset.labelLine;
      const checked = container.querySelector('[data-label-big="' + id + '"]')?.checked;
      return {
        text: validate ? input.value.trim() : input.value,
        size: checked ? 'big' : input.dataset.labelSize === 'medium' ? 'medium' : 'small'
      };
    });
    const piecesText = value('pieces');
    const pieces = Number(piecesText || 1);
    const usage = newlines(value('usage_steps'));
    if (validate && !lines.some(line => line.text)) throw new Error('请先核对并填写至少一行包装文字。');
    if (validate && (!Number.isSafeInteger(pieces) || pieces < 1)) throw new Error('件数请输入大于等于 1 的整数。');
    if (validate && usage.length > 3) throw new Error('使用方法最多三步，请保留三项已核实的步骤。');
    const field = name => validate ? value(name).trim() : value(name);
    return {
      lines: validate ? lines.filter(line => line.text) : lines,
      product_name: field('product_name'),
      capacity: field('capacity'),
      pieces: validate ? pieces : piecesText,
      pieces_text: field('pieces_text'),
      category: field('category') || product?.detail?.category || 'other',
      key_ingredient: field('key_ingredient'),
      ingredient_names: newlines(value('ingredient_names')),
      allowed_claims: [...new Set(Array.from(container.querySelectorAll('[data-label-claim]:checked'), input => input.value))],
      texture: field('texture'),
      texture_caption: field('texture_caption'),
      scene_caption: field('scene_caption'),
      usage_steps: usage
    };
  }

  function capture(container, product) {
    const value = name => container.querySelector('[data-label-field="' + name + '"]')?.value ?? '';
    return {
      ...payload(container, product, { validate: false }),
      claim_candidates: Array.from(container.querySelectorAll('[data-label-claim]'), input => input.value),
      selectedIndices: Array.from(container.querySelectorAll('[data-label-candidate]:checked'), input => Number(input.value)),
      selectedDraftId: container.querySelector('[data-action="apply-copy"]')?.dataset.draftId,
      copy_source: value('copy_source'),
      ingredientNamesText: value('ingredient_names'),
      usageStepsText: value('usage_steps'),
      expandedExtra: Boolean(container.querySelector('[data-label-extra]')?.open)
    };
  }

  async function postCopy(productId, action, body) {
    const path = '/sku/' + encodeURIComponent(productId) + '/product-copy' + action;
    const response = await fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      body
    });
    if (!response.ok || !response.redirected || !response.url) throw new Error('商品文案暂未处理成功，请重试。');
    const url = new URL(response.url);
    if (url.origin !== window.location.origin) throw new Error('商品文案请求未完成，请重试。');
    if (url.pathname === '/settings') throw new Error('请先在设置里完成服务配置，再提炼商品文案。');
    const detail = url.searchParams.get('msg') || '';
    const message = /\bgpt[-\w.]*|error code:|traceback|sk-[\w-]+/i.test(detail) ? '商品文案处理失败，请检查设置后重试。' : detail;
    return { jobId: url.searchParams.get('job') || '', message };
  }

  async function startExtraction(productId, source) {
    const text = checkedSource(source);
    const out = await postCopy(productId, '', new URLSearchParams({ source: text }));
    if (!out.jobId) throw new Error(out.message || '没有启动文案提炼，请重试。');
    return out;
  }

  async function applyExtraction(productId, draftId, selectedIndices) {
    if (!draftId || !selectedIndices.length) throw new Error('请至少勾选一条有依据的文案。');
    const body = new URLSearchParams({ draft_id: draftId });
    for (const index of selectedIndices) body.append('item', String(index));
    const { message } = await postCopy(productId, '/apply', body);
    if (!message.startsWith('已采用')) throw new Error(message || '未能采用勾选的文案，请重试。');
    return { message };
  }

  function checkedSource(value) {
    const text = String(value ?? '').trim();
    if (!text || [...text].length > 6000) throw new Error('请输入 1–6000 字的可核实商品资料。');
    return text;
  }

  async function readSource(file) {
    if (!file) throw new Error('请选择商品文案文件。');
    const extension = file.name.split('.').pop().toLowerCase();
    if (!['txt', 'md', 'csv', 'docx'].includes(extension)) throw new Error('请选择 TXT、MD、CSV 或 DOCX 文件。');
    if (file.size > 8 * 1024 * 1024) throw new Error('文件超过 8 MB，请先摘选商品资料。');
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (extension === 'docx') return checkedSource(await readDocx(bytes));
    const encoding = bytes[0] === 0xff && bytes[1] === 0xfe ? 'utf-16le' :
      bytes[0] === 0xfe && bytes[1] === 0xff ? 'utf-16be' : 'utf-8';
    let decoded;
    try {
      decoded = new TextDecoder(encoding, { fatal: true }).decode(bytes);
    } catch {
      throw new Error('无法读取文本编码，请另存为 UTF-8 后重试。');
    }
    return checkedSource(decoded);
  }

  const DOCX_XML_LIMIT = 4 * 1024 * 1024;

  function documentPart(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const u16 = offset => view.getUint16(offset, true);
    const u32 = offset => view.getUint32(offset, true);
    const invalid = () => new Error('无法读取此 DOCX 的正文，请重新保存后重试。');
    const min = Math.max(0, bytes.length - 65_557);
    let end = bytes.length - 22;
    while (end >= min && (u32(end) !== 0x06054b50 || end + 22 + u16(end + 20) !== bytes.length)) end--;
    if (end < min || u16(end + 4) || u16(end + 6) || u16(end + 8) !== u16(end + 10)) throw invalid();
    const count = u16(end + 10);
    const directory = u32(end + 16);
    const limit = directory + u32(end + 12);
    if (count > 4096 || limit > end) throw invalid();
    let entry = directory;
    for (let index = 0; index < count; index++) {
      if (entry + 46 > limit || u32(entry) !== 0x02014b50) throw invalid();
      const nameStart = entry + 46;
      const nameEnd = nameStart + u16(entry + 28);
      const next = nameEnd + u16(entry + 30) + u16(entry + 32);
      if (next > limit) throw invalid();
      const name = new TextDecoder().decode(bytes.subarray(nameStart, nameEnd));
      if (name === 'word/document.xml') {
        const method = u16(entry + 10);
        const size = u32(entry + 20);
        const expandedSize = u32(entry + 24);
        const local = u32(entry + 42);
        if (u16(entry + 8) & 1 || ![0, 8].includes(method) || expandedSize > DOCX_XML_LIMIT ||
            local + 30 > directory || u32(local) !== 0x04034b50) throw invalid();
        const data = local + 30 + u16(local + 26) + u16(local + 28);
        if (data + size > directory || (method === 0 && size !== expandedSize)) throw invalid();
        return { method, expandedSize, data: bytes.subarray(data, data + size) };
      }
      entry = next;
    }
    throw invalid();
  }

  async function readDocx(bytes) {
    const { method, expandedSize, data } = documentPart(bytes);
    let xml;
    if (method === 0) {
      xml = new TextDecoder().decode(data);
    } else {
      let reader;
      try {
        reader = new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw')).getReader();
      } catch {
        throw new Error('当前浏览器无法解压 DOCX，可将正文粘贴到资料框。');
      }
      const decoder = new TextDecoder();
      const parts = [];
      let length = 0;
      for await (const chunk of streamChunks(reader)) {
        length += chunk.length;
        if (length > expandedSize || length > DOCX_XML_LIMIT) throw new Error('DOCX 正文大小有误，请重新保存后重试。');
        parts.push(decoder.decode(chunk, { stream: true }));
      }
      if (length !== expandedSize) throw new Error('DOCX 正文不完整，请重新保存后重试。');
      xml = parts.join('') + decoder.decode();
    }
    if (/<!DOCTYPE/i.test(xml)) throw new Error('无法读取含有自定义文档类型的 DOCX。');
    const document = new DOMParser().parseFromString(xml, 'application/xml');
    const root = document.documentElement;
    const namespace = root?.namespaceURI;
    if (root?.localName !== 'document' ||
        !['http://schemas.openxmlformats.org/wordprocessingml/2006/main', 'http://purl.oclc.org/ooxml/wordprocessingml/main']
          .includes(namespace)) throw new Error('DOCX 正文格式不正确，请重新保存后重试。');
    return Array.from(document.getElementsByTagNameNS(namespace, 'p'), paragraph => {
      const walker = document.createTreeWalker(paragraph, NodeFilter.SHOW_ELEMENT, node => {
        if (node.namespaceURI !== namespace) return NodeFilter.FILTER_SKIP;
        return node.localName === 'p' ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
      });
      const segments = [];
      while (walker.nextNode()) {
        const node = walker.currentNode;
        if (node.localName === 't') segments.push(node.textContent);
        if (node.localName === 'tab') segments.push('\t');
        if (node.localName === 'br' || node.localName === 'cr') segments.push('\n');
      }
      return segments.join('');
    }).join('\n');
  }

  async function* streamChunks(reader) {
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) return;
        yield value;
      }
    } catch {
      throw new Error('DOCX 正文解压失败，请重新保存后重试。');
    } finally {
      await reader.cancel().catch(() => {});
    }
  }

  window.StudioLabel = { render, payload, capture, readSource, startExtraction, applyExtraction };
})();
