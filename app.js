/* ==========================================================================
   INTEL RADAR — Client Application
   ========================================================================== */

(() => {
  let appState = null;
  let historyData = [];
  let activeDomain = 'ALL';
  let pollTimer = null;
  let isStaticMode = false;

  const SANDBOX_PRESETS = {
    jensen: {
      title: 'Breaking: Jensen Huang returns as NVIDIA CEO after dramatic 48-hour boardroom ouster',
      summary: 'Reuters, Bloomberg, and CNBC confirm NVIDIA founder Jensen Huang has officially returned as Chief Executive Officer following an emergency shareholder revolt and full board resignation.',
      source_type: 'tier1_multi',
    },
    rumor_high: {
      title: 'Unreleased Apple M6 Max MacBook Pro spotted in FCC regulatory filing and Geekbench 6 database ahead of launch',
      summary: 'Official FCC regulatory ID A3491 filings and matching Geekbench metal benchmark logs reveal a 16-core CPU and 48-core GPU configuration, corroborated by 9to5Mac sources.',
      source_type: 'hard_leak',
    },
    rumor_watch: {
      title: 'Supply chain insiders claim NVIDIA RTX 6090 could adopt TSMC N2P node with 512-bit GDDR7 memory bus in late 2027',
      summary: 'According to hardware tipster reports cited by Wccftech, early board partner chatter suggests a 512-bit interface, though final silicon specs are not yet locked.',
      source_type: 'known_leaker',
    },
    rumor_low: {
      title: 'Anonymous forum post claims Intel is buying Apple for $5 trillion next Monday and making all GPUs free',
      summary: 'An unverified anonymous account with no prior track record posted the wild claim without any supporting evidence or filings.',
      source_type: 'anon_blog',
    },
    noise: {
      title: 'Best Black Friday deals on USB-C charging cables and laptop sleeves this weekend',
      summary: 'Roundup of minor 15% discounts on everyday phone accessories and braided cables at retail stores.',
      source_type: 'known_leaker',
    },
  };

  // --- Helpers ---
  const $ = (id) => document.getElementById(id);

  function escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatRelativeTime(isoStr) {
    if (!isoStr) return '';
    const dt = new Date(isoStr);
    if (Number.isNaN(dt.getTime())) return isoStr;
    const diffSec = Math.max(0, Math.floor((Date.now() - dt.getTime()) / 1000));
    if (diffSec < 60) return '剛剛';
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)} 分鐘前`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)} 小時前`;
    return `${dt.getMonth() + 1}/${dt.getDate()} ${String(dt.getHours()).padStart(2, '0')}:${String(dt.getMinutes()).padStart(2, '0')}`;
  }

  let toastTimeout = null;
  function showToast(msg) {
    const el = $('toastBox');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => el.classList.remove('show'), 3200);
  }

  function matchesDomain(item) {
    if (activeDomain === 'ALL') return true;
    return item.domain === activeDomain;
  }

  // --- Card Renderers ---
  function renderSourceLinks(sources) {
    if (!Array.isArray(sources) || !sources.length) return '<span>本地輸入</span>';
    return sources
      .slice(0, 4)
      .map((s) => {
        const name = escapeHtml(s.name || '信源');
        const url = escapeHtml(s.url || '#');
        if (url.startsWith('#sandbox')) {
          return `<span>${name}</span>`;
        }
        return `<a class="source-link" href="${url}" target="_blank" rel="noopener noreferrer">${name} ↗</a>`;
      })
      .join(' · ');
  }

  function renderEvidencePills(tags) {
    if (!Array.isArray(tags) || !tags.length) return '';
    return `<div class="evidence-row">${tags
      .map((t) => {
        const cls = t.includes('升檔') ? 'evidence-pill upgraded' : 'evidence-pill';
        return `<span class="${cls}">${escapeHtml(t)}</span>`;
      })
      .join('')}</div>`;
  }

  function renderMajorCard(item, isNuclear) {
    const cardCls = isNuclear ? 'intel-card nuclear' : 'intel-card';
    const lvlCls = isNuclear ? 'level-tag nuclear' : 'level-tag';
    const sandboxBadge = item.is_sandbox ? '<span class="sandbox-tag">模擬測試</span>' : '';

    return `
      <article class="${cardCls}">
        <div class="card-meta-top">
          <div class="meta-badges">
            <span class="${lvlCls}">Lv.${item.impact_level}</span>
            <span class="domain-tag">${escapeHtml(item.domain)}</span>
            ${sandboxBadge}
          </div>
          <span class="time-stamp">${formatRelativeTime(item.updated_at)}</span>
        </div>
        <h3 class="card-headline">${escapeHtml(item.title_zh)}</h3>
        <p class="card-summary">${escapeHtml(item.summary_zh)}</p>
        ${renderEvidencePills(item.evidence_tags)}
        <div class="card-footer">
          <div class="source-links">${renderSourceLinks(item.sources)}</div>
          <span>${escapeHtml(item.reasoning_zh || '')}</span>
        </div>
      </article>
    `;
  }

  function renderRumorCard(item) {
    const tier = item.rumor_tier || 'watch';
    const pct = item.credibility_pct || 0;
    const sandboxBadge = item.is_sandbox ? '<span class="sandbox-tag">模擬測試</span>' : '';

    return `
      <article class="intel-card">
        <div class="card-meta-top">
          <div class="meta-badges">
            <span class="domain-tag">${escapeHtml(item.domain)}</span>
            ${sandboxBadge}
          </div>
          <span class="time-stamp">${formatRelativeTime(item.updated_at)}</span>
        </div>

        <div class="cred-meter-wrap" title="依據來源紀錄、證據型態與交叉印證數換算">
          <span class="cred-pct ${tier}">${pct}%</span>
          <div class="cred-bar-track">
            <div class="cred-bar-fill ${tier}" style="width: ${Math.max(4, Math.min(100, pct))}%;"></div>
          </div>
        </div>

        <h3 class="card-headline">${escapeHtml(item.title_zh)}</h3>
        <p class="card-summary">${escapeHtml(item.summary_zh)}</p>
        <div class="raw-title-hint">${escapeHtml(item.raw_title)}</div>
        ${renderEvidencePills(item.evidence_tags)}
        <div class="card-footer">
          <div class="source-links">${renderSourceLinks(item.sources)}</div>
          <span>${escapeHtml(item.reasoning_zh || '')}</span>
        </div>
      </article>
    `;
  }

  // --- Full Board Render ---
  function renderBoard() {
    if (!appState) return;

    const { major_alerts, major_archived, rumors, stats, scan_state, model_status, last_scan, settings, heartbeat } = appState;

    // Toggle controls between Local Server Mode vs GitHub Pages Static Mode
    $('btnScanNow').style.display = isStaticMode ? 'none' : 'inline-flex';
    $('btnOpenSandbox').style.display = isStaticMode ? 'none' : 'inline-flex';
    $('btnOpenSettings').style.display = isStaticMode ? 'none' : 'inline-flex';
    $('btnRefreshStatic').style.display = isStaticMode ? 'inline-flex' : 'none';

    // Telemetry strip
    const dot = $('modelStatusDot');
    if (isStaticMode) {
      dot.classList.remove('busy');
      const nodeLabel = heartbeat?.node_name || 'Local-MLX';
      $('modelStatusText').textContent = `${nodeLabel} 同步`;
    } else {
      const modelShort = (model_status?.model_id || 'MLX').split('/').pop();
      if (scan_state?.scanning) {
        dot.classList.add('busy');
        $('modelStatusText').textContent = `${modelShort} 判讀中`;
      } else {
        dot.classList.remove('busy');
        $('modelStatusText').textContent = model_status?.loaded ? `${modelShort} 就緒` : `${modelShort} 待命`;
      }
    }

    $('noiseStatsText').textContent = `已攔截雜訊 ${stats.noise_filtered} 則 (${stats.noise_ratio_pct}%)`;
    if (isStaticMode && heartbeat?.updated_at) {
      $('lastScanText').textContent = `更新於 ${formatRelativeTime(heartbeat.updated_at)}`;
    } else if (last_scan && last_scan.finished_at) {
      $('lastScanText').textContent = `上次巡檢 ${formatRelativeTime(last_scan.finished_at)}`;
    } else if (scan_state?.scanning) {
      $('lastScanText').textContent = '初次巡檢進行中...';
    }

    // Scan progress banner
    const banner = $('scanBanner');
    const scanBtn = $('btnScanNow');
    if (scan_state?.scanning) {
      banner.classList.add('active');
      scanBtn.disabled = true;
      scanBtn.textContent = '掃描判讀中...';
      const cur = scan_state.current || 0;
      const tot = scan_state.total || 0;
      const pct = tot > 0 ? Math.round((cur / tot) * 100) : 18;
      $('scanProgressFill').style.width = `${Math.max(10, pct)}%`;
      const prefix = tot > 0 ? `[${cur}/${tot}] ` : '';
      $('scanBannerText').innerHTML = `<strong>${prefix}</strong>${escapeHtml(scan_state.current_title || '正在處理...')}`;
    } else {
      banner.classList.remove('active');
      scanBtn.disabled = false;
      scanBtn.textContent = '立即掃描全球信源';
    }

    // Threshold range labels
    const lowMax = Number(settings?.rumor_low_max || 33);
    const watchMax = Number(settings?.rumor_watch_max || 67);
    $('rangeUnlikelyLabel').textContent = `0% – ${lowMax}%`;
    $('rangeWatchLabel').textContent = `${lowMax + 1}% – ${watchMax}%`;
    $('rangeLikelyLabel').textContent = `${watchMax + 1}% – 100%`;

    // Filter items by domain
    const filteredNuclear = (major_alerts || []).filter(matchesDomain);
    const filteredArchived = (major_archived || []).filter(matchesDomain);
    const filteredLikely = (rumors?.likely || []).filter(matchesDomain);
    const filteredWatch = (rumors?.watch || []).filter(matchesDomain);
    const filteredUnlikely = (rumors?.unlikely || []).filter(matchesDomain);

    // Tab counts
    const majorBadge = $('majorTabBadge');
    majorBadge.textContent = String(filteredNuclear.length);
    majorBadge.classList.toggle('alert', filteredNuclear.length > 0);
    $('rumorTabBadge').textContent = String(
      filteredLikely.length + filteredWatch.length + filteredUnlikely.length
    );

    // Show clear-sandbox button if any sandbox item exists
    const allItems = [
      ...major_alerts,
      ...major_archived,
      ...(rumors?.likely || []),
      ...(rumors?.watch || []),
      ...(rumors?.unlikely || []),
    ];
    $('btnClearSandbox').style.display = allItems.some((x) => x.is_sandbox) ? 'inline-flex' : 'none';

    // Render Nuclear Alerts
    const nuclearEl = $('nuclearList');
    if (!filteredNuclear.length) {
      const demoBtnHtml = isStaticMode
        ? ''
        : '<button class="btn" type="button" id="btnQuickDemoNuclear">🧪 點此模擬一則「黃仁勳震撼彈」測試警報</button>';
      nuclearEl.innerHTML = `
        <div class="calm-watch-state">
          <div class="calm-watch-icon">🛡️</div>
          <div class="calm-watch-title">目前全球無 Lv.${settings?.major_notify_min || 9}+ 核彈級突發事件</div>
          <p class="calm-watch-desc">
            「沒有通知就是好消息。」本地 MLX 守門員已幫你過濾掉 ${stats.noise_filtered} 則日常新聞，只有出現巨頭人事劇變、地緣衝突或歷史級突破時才會主動響鈴。
          </p>
          ${demoBtnHtml}
        </div>
      `;
      const demoBtn = $('btnQuickDemoNuclear');
      if (demoBtn) {
        demoBtn.addEventListener('click', () => {
          openSandboxWithPreset('jensen');
        });
      }
    } else {
      nuclearEl.innerHTML = filteredNuclear.map((it) => renderMajorCard(it, true)).join('');
    }

    // Render Archived Major
    const archEl = $('archivedMajorList');
    if (!filteredArchived.length) {
      archEl.innerHTML = `<div class="empty-col-note">目前尚無符合此領域的 Lv.7–8 重要動態（若剛啟動，請稍候初次掃描完成）。</div>`;
    } else {
      archEl.innerHTML = filteredArchived.map((it) => renderMajorCard(it, false)).join('');
    }

    // Render Rumor Columns
    $('rumorLikelyList').innerHTML = filteredLikely.length
      ? filteredLikely.map(renderRumorCard).join('')
      : `<div class="empty-col-note">尚無 >${watchMax}% 多方實錘或硬證據傳言</div>`;

    $('rumorWatchList').innerHTML = filteredWatch.length
      ? filteredWatch.map(renderRumorCard).join('')
      : `<div class="empty-col-note">尚無待觀察風聲</div>`;

    $('rumorUnlikelyList').innerHTML = filteredUnlikely.length
      ? filteredUnlikely.map(renderRumorCard).join('')
      : `<div class="empty-col-note">尚無低可信度傳聞</div>`;

    renderHistoryTimeline();
  }

  function parseYearMonth(ym) {
    const parts = String(ym || '2026-01').split('-');
    return (Number(parts[0]) || 2026) * 12 + (Number(parts[1]) || 1);
  }

  function renderHistoryTimeline() {
    const rangeYears = Number($('historyRangeSelect')?.value || 10);
    const now = new Date();
    const nowYm = now.getFullYear() * 12 + (now.getMonth() + 1);
    // Allow 14 months for "past 1 year" so major year-end turns stay visible
    const windowMonths = rangeYears === 1 ? 14 : rangeYears * 12 + 6;
    const minYm = nowYm - windowMonths;
    const requiredTier = rangeYears; // 1 -> >=1, 5 -> >=5, 10 -> >=10, 30 -> >=30

    // Merge live auto-captured nuclear alerts with curated chronicle
    const autoList = Array.isArray(appState?.auto_history) ? appState.auto_history : [];
    const seenTitles = new Set();
    const combined = [];

    for (const item of [...autoList, ...historyData]) {
      const key = String(item.title_zh || '').slice(0, 14);
      if (seenTitles.has(key)) continue;
      seenTitles.add(key);
      combined.push(item);
    }

    const filtered = combined.filter((m) => {
      if (!matchesDomain(m)) return false;
      const tier = Number(m.funnel_tier || 1);
      if (tier < requiredTier) return false;
      return parseYearMonth(m.date) >= minYm;
    });

    filtered.sort((a, b) => parseYearMonth(b.date) - parseYearMonth(a.date));

    $('historyTabBadge').textContent = String(filtered.length);

    const tierDescMap = {
      1: 'Lv.8.5+ 年度級重大事件',
      5: 'Lv.9.0+ 五年格局劇變',
      10: 'Lv.9.5+ 十年時代轉折',
      30: 'Lv.10 教科書級歷史座標',
    };
    const domainNote = activeDomain === 'ALL' ? '全部領域' : activeDomain;
    $('historyMetaNote').innerHTML = `漏斗門檻：<strong>${tierDescMap[rangeYears]}</strong> · 領域：<strong>${escapeHtml(domainNote)}</strong> · 共收錄 <strong>${filtered.length}</strong> 件歷史座標`;

    const container = $('historyTimelineList');
    if (!filtered.length) {
      container.innerHTML = `<div class="empty-col-note">此時間跨度與領域組合下尚無對應等級的歷史紀錄，可切換至「全部領域」或拉長時間跨度查看。</div>`;
      return;
    }

    // Group by year descending
    const byYear = new Map();
    for (const item of filtered) {
      const yr = item.year || String(item.date || '').slice(0, 4);
      if (!byYear.has(yr)) byYear.set(yr, []);
      byYear.get(yr).push(item);
    }

    const htmlBlocks = [];
    for (const [yr, items] of byYear.entries()) {
      const cardsHtml = items
        .map((it) => {
          const isLv10 = String(it.level_label || '').includes('10');
          const lvlCls = isLv10 ? 'level-tag nuclear' : 'level-tag';
          const autoBadge = it.auto_captured ? '<span class="sandbox-tag">⚡ 雷達自動沉澱</span>' : '';
          const linkHtml = it.url && it.url !== '#'
            ? `<a class="source-link" href="${escapeHtml(it.url)}" target="_blank" rel="noopener noreferrer">背景文獻 / 維基參考 ↗</a>`
            : '<span>歷史檔案</span>';

          return `
            <article class="intel-card">
              <div class="card-meta-top">
                <div class="meta-badges">
                  <span class="${lvlCls}">${escapeHtml(it.level_label || 'Lv.9')}</span>
                  <span class="domain-tag">${escapeHtml(it.domain)}</span>
                  ${autoBadge}
                </div>
                <span class="time-stamp">${escapeHtml(it.date)}</span>
              </div>
              <h3 class="card-headline">${escapeHtml(it.title_zh)}</h3>
              <p class="card-summary">${escapeHtml(it.summary_zh)}</p>
              ${it.impact_zh ? `<div class="impact-callout"><strong>歷史座標影響：</strong>${escapeHtml(it.impact_zh)}</div>` : ''}
              <div class="card-footer">
                <div class="source-links">${linkHtml}</div>
                <span>漏斗層級：近 ${it.funnel_tier} 年代表作</span>
              </div>
            </article>
          `;
        })
        .join('');

      htmlBlocks.push(`
        <div class="timeline-year-block">
          <div class="timeline-year-rail">
            <div class="timeline-year-num">${escapeHtml(yr)}</div>
            <div class="timeline-year-count">${items.length} 件座標</div>
          </div>
          <div class="card-stack">${cardsHtml}</div>
        </div>
      `);
    }

    container.innerHTML = htmlBlocks.join('');
  }

  async function loadHistoryChronicle() {
    try {
      const resp = await fetch(`./history.json?v=${Date.now()}`);
      if (resp.ok) {
        const data = await resp.json();
        historyData = Array.isArray(data.milestones) ? data.milestones : [];
        renderHistoryTimeline();
      }
    } catch {
      // ignore
    }
  }

  // --- API / Static State Polling ---
  async function fetchState() {
    if (!isStaticMode) {
      try {
        const resp = await fetch('./api/state');
        if (resp.ok) {
          const prevScanning = appState?.scan_state?.scanning;
          appState = await resp.json();
          renderBoard();

          const nowScanning = appState?.scan_state?.scanning;
          if (prevScanning && !nowScanning && appState.last_scan?.message) {
            showToast(`✅ ${appState.last_scan.message}`);
          }
          schedulePoll(nowScanning ? 1600 : 8000);
          return;
        }
        // HTTP 404 on GitHub Pages -> fall back to static state.json
        if (resp.status === 404) {
          isStaticMode = true;
        }
      } catch {
        // Network error or static file environment -> try state.json
        isStaticMode = true;
      }
    }

    // Static Cloud Mode (GitHub Pages)
    try {
      const resp = await fetch(`./state.json?t=${Date.now()}`);
      if (resp.ok) {
        appState = await resp.json();
        isStaticMode = true;
        renderBoard();
      }
    } catch {
      // ignore
    }
    schedulePoll(45000);
  }

  function schedulePoll(ms) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(fetchState, ms);
  }

  // --- Sandbox Modal ---
  function openSandboxWithPreset(presetKey) {
    const p = SANDBOX_PRESETS[presetKey];
    if (p) {
      $('sandboxTitleInput').value = p.title;
      $('sandboxSummaryInput').value = p.summary;
      $('sandboxSourceSelect').value = p.source_type;
    }
    $('sandboxResultBox').classList.remove('show');
    $('sandboxModal').classList.add('open');
  }

  async function runSandboxEval() {
    const title = $('sandboxTitleInput').value.trim();
    const summary = $('sandboxSummaryInput').value.trim();
    const source_type = $('sandboxSourceSelect').value;
    if (!title) {
      showToast('請先輸入測試標題');
      return;
    }

    const btn = $('btnRunSandbox');
    const box = $('sandboxResultBox');
    btn.disabled = true;
    btn.textContent = 'MLX 本地推論中...';
    box.classList.add('show');
    box.innerHTML = '🧠 正在呼叫本地 Qwen3-MLX 模型進行零幻覺證據判讀...';

    try {
      const resp = await fetch('./api/sandbox', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, summary, source_type, save_to_board: true, fire_notify: true }),
      });
      const data = await resp.json();
      if (!data.ok) {
        box.innerHTML = `❌ 錯誤：${escapeHtml(data.error || '未知錯誤')}`;
        return;
      }
      const r = data.result;
      let verdictHtml = '';
      if (r.kind === 'major') {
        const alertNote = r.impact_level >= Number(appState?.settings?.major_notify_min || 9)
          ? '🚨 <strong>觸發主動推播警報！</strong>（已寫入重大情報看板）'
          : '📌 <strong>靜音收錄（Lv.7–8）</strong>，不發送打擾通知';
        verdictHtml = `分類：<strong>重大確認新聞 (Lv.${r.impact_level})</strong> ｜ ${alertNote}`;
      } else if (r.kind === 'rumor') {
        verdictHtml = `分類：<strong>小道消息傳言版</strong> ｜ 可信度：<strong>${r.credibility_pct}%</strong>（已放入對應傳言欄位）`;
      } else {
        verdictHtml = `分類：<strong>🗑️ 日常雜訊 (Noise)</strong> ｜ 已直接攔截丟棄，不登上看板`;
      }

      box.innerHTML = `
        <div style="margin-bottom:6px;">${verdictHtml} <span class="time-stamp">(${data.latency_ms} ms)</span></div>
        <div style="font-weight:700; margin-bottom:4px;">${escapeHtml(r.title_zh)}</div>
        <div style="color:var(--ink-secondary); margin-bottom:6px;">${escapeHtml(r.summary_zh)}</div>
        <div style="font-size:0.78rem; color:var(--ink-muted);">判讀依據：${escapeHtml(r.reasoning_zh)}</div>
      `;
      await fetchState();
    } catch (err) {
      box.innerHTML = `❌ 請求失敗：${escapeHtml(err.message)}`;
    } finally {
      btn.disabled = false;
      btn.textContent = '送交本地模型判讀';
    }
  }

  async function clearSandbox() {
    await fetch('./api/sandbox/clear', { method: 'POST' });
    showToast('🧹 已清除模擬測試項目');
    $('sandboxResultBox').classList.remove('show');
    fetchState();
  }

  // --- Settings Modal ---
  function openSettingsModal() {
    const s = appState?.settings || {};
    $('setGhRepo').value = s.github_repo || '';
    $('setGhToken').value = s.github_token || '';
    $('setNodeRole').value = s.node_role || 'primary';
    $('setNodeName').value = s.node_name || 'Mac-Primary';
    $('setMajorMin').value = s.major_notify_min || '9';
    $('setScanMin').value = s.scan_interval_min || '15';
    $('setRumorLow').value = s.rumor_low_max || '33';
    $('setRumorWatch').value = s.rumor_watch_max || '67';
    $('setNotifyMacos').value = s.notify_macos || 'true';
    $('setNtfyTopic').value = s.ntfy_topic || '';
    $('setBarkUrl').value = s.bark_url || '';
    $('setTgToken').value = s.telegram_bot_token || '';
    $('setTgChat').value = s.telegram_chat_id || '';
    $('ghPublishStatus').innerHTML = '';
    $('settingsModal').classList.add('open');
  }

  function collectSettingsPayload() {
    return {
      github_repo: $('setGhRepo').value.trim(),
      github_token: $('setGhToken').value.trim(),
      node_role: $('setNodeRole').value,
      node_name: $('setNodeName').value.trim() || 'Mac-Primary',
      major_notify_min: $('setMajorMin').value,
      scan_interval_min: $('setScanMin').value,
      rumor_low_max: $('setRumorLow').value,
      rumor_watch_max: $('setRumorWatch').value,
      notify_macos: $('setNotifyMacos').value,
      ntfy_topic: $('setNtfyTopic').value.trim(),
      bark_url: $('setBarkUrl').value.trim(),
      telegram_bot_token: $('setTgToken').value.trim(),
      telegram_chat_id: $('setTgChat').value.trim(),
    };
  }

  async function saveSettings() {
    const payload = collectSettingsPayload();
    await fetch('./api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    $('settingsModal').classList.remove('open');
    showToast('✅ 設定已儲存');
    fetchState();
  }

  async function publishToGithubNow() {
    const payload = collectSettingsPayload();
    if (!payload.github_token) {
      showToast('⚠️ 請先點右上「取得授權碼」並貼上 ghp_... 授權碼');
      return;
    }
    const btn = $('btnPublishGh');
    const statusEl = $('ghPublishStatus');
    btn.disabled = true;
    btn.textContent = '發佈中...';
    statusEl.textContent = '正在上傳靜態看板與脫敏 state.json 至 gh-pages 分支...';

    try {
      const resp = await fetch('./api/publish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (data.ok) {
        statusEl.innerHTML = `✅ 已同步！手機網址：<a class="source-link" href="${escapeHtml(data.pages_url)}" target="_blank" rel="noopener">${escapeHtml(data.pages_url)} ↗</a>（首次啟用 GitHub Pages 約需等 1 分鐘生效）`;
        showToast('🚀 已成功發佈至 GitHub Pages！');
        fetchState();
      } else {
        statusEl.textContent = `❌ ${data.error || '發佈失敗'}`;
      }
    } catch (err) {
      statusEl.textContent = `❌ 網路錯誤：${err.message}`;
    } finally {
      btn.disabled = false;
      btn.textContent = '🚀 立即發佈 / 更新至 GitHub Pages';
    }
  }

  async function testPushNotification() {
    await saveSettings();
    const resp = await fetch('./api/test-notify', { method: 'POST' });
    const data = await resp.json();
    const ch = (data.channels || []).join(', ');
    showToast(ch ? `🔔 已發送測試通知至：${ch}` : '⚠️ 尚未啟用任何可用的推播管道');
  }

  // --- Event Wiring ---
  function initEvents() {
    // Theme toggle
    const savedTheme = localStorage.getItem('intel_radar_theme') || 'light';
    if (savedTheme === 'dark') {
      document.documentElement.setAttribute('data-theme', 'dark');
    }
    $('btnToggleTheme').addEventListener('click', () => {
      const curr = document.documentElement.getAttribute('data-theme');
      const next = curr === 'dark' ? 'light' : 'dark';
      if (next === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
      else document.documentElement.removeAttribute('data-theme');
      localStorage.setItem('intel_radar_theme', next);
    });

    // Scan trigger & Static refresh
    $('btnScanNow').addEventListener('click', async () => {
      await fetch('./api/scan', { method: 'POST' });
      showToast('📡 已啟動全球信源巡檢');
      fetchState();
    });
    $('btnRefreshStatic').addEventListener('click', () => {
      showToast('🔄 正在讀取最新情報...');
      fetchState();
    });

    // Mode tabs
    document.querySelectorAll('.mode-tab').forEach((btn) => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.mode-tab').forEach((b) => {
          b.classList.remove('active');
          b.setAttribute('aria-selected', 'false');
        });
        document.querySelectorAll('.view-panel').forEach((p) => p.classList.remove('active'));
        btn.classList.add('active');
        btn.setAttribute('aria-selected', 'true');
        $(btn.dataset.target).classList.add('active');
      });
    });

    // Domain chips
    document.querySelectorAll('.filter-chip').forEach((chip) => {
      chip.addEventListener('click', () => {
        document.querySelectorAll('.filter-chip').forEach((c) => c.classList.remove('active'));
        chip.classList.add('active');
        activeDomain = chip.dataset.domain;
        renderBoard();
      });
    });

    // History range dropdown
    const histSelect = $('historyRangeSelect');
    if (histSelect) {
      histSelect.addEventListener('change', renderHistoryTimeline);
    }

    // Sandbox modal
    $('btnOpenSandbox').addEventListener('click', () => openSandboxWithPreset('jensen'));
    $('btnCloseSandbox').addEventListener('click', () => $('sandboxModal').classList.remove('open'));
    $('btnRunSandbox').addEventListener('click', runSandboxEval);
    $('btnClearSandbox').addEventListener('click', clearSandbox);
    $('btnClearSandboxModal').addEventListener('click', clearSandbox);

    document.querySelectorAll('.preset-btn').forEach((btn) => {
      btn.addEventListener('click', () => openSandboxWithPreset(btn.dataset.preset));
    });

    // Settings modal
    $('btnOpenSettings').addEventListener('click', openSettingsModal);
    $('btnCloseSettings').addEventListener('click', () => $('settingsModal').classList.remove('open'));
    $('btnSaveSettings').addEventListener('click', saveSettings);
    $('btnTestPush').addEventListener('click', testPushNotification);
    $('btnPublishGh').addEventListener('click', publishToGithubNow);

    // Close modals on backdrop click
    ['sandboxModal', 'settingsModal'].forEach((id) => {
      $(id).addEventListener('click', (e) => {
        if (e.target === $(id)) $(id).classList.remove('open');
      });
    });
  }

  initEvents();
  loadHistoryChronicle();
  fetchState();
})();
