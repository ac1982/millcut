import { drawSheets, drawPatternStacks, drawParts } from './canvas2d.js';
import { Guillotine3DSimulator } from './sim3d.js';

const $ = (id) => document.getElementById(id);
const HERO_ID = 'data_4e965039991bd2df';
const pct = (v) => `${Number(v).toFixed(1)}%`;
const rmb = (v) => `¥${Math.round(v)}`;
const rmb2 = (v) => `¥${Number(v).toFixed(1)}`;
const pieceCount = (items) => items.reduce((s, it) => s + (it.copies || 1), 0);

let singlePresets = [];
let batchPresets = [];
let factoryData = null;

// Current shop-floor cost parameters (synced with sliders)
const costState = {
  sheetCost: 2.0,
  setupMin: 6.0,
  machineRateHr: 180.0,
  strokeSec: 9.0,
  maxStack: 200,
};

function scorePlan(plan, cfg = costState) {
  const sMax = Math.max(1, cfg.maxStack || 200);
  const plates = plan.plates || [];
  const distinctPatterns = plates.length;
  let clampStacks = 0;
  let totalStrokes = 0;
  for (const pl of plates) {
    const c = Math.max(1, pl.copies || 1);
    const st = Math.ceil(c / sMax);
    clampStacks += st;
    const cutsPerStack = pl.cuts_per_stack != null ? pl.cuts_per_stack : (pl.cuts ? pl.cuts.length : 8);
    totalStrokes += st * cutsPerStack;
  }
  const setupTimeMin = distinctPatterns * cfg.setupMin;
  const cutTimeMin = totalStrokes * ((cfg.strokeSec || 9.0) / 60.0);
  const totalTimeMin = setupTimeMin + cutTimeMin;
  const ratePerMin = cfg.machineRateHr / 60.0;
  const paperCost = plan.sheets_used * cfg.sheetCost;
  const laborMachineCost = totalTimeMin * ratePerMin;
  const totalCost = paperCost + laborMachineCost;
  return {
    sheets: plan.sheets_used,
    patterns: distinctPatterns,
    clampStacks,
    strokes: totalStrokes,
    setupTimeMin,
    cutTimeMin,
    totalTimeMin,
    paperCost,
    laborMachineCost,
    totalCost,
  };
}

function pickNamedRoles(frontier, cfg = costState) {
  const scored = frontier.map((plan) => ({ plan, econ: scorePlan(plan, cfg) }));
  const byPaper = [...scored].sort((a, b) => (a.econ.sheets - b.econ.sheets) || (a.econ.totalCost - b.econ.totalCost))[0];
  const byCost = [...scored].sort((a, b) => (a.econ.totalCost - b.econ.totalCost) || (a.econ.sheets - b.econ.sheets))[0];
  const bySetup = [...scored].sort(
    (a, b) => (a.econ.patterns - b.econ.patterns) || (a.econ.totalTimeMin - b.econ.totalTimeMin) || (a.econ.sheets - b.econ.sheets)
  )[0];
  return [
    {
      key: 'min_paper',
      title: '方案 A · 极限省纸',
      badge: '原纸最少',
      item: byPaper,
    },
    {
      key: 'min_cost',
      title: '方案 B · 综合总成本最低',
      badge: '推荐首选',
      item: byCost,
    },
    {
      key: 'min_setup',
      title: '方案 C · 最少换刀极速',
      badge: '出货最快',
      item: bySetup,
    },
  ];
}

// ---------- 1. Batch Shop-Floor Total-Cost Workbench ----------
let activeBatchIdx = 0;
let activeRoleKey = 'min_cost';

function initBatchWorkbench() {
  if (!batchPresets.length) return;
  const sel = $('batchOrderSelect');
  sel.innerHTML = '';
  batchPresets.forEach((bp, idx) => {
    const opt = document.createElement('option');
    opt.value = String(idx);
    opt.textContent = `${bp.title_zh}（${bp.sku_count} 款共 ${bp.total_pieces} 件）`;
    sel.appendChild(opt);
  });

  sel.addEventListener('change', () => {
    activeBatchIdx = parseInt(sel.value, 10) || 0;
    renderBatchWorkbench();
  });

  const syncSlidersUI = () => {
    $('valSheetCost').textContent = `¥${costState.sheetCost.toFixed(2)}`;
    $('valSetupMin').textContent = `${costState.setupMin} 分钟`;
    $('valMachineRate').textContent = `¥${costState.machineRateHr} / 小时`;
    $('rngSheetCost').value = String(costState.sheetCost);
    $('rngSetupMin').value = String(costState.setupMin);
    $('rngMachineRate').value = String(costState.machineRateHr);
  };

  $('rngSheetCost').addEventListener('input', (e) => {
    costState.sheetCost = parseFloat(e.target.value);
    syncSlidersUI();
    renderBatchWorkbench();
  });
  $('rngSetupMin').addEventListener('input', (e) => {
    costState.setupMin = parseFloat(e.target.value);
    $('presetGuillotine').classList.toggle('active', costState.setupMin === 6);
    $('presetSlitter').classList.toggle('active', costState.setupMin === 15);
    syncSlidersUI();
    renderBatchWorkbench();
  });
  $('rngMachineRate').addEventListener('input', (e) => {
    costState.machineRateHr = parseFloat(e.target.value);
    syncSlidersUI();
    renderBatchWorkbench();
  });

  $('presetGuillotine').addEventListener('click', () => {
    costState.setupMin = 6;
    costState.machineRateHr = 180;
    costState.strokeSec = 9;
    costState.maxStack = 200;
    $('presetGuillotine').classList.add('active');
    $('presetSlitter').classList.remove('active');
    syncSlidersUI();
    renderBatchWorkbench();
  });

  $('presetSlitter').addEventListener('click', () => {
    costState.setupMin = 15;
    costState.machineRateHr = 240;
    costState.strokeSec = 6;
    costState.maxStack = 250;
    $('presetSlitter').classList.add('active');
    $('presetGuillotine').classList.remove('active');
    syncSlidersUI();
    renderBatchWorkbench();
  });

  syncSlidersUI();
  renderBatchWorkbench();
  window.addEventListener('resize', () => renderBatchWorkbench());
}

function renderBatchWorkbench() {
  const bp = batchPresets[activeBatchIdx];
  if (!bp) return;

  const baseEcon = scorePlan(bp.baseline, costState);
  const roles = pickNamedRoles(bp.frontier, costState);
  const activeRole = roles.find((r) => r.key === activeRoleKey) || roles[1];
  const chosenPlan = activeRole.item.plan;
  const chosenEcon = activeRole.item.econ;

  // Render 3 clickable role cards
  const tabsEl = $('batchPlanTabs');
  tabsEl.innerHTML = '';
  roles.forEach((role) => {
    const e = role.item.econ;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `plan-tab${role.key === activeRole.key ? ' active' : ''}`;
    btn.innerHTML = `
      <div class="pt-head">
        <span class="pt-title">${role.title}</span>
        <span class="pt-badge">${role.badge}</span>
      </div>
      <div class="pt-metrics">
        <b>${rmb(e.totalCost)}</b> · ${e.sheets} 张大纸 · <b>${e.patterns} 种版式</b> · ${Math.round(e.totalTimeMin)} 分钟
      </div>
    `;
    btn.addEventListener('click', () => {
      activeRoleKey = role.key;
      renderBatchWorkbench();
    });
    tabsEl.appendChild(btn);
  });

  // Left: conventional PackingSolver 10s baseline
  $('batchBaseMeta').textContent =
    `${baseEcon.sheets} 张大纸 · 拆成 ${baseEcon.patterns} 种版式 · 耗时 ${Math.round(baseEcon.totalTimeMin)} 分钟`;
  $('batchBaseCost').textContent = rmb(baseEcon.totalCost);
  $('batchBaseFoot').innerHTML =
    `原纸 <b>${rmb(baseEcon.paperCost)}</b> + 换版与裁切工时 <b>${rmb(baseEcon.laborMachineCost)}</b>（共推刀 ${baseEcon.strokes} 次 · 利用率 ${pct(bp.baseline.utilization_pct)}）`;

  // Right: chosen Pareto shop-floor plan
  const savedRmb = baseEcon.totalCost - chosenEcon.totalCost;
  const savedPct = baseEcon.totalCost > 0 ? Math.round((100 * savedRmb) / baseEcon.totalCost) : 0;
  const savedTimePct = baseEcon.totalTimeMin > 0
    ? Math.round((100 * (baseEcon.totalTimeMin - chosenEcon.totalTimeMin)) / baseEcon.totalTimeMin)
    : 0;

  $('batchOursTitle').textContent = `我们的排法 · ${activeRole.title.replace(/^方案 [ABC] · /, '')}`;
  $('batchOursMeta').textContent =
    `${chosenEcon.sheets} 张大纸 · 仅 ${chosenEcon.patterns} 种版式 · 耗时 ${Math.round(chosenEcon.totalTimeMin)} 分钟（省时 ${savedTimePct}%）`;
  $('batchSavingBadge').textContent = savedRmb >= 1 ? `单单立省 ${rmb(savedRmb)}（-${savedPct}%）` : '综合持平';
  $('batchOursCost').textContent = rmb(chosenEcon.totalCost);
  $('batchOursFoot').innerHTML =
    `原纸 <b>${rmb(chosenEcon.paperCost)}</b> + 换版与裁切工时 <b class="good">${rmb(chosenEcon.laborMachineCost)}</b>（叠切仅推刀 ${chosenEcon.strokes} 次 · 利用率 ${pct(chosenPlan.utilization_pct)}）`;

  drawPatternStacks($('batchBaseCanvas'), bp.baseline, { maxTotalHeight: 300 });
  drawPatternStacks($('batchOursCanvas'), chosenPlan, { maxTotalHeight: 300 });
}

// ---------- 2. Single-Copy Irregular Orders: 3 -> 2 Sheets + 3D Guillotine ----------
function initSingleSection(p) {
  if (!p) return;
  const slots = Math.max(p.baseline.sheets_used, p.optimized.sheets_used);
  const lastBase = p.baseline.plates[p.baseline.plates.length - 1];

  $('heroBaseCount').textContent = p.baseline.sheets_used;
  $('heroOursCount').textContent = p.optimized.sheets_used;
  $('heroBaseUtil').textContent = pct(p.baseline.utilization_pct);
  $('heroOursUtil').textContent = pct(p.optimized.utilization_pct);
  $('heroDesc').textContent = `${pieceCount(p.items)} 个不同尺寸的异形件，大纸 ${p.sheet_width} × ${p.sheet_height} mm`;

  const render = (prog) => {
    const done = prog >= 1;
    drawSheets($('heroBase'), p.baseline, {
      slots,
      progress: prog,
      weakLast: done ? `尾张仅用 ${Math.round(lastBase.utilization_pct)}%` : null,
    });
    drawSheets($('heroOurs'), p.optimized, {
      slots,
      progress: prog,
      ghostLabel: done ? '省下一整张' : '',
    });
  };

  let start = null;
  let running = false;
  const duration = 3000;
  const tick = (ts) => {
    if (start === null) start = ts;
    const prog = Math.min(1, (ts - start) / duration);
    render(prog);
    if (prog < 1) requestAnimationFrame(tick);
    else running = false;
  };
  const play = () => {
    start = null;
    if (!running) {
      running = true;
      requestAnimationFrame(tick);
    }
  };

  render(1);
  $('heroReplay').addEventListener('click', play);
  window.addEventListener('resize', () => { if (!running) render(1); });

  const drawStatic = () => {
    drawParts($('howInput'), p.items, p.sheet_width, p.sheet_height);
    drawSheets($('howLayout'), p.optimized, {
      slots: p.optimized.sheets_used,
      progress: 1,
      maxHeight: 220,
    });
  };
  drawStatic();
  window.addEventListener('resize', drawStatic);

  const caption = $('howCutCaption');
  const stageText = { 1: '第 1 轮：横切成条', 2: '第 2 轮：竖切成块', 3: '第 3 轮：修齐尺寸' };
  let restartTimer = null;

  const sim = new Guillotine3DSimulator({
    container: $('howCut'),
    hoverBadgeEl: null,
    onStepChange: (step, total, cut, finished) => {
      if (finished) {
        caption.textContent = `切完：${p.optimized.plates[0].part_count} 个零件，每刀都从头切到尾。`;
        clearTimeout(restartTimer);
        restartTimer = setTimeout(() => { sim.setStep(0, false); sim.play(); }, 1800);
        return;
      }
      if (step === 0 || !cut) {
        caption.textContent = '每刀从头切到尾，最多切三轮。';
      } else {
        caption.textContent = `${stageText[Math.min(3, cut.raw_depth)]}（第 ${step} / ${total} 刀）`;
      }
    },
  });
  sim.setTheme('light', 'zh');
  sim.speed = 2.4;
  sim.loadPlate(p.optimized.plates[0], 0);
  sim.setCameraPreset('iso');

  let started = false;
  new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting) && !started) {
      started = true;
      play();
      sim.play();
    }
  }, { threshold: 0.25 }).observe($('single'));
}

// ---------- 3. Populate Proof Scorecard from Archived Benchmark Summary ----------
function initProofStats() {
  if (!factoryData) return;
  const ind = factoryData.r60_headline?.industrial_batch_12;
  if (ind) {
    const defC = ind.default_10s_totals.cost_rmb_guillotine;
    const ourC = ind.factory_pareto_min_cost_guillotine_totals.cost_rmb_guillotine;
    const savePct = Math.round((100 * (defC - ourC)) / defC);
    const defP = ind.default_10s_totals.patterns;
    const ourP = ind.factory_pareto_min_cost_guillotine_totals.patterns;
    const defT = Math.round(ind.default_10s_totals.time_min);
    const ourT = Math.round(ind.factory_pareto_min_cost_guillotine_totals.time_min);
    const paperOnlyP = ind.factory_pareto_min_paper_totals.patterns;
    const paperOnlyS = ind.factory_pareto_min_paper_totals.sheets;
    const defS = ind.default_10s_totals.sheets;

    $('statBatchSave').textContent = `省 ${savePct}%`;
    $('statBatchSub').innerHTML =
      `12 份白卡批量单：版式 <b>${defP} → ${ourP} 种</b>，工时 <b>${defT} → ${ourT} 分钟</b>；若选极限省纸则用纸 <b>${defS} → ${paperOnlyS} 张</b> 且版式仍减至 ${paperOnlyP} 种`;
  }

  const r59 = factoryData.r59_headline;
  if (r59) {
    const c1 = r59.r52_berkey_dev_8;
    const c2 = r59.r55_martello_diag_12;
    const c3 = r59.fresh_berkey_dev_12;
    const totInst = c1.instance_count + c2.instance_count + c3.instance_count;
    const eqBest =
      c1.methods.hp_descent_60s.equal_to_bks + c1.methods.hp_descent_60s.better_than_bks +
      c2.methods.hp_descent_60s.equal_to_bks + c2.methods.hp_descent_60s.better_than_bks +
      c3.methods.hp_descent_30s.equal_to_bks + c3.methods.hp_descent_30s.better_than_bks;
    $('statBksRate').textContent = `${eqBest} / ${totInst} 题`;
    $('statBksSub').innerHTML =
      `对标 Berkey / Martello 公开基准库长期最好成绩，精确目标下探在 <b>${totInst} 题中追平 ${eqBest} 题</b>，其余高难题仅差 1 张`;
  }
}

// ---------- 4. Live Browser WebAssembly Trial (Batch Pareto + Single-Copy) ----------
let worker = null;
let rpcId = 1;
const pending = new Map();
function call(type, payload) {
  if (!worker) {
    worker = new Worker('./mill_worker.js');
    worker.onmessage = (e) => {
      const { id, ...rest } = e.data;
      const resolve = pending.get(id);
      if (resolve) {
        pending.delete(id);
        resolve(rest);
      }
    };
  }
  const id = rpcId++;
  return new Promise((resolve) => {
    pending.set(id, resolve);
    worker.postMessage({ id, type, payload });
  });
}

let customItemsCsv = null;
let customMaxCopies = 1;
let lastDownload = null;

function parseItemsCsv(text) {
  const lines = text.trim().split(/\r?\n/).filter((l) => l.trim());
  let iId = 0, iW = 1, iH = 2, iC = 3;
  let startRow = 0;
  const head = lines[0].split(',').map((s) => s.trim().toUpperCase());
  if (head.includes('WIDTH')) {
    iId = head.indexOf('ID');
    iW = head.indexOf('WIDTH');
    iH = head.indexOf('HEIGHT');
    iC = head.indexOf('COPIES');
    startRow = 1;
  }
  const rows = ['ID,WIDTH,HEIGHT,COPIES'];
  let n = 0;
  let maxC = 1;
  for (let r = startRow; r < lines.length; r++) {
    const c = lines[r].split(',').map((s) => parseInt(s.trim(), 10));
    const w = c[iW];
    const h = c[iH];
    if (!(w > 0 && h > 0)) continue;
    const id = iId >= 0 && Number.isFinite(c[iId]) ? c[iId] : (rows.length - 1);
    const copies = iC >= 0 && c[iC] > 0 ? c[iC] : 1;
    rows.push(`${id},${w},${h},${copies}`);
    n += copies;
    if (copies > maxC) maxC = copies;
  }
  return { csv: rows.join('\n') + '\n', pieces: n, types: rows.length - 1, maxCopies: maxC };
}

function download(name, text) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

function initTry() {
  const sel = $('trySelect');
  sel.innerHTML = '';

  batchPresets.forEach((bp, idx) => {
    const o = document.createElement('option');
    o.value = `batch:${idx}`;
    o.textContent = `【车间批量单】${bp.title_zh}（${bp.total_pieces} 件）`;
    sel.appendChild(o);
  });

  singlePresets.forEach((sp, idx) => {
    const o = document.createElement('option');
    o.value = `single:${idx}`;
    o.textContent = `【异形散单】${pieceCount(sp.items)} 个异形零件 · 大纸 ${sp.sheet_width}×${sp.sheet_height}`;
    sel.appendChild(o);
  });

  const oc = document.createElement('option');
  oc.value = 'custom';
  oc.textContent = '【自定义订单】上传自己的零件清单 CSV';
  sel.appendChild(oc);

  sel.addEventListener('change', () => {
    $('customFields').hidden = sel.value !== 'custom';
  });

  $('dlSampleCsv').addEventListener('click', () => {
    const sample = [
      'ID,WIDTH,HEIGHT,COPIES',
      '0,285,410,120',
      '1,285,360,120',
      '2,295,390,90',
      '3,210,290,180',
      '4,220,290,150',
      '5,180,260,100',
    ].join('\n') + '\n';
    download('车间订单模板_889x1194.csv', sample);
  });

  $('customFile').addEventListener('change', async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    const parsed = parseItemsCsv(await f.text());
    customItemsCsv = parsed.types > 0 ? parsed.csv : null;
    customMaxCopies = parsed.maxCopies;
    $('customInfo').textContent = customItemsCsv
      ? `${f.name}：${parsed.types} 款，共 ${parsed.pieces} 件`
      : '未读到有效零件，请检查列名：ID, WIDTH, HEIGHT, COPIES';
  });

  $('tryRun').addEventListener('click', runTry);
  $('dlPlan').addEventListener('click', () => {
    if (lastDownload) download(`${lastDownload.name}_车间裁切工单.md`, lastDownload.md);
  });
  $('dlTree').addEventListener('click', () => {
    if (lastDownload) download(`${lastDownload.name}_cut_tree.csv`, lastDownload.csv);
  });

  let lastRender = null;
  window.addEventListener('resize', () => lastRender && lastRender());

  async function runTry() {
    const btn = $('tryRun');
    let binsCsv, itemsCsv, baseline = null, name, baseLabel, isBatch = false;

    if (sel.value === 'custom') {
      if (!customItemsCsv) {
        $('customInfo').textContent = '请先选择零件清单 CSV（或点击下载模板）';
        return;
      }
      const W = parseInt($('customW').value, 10) || 889;
      const H = parseInt($('customH').value, 10) || 1194;
      binsCsv = `ID,WIDTH,HEIGHT\n0,${W},${H}\n`;
      itemsCsv = customItemsCsv;
      name = 'custom_order';
      baseLabel = '常规简易排法（对照）';
      isBatch = customMaxCopies >= 2;
    } else if (sel.value.startsWith('batch:')) {
      const bp = batchPresets[parseInt(sel.value.split(':')[1], 10)];
      binsCsv = bp.bins_csv;
      itemsCsv = bp.items_csv;
      baseline = bp.baseline;
      name = bp.id;
      baseLabel = '常规省纸排法（PackingSolver 10 秒）';
      isBatch = true;
    } else {
      const sp = singlePresets[parseInt(sel.value.split(':')[1], 10)];
      binsCsv = sp.bins_csv;
      itemsCsv = sp.items_csv;
      baseline = sp.baseline;
      name = sp.id;
      baseLabel = '常规排法（PackingSolver 10 秒）';
      isBatch = false;
    }

    btn.disabled = true;
    btn.textContent = '浏览器计算中…';
    const t0 = performance.now();

    if (isBatch) {
      const r = await call('solve_pareto', {
        binsCsv,
        itemsCsv,
        sheetCost: costState.sheetCost,
        machineRateHr: costState.machineRateHr,
        setupMin: costState.setupMin,
        strokeSec: costState.strokeSec,
        maxStack: costState.maxStack,
        budgetS: 3.0,
        seed: 601,
        orderName: name,
        existingBaseline: baseline,
      });
      const seconds = (performance.now() - t0) / 1000;
      btn.disabled = false;
      btn.textContent = '立即排样算账';
      $('tryResult').hidden = false;

      if (!r.ok || !r.frontier?.length) {
        $('trySummary').textContent = `无法计算：${r.error || '未知错误'}`;
        return;
      }

      const roles = pickNamedRoles(r.frontier, costState);
      let curKey = 'min_cost';
      const tabsEl = $('tryParetoTabs');
      tabsEl.hidden = false;

      const renderLiveChoice = async () => {
        const active = roles.find((x) => x.key === curKey) || roles[1];
        const plan = active.item.plan;
        const econ = active.item.econ;
        const baseE = scorePlan(r.baseline, costState);
        const v = await call('verify', { binsCsv, itemsCsv, cutTreeCsv: plan.cut_tree_csv, orderName: name });
        const ok = v.ok && v.report?.valid;

        tabsEl.innerHTML = '';
        roles.forEach((rl) => {
          const e = rl.item.econ;
          const b = document.createElement('button');
          b.type = 'button';
          b.className = `plan-tab${rl.key === active.key ? ' active' : ''}`;
          b.innerHTML = `
            <div class="pt-head"><span class="pt-title">${rl.title}</span><span class="pt-badge">${rl.badge}</span></div>
            <div class="pt-metrics"><b>${rmb(e.totalCost)}</b> · ${e.sheets} 张 · <b>${e.patterns} 版</b> · ${Math.round(e.totalTimeMin)} 分钟</div>
          `;
          b.addEventListener('click', () => {
            curKey = rl.key;
            renderLiveChoice();
          });
          tabsEl.appendChild(b);
        });

        const diffCost = baseE.totalCost - econ.totalCost;
        const verdict = diffCost >= 1 ? `综合省 ${rmb2(diffCost)}` : '综合持平';
        $('trySummary').innerHTML =
          `<b>${rmb(baseE.totalCost)} → ${rmb(econ.totalCost)}（${verdict}）</b>　` +
          `版式 ${baseE.patterns} → <b>${econ.patterns} 种</b> · 用纸 ${baseE.sheets} → ${econ.sheets} 张 · 工时 ${Math.round(baseE.totalTimeMin)} → ${Math.round(econ.totalTimeMin)} 分钟` +
          `　<span class="muted">本地耗时 ${seconds.toFixed(1)}s · ${ok ? '✓ 独立几何校验通过' : '✗ 校验异常'}</span>`;

        $('tryBaseLabel').textContent = `${baseLabel}（${baseE.patterns} 版 · ${baseE.sheets} 张 · ${rmb(baseE.totalCost)}）`;
        $('tryOursLabel').textContent = `本地实时方案（${econ.patterns} 版 · ${econ.sheets} 张 · ${rmb(econ.totalCost)}）`;
        lastDownload = { name, md: plan.cutting_plan_md, csv: plan.cut_tree_csv };

        lastRender = () => {
          drawPatternStacks($('tryBase'), r.baseline, { maxTotalHeight: 280 });
          drawPatternStacks($('tryOurs'), plan, { maxTotalHeight: 280 });
        };
        lastRender();
      };
      await renderLiveChoice();
    } else {
      $('tryParetoTabs').hidden = true;
      const r = await call('solve', { binsCsv, itemsCsv, budgetS: 4.5, seed: 501, orderName: name, existingBaseline: baseline });
      const seconds = (performance.now() - t0) / 1000;
      btn.disabled = false;
      btn.textContent = '立即排样算账';
      $('tryResult').hidden = false;

      if (!r.ok) {
        $('trySummary').textContent = `无法计算：${r.error || '未知错误'}`;
        return;
      }
      const v = await call('verify', { binsCsv, itemsCsv, cutTreeCsv: r.optimized.cut_tree_csv, orderName: name });
      const ok = v.ok && v.report?.valid;

      const b = r.baseline.sheets_used;
      const o = r.optimized.sheets_used;
      const diff = b - o;
      const verdict = diff > 0 ? `少用 ${diff} 张大纸` : (diff === 0 ? '张数持平' : `多 ${-diff} 张`);
      $('trySummary').innerHTML =
        `<b>${b} 张 → ${o} 张（${verdict}）</b>　利用率 ${pct(r.baseline.utilization_pct)} → ${pct(r.optimized.utilization_pct)}` +
        `　<span class="muted">本地耗时 ${seconds.toFixed(1)}s · ${ok ? '✓ 独立几何校验通过' : '✗ 校验异常'}</span>`;
      $('tryBaseLabel').textContent = `${baseLabel}（${b} 张）`;
      $('tryOursLabel').textContent = `本地实时方案（${o} 张）`;

      lastDownload = { name, md: r.optimized.cutting_plan_md, csv: r.optimized.cut_tree_csv };
      const slots = Math.max(b, o);
      lastRender = () => {
        drawSheets($('tryBase'), r.baseline, { slots, progress: 1, maxHeight: 250 });
        drawSheets($('tryOurs'), r.optimized, { slots, progress: 1, maxHeight: 250, ghostLabel: diff > 0 ? '省下' : '' });
      };
      lastRender();
    }
  }
}

// ---------- Boot ----------
async function main() {
  const [singleResp, factoryResp] = await Promise.all([
    fetch('./showcase_presets.json').then((r) => r.json()),
    fetch('./factory_showcase.json').then((r) => (r.ok ? r.json() : null)).catch(() => null),
  ]);

  singlePresets = (singleResp.presets || []).filter((p) => p.optimized.sheets_used < p.baseline.sheets_used);
  if (factoryResp) {
    factoryData = factoryResp;
    batchPresets = factoryResp.batch_presets || [];
  }

  initBatchWorkbench();
  const hero = singlePresets.find((p) => p.id === HERO_ID) || singlePresets[0];
  initSingleSection(hero);
  initProofStats();
  initTry();
}

main();
