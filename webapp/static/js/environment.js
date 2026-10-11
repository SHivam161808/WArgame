// Weather, time of day and sensor modes (TH-2..4, DC-2) as Cesium post-process stages.
const C = Cesium;

const RAIN = `
uniform sampler2D colorTexture; uniform float intensity; uniform float time;
in vec2 v_textureCoordinates;
float hash(float x){ return fract(sin(x*23.3)*13.13); }
void main(){
  vec4 col = texture(colorTexture, v_textureCoordinates);
  vec2 res = czm_viewport.zw;
  vec2 uv = (gl_FragCoord.xy*2.0 - res)/min(res.x,res.y);
  float a = -0.25; float si = sin(a), co = cos(a);
  uv *= mat2(co,-si,si,co);
  float streak = 0.0;
  for (float i = 1.0; i < 3.5; i += 1.0) {
    vec2 p = uv * (0.9 + i*0.35);
    float v = 1.0 - sin(hash(floor(p.x*110.0 + i*17.0))*2.0);
    float b = clamp(abs(sin(18.0*time*v + p.y*(5.0/(2.0+v)))) - 0.95, 0.0, 1.0) * 20.0;
    streak += v*b*(0.55/i);
  }
  vec3 rainCol = vec3(0.7,0.78,0.88)*streak;
  vec3 wet = col.rgb*(1.0 - 0.28*intensity) + vec3(0.03,0.04,0.05)*intensity;
  out_FragColor = vec4(wet + rainCol*0.32*intensity, col.a);
}`;

const SNOW = `
uniform sampler2D colorTexture; uniform float intensity; uniform float time;
in vec2 v_textureCoordinates;
float h21(vec2 p){ p = fract(p*vec2(123.34,456.21)); p += dot(p,p+45.32); return fract(p.x*p.y); }
float layer(vec2 uv, float scale, float speed){
  uv *= scale;
  uv.y += time*speed; uv.x += sin(time*0.6 + uv.y*0.15)*0.6;
  vec2 id = floor(uv); vec2 f = fract(uv) - 0.5;
  float r = h21(id);
  vec2 o = vec2(r-0.5, fract(r*13.7)-0.5)*0.6;
  float d = length(f - o);
  float size = 0.04 + 0.08*fract(r*7.3);
  return smoothstep(size, size*0.3, d) * step(0.35, r);
}
void main(){
  vec4 col = texture(colorTexture, v_textureCoordinates);
  vec2 uv = gl_FragCoord.xy / min(czm_viewport.z, czm_viewport.w);
  float s = layer(uv, 9.0, 1.6) + layer(uv, 15.0, 2.2)*0.8 + layer(uv, 24.0, 3.0)*0.55;
  vec3 c = mix(col.rgb, vec3(0.92,0.95,1.0), clamp(s*intensity, 0.0, 1.0)*0.85);
  c = mix(c, vec3(0.82,0.86,0.92), 0.12*intensity);
  out_FragColor = vec4(c, col.a);
}`;

const FOG = `
uniform sampler2D colorTexture; uniform sampler2D depthTexture;
uniform float density; uniform vec3 fogColor; uniform float maxFog;
in vec2 v_textureCoordinates;
void main(){
  vec4 col = texture(colorTexture, v_textureCoordinates);
  float depth = czm_readDepth(depthTexture, v_textureCoordinates);
  float dist;
  if (depth >= 1.0) { dist = 1.0e7; }
  else { vec4 eye = czm_windowToEyeCoordinates(gl_FragCoord.xy, depth); dist = length(eye.xyz / eye.w); }
  float f = clamp(1.0 - exp(-dist*density), 0.0, maxFog);
  out_FragColor = vec4(mix(col.rgb, fogColor, f), col.a);
}`;

const DUST = `
uniform sampler2D colorTexture; uniform float intensity; uniform float time;
in vec2 v_textureCoordinates;
float n(vec2 p){ return fract(sin(dot(p, vec2(12.9898,78.233)))*43758.5453); }
float sn(vec2 p){ vec2 i=floor(p), f=fract(p); f=f*f*(3.0-2.0*f);
  return mix(mix(n(i),n(i+vec2(1,0)),f.x), mix(n(i+vec2(0,1)),n(i+vec2(1,1)),f.x), f.y); }
void main(){
  vec2 uv = v_textureCoordinates;
  vec4 col = texture(colorTexture, uv);
  float t = time*0.25;
  float m = sn(uv*vec2(6.0,3.0) + vec2(t, 0.0))*0.6 + sn(uv*vec2(14.0,7.0) + vec2(t*1.7, t*0.3))*0.4;
  vec3 dust = vec3(0.74,0.58,0.38);
  out_FragColor = vec4(mix(col.rgb, dust, clamp(intensity*(0.35 + 0.35*m), 0.0, 0.8)), col.a);
}`;

const HAZE = `
uniform sampler2D colorTexture; uniform float intensity; uniform float time;
in vec2 v_textureCoordinates;
void main(){
  vec2 uv = v_textureCoordinates;
  float w = sin(uv.y*160.0 + time*4.0)*0.0009*intensity + sin(uv.y*73.0 - time*2.7)*0.0006*intensity;
  vec4 col = texture(colorTexture, uv + vec2(w*(1.0-uv.y), 0.0));
  out_FragColor = vec4(mix(col.rgb, vec3(0.86,0.82,0.74), 0.12*intensity), col.a);
}`;

const GRADE = `
uniform sampler2D colorTexture; uniform vec3 tint; uniform float saturation; uniform float brightness;
uniform float contrast; uniform float vignette; uniform vec3 night;
in vec2 v_textureCoordinates;
void main(){
  vec4 col = texture(colorTexture, v_textureCoordinates);
  vec3 c = col.rgb * tint;
  float l = dot(c, vec3(0.299,0.587,0.114));
  c = mix(vec3(l), c, saturation);
  c = (c - 0.5)*contrast + 0.5;
  c *= brightness;
  c = mix(c, c*night, step(0.001, night.r+night.g+night.b) * 1.0);
  vec2 d = v_textureCoordinates - 0.5;
  c *= 1.0 - vignette*dot(d,d)*1.6;
  out_FragColor = vec4(clamp(c, 0.0, 1.0), col.a);
}`;

const NIGHTVISION = `
uniform sampler2D colorTexture; uniform float time;
in vec2 v_textureCoordinates;
float rnd(vec2 p){ return fract(sin(dot(p, vec2(12.9898,78.233)) + time*13.0)*43758.5453); }
void main(){
  vec2 uv = v_textureCoordinates;
  vec3 c = texture(colorTexture, uv).rgb;
  float l = dot(c, vec3(0.3,0.59,0.11));
  l = pow(clamp(l*3.2, 0.0, 1.0), 0.8);
  l += (rnd(uv*czm_viewport.zw) - 0.5)*0.12;
  vec3 g = vec3(0.12, 1.0, 0.35) * l;
  float scan = 0.93 + 0.07*sin(uv.y*czm_viewport.w*1.5);
  out_FragColor = vec4(g*scan, 1.0);
}`;

const THERMAL = `
uniform sampler2D colorTexture; uniform sampler2D depthTexture; uniform float whiteHot;
in vec2 v_textureCoordinates;
void main(){
  vec2 uv = v_textureCoordinates;
  vec2 px = 1.0/czm_viewport.zw;
  vec3 c = (texture(colorTexture, uv).rgb*4.0 + texture(colorTexture, uv+vec2(px.x,0)).rgb + texture(colorTexture, uv-vec2(px.x,0)).rgb
           + texture(colorTexture, uv+vec2(0,px.y)).rgb + texture(colorTexture, uv-vec2(0,px.y)).rgb)/8.0;
  float depth = czm_readDepth(depthTexture, uv);
  float l = dot(c, vec3(0.3,0.59,0.11));
  // vehicles are tinted white in thermal mode → hot; terrain is a mid-grey thermal background; sky is cold
  float hot = smoothstep(0.86, 0.99, min(c.r, min(c.g, c.b)));
  float v = depth >= 1.0 ? 0.06 : clamp(0.18 + l*0.32 + hot*0.75, 0.0, 1.0);
  v = mix(1.0 - v, v, whiteHot);
  out_FragColor = vec4(vec3(v), 1.0);
}`;

const SEASON_DATE = { Summer: '2026-05-20', Monsoon: '2026-07-25', Autumn: '2026-10-20', Winter: '2026-01-15', 'Post-monsoon': '2026-11-10' };
const WEATHER_FX = {
  CLEAR: {}, RAIN: { rain: 0.8, fog: 0.00008 }, STORM: { rain: 1.0, fog: 0.00016 }, SNOW: { snow: 0.7, fog: 0.0001 },
  BLIZZARD: { snow: 1.0, fog: 0.00045 }, DUST: { dust: 0.9, fog: 0.00025, fogColor: [0.72, 0.58, 0.4] },
  FOG: { fog: 0.0006 }, HAZE: { haze: 0.8, fog: 0.00006, fogColor: [0.86, 0.82, 0.74] },
};

export class Environment {
  constructor(g, theatre) {
    this.g = g;
    this.theatre = theatre;
    const stages = g.scene.postProcessStages;
    const t0 = performance.now();
    const time = () => (performance.now() - t0) / 1000;
    this.weather = 'CLEAR';
    this.fx = {};
    this.stage = {
      fog: stages.add(new C.PostProcessStage({ fragmentShader: FOG, uniforms: { density: 0, fogColor: new C.Cartesian3(0.78, 0.8, 0.82), maxFog: 0.92 } })),
      haze: stages.add(new C.PostProcessStage({ fragmentShader: HAZE, uniforms: { intensity: 0, time } })),
      dust: stages.add(new C.PostProcessStage({ fragmentShader: DUST, uniforms: { intensity: 0, time } })),
      rain: stages.add(new C.PostProcessStage({ fragmentShader: RAIN, uniforms: { intensity: 0, time } })),
      snow: stages.add(new C.PostProcessStage({ fragmentShader: SNOW, uniforms: { intensity: 0, time } })),
      grade: stages.add(new C.PostProcessStage({ fragmentShader: GRADE, uniforms: {
        tint: new C.Cartesian3(1, 1, 1), saturation: 1, brightness: 1, contrast: 1.04, vignette: 0.35, night: new C.Cartesian3(0, 0, 0) } })),
      nv: stages.add(new C.PostProcessStage({ fragmentShader: NIGHTVISION, uniforms: { time } })),
      thermal: stages.add(new C.PostProcessStage({ fragmentShader: THERMAL, uniforms: { whiteHot: 1 } })),
    };
    this.stage.nv.enabled = false;
    this.stage.thermal.enabled = false;
    this.sensor = 'DAY';
    const p = theatre.palette || {};
    this.basePalette = { tint: p.tint || [1, 1, 1], saturation: p.saturation ?? 1 };
  }

  setWeather(w) {
    this.weather = w;
    const fx = WEATHER_FX[w] || {};
    this.fx = fx;
    const s = this.stage;
    s.rain.enabled = !!fx.rain; s.rain.uniforms.intensity = fx.rain || 0;
    s.snow.enabled = !!fx.snow; s.snow.uniforms.intensity = fx.snow || 0;
    s.dust.enabled = !!fx.dust; s.dust.uniforms.intensity = fx.dust || 0;
    s.haze.enabled = !!fx.haze; s.haze.uniforms.intensity = fx.haze || 0;
    s.fog.enabled = !!fx.fog;
    s.fog.uniforms.density = fx.fog || 0;
    const fc = fx.fogColor || [0.78, 0.8, 0.83];
    s.fog.uniforms.fogColor = new C.Cartesian3(...fc);
    this.g.scene.fog.density = 0.0002 + (fx.fog || 0) * 0.5;
  }

  /** Local solar hour → sun position, lighting and colour grading. */
  setHour(hour) {
    this.hour = hour;
    const date = SEASON_DATE[this.theatre.season] || '2026-03-21';
    const utc = hour - this.theatre.lon / 15;
    const jd = C.JulianDate.fromIso8601(`${date}T00:00:00Z`);
    C.JulianDate.addSeconds(jd, utc * 3600, jd);
    const v = this.g.viewer;
    v.clock.currentTime = jd;
    v.clock.shouldAnimate = false;
    const sun = Math.sin(((hour - 6) / 12) * Math.PI);
    const isNight = sun < -0.08;
    const scene = this.g.scene;
    scene.globe.enableLighting = !isNight;
    scene.globe.dynamicAtmosphereLighting = !isNight;
    const s = this.stage.grade.uniforms;
    const [tr, tg, tb] = this.basePalette.tint;
    if (isNight) {
      s.tint = new C.Cartesian3(tr * 0.75, tg * 0.85, tb * 1.15);
      s.brightness = 0.42; s.saturation = this.basePalette.saturation * 0.45;
      s.night = new C.Cartesian3(0.7, 0.82, 1.15);
      scene.skyAtmosphere.brightnessShift = -0.6;
    } else {
      const golden = Math.max(0, 1 - Math.abs(sun) * 3.5) * (sun < 0.5 ? 1 : 0);
      s.tint = new C.Cartesian3(tr * (1 + 0.12 * golden), tg * (1 + 0.02 * golden), tb * (1 - 0.1 * golden));
      s.brightness = 1.02 - 0.15 * golden;
      s.saturation = this.basePalette.saturation * (1 + 0.1 * golden) * (this.fx.rain ? 0.8 : 1);
      s.night = new C.Cartesian3(0, 0, 0);
      scene.skyAtmosphere.brightnessShift = 0;
    }
    this.isNight = isNight;
  }

  /** Scope sensor mode: DAY | NV | THERMAL. */
  setSensor(mode) {
    this.sensor = mode;
    this.stage.nv.enabled = mode === 'NV';
    this.stage.thermal.enabled = mode === 'THERMAL';
    const weatherOn = mode === 'DAY';
    // thermal sees through rain / dust better, NV amplifies light
    this.stage.rain.enabled = weatherOn && !!this.fx.rain;
    this.stage.snow.enabled = weatherOn && !!this.fx.snow;
    this.stage.dust.enabled = !!this.fx.dust && mode !== 'THERMAL';
    this.stage.fog.uniforms.density = (this.fx.fog || 0) * (mode === 'THERMAL' ? 0.35 : 1);
    this.stage.grade.enabled = mode === 'DAY';
  }
}
