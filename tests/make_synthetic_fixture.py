#!/usr/bin/env python3
"""
Build a SYNTHETIC fixture that reproduces the dummy-shader failure class.

The real-world export that first exposed this class is not redistributable,
so it cannot serve as a test fixture. This synthetic export reproduces its
signature exactly: a Toony Colors Pro 2 shader exported by AssetRipper as a
DummyShaderTextExporter stub returning pure white, with materials on it. It
also plants one empty-primary-map material, one dangling texture reference,
and one zero-byte texture.

Everything under the output dir is fake and labeled SYNTHETIC.
"""
import sys
from pathlib import Path

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/ripdoctor-synthetic")

TCP2_GUID = "aaaa0000000000000000000000000001"
GOOD_SHADER_GUID = "aaaa0000000000000000000000000002"
GOOD_TEX_GUID = "bbbb0000000000000000000000000001"
ZERO_TEX_GUID = "bbbb0000000000000000000000000002"
MISSING_TEX_GUID = "bbbb0000000000000000000000000999"  # never written to disk

META = "fileFormatVersion: 2\nguid: {guid}\nlicenseType: Free\n"


def write(rel, content, guid=None):
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content)
    if guid:
        Path(str(p) + ".meta").write_text(META.format(guid=guid))
    return p


TCP2_SHADER = '''Shader "Toony Colors Pro 2/Standard" {
\tProperties {
\t\t_MainTex ("Main Texture", 2D) = "white" {}
\t\t_Color ("Color", Vector) = (1,1,1,1)
\t\t_Ramp ("Ramp", 2D) = "white" {}
\t}
\t//DummyShaderTextExporter
\tSubShader{
\t\tTags { "RenderType" = "Opaque" }
\t\tLOD 200
\t\tPass
\t\t{
\t\t\tHLSLPROGRAM
\t\t\t#pragma vertex vert
\t\t\t#pragma fragment frag
\t\t\tfloat4x4 unity_ObjectToWorld;
\t\t\tfloat4x4 unity_MatrixVP;
\t\t\tstruct Vertex_Stage_Input { float4 pos : POSITION; };
\t\t\tstruct Vertex_Stage_Output { float4 pos : SV_POSITION; };
\t\t\tVertex_Stage_Output vert(Vertex_Stage_Input input)
\t\t\t{
\t\t\t\tVertex_Stage_Output output;
\t\t\t\toutput.pos = mul(unity_MatrixVP, mul(unity_ObjectToWorld, input.pos));
\t\t\t\treturn output;
\t\t\t}
\t\t\tfloat4 frag(Vertex_Stage_Output input) : SV_TARGET
\t\t\t{
\t\t\t\treturn float4(1.0, 1.0, 1.0, 1.0);
\t\t\t}
\t\t\tENDHLSL
\t\t}
\t}
}
'''

GOOD_SHADER = '''Shader "Custom/GoodLit" {
\tProperties {
\t\t_MainTex ("Albedo", 2D) = "white" {}
\t\t_BumpMap ("Normal", 2D) = "bump" {}
\t}
\tSubShader {
\t\tTags { "RenderType" = "Opaque" }
\t\tPass {
\t\t\tCGPROGRAM
\t\t\t#pragma vertex vert
\t\t\t#pragma fragment frag
\t\t\tsampler2D _MainTex;
\t\t\tfloat4 frag(float2 uv : TEXCOORD0) : SV_Target
\t\t\t{
\t\t\t\treturn tex2D(_MainTex, uv);
\t\t\t}
\t\t\tENDCG
\t\t}
\t}
}
'''


def material(name, shader_guid, texenvs):
    lines = [
        "%YAML 1.1",
        "%TAG !u! tag:unity3d.com,2011:",
        "--- !u!21 &2100000",
        "Material:",
        "  serializedVersion: 8",
        f"  m_Name: {name}",
        f"  m_Shader: {{fileID: 4800000, guid: {shader_guid}, type: 3}}",
        "  m_SavedProperties:",
        "    serializedVersion: 3",
        "    m_TexEnvs:",
    ]
    for slot, guid in texenvs:
        if guid:
            lines.append(f"      {slot}:")
            lines.append(f"        m_Texture: {{fileID: 2800000, guid: {guid}, type: 3}}")
            lines.append("        m_Scale: {x: 1, y: 1}")
            lines.append("        m_Offset: {x: 0, y: 0}")
        else:
            lines.append(f"      {slot}:")
            lines.append("        m_Texture: {fileID: 0}")
            lines.append("        m_Scale: {x: 1, y: 1}")
            lines.append("        m_Offset: {x: 0, y: 0}")
    lines += ["    m_Floats: {}", "    m_Colors: {}", ""]
    return "\n".join(lines)


def main():
    write("Assets/Shader/ToonyColorsPro2.shader", TCP2_SHADER, TCP2_GUID)
    write("Assets/Shader/GoodLit.shader", GOOD_SHADER, GOOD_SHADER_GUID)
    # three materials on the dummy TCP2 shader (the SRI class)
    for i, name in enumerate(["holly_body", "holly_hair", "sam_body"]):
        write(f"Assets/Material/{name}.mat",
              material(name, TCP2_GUID,
                       [("_MainTex", GOOD_TEX_GUID), ("_Ramp", None)]))
    # one material on the good shader with its primary map left empty
    write("Assets/Material/flat_prop.mat",
          material("flat_prop", GOOD_SHADER_GUID,
                   [("_MainTex", None), ("_BumpMap", None)]))
    # one material referencing a texture that is not in the export
    write("Assets/Material/broken_ref.mat",
          material("broken_ref", GOOD_SHADER_GUID,
                   [("_MainTex", MISSING_TEX_GUID)]))
    # one healthy material
    write("Assets/Material/healthy.mat",
          material("healthy", GOOD_SHADER_GUID,
                   [("_MainTex", GOOD_TEX_GUID)]))
    # textures: one real (non-empty), one zero-byte
    write("Assets/Texture2D/body_diff.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
          GOOD_TEX_GUID)
    write("Assets/Texture2D/empty_tex.png", b"", ZERO_TEX_GUID)
    write("SYNTHETIC.txt",
          "SYNTHETIC FIXTURE — not a real rip. Reproduces the SRI/TCP2 "
          "dummy-shader class for Rip Doctor validation.\n")
    print(f"synthetic fixture written to {OUT}")


if __name__ == "__main__":
    main()
