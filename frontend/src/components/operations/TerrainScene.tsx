/**
 * The terrain's WebGL scene (5.306.0) — three.js, loaded in its own chunk only
 * when the Operations terrain scrolls into view (AddressTerrainSection).
 *
 * One draw call for every tower band (an InstancedMesh with a colour per
 * instance), one for the beacons, one invisible box per block for picking.
 * Shading is baked into the geometry's vertex colours (top face full, sides
 * stepped darker) on unlit materials, so a band's top is EXACTLY the legend
 * colour in every theme — no light can shift it. Frames are drawn on demand:
 * nothing runs while the view is still.
 */
import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

import { TERRAIN_STAGES, type TerrainLayout } from '../../utils/addressTerrain';
import { readTerrainTokens, terrainPalette, type TerrainPalette } from '../../utils/terrainPalette';

export interface TerrainSceneHandle {
  zoomBy: (factor: number) => void;
  resetView: () => void;
}

export interface TerrainSceneProps {
  layout: TerrainLayout;
  selected: number | null;
  onHover: (index: number | null) => void;
  onSelect: (index: number | null) => void;
  onOpen: (index: number) => void;
  /** WebGL could not start: the section falls back to the table. */
  onUnavailable: () => void;
}

const MIN_HEIGHT = 0.14;
const FOOTPRINT = 0.74;
const BAND_GAP = 0.05;
const INTRO_MS = 900;

/** A unit box (0..1 in y) whose faces carry baked light: top 1, sides darker. */
function shadedBox(): THREE.BufferGeometry {
  const g = new THREE.BoxGeometry(1, 1, 1).toNonIndexed();
  g.translate(0, 0.5, 0);
  const normals = g.getAttribute('normal');
  const colours = new Float32Array(normals.count * 3);
  for (let i = 0; i < normals.count; i += 1) {
    const nx = normals.getX(i);
    const ny = normals.getY(i);
    const nz = normals.getZ(i);
    const shade = ny > 0.5 ? 1 : ny < -0.5 ? 0.45 : nx > 0.5 || nz > 0.5 ? 0.8 : 0.66;
    colours.set([shade, shade, shade], i * 3);
  }
  g.setAttribute('color', new THREE.BufferAttribute(colours, 3));
  return g;
}

function shadedOctahedron(): THREE.BufferGeometry {
  const g = new THREE.OctahedronGeometry(1, 0).toNonIndexed();
  const normals = g.getAttribute('normal');
  const colours = new Float32Array(normals.count * 3);
  for (let i = 0; i < normals.count; i += 1) {
    const shade = normals.getY(i) > 0 ? 1 : 0.7;
    colours.set([shade, shade, shade], i * 3);
  }
  g.setAttribute('color', new THREE.BufferAttribute(colours, 3));
  return g;
}

const easeOut = (t: number) => 1 - (1 - t) ** 3;

const TerrainScene = forwardRef<TerrainSceneHandle, TerrainSceneProps>(function TerrainScene(
  { layout, selected, onHover, onSelect, onOpen, onUnavailable },
  ref,
) {
  const hostRef = useRef<HTMLDivElement>(null);
  const labelsRef = useRef<HTMLDivElement>(null);
  // Callbacks change identity every render; the scene reads the latest.
  const cb = useRef({ onHover, onSelect, onOpen, onUnavailable });
  cb.current = { onHover, onSelect, onOpen, onUnavailable };
  const api = useRef<{ select: (i: number | null) => void; zoomBy: (f: number) => void; reset: () => void } | null>(null);

  useImperativeHandle(ref, () => ({
    zoomBy: (f) => api.current?.zoomBy(f),
    resetView: () => api.current?.reset(),
  }), []);

  useEffect(() => {
    const host = hostRef.current;
    const labelLayer = labelsRef.current;
    if (!host || !labelLayer) return undefined;

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' });
    } catch {
      cb.current.onUnavailable();
      return undefined;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    host.appendChild(renderer.domElement);
    renderer.domElement.style.display = 'block';

    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    const scene = new THREE.Scene();
    const span = Math.max(layout.width, layout.depth, 8);
    // The tallest tower is about a quarter of the map: readable on a sparse
    // project, not a skyline over a dense one.
    const MAX_HEIGHT = Math.min(9, Math.max(3, span * 0.26));
    // Aim at the towers' middle, not the ground, so the tallest (and its
    // beacon) stays in frame.
    const focus = new THREE.Vector3(0, MAX_HEIGHT * 0.38, 0);
    const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -500, 500);
    const home = new THREE.Vector3().setFromSphericalCoords(60, 0.95, Math.PI / 4).add(focus);
    camera.position.copy(home);
    camera.lookAt(focus);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.12;
    controls.enableZoom = false; // Ctrl/⌘ + wheel and the buttons: a plain wheel scrolls the page
    controls.minPolarAngle = 0.35;
    controls.maxPolarAngle = 1.3;
    controls.screenSpacePanning = true;
    controls.target.copy(focus);

    // --- ground: one plate and a plot grid per district ----------------------
    const groundMat = new THREE.MeshBasicMaterial();
    const gridMat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.55 });
    const districtGroup = new THREE.Group();
    layout.districts.forEach((d) => {
      const size = d.size;
      const plate = new THREE.PlaneGeometry(size, size).rotateX(-Math.PI / 2).translate(size / 2, 0, size / 2);
      const pts: number[] = [];
      for (let i = 0; i <= size; i += 1) pts.push(i, 0.002, 0, i, 0.002, size, 0, 0.002, i, size, 0.002, i);
      const grid = new THREE.BufferGeometry().setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
      const m = new THREE.Mesh(plate, groundMat);
      m.position.set(d.x, 0, d.z);
      const lines = new THREE.LineSegments(grid, gridMat);
      lines.position.set(d.x, 0, d.z);
      districtGroup.add(m, lines);
    });
    scene.add(districtGroup);

    // --- towers ---------------------------------------------------------------
    const n = layout.placed.length;
    const heights = layout.placed.map(({ block }) => (layout.maxHosts > 0
      ? Math.max(MIN_HEIGHT, (block.hosts / layout.maxHosts) * MAX_HEIGHT) : MIN_HEIGHT));
    const bands: Array<{ block: number; stage: number; y0: number; h: number }> = [];
    layout.placed.forEach(({ block }, i) => {
      let y = 0;
      const total = Math.max(1, block.hosts);
      TERRAIN_STAGES.forEach(({ key }, s) => {
        const count = block[key];
        if (count <= 0) return;
        const h = (count / total) * heights[i];
        const gap = h > BAND_GAP * 3 && y > 0 ? BAND_GAP : 0;
        bands.push({ block: i, stage: s, y0: y + gap, h: Math.max(0.01, h - gap) });
        y += h;
      });
    });
    const boxGeo = shadedBox();
    const bandMat = new THREE.MeshBasicMaterial({ vertexColors: true });
    const bandMesh = new THREE.InstancedMesh(boxGeo, bandMat, Math.max(1, bands.length));
    bandMesh.count = bands.length;
    bandMesh.frustumCulled = false;
    scene.add(bandMesh);

    const beaconIdx = layout.placed.map((_, i) => i).filter((i) => layout.placed[i].block.critical_untouched > 0);
    const beaconMat = new THREE.MeshBasicMaterial({ vertexColors: true });
    const beaconMesh = new THREE.InstancedMesh(shadedOctahedron(), beaconMat, Math.max(1, beaconIdx.length));
    beaconMesh.count = beaconIdx.length;
    beaconMesh.frustumCulled = false;
    scene.add(beaconMesh);
    const stemPts: number[] = [];
    const stemGeo = new THREE.BufferGeometry();
    const stemMat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.5 });
    const stems = new THREE.LineSegments(stemGeo, stemMat);
    scene.add(stems);

    // Picking: one full-height box per block, never drawn.
    const hitMesh = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0),
      new THREE.MeshBasicMaterial({ visible: false }), Math.max(1, n));
    hitMesh.count = n;
    const m4 = new THREE.Matrix4();
    layout.placed.forEach(({ x, z }, i) => {
      m4.makeScale(0.96, heights[i] + 0.9, 0.96).setPosition(x, 0, z);
      hitMesh.setMatrixAt(i, m4);
    });
    hitMesh.instanceMatrix.needsUpdate = true;
    hitMesh.computeBoundingSphere();
    scene.add(hitMesh);

    // Hover / selection outlines.
    const edgeGeo = new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1).translate(0, 0.5, 0));
    const hoverLine = new THREE.LineSegments(edgeGeo, new THREE.LineBasicMaterial());
    const selectLine = new THREE.LineSegments(edgeGeo, new THREE.LineBasicMaterial());
    hoverLine.visible = false;
    selectLine.visible = false;
    scene.add(hoverLine, selectLine);
    const frame = (line: THREE.LineSegments, i: number | null) => {
      if (i == null || i < 0 || i >= n) { line.visible = false; return; }
      const { x, z } = layout.placed[i];
      line.visible = true;
      line.position.set(x, 0, z);
      line.scale.set(FOOTPRINT + 0.12, heights[i] + 0.06, FOOTPRINT + 0.12);
    };

    // --- geometry for a given rise (0..1): the intro animates this -------------
    const beaconSize = (i: number) => 0.16 + 0.24 * Math.sqrt(
      layout.placed[i].block.critical_untouched / Math.max(1, layout.maxCriticalUntouched));
    const placeAll = (rise: number) => {
      bands.forEach(({ block, y0, h }, k) => {
        const { x, z } = layout.placed[block];
        m4.makeScale(FOOTPRINT, Math.max(0.0001, h * rise), FOOTPRINT).setPosition(x, y0 * rise, z);
        bandMesh.setMatrixAt(k, m4);
      });
      bandMesh.instanceMatrix.needsUpdate = true;
      stemPts.length = 0;
      beaconIdx.forEach((i, k) => {
        const { x, z } = layout.placed[i];
        const top = heights[i] * rise;
        const s = beaconSize(i) * rise;
        const y = top + 0.7 + s;
        m4.makeScale(s, s * 1.3, s).setPosition(x, y, z);
        beaconMesh.setMatrixAt(k, m4);
        stemPts.push(x, top, z, x, y - s * 1.3, z);
      });
      beaconMesh.instanceMatrix.needsUpdate = true;
      stemGeo.setAttribute('position', new THREE.Float32BufferAttribute(stemPts, 3));
    };

    // --- theme ----------------------------------------------------------------
    const applyPalette = (p: TerrainPalette) => {
      const stageColour = [p.tested, p.planned, p.worked, p.untouched].map((c) => new THREE.Color(c));
      bands.forEach(({ stage }, k) => bandMesh.setColorAt(k, stageColour[stage]));
      if (bandMesh.instanceColor) bandMesh.instanceColor.needsUpdate = true;
      const beacon = new THREE.Color(p.beacon);
      beaconIdx.forEach((_, k) => beaconMesh.setColorAt(k, beacon));
      if (beaconMesh.instanceColor) beaconMesh.instanceColor.needsUpdate = true;
      stemMat.color.set(p.beacon);
      groundMat.color.set(p.ground);
      gridMat.color.set(p.grid);
      (hoverLine.material as THREE.LineBasicMaterial).color.set(p.hover);
      (selectLine.material as THREE.LineBasicMaterial).color.set(p.outline);
    };
    applyPalette(terrainPalette(readTerrainTokens()));

    // --- district labels (HTML, projected) -----------------------------------
    const labelEls = layout.districts.map((d) => {
      const el = document.createElement('div');
      el.className = 'pointer-events-none absolute left-0 top-0 whitespace-nowrap font-mono text-caption text-muted-foreground';
      el.textContent = d.label;
      labelLayer.appendChild(el);
      return el;
    });
    const v3 = new THREE.Vector3();
    const placeLabels = (w: number, h: number) => {
      layout.districts.forEach((d, i) => {
        // Under the plate's corner nearest the viewer, centred.
        const toward = camera.position.clone().sub(controls.target);
        const cx = d.x + (toward.x >= 0 ? d.size : 0);
        const cz = d.z + (toward.z >= 0 ? d.size : 0);
        v3.set(cx, 0, cz).project(camera);
        const px = (v3.x * 0.5 + 0.5) * w;
        const py = (-v3.y * 0.5 + 0.5) * h;
        labelEls[i].style.transform = `translate(${px.toFixed(1)}px, ${py.toFixed(1)}px) translate(-50%, 6px)`;
        labelEls[i].style.visibility = px < -40 || px > w + 40 || py < -20 || py > h + 20 ? 'hidden' : 'visible';
      });
    };

    // --- sizing and drawing ---------------------------------------------------
    let width = 1;
    let height = 1;
    let zoom = 1;
    const fit = () => {
      const aspect = width / Math.max(1, height);
      const half = Math.max(span * 0.62, MAX_HEIGHT * 0.95 + 2);
      camera.left = -half * aspect;
      camera.right = half * aspect;
      camera.top = half;
      camera.bottom = -half;
      camera.zoom = zoom;
      camera.updateProjectionMatrix();
    };
    let raf = 0;
    let introStart = reduceMotion ? -1 : performance.now();
    const draw = () => {
      raf = 0;
      let again = controls.update();
      if (introStart >= 0) {
        const t = Math.min(1, (performance.now() - introStart) / INTRO_MS);
        placeAll(easeOut(t));
        if (t < 1) again = true; else introStart = -1;
      }
      renderer.render(scene, camera);
      placeLabels(width, height);
      if (again) request();
    };
    const request = () => { if (!raf) raf = requestAnimationFrame(draw); };
    placeAll(reduceMotion ? 1 : 0);

    const resize = () => {
      width = Math.max(1, host.clientWidth);
      height = Math.max(1, host.clientHeight);
      renderer.setSize(width, height, false);
      renderer.domElement.style.width = `${width}px`;
      renderer.domElement.style.height = `${height}px`;
      fit();
      request();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(host);
    resize();
    controls.addEventListener('change', request);
    controls.addEventListener('start', request);

    const themeObserver = new MutationObserver(() => {
      applyPalette(terrainPalette(readTerrainTokens()));
      request();
    });
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['style', 'class', 'data-theme'] });

    // --- pointer ----------------------------------------------------------------
    const raycaster = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    let hovered: number | null = null;
    const pick = (ev: PointerEvent | MouseEvent): number | null => {
      const r = renderer.domElement.getBoundingClientRect();
      ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
      raycaster.setFromCamera(ndc, camera);
      const hit = raycaster.intersectObject(hitMesh, false)[0];
      return hit?.instanceId ?? null;
    };
    let down: { x: number; y: number } | null = null;
    const onMove = (ev: PointerEvent) => {
      if (down) return;
      const i = pick(ev);
      if (i === hovered) return;
      hovered = i;
      renderer.domElement.style.cursor = i == null ? 'grab' : 'pointer';
      frame(hoverLine, i);
      cb.current.onHover(i);
      request();
    };
    const onLeave = () => {
      if (hovered == null) return;
      hovered = null;
      frame(hoverLine, null);
      cb.current.onHover(null);
      request();
    };
    const onDown = (ev: PointerEvent) => { down = { x: ev.clientX, y: ev.clientY }; };
    const onUp = (ev: PointerEvent) => {
      const d = down;
      down = null;
      if (!d || Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > 4) return; // a drag, not a click
      cb.current.onSelect(pick(ev));
    };
    const onDbl = (ev: MouseEvent) => {
      const i = pick(ev);
      if (i != null) cb.current.onOpen(i);
    };
    const zoomTo = (z: number) => {
      zoom = Math.min(8, Math.max(0.5, z));
      fit();
      request();
    };
    const onWheel = (ev: WheelEvent) => {
      if (!ev.ctrlKey && !ev.metaKey) return; // let the page scroll
      ev.preventDefault();
      zoomTo(zoom * Math.exp(-ev.deltaY * 0.0015));
    };
    const canvas = renderer.domElement;
    canvas.style.cursor = 'grab';
    canvas.addEventListener('pointermove', onMove);
    canvas.addEventListener('pointerleave', onLeave);
    canvas.addEventListener('pointerdown', onDown);
    canvas.addEventListener('pointerup', onUp);
    canvas.addEventListener('dblclick', onDbl);
    canvas.addEventListener('wheel', onWheel, { passive: false });

    api.current = {
      select: (i) => { frame(selectLine, i); request(); },
      zoomBy: (f) => zoomTo(zoom * f),
      reset: () => {
        camera.position.copy(home);
        controls.target.copy(focus);
        zoomTo(1);
      },
    };

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      themeObserver.disconnect();
      controls.dispose();
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerleave', onLeave);
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointerup', onUp);
      canvas.removeEventListener('dblclick', onDbl);
      canvas.removeEventListener('wheel', onWheel);
      labelEls.forEach((el) => el.remove());
      scene.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        mesh.geometry?.dispose();
        const mat = mesh.material as THREE.Material | THREE.Material[] | undefined;
        (Array.isArray(mat) ? mat : mat ? [mat] : []).forEach((m) => m.dispose());
      });
      bandMesh.dispose();
      beaconMesh.dispose();
      hitMesh.dispose();
      renderer.dispose();
      canvas.remove();
      api.current = null;
    };
  }, [layout]);

  useEffect(() => { api.current?.select(selected); }, [selected, layout]);

  return (
    <div className="relative size-full overflow-hidden">
      <div ref={hostRef} className="absolute inset-0" />
      <div ref={labelsRef} className="pointer-events-none absolute inset-0" aria-hidden />
    </div>
  );
});

export default TerrainScene;
