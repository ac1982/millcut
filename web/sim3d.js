// Three.js 3D Industrial Paper Mill Guillotine Digital Twin
// Visualizes physical 3-Stage NonExact Horizontal Guillotine Cutting:
//   Step 0: Intact Parent Paper Sheet (W × H) on steel cutting table
//   Stage 1 (Red Laser + Gantry): Horizontal through-cuts -> horizontal strips physically slide apart!
//   Stage 2 (Blue Laser + Gantry): Vertical column cuts inside strips -> columns separate along X!
//   Stage 3 (Green Laser + Gantry): Final stack/trim cuts -> finished paper stacks highlight, waste drops!

import * as THREE from './vendor/three.module.min.js';
import { OrbitControls } from './vendor/OrbitControls.js';
import { itemColor } from './canvas2d.js';

export class Guillotine3DSimulator {
  constructor({ container, hoverBadgeEl, onStepChange }) {
    this.container = container;
    this.hoverBadgeEl = hoverBadgeEl;
    this.onStepChange = onStepChange;

    this.theme = 'dark';
    this.lang = 'zh';
    this.plate = null;
    this.cuts = [];
    this.currentStep = 0; // 0 = uncut intact sheet; 1..cuts.length = after cut k
    this.playing = false;
    this.speed = 1.0;
    this.stepTimer = 0;
    this.bladeAnim = 0; // 0..1 blade strike animation when advancing a step

    this.pieceActors = [];
    this.wasteActors = [];
    this.pickables = [];

    this._initScene();
    this._bindEvents();
    this._animate = this._animate.bind(this);
    requestAnimationFrame(this._animate);
  }

  _initScene() {
    const rect = this.container.getBoundingClientRect();
    const w = Math.max(320, rect.width || 800);
    const h = Math.max(420, rect.height || 500);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x070c17);
    this.scene.fog = new THREE.FogExp2(0x070c17, 0.012);

    this.camera = new THREE.PerspectiveCamera(40, w / h, 0.1, 200);
    this.camera.position.set(0, 12.5, 13.5);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setSize(w, h);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.container.innerHTML = '';
    this.container.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.07;
    this.controls.maxPolarAngle = Math.PI * 0.47;
    this.controls.minDistance = 5;
    this.controls.maxDistance = 34;
    this.controls.target.set(0, 0.2, 0);

    // Studio + Industrial Shop Lighting
    this.ambientLight = new THREE.AmbientLight(0xdbeafe, 0.85);
    this.scene.add(this.ambientLight);

    const hemi = new THREE.HemisphereLight(0xe0f2fe, 0x0f172a, 0.65);
    this.scene.add(hemi);

    this.dirLight = new THREE.DirectionalLight(0xffffff, 1.35);
    this.dirLight.position.set(8, 18, 10);
    this.dirLight.castShadow = true;
    this.dirLight.shadow.mapSize.set(2048, 2048);
    this.dirLight.shadow.camera.near = 2;
    this.dirLight.shadow.camera.far = 42;
    const d = 11;
    this.dirLight.shadow.camera.left = -d;
    this.dirLight.shadow.camera.right = d;
    this.dirLight.shadow.camera.top = d;
    this.dirLight.shadow.camera.bottom = -d;
    this.scene.add(this.dirLight);

    const rimLight = new THREE.DirectionalLight(0x38bdf8, 0.45);
    rimLight.position.set(-10, 9, -8);
    this.scene.add(rimLight);

    // Industrial Machine Bed & Gantry Group
    this.machineGroup = new THREE.Group();
    this.scene.add(this.machineGroup);

    const bedGeo = new THREE.BoxGeometry(17.5, 0.5, 16.5);
    this.bedMat = new THREE.MeshStandardMaterial({
      color: 0x0f172a,
      roughness: 0.55,
      metalness: 0.45,
    });
    const bedMesh = new THREE.Mesh(bedGeo, this.bedMat);
    bedMesh.position.y = -0.26;
    bedMesh.receiveShadow = true;
    this.machineGroup.add(bedMesh);

    this.gridHelper = new THREE.GridHelper(16, 32, 0x334155, 0x1e293b);
    this.gridHelper.position.y = 0.002;
    this.machineGroup.add(this.gridHelper);

    // Side precision guide rails
    const railGeo = new THREE.BoxGeometry(0.18, 0.22, 15.6);
    const railMat = new THREE.MeshStandardMaterial({ color: 0x475569, metalness: 0.8, roughness: 0.25 });
    const leftRail = new THREE.Mesh(railGeo, railMat);
    leftRail.position.set(-8.1, 0.11, 0);
    const rightRail = new THREE.Mesh(railGeo, railMat);
    rightRail.position.set(8.1, 0.11, 0);
    this.machineGroup.add(leftRail, rightRail);

    // Moving CNC Guillotine Bridge Gantry + Descending Steel Blade + Laser Line
    this.gantryGroup = new THREE.Group();
    this.scene.add(this.gantryGroup);

    const bridgeMat = new THREE.MeshStandardMaterial({ color: 0x334155, metalness: 0.78, roughness: 0.24 });
    const pillarGeo = new THREE.BoxGeometry(0.28, 2.2, 0.36);
    const pL = new THREE.Mesh(pillarGeo, bridgeMat);
    pL.position.set(-6.4, 1.1, 0);
    const pR = new THREE.Mesh(pillarGeo, bridgeMat);
    pR.position.set(6.4, 1.1, 0);
    const beamGeo = new THREE.BoxGeometry(13.2, 0.32, 0.38);
    const beam = new THREE.Mesh(beamGeo, bridgeMat);
    beam.position.set(0, 2.1, 0);
    this.gantryGroup.add(pL, pR, beam);

    // Beveled Steel Guillotine Blade
    const bladeGeo = new THREE.BoxGeometry(12.2, 0.55, 0.045);
    this.bladeMat = new THREE.MeshStandardMaterial({
      color: 0xe2e8f0,
      metalness: 0.92,
      roughness: 0.12,
      emissive: 0xef4444,
      emissiveIntensity: 0.18,
    });
    this.bladeMesh = new THREE.Mesh(bladeGeo, this.bladeMat);
    this.bladeMesh.position.set(0, 1.55, 0);
    this.gantryGroup.add(this.bladeMesh);

    // Glowing Laser Alignment Beam on Paper Surface
    const laserGeo = new THREE.BoxGeometry(12.2, 0.035, 0.055);
    this.laserMat = new THREE.MeshBasicMaterial({ color: 0xef4444, transparent: true, opacity: 0.92 });
    this.laserMesh = new THREE.Mesh(laserGeo, this.laserMat);
    this.laserMesh.position.set(0, 0.38, 0);
    this.gantryGroup.add(this.laserMesh);

    this.gantryGroup.visible = false;

    // Group holding paper parts & offcuts
    this.paperGroup = new THREE.Group();
    this.scene.add(this.paperGroup);

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2(-99, -99);
    this.lastTime = performance.now();
  }

  _bindEvents() {
    this.resizeObserver = new ResizeObserver(() => {
      const rect = this.container.getBoundingClientRect();
      if (rect.width > 40 && rect.height > 40) {
        this.camera.aspect = rect.width / rect.height;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(rect.width, rect.height);
        this.needsRender = true;
      }
    });
    this.resizeObserver.observe(this.container);

    this.inViewport = true;
    this.needsRender = true;
    if (typeof IntersectionObserver !== 'undefined') {
      this.intersectionObserver = new IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            this.inViewport = entry.isIntersecting;
            if (this.inViewport) this.needsRender = true;
          }
        },
        { threshold: 0.02 }
      );
      this.intersectionObserver.observe(this.container);
    }

    this.renderer.domElement.addEventListener('mousemove', (e) => {
      const rect = this.renderer.domElement.getBoundingClientRect();
      this.pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      this.pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      this.pointerClient = { x: e.clientX, y: e.clientY };
      this._updateHover();
    });

    this.renderer.domElement.addEventListener('mouseleave', () => {
      this.pointer.set(-99, -99);
      if (this.hoverBadgeEl) this.hoverBadgeEl.style.display = 'none';
    });
  }

  setTheme(theme, lang) {
    this.theme = theme;
    if (lang) this.lang = lang;
    const isLight = theme === 'light';
    const bgHex = isLight ? 0xeef2f7 : 0x070c17;
    this.scene.background = new THREE.Color(bgHex);
    this.scene.fog = new THREE.FogExp2(bgHex, 0.011);
    this.bedMat.color.setHex(isLight ? 0xcbd5e1 : 0x0f172a);
    if (this.plate) {
      this.loadPlate(this.plate, this.currentStep);
    }
  }

  setCameraPreset(preset) {
    if (preset === 'top') {
      this.camera.position.set(0, 17.5, 0.01);
      this.controls.target.set(0, 0, 0);
    } else if (preset === 'blade') {
      this.camera.position.set(6.8, 5.2, 7.8);
      this.controls.target.set(0, 0.4, 0);
    } else {
      this.camera.position.set(0, 12.2, 13.2);
      this.controls.target.set(0, 0.1, 0);
    }
    this.controls.update();
  }

  loadPlate(plate, initialStep = 0) {
    this.plate = plate;
    this.cuts = plate ? (plate.cuts || []) : [];
    this.currentStep = Math.max(0, Math.min(initialStep, this.cuts.length));

    // Clear previous meshes cleanly
    while (this.paperGroup.children.length > 0) {
      const child = this.paperGroup.children[0];
      this.paperGroup.remove(child);
    }
    this.pieceActors = [];
    this.wasteActors = [];
    this.pickables = [];

    if (!plate) return;

    const W = plate.sheet_width;
    const H = plate.sheet_height;
    const maxDim = Math.max(W, H, 1);
    this.worldScale = 10.2 / maxDim; // Maps max sheet dimension -> ~10.2 Three.js units
    this.sheetWorldW = W * this.worldScale;
    this.sheetWorldH = H * this.worldScale;

    // Paper stack height reflects copies (visualizing stack cutting of multiple sheets at once!)
    const stackH = 0.24 + Math.min(4, (plate.copies || 1) - 1) * 0.08;
    this.stackH = stackH;

    // Helper mapping (x, y, w, h) in CAD bottom-left coords to 3D center (wx, wz)
    // Note: CAD X -> +World X; CAD Y (0 at bottom, H at top) -> -World Z so Y=0 is front (+Z) and Y=H is back (-Z), matching 2D blueprint!
    const toWorld = (x, y, w, h) => {
      const ww = w * this.worldScale;
      const wz = h * this.worldScale;
      const cx = (x + w * 0.5 - W * 0.5) * this.worldScale;
      const cz = (H * 0.5 - (y + h * 0.5)) * this.worldScale;
      return { cx, cz, ww, wz };
    };
    this._toWorld = toWorld;

    // Precompute which strip & column each placed item belongs to, and which cut step triggers its separation!
    const strips = plate.strips && plate.strips.length > 0 ? plate.strips : [];
    const totalStrips = Math.max(1, strips.length);

    // Map each cut to an index 1..cuts.length
    const findCutStep = (pred) => {
      const idx = this.cuts.findIndex(pred);
      return idx >= 0 ? idx + 1 : 1;
    };

    const sideCreamMat = new THREE.MeshStandardMaterial({
      color: 0xfef3c7,
      roughness: 0.78,
      metalness: 0.02,
    });

    // Build Placed Item 3D Blocks
    if (strips.length > 0) {
      strips.forEach((sp, sIdx) => {
        const s1Step = findCutStep(c => c.raw_depth === 1 && c.coord >= sp.y + sp.h - 1);
        const totalCols = Math.max(1, sp.columns.length);
        sp.columns.forEach((col, cIdx) => {
          const s2Step = findCutStep(c =>
            c.raw_depth === 2 &&
            Math.abs(c.parent_y - sp.y) <= 2 &&
            c.coord >= col.x + col.w - 1
          );
          col.slices.forEach((sl, lIdx) => {
            const s3Step = (col.slices.length > 1 || sl.h < sp.h || sl.w < col.w)
              ? findCutStep(c => c.raw_depth >= 3 && c.parent_x <= sl.x + 1 && c.parent_x + c.parent_w >= sl.x + sl.w - 1)
              : 999999;

            const { cx, cz, ww, wz } = toWorld(sl.x, sl.y, sl.w, sl.h);
            const geo = new THREE.BoxGeometry(Math.max(0.02, ww - 0.008), stackH, Math.max(0.02, wz - 0.008));
            const topColor = new THREE.Color(itemColor(sl.item_id, this.theme === 'light' ? 'light' : 'dark', 1));
            const topMat = new THREE.MeshStandardMaterial({
              color: topColor,
              roughness: 0.36,
              metalness: 0.08,
            });
            // 6-face material: sides look like stacked cream paper edges, top face is coated label color!
            const mats = [sideCreamMat, sideCreamMat, topMat, sideCreamMat, sideCreamMat, sideCreamMat];
            const mesh = new THREE.Mesh(geo, mats);
            mesh.castShadow = true;
            mesh.receiveShadow = true;
            mesh.position.set(cx, stackH * 0.5, cz);

            const edges = new THREE.LineSegments(
              new THREE.EdgesGeometry(geo),
              new THREE.LineBasicMaterial({ color: 0x0f172a, transparent: true, opacity: 0.65 })
            );
            mesh.add(edges);

            mesh.userData = { item: sl, sheetW: W, sheetH: H };
            this.paperGroup.add(mesh);
            this.pickables.push(mesh);

            // Physical separation target offsets once Stage 1, Stage 2, Stage 3 cuts happen!
            const stripSepZ = (totalStrips > 1 ? ((totalStrips - 1) * 0.5 - sIdx) : 0) * 0.36;
            const colSepX = (totalCols > 1 ? (cIdx - (totalCols - 1) * 0.5) : 0) * 0.24;
            const sliceSepZ = (col.slices.length > 1 ? ((col.slices.length - 1) * 0.5 - lIdx) : 0) * 0.12;

            this.pieceActors.push({
              mesh,
              baseX: cx,
              baseY: stackH * 0.5,
              baseZ: cz,
              curX: cx,
              curY: stackH * 0.5,
              curZ: cz,
              s1Step,
              s2Step,
              s3Step,
              stripSepZ,
              colSepX,
              sliceSepZ,
            });
          });
        });
      });
    } else {
      // Fallback if flat items only
      for (const sl of (plate.items || [])) {
        const { cx, cz, ww, wz } = toWorld(sl.x, sl.y, sl.w, sl.h);
        const geo = new THREE.BoxGeometry(Math.max(0.02, ww - 0.008), stackH, Math.max(0.02, wz - 0.008));
        const topMat = new THREE.MeshStandardMaterial({
          color: new THREE.Color(itemColor(sl.item_id, 'dark', 1)),
          roughness: 0.38,
        });
        const mesh = new THREE.Mesh(geo, [sideCreamMat, sideCreamMat, topMat, sideCreamMat, sideCreamMat, sideCreamMat]);
        mesh.position.set(cx, stackH * 0.5, cz);
        mesh.userData = { item: sl, sheetW: W, sheetH: H };
        this.paperGroup.add(mesh);
        this.pickables.push(mesh);
        this.pieceActors.push({
          mesh, baseX: cx, baseY: stackH * 0.5, baseZ: cz,
          curX: cx, curY: stackH * 0.5, curZ: cz,
          s1Step: 1, s2Step: 2, s3Step: 3, stripSepZ: 0, colSepX: 0, sliceSepZ: 0,
        });
      }
    }

    // Build Waste Offcut 3D Blocks (grey Kraft offcuts or green reusable remnant!)
    for (const wRect of (plate.wastes || [])) {
      const { cx, cz, ww, wz } = toWorld(wRect.x, wRect.y, wRect.w, wRect.h);
      if (ww < 0.015 || wz < 0.015) continue;
      const geo = new THREE.BoxGeometry(Math.max(0.015, ww - 0.008), stackH * 0.96, Math.max(0.015, wz - 0.008));
      const mat = new THREE.MeshStandardMaterial({
        color: wRect.reusable ? 0x10b981 : 0x475569,
        roughness: 0.75,
        metalness: 0.05,
        transparent: true,
        opacity: 0.88,
      });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.castShadow = true;
      mesh.position.set(cx, stackH * 0.48, cz);

      const triggerStep = findCutStep(c => {
        if (wRect.cut === 1) return c.raw_depth === 1 && Math.abs(c.coord - wRect.y) <= 2;
        if (wRect.cut === 2) return c.raw_depth === 2 && Math.abs(c.parent_y - wRect.y) <= 2 && Math.abs(c.coord - wRect.x) <= 2;
        return c.raw_depth >= 3;
      });

      this.paperGroup.add(mesh);
      this.wasteActors.push({
        mesh,
        mat,
        reusable: !!wRect.reusable,
        baseX: cx,
        baseY: stackH * 0.48,
        baseZ: cz,
        curX: cx,
        curY: stackH * 0.48,
        curZ: cz,
        curOpacity: 0.88,
        triggerStep: triggerStep || 1,
      });
    }

    // Faint Parent Sheet Footprint Outline on Bed
    const borderPts = [
      new THREE.Vector3(-this.sheetWorldW * 0.5, 0.015, -this.sheetWorldH * 0.5),
      new THREE.Vector3(this.sheetWorldW * 0.5, 0.015, -this.sheetWorldH * 0.5),
      new THREE.Vector3(this.sheetWorldW * 0.5, 0.015, this.sheetWorldH * 0.5),
      new THREE.Vector3(-this.sheetWorldW * 0.5, 0.015, this.sheetWorldH * 0.5),
      new THREE.Vector3(-this.sheetWorldW * 0.5, 0.015, -this.sheetWorldH * 0.5),
    ];
    const borderLine = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(borderPts),
      new THREE.LineBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.45 })
    );
    this.paperGroup.add(borderLine);

    this.setStep(this.currentStep, false);
  }

  setStep(step, animateBlade = true) {
    if (!this.plate) return;
    this.currentStep = Math.max(0, Math.min(step, this.cuts.length));
    if (animateBlade && this.currentStep > 0) {
      this.bladeAnim = 1.0;
    }
    this.needsRender = true;
    this._syncGantryToCurrentCut();
    if (this.onStepChange) {
      this.onStepChange(this.currentStep, this.cuts.length, this.cuts[this.currentStep - 1] || null);
    }
  }

  play() {
    if (!this.plate) return;
    if (this.currentStep >= this.cuts.length) {
      this.setStep(0, false);
    }
    this.playing = true;
    this.stepTimer = 0;
  }

  pause() {
    this.playing = false;
  }

  _syncGantryToCurrentCut() {
    if (!this.plate || this.currentStep <= 0 || this.currentStep > this.cuts.length) {
      this.gantryGroup.visible = false;
      return;
    }
    const cut = this.cuts[this.currentStep - 1];
    this.gantryGroup.visible = true;

    const W = this.plate.sheet_width;
    const H = this.plate.sheet_height;

    const stageColor = cut.raw_depth === 1 ? 0xef4444 : (cut.raw_depth === 2 ? 0x38bdf8 : 0x10b981);
    this.laserMat.color.setHex(stageColor);
    this.bladeMat.emissive.setHex(stageColor);

    if (cut.orientation === 'H') {
      const wz = (H * 0.5 - cut.coord) * this.worldScale;
      const cx = ((cut.x1 + cut.x2) * 0.5 - W * 0.5) * this.worldScale;
      const span = Math.max(0.6, (cut.x2 - cut.x1) * this.worldScale);
      this.gantryGroup.rotation.y = 0;
      this.gantryGroup.position.set(0, 0, wz);
      this.laserMesh.scale.set(span / 12.2, 1, 1);
      this.laserMesh.position.set(cx, this.stackH + 0.03, 0);
      this.bladeMesh.scale.set(Math.min(1.0, (span + 0.4) / 12.2), 1, 1);
      this.bladeMesh.position.x = cx;
    } else {
      const wx = (cut.coord - W * 0.5) * this.worldScale;
      const cz = (H * 0.5 - (cut.y1 + cut.y2) * 0.5) * this.worldScale;
      const span = Math.max(0.5, (cut.y2 - cut.y1) * this.worldScale);
      this.gantryGroup.rotation.y = Math.PI * 0.5;
      this.gantryGroup.position.set(wx, 0, 0);
      this.laserMesh.scale.set(span / 12.2, 1, 1);
      // In rotated +90deg Y frame, local X maps to -World Z -> local X = -cz
      this.laserMesh.position.set(-cz, this.stackH + 0.03, 0);
      this.bladeMesh.scale.set(Math.min(1.0, (span + 0.35) / 12.2), 1, 1);
      this.bladeMesh.position.x = -cz;
    }
  }

  _updateHover() {
    if (!this.hoverBadgeEl || !this.plate || this.pickables.length === 0) return;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const hits = this.raycaster.intersectObjects(this.pickables, false);
    if (hits.length > 0 && this.pointerClient) {
      const { item, sheetW, sheetH } = hits[0].object.userData;
      const pct = ((item.w * item.h * 100) / (sheetW * sheetH)).toFixed(2);
      this.hoverBadgeEl.style.display = 'block';
      this.hoverBadgeEl.style.left = `${this.pointerClient.x + 14}px`;
      this.hoverBadgeEl.style.top = `${this.pointerClient.y + 14}px`;
      this.hoverBadgeEl.innerHTML = this.lang === 'zh'
        ? `<b>3D 零件 #${item.item_id}</b> · ${item.w}×${item.h} · 坐标(${item.x}, ${item.y}) · 面积 ${pct}%`
        : `<b>3D Part #${item.item_id}</b> · ${item.w}×${item.h} · (${item.x}, ${item.y}) · ${pct}%`;
    } else {
      this.hoverBadgeEl.style.display = 'none';
    }
  }

  _animate(now) {
    requestAnimationFrame(this._animate);
    const dt = Math.min(0.08, (now - this.lastTime) / 1000);
    this.lastTime = now;
    if (!this.inViewport && !this.needsRender) {
      return;
    }
    this.needsRender = false;

    if (this.playing && this.plate) {
      this.stepTimer += dt * this.speed;
      if (this.stepTimer >= 0.85) {
        this.stepTimer = 0;
        if (this.currentStep < this.cuts.length) {
          this.setStep(this.currentStep + 1, true);
        } else {
          this.playing = false;
          if (this.onStepChange) {
            this.onStepChange(this.currentStep, this.cuts.length, null, true);
          }
        }
      }
    }

    // Animate blade strike chop (down to bed -> retract up smoothly)
    if (this.bladeAnim > 0) {
      this.bladeAnim = Math.max(0, this.bladeAnim - dt * 3.4);
      const chop = Math.sin(this.bladeAnim * Math.PI);
      this.bladeMesh.position.y = 1.45 - chop * (1.45 - this.stackH * 0.55);
      this.bladeMat.emissiveIntensity = 0.18 + chop * 0.85;
    } else if (this.bladeMesh) {
      this.bladeMesh.position.y = 1.45;
    }

    // Smooth spring interpolation for paper strips, columns, slices & dropping waste!
    const lerpK = Math.min(1, dt * 8.5);
    const step = this.currentStep;

    for (const a of this.pieceActors) {
      let tx = a.baseX;
      let tz = a.baseZ;
      if (step >= a.s1Step) tz += a.stripSepZ;
      if (step >= a.s2Step) tx += a.colSepX;
      if (step >= a.s3Step) tz += a.sliceSepZ;

      a.curX += (tx - a.curX) * lerpK;
      a.curZ += (tz - a.curZ) * lerpK;
      a.mesh.position.set(a.curX, a.baseY, a.curZ);
    }

    for (const w of this.wasteActors) {
      const cutDone = step >= w.triggerStep;
      let tx = w.baseX;
      let ty = w.baseY;
      let tz = w.baseZ;
      let tOp = 0.88;
      if (cutDone) {
        if (w.reusable) {
          // Reusable remnant slides neatly to the back reserve rack & stays visible!
          tz -= 0.75;
          tOp = 0.92;
        } else {
          // Scrap offcut drops slightly & fades out so finished parts shine clearly!
          ty = -0.14;
          tx += (w.baseX >= 0 ? 0.55 : -0.55);
          tz += (w.baseZ >= 0 ? 0.35 : -0.55);
          tOp = 0.12;
        }
      }
      w.curX += (tx - w.curX) * lerpK;
      w.curY += (ty - w.curY) * lerpK;
      w.curZ += (tz - w.curZ) * lerpK;
      w.curOpacity += (tOp - w.curOpacity) * lerpK;
      w.mesh.position.set(w.curX, w.curY, w.curZ);
      w.mat.opacity = w.curOpacity;
      w.mesh.visible = w.curOpacity > 0.04;
    }

    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
