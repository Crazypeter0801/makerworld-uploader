#!/usr/bin/env python3
"""MakerWorld 发布前素材体检 —— 只用标准库。

用法：
    python3 preflight.py <模型.3mf> [--cover 实拍图.jpg] [--out preview.png]

输出：人读摘要 + 末尾一行 JSON（agent 可直接解析）。
退出码：0 = 可以发；1 = 有阻塞项（必须修）；2 = 参数/文件错误。
"""
import argparse
import json
import os
import re
import sys
import zipfile

REQUIRED_3MF = ["[Content_Types].xml", "3D/3dmodel.model"]

# 官方支持的原始模型文件扩展名（wiki.bambulab.com/zh/makerworld/tutorials/how-to-upload-models）
RAW_EXTS = {
    "3ds", "amf", "dwg", "dxf", "f3d", "f3z", "factory", "fcstd", "iges", "ipt",
    "obj", "ply", "py", "rsdoc", "scad", "shape", "shapr", "skp", "sldasm",
    "sldprt", "slvs", "step", "stl", "stp", "studio3", "zip", "3mf", "stpz",
}
# 打印配置（Bambu Studio 项目）只吃 3mf
PROFILE_EXTS = {"3mf"}

# Bambu Studio 的耗材温度分组（随 Studio 安装在 Resources/info/filament_info.json，v1.0.0.4）。
# 云切片报「高低温耗材无法混合打印」= 同一工程同时有 high_temp 与 low_temp 组的耗材。
# 注意：PETG / PETG-CF / PCTG 属于 low_temp，与 PLA 同组，可以混打。
FILAMENT_TEMP_GROUPS = {
    "high_temp_filament": ["ABS", "ASA", "ASA-CF", "PC", "PA", "PA-CF", "PA-GF", "PA6-CF",
                           "PET-CF", "PPS", "PPS-CF", "PPA-CF", "PPA-GF", "ABS-GF", "ASA-AERO"],
    "low_temp_filament": ["PLA", "TPU", "TPU-AMS", "PLA-CF", "PLA-AERO", "PVA", "BVOH",
                          "PCTG", "PETG", "PETG-CF"],
    "high_low_compatible_filament": ["HIPS", "PE", "PP", "EVA", "PE-CF", "PP-CF", "PP-GF", "PHA"],
}
STUDIO_FILAMENT_INFO = [
    "/Applications/BambuStudio.app/Contents/Resources/info/filament_info.json",
    os.path.expandvars(r"%ProgramFiles%\Bambu Studio\resources\info\filament_info.json"),
    "/usr/share/bambu-studio/resources/info/filament_info.json",
]


def temp_groups():
    """优先读本机 Bambu Studio 的最新分组，读不到用内置副本。"""
    for p in STUDIO_FILAMENT_INFO:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if all(k in d for k in FILAMENT_TEMP_GROUPS):
                return {k: d[k] for k in FILAMENT_TEMP_GROUPS}, p
        except (OSError, ValueError):
            continue
    return FILAMENT_TEMP_GROUPS, "builtin"


def temp_group_of(ftype, groups):
    for g, names in groups.items():
        if str(ftype).upper() in {n.upper() for n in names}:
            return g
    return None


# 各机型打印床尺寸（mm），用于判断是否超床 / 离边缘太近触发规避区
BEDS = {
    "Bambu Lab A1 mini": (180, 180),
    "Bambu Lab A1": (256, 256),
    "Bambu Lab P1P": (256, 256),
    "Bambu Lab P1S": (256, 256),
    "Bambu Lab X1": (256, 256),
    "Bambu Lab X1C": (256, 256),
    "Bambu Lab X1E": (256, 256),
    "Bambu Lab P2S": (256, 256),
    "Bambu Lab H2D": (350, 320),
    "Bambu Lab H2S": (340, 320),
}


def read_json_from_zip(z, name):
    try:
        return json.loads(z.read(name).decode("utf-8", "replace"))
    except Exception:
        return None


def parse_project_settings(z):
    """project_settings.config 是 JSON 里的值大多是列表，取首元素方便展示。"""
    d = read_json_from_zip(z, "Metadata/project_settings.config") or {}
    first = lambda v: v[0] if isinstance(v, list) and v else v
    return {
        "printer": first(d.get("printer_model")),
        "nozzle": first(d.get("nozzle_diameter")),
        "layer_height": d.get("layer_height"),
        "filaments": d.get("filament_type"),
        "infill": d.get("sparse_infill_density"),
        "walls": d.get("wall_loops"),
        "bed": d.get("curr_bed_type"),
        "slicer": first(d.get("slicer_version")) or d.get("slicer_version"),
    }


# 官方 FAQ 明确列为「切片验证失败」的错误项：高低温耗材混打、自定义预设
def parse_filament_slots(z):
    """料槽明细：类型 + 预设名 + 喷嘴温度。

    nozzle_temperature 是「每槽 3 项」的扁平数组，不能按槽数直接切。
    """
    d = read_json_from_zip(z, "Metadata/project_settings.config") or {}
    types = d.get("filament_type") or []
    presets = d.get("filament_settings_id") or []
    temps = d.get("nozzle_temperature") or []
    stride = max(1, len(temps) // len(types)) if types else 1
    per_slot = []
    for i, t in enumerate(types):
        try:
            temp = int(float(temps[i * stride]))
        except (IndexError, TypeError, ValueError):
            temp = None
        per_slot.append({
            "slot": i + 1,
            "type": t,
            "preset": presets[i] if i < len(presets) else None,
            "nozzle_temp": temp,
        })
    return per_slot


def parse_plates(z):
    """plate 数量：model_settings.config 里的 <plate> 节点最准。"""
    try:
        ms = z.read("Metadata/model_settings.config").decode("utf-8", "replace")
        n = len(re.findall(r"<plate\b", ms))
        if n:
            return n
    except KeyError:
        pass
    return len([n for n in z.namelist() if re.fullmatch(r"Metadata/plate_\d+\.png", n)])


def parse_plate_json(z):
    """plate_1.json 里有 bbox（毫米）—— 用来判断是否超出打印床。"""
    p = read_json_from_zip(z, "Metadata/plate_1.json") or {}
    bbox = p.get("bbox_all")
    layer = None
    for o in p.get("bbox_objects", []):
        if o.get("name") != "wipe_tower":
            layer = o.get("layer_height")
            break
    return {"bbox_mm": bbox, "plate_layer_height": layer}


def mesh_bbox(z):
    """plate_1.json 缺失时的兜底：从网格顶点 + build 变换估包围盒。

    有些保存方式不写 Metadata/plate_1.json（但 plate_*.png 还在），
    这时只能自己算。多个 <item> 时不做变换，直接给未变换的并集（标注为估算）。
    """
    try:
        main = z.read("3D/3dmodel.model").decode("utf-8", "replace")
    except KeyError:
        return None
    items = re.findall(r'<item\b[^>]*transform="([^"]+)"', main)
    lo = [1e9, 1e9, 1e9]
    hi = [-1e9, -1e9, -1e9]
    got = False
    for n in z.namelist():
        if not (n.startswith("3D/Objects/") and n.endswith(".model")):
            continue
        body = z.read(n).decode("utf-8", "replace")
        for vx, vy, vz in re.findall(
                r'<vertex x="([-\d.eE+]+)" y="([-\d.eE+]+)" z="([-\d.eE+]+)"', body):
            x, y, zz = float(vx), float(vy), float(vz)
            lo = [min(lo[0], x), min(lo[1], y), min(lo[2], zz)]
            hi = [max(hi[0], x), max(hi[1], y), max(hi[2], zz)]
            got = True
    if not got:
        return None
    if len(items) == 1:
        v = [float(t) for t in items[0].split()]
        if len(v) == 12:
            m, t = v[:9], v[9:]
            corners = [(x, y, zz)
                       for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for zz in (lo[2], hi[2])]
            pts = []
            for (x, y, zz) in corners:
                pts.append((m[0] * x + m[1] * y + m[2] * zz + t[0],
                            m[3] * x + m[4] * y + m[5] * zz + t[1],
                            m[6] * x + m[7] * y + m[8] * zz + t[2]))
            lo = [min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts)]
            hi = [max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts)]
    return {"bbox_mm": [lo[0], lo[1], hi[0], hi[1]],
            "height_mm": round(hi[2] - lo[2], 1),
            "bbox_source": "mesh"}


def has_gcode(z):
    """没有 <plate> 的 slice_info 说明这个 3mf 没切片；有 gcode 才算真·打印配置。"""
    gcode = [n for n in z.namelist() if n.startswith("Metadata/plate_") and n.endswith(".gcode")]
    return bool(gcode)


def check(path, cover=None, out=None, expect="profile"):
    report = {"file": os.path.abspath(path), "kind": expect,
              "blockers": [], "warnings": [], "notes": [], "info": {}}
    ext = path.rsplit(".", 1)[-1].lower()
    report["info"]["size_mb"] = round(os.path.getsize(path) / 1048576, 2) if os.path.exists(path) else None

    allowed = PROFILE_EXTS if expect == "profile" else RAW_EXTS
    if ext not in allowed:
        report["blockers"].append(
            f"扩展名 .{ext} 不在「{'打印配置' if expect == 'profile' else '原始模型'}」允许列表里"
            f"（允许：{', '.join(sorted(allowed))}）")

    if not os.path.exists(path):
        report["blockers"].append("文件不存在")
        return report

    if ext == "3mf":
        try:
            z = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            report["blockers"].append("3mf 不是合法 zip（可能下载/导出中断）")
            return report
        names = z.namelist()
        for req in REQUIRED_3MF:
            if req not in names:
                report["blockers"].append(f"缺 {req}，不是合法 3mf")
        if not any(n.endswith(".model") for n in names):
            report["blockers"].append("没有 .model 几何文件")

        info = report["info"]
        info["is_bambu_project"] = "Metadata/project_settings.config" in names
        info.update({k: v for k, v in parse_project_settings(z).items() if v is not None})
        info["plates"] = parse_plates(z)
        pj = parse_plate_json(z)
        if not pj.get("bbox_mm"):
            pj = mesh_bbox(z) or {}
            if pj:
                report["notes"].append(
                    "这个文件没写 Metadata/plate_1.json，外形尺寸由网格顶点 + build 变换估算，"
                    "误差通常在 0.1mm 内")
        info.update({k: v for k, v in pj.items() if v is not None})
        info["sliced"] = has_gcode(z)
        slots = parse_filament_slots(z)
        info["slots"] = slots

        if not info["is_bambu_project"]:
            report["warnings"].append(
                "没有 project_settings.config：这更像「原始模型 3mf」而不是「打印配置」，"
                "上传时选错类型会被判为不合规的打印配置")

        # —— 料槽类问题（官方 FAQ 里明确会导致「切片验证失败」）——
        types = [s["type"] for s in slots if s["type"]]
        groups, group_src = temp_groups()
        info["temp_group_source"] = group_src
        by_group = {}
        for t in set(types):
            by_group.setdefault(temp_group_of(t, groups), []).append(t)
        if by_group.get("high_temp_filament") and by_group.get("low_temp_filament"):
            report["blockers"].append(
                f"高低温耗材混打：高温 {'/'.join(sorted(by_group['high_temp_filament']))} + "
                f"低温 {'/'.join(sorted(by_group['low_temp_filament']))}。"
                "官方 FAQ 明确会报「高低温耗材无法混合打印」—— 去掉其中一组再上传")
        if by_group.get(None):
            report["warnings"].append(
                f"耗材类型 {'/'.join(sorted(by_group[None]))} 不在 Bambu 温度分组表里，无法判断能否混打")
        if len(set(types)) > 1 and not report["blockers"]:
            report["notes"].append(
                f"多种材质同盘（{'/'.join(sorted(set(types)))}）：按 Bambu 分组不算高低温混打，"
                "但层间结合和换料成本仍需确认")

        if len(types) > 1 and len(set(types)) == 1:
            report["warnings"].append(
                f"有 {len(types)} 个料槽但只有一种材质（{'/'.join(sorted(set(types)))}）："
                "多槽通常是从别的项目继承来的，单色打印建议只留 1 槽，否则打印配置会显示一堆无用槽位")

        # 系统预设都带 @BBL（含 Generic / PolyLite 等第三方品牌的官方预设）；
        # 用户另存的预设通常没有，或带 "(1)" / "- Copy" 之类后缀
        custom = [s["preset"] for s in slots
                  if s["preset"] and ("@BBL" not in str(s["preset"])
                                      or re.search(r"\(\d+\)$|copy|副本", str(s["preset"]), re.I))]
        if custom:
            report["warnings"].append(
                f"疑似自定义耗材预设：{custom[:3]}。官方要求使用系统预设，否则校验不通过")
        changed = read_json_from_zip(z, "Metadata/project_settings.config") or {}
        diff = [x for x in (changed.get("different_settings_to_system") or []) if x]
        if diff:
            info["changed_vs_system_preset"] = diff[0].split(";")

        if expect == "profile":
            if not info.get("sliced"):
                report["notes"] = report.get("notes", []) + [
                    "文件里没有切片结果（plate_*.gcode）。这**不是**必须项：MakerWorld 用云切片做验证，"
                    "上传后会按你勾选的机型自己切。本地先切一遍的好处是能提前发现规避区/超时问题，"
                    "且发布后用户端直接显示打印时间与耗材"]

        bbox = info.get("bbox_mm")
        if bbox and len(bbox) == 4:
            w, d = bbox[2] - bbox[0], bbox[3] - bbox[1]
            info["footprint_mm"] = [round(w, 1), round(d, 1)]
            bed = BEDS.get(str(info.get("printer") or ""), (256, 256))
            margins = [bbox[0], bed[0] - bbox[2], bbox[1], bed[1] - bbox[3]]
            info["edge_margin_mm"] = round(min(margins), 1)
            if w > bed[0] or d > bed[1]:
                report["warnings"].append(
                    f"外形 {w:.0f}x{d:.0f}mm 超过 {bed[0]}x{bed[1]} 打印床（{info.get('printer')}），"
                    "上传时注意标注适用机型")
            elif min(margins) < 10:
                report["notes"].append(
                    f"离打印板边缘 {min(margins):.1f}mm，属贴边摆放。若云切片报「超出规避区」，"
                    "把模型往中间挪 3–5mm 或缩小 2% 再上传")

        # plate_1.json 里记录的是「首层」层高（实测：0.12 层高、0.2 首层切片后这里是 0.2），
        # 和 initial_layer_print_height 比，不能和 layer_height 比，否则每个首层更厚的工程都会误报
        plh = info.get("plate_layer_height")
        ph = (read_json_from_zip(z, "Metadata/project_settings.config") or {}).get(
            "initial_layer_print_height") or info.get("layer_height")
        if plh and ph and abs(float(plh) - float(ph)) > 1e-6:
            report["warnings"].append(
                f"plate_1.json 的首层层高 {float(plh):g} 与 project 首层 {ph} 不一致："
                "缩略图/预览是按旧参数生成的，建议重新切片后保存再上传")

        # 文件名里的版本号（层高/填充）先过一眼：批量导出的文件夹里最容易发错版本
        m = re.search(r"(?<![\d.])(0\.\d{1,2})(?=[_\-. ]|$)", os.path.basename(path))
        if m and not 0.04 <= float(m.group(1)) <= 0.4:
            m = None
        if m and info.get("layer_height"):
            try:
                if abs(float(m.group(1)) - float(info["layer_height"])) > 1e-6:
                    report["warnings"].append(
                        f"文件名标的是 {m.group(1)}mm，project 里层高是 {info['layer_height']}mm"
                        " —— 确认要发的是哪一版")
            except (TypeError, ValueError):
                pass

        # 顺手把官方 plate 缩略图抠出来，可作为封面参考（不能当实拍封面用）
        if out and "Metadata/plate_1.png" in names:
            with open(out, "wb") as f:
                f.write(z.read("Metadata/plate_1.png"))
            info["plate_thumb"] = os.path.abspath(out)

    if cover:
        if not os.path.exists(cover):
            report["blockers"].append(f"封面不存在：{cover}")
        else:
            info["cover"] = os.path.abspath(cover)
            report["warnings"].append(
                "封面必须是「真实打印成品实拍图」，渲染图/切片缩略图会被平台要求更换，"
                "或导致打印配置被下架")

    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--cover", help="实拍封面图路径")
    ap.add_argument("--out", help="导出 plate 缩略图路径")
    ap.add_argument("--kind", choices=["profile", "raw"], default="profile",
                    help="profile=打印配置(3mf)，raw=原始模型文件")
    a = ap.parse_args()

    if not os.path.exists(a.file):
        print(f"文件不存在：{a.file}")
        return 2
    r = check(a.file, a.cover, a.out, a.kind)
    print(f"\n=== {os.path.basename(r['file'])} ({r['kind']}) ===")
    for k, v in r["info"].items():
        print(f"  {k}: {v}")
    for b in r["blockers"]:
        print(f"  [阻塞] {b}")
    for w in r["warnings"]:
        print(f"  [注意] {w}")
    for n in r.get("notes", []):
        print(f"  [说明] {n}")
    print("\nJSON " + json.dumps(r, ensure_ascii=False))
    return 1 if r["blockers"] else 0


if __name__ == "__main__":
    sys.exit(main())
