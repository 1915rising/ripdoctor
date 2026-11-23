# RIP HEALTH REPORT

**Target:** `synthetic-export`  
**Kind:** assetripper_export  
**Overall:** ❌ FAIL
**Census:** shaders: 2, materials: 6, textures: 2

## Checks

### ❌ FAIL — Dummy (stub) shaders — the renders-pure-white class

2 shader(s) in export, 1 dummy, 3 material(s) on dummy shaders

- **Toony Colors Pro 2/Standard** (`synthetic-export/Assets/Shader/ToonyColorsPro2.shader`)
  - signals: DummyShaderTextExporter marker comment; fragment returns constant float4(1,1,1,1) (pure white)
  - materials affected: 3
    - holly_body
    - holly_hair
    - sam_body

### ✅ PASS — Material census by shader

6 material(s) parsed

- Custom/GoodLit: 3
- Toony Colors Pro 2/Standard: 3

### ⚠️ WARN — Texture slots — empty primary maps

1 material(s) with an empty primary map slot on a real shader (renders untextured/flat)

- flat_prop (Custom/GoodLit): `_MainTex` empty
- also empty (declared, non-primary): Custom/GoodLit :: _BumpMap × 1 material(s)

### ❌ FAIL — Texture files — missing references & zero-byte files

2 texture file(s) in export; 1 material texture reference(s) point at GUIDs with no file in the export; 1 zero-byte texture file(s)

- missing texture: broken_ref slot `_MainTex` → guid bbbb0000000000000000000000000999 not in export
- zero-byte: `synthetic-export/Assets/Texture2D/empty_tex.png`

## Known blind spots (v1)

- Shader *semantics*: a shader that decompiled but computes the wrong
  thing (beyond the constant-white dummy signature) is not detected.
- Texture references from scenes/prefabs (only material references
  are followed in v1), and texture *content* (a present file can
  still be the wrong image).
- Animation clips, avatar configuration, and everything Unity does
  at import time — an export can pass here and still fail in-engine.
  Rip Doctor narrows the surprise list; it does not replace opening
  the project.

