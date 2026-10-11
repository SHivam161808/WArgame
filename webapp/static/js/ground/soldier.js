// Player soldiers: Quaternius CC0 SWAT body + animations, carrying the rifle from the Quaternius soldier pack.
import * as THREE from 'three';
import { clone as cloneSkinned } from 'three/addons/utils/SkeletonUtils.js';

// uniform tints for the SWAT 'Swat' material: BLUE olive drab, RED desert khaki
export const UNIFORM = { BLUE: 0x56653d, RED: 0x8c6a45 };
export const SOLDIER_ANIMS = ['Idle_Gun', 'Idle_Gun_Pointing', 'Idle_Gun_Shoot', 'Walk', 'Run', 'Run_Back', 'Run_Left', 'Run_Right', 'Run_Shoot', 'Death', 'HitRecieve'];
const RIFLE_LEN = 0.85; // metres
const HAND = 'Wrist.R';
// GLTFLoader sanitises node names ('Wrist.R' → 'WristR')
const byName = (root, n) => root.getObjectByName(n) || root.getObjectByName(THREE.PropertyBinding.sanitizeNodeName(n));

const GUNS = ['AK', 'SMG', 'Pistol', 'Sniper', 'Sniper_2', 'Shotgun', 'RocketLauncher'];

/** Load body, clips and weapons once; returns a template for makeSoldier(). */
export async function loadSoldierKit(loader, base = '/static/characters/') {
  const [body, pack] = await Promise.all([loader.loadAsync(`${base}swat.glb`), loader.loadAsync(`${base}soldier.glb`).catch(() => null)]);
  const clips = {};
  for (const c of body.animations) clips[c.name.split('|').pop()] = c;
  body.scene.updateMatrixWorld(true);
  body.scene.traverse((o) => o.isSkinnedMesh && o.skeleton.update());
  const box = new THREE.Box3().setFromObject(body.scene, true);
  const h = box.max.y - box.min.y;
  const ok = h > 0.3 && h < 500;
  const kit = { scene: body.scene, clips, scale: ok ? 1.8 / h : 1, minY: ok ? box.min.y : 0, gun: null, guns: {}, hand: {} };
  if (!pack) return kit;
  // every weapon of the soldier pack: a clean copy (loot on the floor) and its original hand-local matrix
  const orig = {};
  for (const n of GUNS) {
    const node = byName(pack.scene, n);
    if (!node) continue;
    node.updateMatrix();
    orig[n] = node.matrix.clone();
    const g = node.clone(true);
    g.position.set(0, 0, 0); g.quaternion.identity(); g.scale.set(1, 1, 1);
    g.visible = true;
    g.traverse((o) => {
      if (!o.isMesh) return;
      o.visible = true; o.castShadow = true;
      o.material = o.material.clone();
      if (/Wood/.test(o.material.name)) o.material.color.setHex(0x2b2d2a); // polymer furniture
      o.material.roughness = 0.55; o.material.metalness = 0.35;
    });
    kit.guns[n] = g;
  }
  if (!kit.guns.AK) return kit;
  kit.gun = rifleFor(body.scene, clips, kit.guns.AK);
  if (!kit.gun) return kit;
  // the pack holds all its weapons in the same hand: carry the AK's solved grip over to the others
  const ms = kit.gun.obj.matrix.clone();
  const inv = orig.AK.clone().invert();
  for (const n of Object.keys(kit.guns)) {
    const m = ms.clone().multiply(inv).multiply(orig[n]);
    const g = kit.guns[n].clone(true);
    m.decompose(g.position, g.quaternion, g.scale);
    kit.hand[n] = g;
  }
  return kit;
}

/**
 * The rifle's transform relative to the SWAT right hand: solved in the arm-forward "pointing" pose so the
 * barrel (+X of the AK mesh) points where the soldier faces (+Z) and the magazine hangs down (AK +Z → up).
 */
function rifleFor(bodyScene, clips, ak) {
  const gun = ak.clone(true);
  gun.updateMatrixWorld(true);
  const gb = new THREE.Box3().setFromObject(gun, true);
  const len = Math.max(1e-6, gb.max.x - gb.min.x);
  const probe = cloneSkinned(bodyScene);
  const mixer = new THREE.AnimationMixer(probe);
  const pose = clips.Idle_Gun_Pointing || clips.Idle_Gun;
  if (pose) { mixer.clipAction(pose).play(); mixer.update(0.3); }
  probe.updateMatrixWorld(true);
  const hand = byName(probe, HAND);
  if (!hand) return null;
  const qh = hand.getWorldQuaternion(new THREE.Quaternion());
  const sh = hand.getWorldScale(new THREE.Vector3()).x;
  const want = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(
    new THREE.Vector3(0, 0, 1), new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0)));
  const s = RIFLE_LEN / (len * sh);
  gun.quaternion.copy(qh.clone().invert().multiply(want));
  gun.scale.setScalar(s);
  // grip in the palm: shift the rifle back so ~35% of its length sits behind the hand
  const back = new THREE.Vector3(gb.min.x + len * 0.35, 0, 0).applyQuaternion(gun.quaternion).multiplyScalar(s);
  gun.position.copy(back.multiplyScalar(-1));
  gun.updateMatrix();
  return { obj: gun, bone: HAND };
}

/** A soldier instance: { root (feet at y=0, faces +Z), mixer, actions, mats }. */
export function makeSoldier(kit, side) {
  const m = cloneSkinned(kit.scene);
  m.scale.setScalar(kit.scale);
  m.position.y = -kit.minY * kit.scale;
  const mats = [];
  m.traverse((o) => {
    if (!o.isMesh) return;
    o.castShadow = true; o.receiveShadow = true; o.frustumCulled = false;
    o.material = o.material.clone();
    o.material.roughness = Math.max(0.7, o.material.roughness ?? 0.7); o.material.metalness = 0; o.material.envMapIntensity = 0.5;
    if (o.material.name === 'Swat') o.material.color.setHex(UNIFORM[side] || 0x666666);
    mats.push(o.material);
  });
  const hand = kit.gun ? byName(m, kit.gun.bone) : null;
  const held = {};
  let current = null;
  /** Put a weapon of the soldier pack in the right hand (AK, SMG, Pistol, Sniper, Sniper_2, Shotgun, RocketLauncher). */
  const setWeapon = (name) => {
    if (!hand) return;
    name = kit.hand[name] ? name : 'AK';
    if (current === name) return;
    if (current && held[current]) held[current].visible = false;
    if (!held[name]) {
      const g = kit.hand[name].clone(true);
      g.traverse((o) => { if (o.isMesh) mats.push(o.material = o.material.clone()); });
      hand.add(g);
      held[name] = g;
    }
    held[name].visible = true;
    current = name;
  };
  setWeapon('AK');
  const root = new THREE.Group(); // crouch squash pivots at the feet
  root.add(m);
  const mixer = new THREE.AnimationMixer(m);
  const actions = {};
  for (const n of SOLDIER_ANIMS) if (kit.clips[n]) actions[n] = mixer.clipAction(kit.clips[n]);
  for (const n of ['Death', 'HitRecieve']) if (actions[n]) { actions[n].setLoop(THREE.LoopOnce, 1); actions[n].clampWhenFinished = true; }
  return { root, mixer, actions, mats, setWeapon };
}
