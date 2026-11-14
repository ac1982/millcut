// Dedicated Web Worker for Pure C++23 WebAssembly Paper Mill Solver & Verifier
// Runs off the main UI thread so 60fps Three.js & 2D Canvas animations never stutter.

importScripts('./wasm/mill_cxx23.js');

let wasmMod = null;
let engineInfo = null;

async function ensureWasm() {
  if (!wasmMod) {
    wasmMod = await createMillCxx23({
      locateFile: (path) => `./wasm/${path}`,
    });
    const infoRaw = wasmMod.ccall('wasm_engine_info', 'string', [], []);
    engineInfo = JSON.parse(infoRaw);
  }
  return wasmMod;
}

function parseCsvRows(csvText) {
  const lines = csvText.trim().split(/\r?\n/).map(l => l.trim()).filter(Boolean);
  if (lines.length === 0) return [];
  const headers = lines[0].split(',').map(s => s.trim());
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    const cols = lines[i].split(',').map(s => s.trim());
    const obj = {};
    headers.forEach((h, idx) => { obj[h] = cols[idx] ?? ''; });
    rows.push(obj);
  }
  return rows;
}

function parseCutTreeForWeb(binsCsv, itemsCsv, cutTreeCsv) {
  const bRows = parseCsvRows(binsCsv);
  const W = parseInt(bRows[0].WIDTH, 10);
  const H = parseInt(bRows[0].HEIGHT, 10);

  const iRows = parseCsvRows(itemsCsv);
  const itemSpecs = iRows.map((r, idx) => ({
    item_idx: idx,
    item_id: parseInt(r.ID ?? idx, 10),
    w: parseInt(r.WIDTH, 10),
    h: parseInt(r.HEIGHT, 10),
    copies: parseInt(r.COPIES ?? '1', 10),
  }));

  const tRows = parseCsvRows(cutTreeCsv);
  const byPlate = new Map();
  for (const r of tRows) {
    const pid = parseInt(r.PLATE_ID, 10);
    if (!byPlate.has(pid)) byPlate.set(pid, []);
    byPlate.get(pid).push({
      plate_id: pid,
      node_id: parseInt(r.NODE_ID, 10),
      x: parseInt(r.X, 10),
      y: parseInt(r.Y, 10),
      w: parseInt(r.WIDTH, 10),
      h: parseInt(r.HEIGHT, 10),
      type: parseInt(r.TYPE, 10),
      cut: parseInt(r.CUT, 10),
      parent: r.PARENT === '' ? -1 : parseInt(r.PARENT, 10),
      copies: r.COPIES === '' ? 1 : parseInt(r.COPIES, 10),
    });
  }

  const platesOut = [];
  let totalSheets = 0;
  let totalItemArea = 0;

  const sortedPids = Array.from(byPlate.keys()).sort((a, b) => a - b);
  for (const pid of sortedPids) {
    const pnodes = byPlate.get(pid);
    const children = new Map();
    let root = null;
    for (const n of pnodes) {
      if (n.parent < 0) root = n;
      else {
        if (!children.has(n.parent)) children.set(n.parent, []);
        children.get(n.parent).push(n);
      }
    }
    const copies = root ? root.copies : 1;
    totalSheets += copies;

    const itemsPlaced = [];
    const wastes = [];
    const cutLines = [];

    for (const n of pnodes) {
      const ch = children.get(n.node_id) || [];
      if (ch.length === 0) {
        if (n.type >= 0 && (!root || n.node_id !== root.node_id)) {
          const spec = itemSpecs[n.type] || { item_id: n.type };
          itemsPlaced.push({
            item_idx: n.type,
            item_id: spec.item_id,
            x: n.x,
            y: n.y,
            w: n.w,
            h: n.h,
          });
          totalItemArea += n.w * n.h * copies;
        } else if ((n.type === -1 || n.type === -3) && n.w > 0 && n.h > 0) {
          wastes.push({
            x: n.x,
            y: n.y,
            w: n.w,
            h: n.h,
            reusable: n.type === -3,
            cut: n.cut,
          });
        }
      } else {
        const nextD = n.cut + 1;
        if (nextD % 2 === 1) {
          const chSorted = [...ch].sort((a, b) => a.y - b.y);
          for (let i = 0; i < chSorted.length - 1; i++) {
            const yCut = chSorted[i].y + chSorted[i].h;
            if (yCut > n.y && yCut < n.y + n.h) {
              cutLines.push({
                stage: Math.min(3, nextD),
                raw_depth: nextD,
                orientation: 'H',
                x1: n.x,
                y1: yCut,
                x2: n.x + n.w,
                y2: yCut,
                coord: yCut,
                parent_x: n.x,
                parent_y: n.y,
                parent_w: n.w,
                parent_h: n.h,
              });
            }
          }
        } else {
          const chSorted = [...ch].sort((a, b) => a.x - b.x);
          for (let i = 0; i < chSorted.length - 1; i++) {
            const xCut = chSorted[i].x + chSorted[i].w;
            if (xCut > n.x && xCut < n.x + n.w) {
              cutLines.push({
                stage: Math.min(3, nextD),
                raw_depth: nextD,
                orientation: 'V',
                x1: xCut,
                y1: n.y,
                x2: xCut,
                y2: n.y + n.h,
                coord: xCut,
                parent_x: n.x,
                parent_y: n.y,
                parent_w: n.w,
                parent_h: n.h,
              });
            }
          }
        }
      }
    }

    cutLines.sort((a, b) =>
      (a.raw_depth - b.raw_depth) ||
      (a.parent_y - b.parent_y) ||
      (a.parent_x - b.parent_x) ||
      (a.coord - b.coord)
    );

    const stripsStruct = [];
    if (root) {
      const lvl1 = [...(children.get(root.node_id) || [])].sort((a, b) => a.y - b.y);
      for (const sNode of lvl1) {
        const sCh = children.get(sNode.node_id) || [];
        if ((sNode.type === -1 || sNode.type === -3) && sCh.length === 0) continue;
        const sObj = { y: sNode.y, h: sNode.h, columns: [] };
        const lvl2 = [...sCh].sort((a, b) => a.x - b.x);
        if (lvl2.length === 0 && sNode.type >= 0) {
          const spec = itemSpecs[sNode.type] || { item_id: sNode.type };
          sObj.columns.push({
            x: sNode.x, y: sNode.y, w: sNode.w, h: sNode.h,
            slices: [{ item_idx: sNode.type, item_id: spec.item_id, x: sNode.x, y: sNode.y, w: sNode.w, h: sNode.h }],
          });
        }
        for (const cNode of lvl2) {
          const cCh = children.get(cNode.node_id) || [];
          if ((cNode.type === -1 || cNode.type === -3) && cCh.length === 0) continue;
          const colObj = { x: cNode.x, y: cNode.y, w: cNode.w, h: sNode.h, slices: [] };
          if (cNode.type >= 0 && cCh.length === 0) {
            const spec = itemSpecs[cNode.type] || { item_id: cNode.type };
            colObj.slices.push({
              item_idx: cNode.type, item_id: spec.item_id, x: cNode.x, y: cNode.y, w: cNode.w, h: cNode.h,
            });
          } else {
            const lvl3 = [...cCh].sort((a, b) => a.y - b.y);
            for (const l3 of lvl3) {
              if (l3.type >= 0) {
                const spec = itemSpecs[l3.type] || { item_id: l3.type };
                colObj.slices.push({
                  item_idx: l3.type, item_id: spec.item_id, x: l3.x, y: l3.y, w: l3.w, h: l3.h,
                });
              } else {
                for (const l4 of (children.get(l3.node_id) || [])) {
                  if (l4.type >= 0) {
                    const spec = itemSpecs[l4.type] || { item_id: l4.type };
                    colObj.slices.push({
                      item_idx: l4.type, item_id: spec.item_id, x: l4.x, y: l4.y, w: l4.w, h: l4.h,
                    });
                  }
                }
              }
            }
          }
          if (colObj.slices.length > 0) sObj.columns.push(colObj);
        }
        if (sObj.columns.length > 0) stripsStruct.push(sObj);
      }
    }

    const usedAreaPlate = itemsPlaced.reduce((acc, it) => acc + it.w * it.h, 0);
    platesOut.push({
      plate_id: pid,
      copies,
      sheet_width: W,
      sheet_height: H,
      part_count: itemsPlaced.length,
      cuts_per_stack: cutLines.length,
      used_area: usedAreaPlate,
      utilization_pct: Math.round((1000000 * usedAreaPlate) / (W * H)) / 10000,
      items: itemsPlaced,
      wastes,
      cuts: cutLines,
      strips: stripsStruct,
    });
  }

  const totSheetArea = totalSheets * W * H;
  const utilPct = Math.round((1000000 * totalItemArea) / Math.max(1, totSheetArea)) / 10000;
  return {
    sheets_used: totalSheets,
    distinct_patterns: platesOut.length,
    sheet_width: W,
    sheet_height: H,
    total_item_area: totalItemArea,
    total_sheet_area: totSheetArea,
    waste_area: totSheetArea - totalItemArea,
    utilization_pct: utilPct,
    waste_pct: Math.round((10000 - utilPct * 100)) / 100,
    cut_tree_csv: cutTreeCsv,
    plates: platesOut,
  };
}

function buildShopFloorMd(orderName, plan, baseSheets) {
  const saved = baseSheets - plan.sheets_used;
  const lines = [
    `# 车间三阶段断头台裁切工单 — \`${orderName}\``,
    ``,
    `- **母板规格**: \`${plan.sheet_width} × ${plan.sheet_height}\`（固定纹理方向，禁止旋转，零刀缝）`,
    `- **工艺标准**: 三阶段非精确横首刀通刀裁切（Stage 1 横切分条 → Stage 2 纵切分列 → Stage 3 齐头精修）`,
    `- **最终用纸量**: **\`${plan.sheets_used}\` 张**（对比基线 \`${baseSheets}\` 张，**净省 \`${saved}\` 张**）`,
    `- **整单成材率**: **\`${plan.utilization_pct.toFixed(2)}%\`**（废料率 \`${plan.waste_pct.toFixed(2)}%\`）`,
    ``,
  ];
  for (const pl of plan.plates) {
    lines.push(`## 第 ${pl.plate_id + 1} 张大纸（执行 \`${pl.copies}\` 份 · 利用率 \`${pl.utilization_pct.toFixed(2)}%\` · 产出 \`${pl.part_count}\` 件）`);
    const s1 = pl.cuts.filter(c => c.raw_depth === 1).map(c => c.coord);
    const s2 = pl.cuts.filter(c => c.raw_depth === 2).length;
    const s3 = pl.cuts.filter(c => c.raw_depth >= 3).length;
    lines.push(`- **第一阶段（横首刀分条）**: 共 \`${s1.length}\` 刀 -> Y 坐标 \`[${s1.join(', ')}]\``);
    pl.strips.forEach((sp, idx) => {
      const ids = sp.columns.flatMap(col => col.slices.map(s => s.item_id));
      lines.push(`  - 条带 #${idx + 1} (\`Y=${sp.y}..${sp.y + sp.h}\`, 高 \`${sp.h}\`): 纵切 \`${sp.columns.length}\` 列，产出零件 ID \`[${ids.join(', ')}]\``);
    });
    lines.push(`- **第二阶段（纵切分列）**: \`${s2}\` 刀；**第三阶段（尾刀精修）**: \`${s3}\` 刀`);
    lines.push(``);
  }
  return lines.join('\n');
}

self.onmessage = async (ev) => {
  const { id, type, payload } = ev.data;
  try {
    const mod = await ensureWasm();

    if (type === 'init') {
      self.postMessage({ id, ok: true, engineInfo });
      return;
    }

    if (type === 'solve') {
      const { binsCsv, itemsCsv, budgetS = 4.0, seed = 501, orderName = 'custom_order', existingBaseline = null } = payload;

      // 1. If no archived baseline passed, compute fast classical FFDH baseline inside Wasm (mode = 2)
      let baselinePlan = existingBaseline;
      if (!baselinePlan) {
        const baseRaw = mod.ccall(
          'wasm_solve_order',
          'string',
          ['string', 'string', 'number', 'number', 'number', 'string'],
          [binsCsv, itemsCsv, 2, 0.5, seed, orderName]
        );
        const baseJson = JSON.parse(baseRaw);
        if (!baseJson.ok) {
          self.postMessage({ id, ok: false, error: baseJson.error || 'Baseline solve failed', error_code: baseJson.error_code });
          return;
        }
        baselinePlan = parseCutTreeForWeb(binsCsv, itemsCsv, baseJson.cut_tree_csv);
        baselinePlan.solver_label = 'Classical FFDH Shelf Baseline (Wasm)';
      }

      // 2. Run Pure C++23 Mill Router inside Wasm (mode = 0)
      const optRaw = mod.ccall(
        'wasm_solve_order',
        'string',
        ['string', 'string', 'number', 'number', 'number', 'string'],
        [binsCsv, itemsCsv, 0, budgetS, seed, orderName]
      );
      const optJson = JSON.parse(optRaw);
      if (!optJson.ok) {
        self.postMessage({ id, ok: false, error: optJson.error || 'Solve failed', error_code: optJson.error_code });
        return;
      }

      const optPlan = parseCutTreeForWeb(binsCsv, itemsCsv, optJson.cut_tree_csv);
      optPlan.solver_label = `Mill C++23 Wasm (${optJson.winning_engine})`;
      optPlan.wall_ms = optJson.wall_ms;
      optPlan.branch = optJson.branch;
      optPlan.winning_engine = optJson.winning_engine;
      optPlan.bnb_nodes = optJson.bnb_nodes;
      optPlan.lower_bound = optJson.lower_bound;
      optPlan.proven_optimal = optJson.proven_optimal;
      optPlan.features = optJson.features;
      optPlan.verification = optJson.verification;
      optPlan.cutting_plan_md = buildShopFloorMd(orderName, optPlan, baselinePlan.sheets_used);

      self.postMessage({
        id,
        ok: true,
        baseline: baselinePlan,
        optimized: optPlan,
      });
      return;
    }

    if (type === 'solve_pareto') {
      const {
        binsCsv,
        itemsCsv,
        sheetCost = 2.0,
        machineRateHr = 180.0,
        setupMin = 6.0,
        strokeSec = 9.0,
        maxStack = 200,
        budgetS = 3.5,
        seed = 501,
        orderName = 'batch_order',
        existingBaseline = null,
      } = payload;

      let baselinePlan = existingBaseline;
      if (!baselinePlan) {
        const baseRaw = mod.ccall(
          'wasm_solve_order',
          'string',
          ['string', 'string', 'number', 'number', 'number', 'string'],
          [binsCsv, itemsCsv, 2, 0.5, seed, orderName]
        );
        const baseJson = JSON.parse(baseRaw);
        if (baseJson.ok) {
          baselinePlan = parseCutTreeForWeb(binsCsv, itemsCsv, baseJson.cut_tree_csv);
        }
      }

      const parRaw = mod.ccall(
        'wasm_solve_pareto',
        'string',
        ['string', 'string', 'number', 'number', 'number', 'number', 'number', 'number', 'number', 'string'],
        [binsCsv, itemsCsv, sheetCost, machineRateHr, setupMin, strokeSec, maxStack, budgetS, seed, orderName]
      );
      const parJson = JSON.parse(parRaw);
      if (!parJson.ok) {
        self.postMessage({ id, ok: false, error: parJson.error || 'Pareto solve failed', error_code: parJson.error_code });
        return;
      }

      const frontierPlans = parJson.frontier.map((fp) => {
        const vis = parseCutTreeForWeb(binsCsv, itemsCsv, fp.cut_tree_csv);
        vis.plan_id = fp.plan_id;
        vis.distinct_patterns = fp.distinct_patterns;
        vis.clamp_stacks = fp.clamp_stacks;
        vis.total_strokes = fp.total_strokes;
        vis.setup_time_min = fp.setup_time_min;
        vis.cut_time_min = fp.cut_time_min;
        vis.total_time_min = fp.total_time_min;
        vis.paper_cost_rmb = fp.paper_cost_rmb;
        vis.setup_cost_rmb = fp.setup_cost_rmb;
        vis.cut_cost_rmb = fp.cut_cost_rmb;
        vis.total_cost_rmb = fp.total_cost_rmb;
        vis.cutting_plan_md = buildShopFloorMd(orderName, vis, baselinePlan ? baselinePlan.sheets_used : vis.sheets_used);
        return vis;
      });

      self.postMessage({
        id,
        ok: true,
        wall_ms: parJson.wall_ms,
        candidate_patterns: parJson.candidate_patterns,
        recommended: parJson.recommended,
        cost_model: parJson.cost_model,
        baseline: baselinePlan,
        frontier: frontierPlans,
      });
      return;
    }

    if (type === 'verify') {
      const { binsCsv, itemsCsv, cutTreeCsv, orderName = 'verified_order' } = payload;
      const verRaw = mod.ccall(
        'wasm_verify_certificate',
        'string',
        ['string', 'string', 'string', 'string'],
        [binsCsv, itemsCsv, cutTreeCsv, orderName]
      );
      const verJson = JSON.parse(verRaw);
      let visualPlan = null;
      if (verJson.valid) {
        visualPlan = parseCutTreeForWeb(binsCsv, itemsCsv, cutTreeCsv);
      }
      self.postMessage({ id, ok: true, report: verJson, visualPlan });
      return;
    }
  } catch (err) {
    self.postMessage({ id, ok: false, error: String(err && err.message ? err.message : err) });
  }
};
