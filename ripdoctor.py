#!/usr/bin/env python3
"""
Rip Doctor v1 — a validation layer around AssetRipper exports.

AssetRipper is a good ripper with silent failure classes. This tool catches
the ones that have actually cost us hours:

  1. Dummy shaders. When AssetRipper cannot recover a shader it exports a
     stub (marked `//DummyShaderTextExporter`) whose fragment returns pure
     white `float4(1,1,1,1)`. Every material on that shader renders white
     in-engine, and nothing in the export warns you. (Field case, Nov 17 2026:
     the game's Toony Colors Pro 2 exported as a dummy — all 146 materials
     on it rendered white, discovered only at playtest.)
  2. Materials whose primary map slot is empty, or that reference textures
     which never made it into the export.
  3. Zero-byte texture files.
  4. Loose-FBX sanity: unit/scale anomalies (the 0.01-scale class), missing
     skinning data, empty meshes — checked via Blender headless.

Usage:
    ripdoctor.py <export_dir> [--json out.json] [--md out.md] [--fbx]
    ripdoctor.py <model.fbx> [--json out.json] [--md out.md]

Stdlib only for project checks; FBX checks shell out to `blender`.
Exit code: 0 = PASS/WARN, 1 = FAIL, 2 = usage/internal error.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

TEXTURE_EXTS = {
    ".png", ".tga", ".jpg", ".jpeg", ".psd", ".exr", ".hdr", ".dds",
    ".bmp", ".tif", ".tiff", ".webp", ".ktx", ".astc", ".pvr", ".gif",
}
BUILTIN_GUIDS = {
    "0000000000000000e000000000000000",
    "0000000000000000f000000000000000",
}
# Slots that carry a material's main color map across the shader families we
# actually rip (built-in/URP/HDRP/TCP2/custom). An empty primary slot on a
# textured shader is the "invisible / white / untextured" symptom class.
PRIMARY_MAP_SLOTS = (
    "_MainTex", "_BaseMap", "_BaseTex", "_AlbedoMap", "_Albedo",
    "_DiffuseMap", "_Diffuse", "_MainTexture",
)
LIST_CAP = 25  # per-list cap in the markdown report; JSON keeps everything

SEVERITY_ORDER = {"PASS": 0, "INFO": 0, "WARN": 1, "FAIL": 2}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def read_guid(meta_path: Path):
    """Extract the GUID from a Unity .meta file, or None."""
    if not meta_path.is_file():
        return None
    m = re.search(r"^guid:\s*([0-9a-f]{32})\s*$", read_text(meta_path), re.M)
    return m.group(1) if m else None


def worst(statuses):
    w = "PASS"
    for s in statuses:
        if SEVERITY_ORDER[s] > SEVERITY_ORDER[w]:
            w = s
    return w


# --------------------------------------------------------------------------
# project (export dir) checks
# --------------------------------------------------------------------------

def build_asset_index(root: Path):
    """guid -> {path, kind} for every asset that has a .meta file."""
    index = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".meta"):
                continue
            p = Path(dirpath) / name
            guid = read_guid(Path(str(p) + ".meta"))
            if not guid:
                continue
            ext = p.suffix.lower()
            if ext == ".shader":
                kind = "shader"
            elif ext in TEXTURE_EXTS:
                kind = "texture"
            elif ext == ".mat":
                kind = "material"
            else:
                kind = "other"
            index[guid] = {"path": p, "kind": kind}
    return index


def parse_shader(path: Path):
    text = read_text(path)
    name_m = re.search(r'^\s*Shader\s+"([^"]+)"', text, re.M)
    tex_props = re.findall(r"^\s*(\w+)\s*\([^)]*,\s*2D\)", text, re.M)
    signals = []
    if "DummyShaderTextExporter" in text:
        signals.append("DummyShaderTextExporter marker comment")
    const_ret = re.search(
        r"return\s+float4\(\s*1(?:\.0+)?\s*,\s*1(?:\.0+)?\s*,"
        r"\s*1(?:\.0+)?\s*,\s*1(?:\.0+)?\s*\)", text)
    if const_ret:
        signals.append("fragment returns constant float4(1,1,1,1) (pure white)")
    samples = len(re.findall(r"tex2D\s*\(|SAMPLE_TEXTURE2D|Sample\s*\(", text))
    is_dummy = "DummyShaderTextExporter" in text or (
        bool(const_ret) and samples == 0 and len(tex_props) > 0)
    return {
        "path": path,
        "name": name_m.group(1) if name_m else path.stem,
        "tex_props": sorted(set(tex_props)),
        "is_dummy": is_dummy,
        "signals": signals,
        "samples_textures": samples > 0,
    }


def parse_material(path: Path):
    text = read_text(path)
    if not text.startswith("%YAML"):
        return None  # binary-serialized material; AssetRipper text export expected
    name_m = re.search(r"^\s*m_Name:\s*(.+?)\s*$", text, re.M)
    sh_m = re.search(
        r"m_Shader:\s*\{fileID:\s*(-?\d+)"
        r"(?:,\s*guid:\s*([0-9a-f]{32}))?(?:,\s*type:\s*(\d+))?", text)
    texenvs = {}
    block_m = re.search(
        r"m_TexEnvs:\s*\n(.*?)(?=\n\s{4}m_(?:Floats|Ints|Colors|Strings):|\Z)",
        text, re.S)
    if block_m:
        for slot, fid, guid in re.findall(
                r"^\s{6}(\S+):\s*\n\s*m_Texture:\s*\{fileID:\s*(-?\d+)"
                r"(?:,\s*guid:\s*([0-9a-f]{32}))?",
                block_m.group(1), re.M):
            texenvs[slot] = guid if (guid and int(fid) != 0) else None
    return {
        "path": path,
        "name": name_m.group(1) if name_m else path.stem,
        "shader_fileid": int(sh_m.group(1)) if sh_m else None,
        "shader_guid": sh_m.group(2) if sh_m else None,
        "shader_type": int(sh_m.group(3)) if sh_m and sh_m.group(3) else None,
        "texenvs": texenvs,
    }


def check_project(root: Path):
    checks = []
    index = build_asset_index(root)

    # ---- shaders ------------------------------------------------------
    shader_files = sorted(root.rglob("*.shader"))
    shaders = {}  # guid -> parsed
    for sf in shader_files:
        parsed = parse_shader(sf)
        guid = read_guid(Path(str(sf) + ".meta"))
        parsed["guid"] = guid
        if guid:
            shaders[guid] = parsed
    dummies = [s for s in shaders.values() if s["is_dummy"]]
    # shaders found on disk but without a GUID in any .meta (rare)
    unguided = [sf.name for sf in shader_files
                if read_guid(Path(str(sf) + ".meta")) is None]

    # ---- materials ----------------------------------------------------
    mat_files = sorted(root.rglob("*.mat"))
    materials = []
    skipped_binary = 0
    for mf in mat_files:
        parsed = parse_material(mf)
        if parsed is None:
            skipped_binary += 1
            continue
        materials.append(parsed)

    by_shader = defaultdict(list)
    unresolved = []   # shader guid not in export, not a known built-in
    builtin = []
    for mat in materials:
        guid = mat["shader_guid"]
        if guid and guid in shaders:
            by_shader[guid].append(mat)
        elif (guid in BUILTIN_GUIDS) or (mat["shader_type"] == 0):
            builtin.append(mat)
        elif guid:
            unresolved.append(mat)
        else:
            builtin.append(mat)  # fileID-only reference -> built-in shader

    # dummy-shader check
    affected = []
    for s in dummies:
        mats = by_shader.get(s["guid"], [])
        affected.append({
            "shader": s["name"], "file": str(s["path"]),
            "signals": s["signals"],
            "material_count": len(mats),
            "materials": sorted(m["name"] for m in mats),
        })
    affected.sort(key=lambda a: -a["material_count"])
    n_affected_mats = sum(a["material_count"] for a in affected)
    if any(a["material_count"] > 0 for a in affected):
        status = "FAIL"
    elif affected:
        status = "WARN"
    else:
        status = "PASS"
    checks.append({
        "id": "dummy_shaders",
        "title": "Dummy (stub) shaders — the renders-pure-white class",
        "status": status,
        "summary": (
            f"{len(shaders)} shader(s) in export, {len(dummies)} dummy, "
            f"{n_affected_mats} material(s) on dummy shaders"),
        "details": {"dummy_shaders": affected,
                    "unguided_shader_files": unguided},
    })

    # material census check
    census = Counter()
    for guid, mats in by_shader.items():
        census[shaders[guid]["name"]] += len(mats)
    if builtin:
        census["<Unity built-in / fileID-only>"] += len(builtin)
    if unresolved:
        census["<shader not present in export>"] += len(unresolved)
    checks.append({
        "id": "material_census",
        "title": "Material census by shader",
        "status": "WARN" if unresolved else "PASS",
        "summary": (
            f"{len(materials)} material(s) parsed"
            + (f", {skipped_binary} binary material(s) skipped" if skipped_binary else "")
            + (f", {len(unresolved)} reference shader(s) absent from the export "
               "(cannot verify those shaders)" if unresolved else "")),
        "details": {
            "by_shader": dict(census.most_common()),
            "unresolved_shader_materials": sorted(m["name"] for m in unresolved),
        },
    })

    # texture slot check (only for materials on real, in-export shaders)
    empty_primary = []
    empty_declared = Counter()
    missing_tex = []  # referenced texture GUID with no file in the export
    for mat in materials:
        guid = mat["shader_guid"]
        shader = shaders.get(guid) if guid else None
        for slot, tguid in mat["texenvs"].items():
            if tguid and tguid not in index:
                missing_tex.append({"material": mat["name"], "slot": slot,
                                    "guid": tguid})
        if shader is None or shader["is_dummy"]:
            continue
        declared = set(shader["tex_props"])
        for slot in PRIMARY_MAP_SLOTS:
            if slot in declared and mat["texenvs"].get(slot) is None \
                    and slot in mat["texenvs"]:
                empty_primary.append({"material": mat["name"],
                                      "shader": shader["name"], "slot": slot})
        for slot in declared:
            if slot in mat["texenvs"] and mat["texenvs"][slot] is None \
                    and slot not in PRIMARY_MAP_SLOTS:
                empty_declared[f"{shader['name']} :: {slot}"] += 1
    checks.append({
        "id": "texture_slots",
        "title": "Texture slots — empty primary maps",
        "status": "WARN" if empty_primary else "PASS",
        "summary": (
            f"{len(empty_primary)} material(s) with an empty primary map slot "
            "on a real shader (renders untextured/flat)"),
        "details": {
            "empty_primary_slots": empty_primary,
            "other_empty_declared_slots_top": dict(empty_declared.most_common(15)),
        },
    })

    # texture file census
    tex_files = [v["path"] for v in index.values() if v["kind"] == "texture"]
    zero_byte = sorted(str(p) for p in tex_files if p.stat().st_size == 0)
    checks.append({
        "id": "texture_files",
        "title": "Texture files — missing references & zero-byte files",
        "status": "FAIL" if missing_tex else ("WARN" if zero_byte else "PASS"),
        "summary": (
            f"{len(tex_files)} texture file(s) in export; "
            f"{len(missing_tex)} material texture reference(s) point at GUIDs "
            f"with no file in the export; {len(zero_byte)} zero-byte texture file(s)"),
        "details": {"missing_referenced_textures": missing_tex,
                    "zero_byte_textures": zero_byte},
    })

    return {
        "target": str(root),
        "kind": "assetripper_export",
        "counts": {"shaders": len(shaders), "materials": len(materials),
                   "textures": len(tex_files)},
        "checks": checks,
    }


# --------------------------------------------------------------------------
# FBX checks (Blender headless)
# --------------------------------------------------------------------------

BLENDER_PROBE = r"""
import bpy, json, sys
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
path = argv[0]

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=path)

meshes, armatures = [], []
all_corners = []
for obj in bpy.context.scene.objects:
    if obj.type == "MESH":
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        dims = [0.0, 0.0, 0.0]
        if corners:
            for i in range(3):
                vals = [c[i] for c in corners]
                dims[i] = max(vals) - min(vals)
            all_corners.extend(corners)
        meshes.append({
            "name": obj.name,
            "verts": len(obj.data.vertices),
            "polys": len(obj.data.polygons),
            "dims_m": [round(d, 4) for d in dims],
            "armature_modifier": any(m.type == "ARMATURE" for m in obj.modifiers),
            "vertex_groups": len(obj.vertex_groups),
            "materials": len(obj.data.materials),
        })
    elif obj.type == "ARMATURE":
        armatures.append({"name": obj.name, "bones": len(obj.data.bones)})

scene_dims = [0.0, 0.0, 0.0]
if all_corners:
    for i in range(3):
        vals = [c[i] for c in all_corners]
        scene_dims[i] = round(max(vals) - min(vals), 4)

print("RIPDOCTOR_JSON_BEGIN")
print(json.dumps({"meshes": meshes, "armatures": armatures,
                  "scene_dims_m": scene_dims}))
print("RIPDOCTOR_JSON_END")
"""


def probe_fbx(fbx_path: Path):
    """Run Blender headless on one FBX; return the raw probe dict or None."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
        tf.write(BLENDER_PROBE)
        script = tf.name
    try:
        proc = subprocess.run(
            ["blender", "-b", "--factory-startup", "-noaudio",
             "--python", script, "--", str(fbx_path)],
            capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"blender probe failed: {exc}"
    finally:
        os.unlink(script)
    m = re.search(r"RIPDOCTOR_JSON_BEGIN\s*(\{.*?\})\s*RIPDOCTOR_JSON_END",
                  proc.stdout, re.S)
    if not m:
        tail = (proc.stderr or proc.stdout or "")[-500:]
        return None, f"blender produced no probe output: {tail}"
    return json.loads(m.group(1)), None


def check_fbx(fbx_path: Path):
    probe, err = probe_fbx(fbx_path)
    checks = []
    if probe is None:
        checks.append({
            "id": "fbx_probe", "title": "FBX probe (Blender headless)",
            "status": "FAIL", "summary": err, "details": {}})
        return {"target": str(fbx_path), "kind": "fbx", "counts": {},
                "checks": checks}

    meshes = probe["meshes"]
    armatures = probe["armatures"]
    dims = probe["scene_dims_m"]
    max_dim = max(dims) if dims else 0.0
    total_verts = sum(m["verts"] for m in meshes)
    empty_meshes = [m["name"] for m in meshes if m["verts"] == 0]
    skinned = [m for m in meshes if m["armature_modifier"] and m["vertex_groups"] > 0]
    bones = sum(a["bones"] for a in armatures)

    flags = []
    status = "PASS"
    if max_dim and max_dim < 0.05:
        status = "FAIL"
        flags.append(
            f"scene max dimension is {max_dim:.4f} m — the 0.01-scale class: "
            "geometry is ~100x too small (unit mismatch at export/import)")
    elif max_dim and max_dim < 0.5:
        status = "WARN"
        flags.append(f"scene max dimension is only {max_dim:.3f} m — verify "
                     "this is a small prop, not a mis-scaled character")
    elif max_dim > 50:
        status = "WARN"
        flags.append(f"scene max dimension is {max_dim:.1f} m — centimeter "
                     "units suspected (100x too large)")
    if empty_meshes:
        status = worst([status, "WARN"])
        flags.append(f"{len(empty_meshes)} mesh(es) with zero vertices: "
                     + ", ".join(empty_meshes[:LIST_CAP]))
    if not flags:
        flags.append("scale and geometry within plausible bounds")

    checks.append({
        "id": "fbx_geometry",
        "title": "FBX geometry & scale plausibility",
        "status": status,
        "summary": (f"{len(meshes)} mesh(es), {total_verts} verts total, "
                    f"scene dims {dims} m (max {max_dim:.3f} m)"),
        "details": {"flags": flags, "meshes": meshes,
                    "empty_meshes": empty_meshes},
    })

    if armatures:
        skin_status = "PASS" if skinned else "WARN"
        skin_summary = (f"{len(armatures)} armature(s), {bones} bone(s) total; "
                        f"{len(skinned)}/{len(meshes)} mesh(es) carry an "
                        "armature modifier + vertex groups (skinned)")
        if not skinned:
            skin_summary += " — armature exists but no mesh is bound to it"
    else:
        skin_status = "INFO"
        skin_summary = ("no armature in the FBX — static/unrigged as exported; "
                        "fine for props, a defect for characters")
    checks.append({
        "id": "fbx_skinning",
        "title": "FBX skinning / armature presence",
        "status": skin_status,
        "summary": skin_summary,
        "details": {"armatures": armatures,
                    "skinned_meshes": [m["name"] for m in skinned]},
    })

    checks.append({
        "id": "fbx_blind_spots",
        "title": "What an FBX check cannot see (stated, not hidden)",
        "status": "INFO",
        "summary": (
            "Detectable from the FBX alone: vertex counts, world-space scale, "
            "armature/bone presence, skin binding presence. NOT detectable "
            "from the FBX alone: bindpose matrix *values* (a bound skeleton "
            "can still carry garbage bindposes that only deform wrong after "
            "Unity import — a lesson from a real rip), Unity importer settings "
            "(scale factor, avatar configuration, material remapping), and "
            "anything the destination engine does at import time. An FBX "
            "PASS is necessary, not sufficient: the mesh must still be "
            "import-checked in Unity."),
        "details": {},
    })
    return {"target": str(fbx_path), "kind": "fbx",
            "counts": {"meshes": len(meshes), "verts": total_verts,
                       "bones": bones},
            "checks": checks}


# --------------------------------------------------------------------------
# report rendering
# --------------------------------------------------------------------------

ICON = {"PASS": "✅ PASS", "INFO": "ℹ️ INFO", "WARN": "⚠️ WARN", "FAIL": "❌ FAIL"}


def render_markdown(result):
    overall = worst([c["status"] for c in result["checks"]])
    lines = [
        "# RIP HEALTH REPORT",
        "",
        f"**Target:** `{result['target']}`  ",
        f"**Kind:** {result['kind']}  ",
        f"**Overall:** {ICON[overall]}",
    ]
    if result.get("counts"):
        counts = ", ".join(f"{k}: {v}" for k, v in result["counts"].items())
        lines.append(f"**Census:** {counts}")
    lines += ["", "## Checks", ""]
    for c in result["checks"]:
        lines.append(f"### {ICON[c['status']]} — {c['title']}")
        lines.append("")
        lines.append(c["summary"])
        lines.append("")
        d = c.get("details") or {}
        if c["id"] == "dummy_shaders":
            for a in d.get("dummy_shaders", []):
                lines.append(f"- **{a['shader']}** (`{a['file']}`)")
                lines.append(f"  - signals: {'; '.join(a['signals'])}")
                lines.append(f"  - materials affected: {a['material_count']}")
                for m in a["materials"][:LIST_CAP]:
                    lines.append(f"    - {m}")
                if len(a["materials"]) > LIST_CAP:
                    lines.append(f"    - … and {len(a['materials']) - LIST_CAP} more (full list in JSON)")
            if d.get("unguided_shader_files"):
                lines.append(f"- shader files without .meta GUIDs (not mapped): "
                             f"{', '.join(d['unguided_shader_files'])}")
            lines.append("")
        elif c["id"] == "material_census":
            for name, n in d.get("by_shader", {}).items():
                lines.append(f"- {name}: {n}")
            lines.append("")
        elif c["id"] == "texture_slots":
            for e in d.get("empty_primary_slots", [])[:LIST_CAP]:
                lines.append(f"- {e['material']} ({e['shader']}): `{e['slot']}` empty")
            if len(d.get("empty_primary_slots", [])) > LIST_CAP:
                lines.append(f"- … and {len(d['empty_primary_slots']) - LIST_CAP} more (full list in JSON)")
            for k, v in d.get("other_empty_declared_slots_top", {}).items():
                lines.append(f"- also empty (declared, non-primary): {k} × {v} material(s)")
            lines.append("")
        elif c["id"] == "texture_files":
            for e in d.get("missing_referenced_textures", [])[:LIST_CAP]:
                lines.append(f"- missing texture: {e['material']} slot `{e['slot']}` → guid {e['guid']} not in export")
            for p in d.get("zero_byte_textures", [])[:LIST_CAP]:
                lines.append(f"- zero-byte: `{p}`")
            lines.append("")
        elif c["id"] == "fbx_geometry":
            for f in d.get("flags", []):
                lines.append(f"- {f}")
            for m in d.get("meshes", [])[:LIST_CAP]:
                lines.append(f"- mesh `{m['name']}`: {m['verts']} verts, "
                             f"dims {m['dims_m']} m, skinned={bool(m['armature_modifier'] and m['vertex_groups'])}")
            lines.append("")
    lines += [
        "## Known blind spots (v1)",
        "",
        "- Shader *semantics*: a shader that decompiled but computes the wrong",
        "  thing (beyond the constant-white dummy signature) is not detected.",
        "- Texture references from scenes/prefabs (only material references",
        "  are followed in v1), and texture *content* (a present file can",
        "  still be the wrong image).",
        "- Animation clips, avatar configuration, and everything Unity does",
        "  at import time — an export can pass here and still fail in-engine.",
        "  Rip Doctor narrows the surprise list; it does not replace opening",
        "  the project.",
        "",
    ]
    return "\n".join(lines), overall


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ripdoctor",
                                 description="Validate an AssetRipper export or FBX.")
    ap.add_argument("target", help="export directory or .fbx file")
    ap.add_argument("--json", dest="json_out", help="write JSON report here")
    ap.add_argument("--md", dest="md_out", help="write Markdown report here")
    ap.add_argument("--fbx", action="store_true",
                    help="(dir mode) also probe FBX files inside the export (max 20)")
    args = ap.parse_args(argv)

    target = Path(args.target).expanduser()
    if not target.exists():
        print(f"error: target does not exist: {target}", file=sys.stderr)
        return 2

    results = []
    if target.is_file() and target.suffix.lower() == ".fbx":
        results.append(check_fbx(target))
    elif target.is_dir():
        results.append(check_project(target))
        if args.fbx:
            fbxs = sorted(target.rglob("*.fbx"))[:20]
            for f in fbxs:
                results.append(check_fbx(f))
    else:
        print(f"error: target is neither an export directory nor an .fbx: {target}",
              file=sys.stderr)
        return 2

    overall = "PASS"
    for result in results:
        md, ov = render_markdown(result)
        print(md)
        overall = worst([overall, ov])
        if args.md_out:
            mode = "a" if len(results) > 1 and result is not results[0] else "w"
            with open(args.md_out, mode, encoding="utf-8") as fh:
                fh.write(md + "\n")
    if args.json_out:
        payload = results[0] if len(results) == 1 else {"targets": results}
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
    return 1 if overall == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
