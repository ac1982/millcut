// Shared 2D drawing helpers: one calm palette, sheets & stacked patterns drawn cleanly everywhere.

const FONT = '"Inter", "PingFang SC", "Noto Sans SC", "Microsoft YaHei", system-ui, sans-serif';
const HUES = [205, 192, 218, 176, 230, 199, 184, 212, 164, 240];

// Same part id -> same colour on every view (2D and 3D), so viewers see "the same parts".
export function itemColor(id, _theme = 'light', alpha = 1) {
  const n = Math.abs(id | 0);
  const h = HUES[n % HUES.length];
  const l = 48 + ((n * 37) % 5) * 5;
  return alpha >= 1 ? `hsl(${h}, 66%, ${l}%)` : `hsla(${h}, 66%, ${l}%, ${alpha})`;
}

function setup(canvas, cssHeight) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
  const w = Math.max(240, canvas.clientWidth || canvas.parentElement?.clientWidth || 480);
  canvas.style.height = `${cssHeight}px`;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(cssHeight * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, cssHeight);
  return { ctx, w, h: cssHeight };
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/**
 * Draw a single-copy plan as a single row of sheets (used for 3 -> 2 sheet comparison).
 */
export function drawSheets(canvas, plan, opts = {}) {
  if (!canvas || !plan) return;
  const slots = Math.max(opts.slots || plan.plates.length, plan.plates.length);
  const W = plan.sheet_width;
  const H = plan.sheet_height;
  const gap = 14;
  const labelH = opts.noLabels ? 4 : 30;
  const width = Math.max(240, canvas.clientWidth || canvas.parentElement?.clientWidth || 480);

  let sw = (width - gap * (slots - 1)) / slots;
  let sh = sw * (H / W);
  const maxH = opts.maxHeight || 340;
  if (sh > maxH) {
    sh = maxH;
    sw = sh * (W / H);
  }
  const { ctx } = setup(canvas, Math.ceil(sh + labelH + 2));
  const totalW = sw * slots + gap * (slots - 1);
  const x0 = (width - totalW) / 2;
  const scale = sw / W;

  const order = [];
  plan.plates.forEach((pl, pi) => {
    [...pl.items]
      .sort((a, b) => a.y - b.y || a.x - b.x)
      .forEach((it) => order.push({ pi, it }));
  });
  const shown = (opts.progress ?? 1) * order.length;

  for (let i = 0; i < slots; i++) {
    const ox = x0 + i * (sw + gap);
    const oy = 1;
    if (i < plan.plates.length) {
      roundRect(ctx, ox, oy, sw, sh, 3);
      ctx.fillStyle = '#eef1f5';
      ctx.fill();
    } else {
      ctx.save();
      roundRect(ctx, ox + 0.5, oy + 0.5, sw - 1, sh - 1, 3);
      ctx.setLineDash([6, 5]);
      ctx.strokeStyle = '#86d6b2';
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.restore();
      if (opts.ghostLabel) {
        ctx.fillStyle = '#0f9f6e';
        ctx.font = `600 ${Math.max(12, Math.min(17, sw / 9))}px ${FONT}`;
        ctx.textAlign = 'center';
        ctx.fillText(opts.ghostLabel, ox + sw / 2, oy + sh / 2 + 5);
      }
    }
  }

  order.forEach(({ pi, it }, idx) => {
    const t = Math.max(0, Math.min(1, shown - idx));
    if (t <= 0) return;
    const ox = x0 + pi * (sw + gap);
    const x = ox + it.x * scale;
    const y = 1 + (H - it.y - it.h) * scale;
    const w = it.w * scale;
    const h = it.h * scale;
    const drop = (1 - t) * 10;
    ctx.globalAlpha = t;
    ctx.fillStyle = itemColor(it.item_id);
    ctx.fillRect(x + 0.6, y + 0.6 - drop, Math.max(0, w - 1.2), Math.max(0, h - 1.2));
    ctx.globalAlpha = 1;
  });

  if (!opts.noLabels) {
    ctx.textAlign = 'center';
    plan.plates.forEach((pl, i) => {
      const cx = x0 + i * (sw + gap) + sw / 2;
      const isLast = i === plan.plates.length - 1;
      if (opts.weakLast && isLast) {
        ctx.fillStyle = '#d64545';
        ctx.font = `600 13px ${FONT}`;
        ctx.fillText(opts.weakLast, cx, sh + 22);
      } else {
        ctx.fillStyle = '#7b8494';
        ctx.font = `500 12.5px ${FONT}`;
        const cp = pl.copies || 1;
        const txt = cp > 1
          ? `版 ${i + 1} · ×${cp}张`
          : `第 ${i + 1} 张 · ${Math.round(pl.utilization_pct)}%`;
        ctx.fillText(txt, cx, sh + 22);
      }
    });
  }
}

/**
 * Draw a batch production cutting plan as clean stacked cutting patterns (版式).
 * Each distinct pattern shows physical stacked-paper shadow layers proportional to log(copies)
 * and a clear `版 k · 叠切 N 张` badge so shop-floor setup differences jump out visually.
 */
export function drawPatternStacks(canvas, plan, opts = {}) {
  if (!canvas || !plan || !plan.plates) return;
  const plates = [...plan.plates].sort((a, b) => (b.copies || 1) - (a.copies || 1));
  const K = Math.max(1, plates.length);
  const W = plan.sheet_width;
  const H = plan.sheet_height;
  const width = Math.max(260, canvas.clientWidth || canvas.parentElement?.clientWidth || 480);

  // Pick a clean grid column count based on pattern count K
  let cols = K;
  if (K > 10) cols = 6;
  else if (K > 6) cols = 5;
  else if (K > 4) cols = 4;
  const rows = Math.ceil(K / cols);

  const gapX = K > 6 ? 12 : 18;
  const labelH = 34;
  const gapY = 14 + labelH;
  const stackPad = 9; // room for 3D paper-pile shadow layers

  let sw = (width - gapX * (cols - 1)) / cols - stackPad;
  let sh = sw * (H / W);

  const targetMaxTotalH = opts.maxTotalHeight || 310;
  const maxPerRowH = (targetMaxTotalH - rows * labelH - (rows - 1) * 14) / rows - stackPad;
  if (sh > maxPerRowH && maxPerRowH > 46) {
    sh = maxPerRowH;
    sw = sh * (W / H);
  }

  const cellW = sw + stackPad;
  const cellH = sh + stackPad;
  const totalGridW = cols * cellW + (cols - 1) * gapX;
  const xStart = Math.max(0, (width - totalGridW) / 2);
  const canvasH = Math.ceil(rows * (cellH + labelH) + Math.max(0, rows - 1) * 12 + 4);

  const { ctx } = setup(canvas, canvasH);
  const scale = sw / W;

  plates.forEach((pl, idx) => {
    const r = Math.floor(idx / cols);
    const c = idx % cols;
    const baseOx = xStart + c * (cellW + gapX);
    const baseOy = 2 + r * (cellH + labelH + 12);
    const copies = pl.copies || 1;

    // Draw stacked-paper layers behind top sheet (up to 4 visible layers for copies >= 30)
    const layers = copies <= 1 ? 0 : Math.min(4, Math.ceil(Math.log2(copies + 1) * 0.75));
    for (let l = layers; l >= 1; l--) {
      const dx = l * 2.1;
      const dy = l * 2.1;
      roundRect(ctx, baseOx + dx, baseOy + dy, sw, sh, 3);
      ctx.fillStyle = l % 2 === 0 ? '#dce3ec' : '#cbd5e1';
      ctx.fill();
      ctx.strokeStyle = '#b8c4d4';
      ctx.lineWidth = 0.7;
      ctx.stroke();
    }

    // Top sheet background
    roundRect(ctx, baseOx, baseOy, sw, sh, 3);
    ctx.fillStyle = '#eef2f6';
    ctx.fill();
    ctx.strokeStyle = '#cfd7e3';
    ctx.lineWidth = 0.9;
    ctx.stroke();

    // Placed parts on this pattern
    for (const it of pl.items) {
      const x = baseOx + it.x * scale;
      const y = baseOy + (H - it.y - it.h) * scale;
      const w = it.w * scale;
      const h = it.h * scale;
      ctx.fillStyle = itemColor(it.item_id);
      ctx.fillRect(x + 0.45, y + 0.45, Math.max(0, w - 0.9), Math.max(0, h - 0.9));
    }

    // Copy count pill in top-right corner if copies > 1
    if (copies > 1 && sw >= 48) {
      const badgeText = `×${copies}张`;
      ctx.font = `700 ${sw < 72 ? 10 : 11}px ${FONT}`;
      const tw = ctx.measureText(badgeText).width + 10;
      const bh = 18;
      const bx = baseOx + sw - tw - 4;
      const by = baseOy + 4;
      roundRect(ctx, bx, by, tw, bh, 9);
      ctx.fillStyle = copies >= 15 ? 'rgba(15, 159, 110, 0.92)' : 'rgba(17, 24, 39, 0.82)';
      ctx.fill();
      ctx.fillStyle = '#ffffff';
      ctx.textAlign = 'center';
      ctx.fillText(badgeText, bx + tw / 2, by + 12.5);
    }

    // Caption under each pattern stack
    const cx = baseOx + sw / 2 + (layers * 1.0);
    const ly = baseOy + cellH + 13;
    ctx.textAlign = 'center';
    ctx.fillStyle = '#374151';
    ctx.font = `600 ${cols >= 5 ? 11 : 12}px ${FONT}`;
    ctx.fillText(copies > 1 ? `版 ${idx + 1} · 切 ${copies} 张` : `版 ${idx + 1} · 1 张`, cx, ly);

    ctx.fillStyle = '#8a93a3';
    ctx.font = `400 ${cols >= 5 ? 10.5 : 11.5}px ${FONT}`;
    ctx.fillText(`利用率 ${Math.round(pl.utilization_pct)}%`, cx, ly + 14);
  });
}

/** Draw the input part list as proportional tiles. */
export function drawParts(canvas, items, W, H) {
  if (!canvas) return;
  const width = Math.max(240, canvas.clientWidth || canvas.parentElement?.clientWidth || 360);
  const height = Math.round(Math.min(width * 0.82, 260));
  const { ctx } = setup(canvas, height);

  const pieces = [];
  items.forEach((it) => {
    const cap = Math.min(it.copies || 1, 80);
    for (let c = 0; c < cap; c++) pieces.push(it);
  });
  pieces.sort((a, b) => b.h - a.h || b.w - a.w);

  const gap = 3;
  const fits = (s) => {
    let x = 0;
    let y = 0;
    let rowH = 0;
    for (const p of pieces) {
      const w = p.w * s;
      const h = p.h * s;
      if (x + w > width) {
        x = 0;
        y += rowH + gap;
        rowH = 0;
      }
      x += w + gap;
      rowH = Math.max(rowH, h);
    }
    return y + rowH <= height;
  };
  let lo = 0.0001;
  let hi = Math.min(width / W, height / H);
  for (let k = 0; k < 30; k++) {
    const mid = (lo + hi) / 2;
    if (fits(mid)) lo = mid;
    else hi = mid;
  }
  const s = lo;
  let x = 0;
  let y = 0;
  let rowH = 0;
  for (const p of pieces) {
    const w = p.w * s;
    const h = p.h * s;
    if (x + w > width) {
      x = 0;
      y += rowH + gap;
      rowH = 0;
    }
    ctx.fillStyle = itemColor(p.id);
    ctx.fillRect(x, y, w, h);
    x += w + gap;
    rowH = Math.max(rowH, h);
  }
}
