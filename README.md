# Rip Doctor

A validation layer around **AssetRipper**. AssetRipper stays the ripper;
Rip Doctor is the checkup you run on its output *before* the export costs
you a playtest cycle.

AssetRipper is a good ripper with silent failure classes. The worst of
them: when it cannot recover a shader, it exports a **dummy stub** whose
fragment shader returns pure white — and nothing in the export tells you.
Every material on that shader renders white in-engine. In the incident
that motivated this tool, a game's cel-shading shader exported as a dummy
and **all 146 materials on it rendered white**, discovered only at
playtest, hours in.

Rip Doctor catches that class — and maps every affected material by GUID,
so you get the exact blast radius ("shader X, N materials: …") up front
instead of discovering it in-engine.

## What it checks

| Check | Catches |
|---|---|
| Dummy shaders | `.shader` files carrying the `//DummyShaderTextExporter` marker, or whose fragment returns constant `float4(1,1,1,1)` with no texture sampling — then maps **every material on each stub** by GUID. |
| Material census | Every material counted by shader; materials whose shader GUID is **absent from the export** are called out as unverifiable (common when shaders live in an SDK/package folder AssetRipper didn't export — a WARN, not a false alarm). |
| Texture slots | Materials on real shaders whose **primary map slot** (`_MainTex`, `_BaseMap`, …) is empty; other declared-but-empty slots are tallied separately so optional maps (normals etc.) don't cry wolf. |
| Texture files | Material texture references pointing at GUIDs with **no file in the export** (dangling references = textures that failed to export), and **zero-byte** texture files. |
| FBX sanity | Via Blender (headless): per-mesh vertex counts, world-space bounding boxes, **scale plausibility** (max dimension < 0.05 m ⇒ the 0.01-scale unit class, FAIL; > 50 m ⇒ centimeter units suspected), zero-vertex meshes, armature/bone counts, and whether meshes are actually bound (armature modifier + vertex groups). |

## Requirements

- Python 3.8+ — project (export directory) checks are **standard library
  only**, no pip installs.
- Blender on your `PATH` — only for the optional FBX checks. Without
  Blender, the FBX section is skipped/fails soft; the export checks are
  unaffected.
- Input: an AssetRipper **text** export (an `ExportedProject` folder, or
  any folder holding `Assets/`). Binary-serialized `.mat` files are
  skipped and counted.

## Usage

```bash
# After any rip, point it at the ExportedProject (or the folder holding Assets/):
python3 ripdoctor.py <export_dir> [--json out.json] [--md out.md]

# For a loose FBX:
python3 ripdoctor.py <model.fbx>

# Optionally also probe FBXs inside an export (max 20):
python3 ripdoctor.py <export_dir> --fbx
```

Exit codes: `0` = PASS/WARN, `1` = FAIL, `2` = usage error.

Reports print to stdout as Markdown; `--json` and `--md` also write them
to files. A sample report (generated from the synthetic test fixture) is
in [`examples/`](examples/example-report.md).

## Tests

```bash
python3 tests/run_tests.py
```

The suite builds a synthetic export with planted defects — a dummy shader
with 3 materials on it, an empty primary map, a dangling texture
reference, and a zero-byte texture — and asserts Rip Doctor catches every
one while leaving the healthy material alone.

## Known blind spots (v1, stated on purpose)

- **Bindpose matrix values.** A skeleton can be present and bound while
  its bindposes are garbage; that only deforms wrong after Unity import.
  FBX PASS is necessary, not sufficient.
- **Unity import-time everything**: importer scale factor, avatar
  configuration, material remapping, what the engine does with the asset.
- **Shader semantics** beyond the dummy signature: a shader that
  decompiled but computes the wrong thing is not detected.
- **Texture references from scenes/prefabs** (v1 follows material
  references only) and **texture content** (a present file can still be
  the wrong image).

Run Rip Doctor on **every** new AssetRipper export before the project is
opened in Unity. A FAIL on dummy shaders means: budget a real shader
replacement *before* importing anything that depends on those materials.

## Tested on

Rip Doctor was validated against a census of **23 FBX game rips**
from a single JRPG, which came back **20 FAIL / 3 WARN / 0 PASS** — a result
that says more about the state of those rips than about the tool, and is
exactly why a pre-flight check is worth running. The synthetic fixture in
`tests/` reproduces the dummy-shader failure class deterministically.

Other games and other export formats are **untested**. The checks operate
on AssetRipper's text export format and standard Unity YAML, so they
should transfer — but "should" isn't "did". Issues and reports from other
games are welcome.

## License

MIT — see [LICENSE](LICENSE).
