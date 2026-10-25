#!/usr/bin/env python3
"""填 MakerWorld 模型草稿（/zh/my/models/drafts/<id>/edit）。

前置：先跑 mw.py 把 3mf 传上去拿到 draft id。
本脚本只负责「把 spec 的内容填进表单并存草稿」，不点发布。

实测结构（2026-10 中国站，中文 UI；全球站是英文需另测）：
- 模型名  input[name="title"]                计数 0/50（不是 100）
- 分类    input[type=text] 第 3 个            MUI Autocomplete，必须 press_sequentially
         真实分类是「其他家用模型 / 收纳工具 / 装饰品 …」，没有「家居」这种叫法
- 标签    input[type=text] 第 4 个            计数 0/100，Enter 分隔
- 许可证  input[name="license"] 的 MUI Select，选项是英文全名，见 LICENSE_ALIAS
- 可见性  radio[value="true"] = 公开
- 封面    单文件 image input，DOM 顺序 = 4:3、3:4
- 图片    多文件 image input，顺序 = 实物图、模型图片；
         上传后会弹 cropper 弹窗，不确认会一直挡住后面的操作
- 描述    CKEditor 5（.ck-editor__editable），只能靠 paste 事件塞 HTML
"""
import argparse
import html as htmlmod
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mw import Session, BASE  # noqa: E402

LICENSE_ALIAS = {
    "cc0": "Creative Commons Public Domain",
    "cc by": "Creative Commons Attribution",
    "cc by-sa": "Creative Commons Attribution-Share Alike",
    "cc by-nd": "Creative Commons Attribution-NoDerivatives",
    "cc by-nc": "Creative Commons Attribution-Noncommercial",
    "cc by-nc-sa": "Creative Commons Attribution-Noncommercial-Share Alike",
    "cc by-nc-nd": "Creative Commons Attribution-Noncommercial-NoDerivatives",
    "sdfl": "Standard Digital File License",
}


# ---------------------------------------------------------------- markdown

def md_to_html(md):
    """够用就好的 Markdown 子集 → HTML：标题/粗体/列表/表格/段落。"""
    out, in_ul, in_table = [], False, False

    def close_blocks():
        nonlocal in_ul, in_table
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    def inline(t):
        t = htmlmod.escape(t)
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)

    for raw in md.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            close_blocks()
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue
            if not in_table:
                close_blocks()
                out.append('<table border="1" cellpadding="6" '
                           'style="border-collapse:collapse"><tbody>')
                in_table = True
            out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
            continue
        close_blocks()
        if line.startswith("#### "):
            out.append(f"<h4>{inline(line[5:])}</h4>")
        elif line.startswith("### "):
            out.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif re.match(r"^\s*[-*]\s+", line):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{inline(re.sub(r'^\s*[-*]\s+', '', line))}</li>")
        else:
            out.append(f"<p>{inline(line)}</p>")
    close_blocks()
    return "\n".join(out)


# ---------------------------------------------------------------- helpers

def text_inputs(page):
    return page.locator('input[type="text"], textarea')


def pick_images(page):
    """(封面4:3, 封面3:4, 实物图, 模型图片)。排除富文本编辑器自带的图片上传器。"""
    files = page.locator('input[type="file"]')
    singles, multis = [], []
    for i in range(files.count()):
        el = files.nth(i)
        accept = el.get_attribute("accept") or ""
        if "image" not in accept or "bmp" in accept:
            continue
        (multis if el.get_attribute("multiple") is not None else singles).append(el)
    return (singles[0] if len(singles) > 0 else None,
            singles[1] if len(singles) > 1 else None,
            multis[0] if len(multis) > 0 else None,
            multis[1] if len(multis) > 1 else None)


def dismiss_dialog(page, timeout=6):
    """图片上传后会弹 cropper 弹窗，不处理会挡住后续所有点击。"""
    end = time.time() + timeout
    while time.time() < end:
        d = page.evaluate("""() => {
          const d = [...document.querySelectorAll('.MuiDialog-root')].filter(x => x.offsetParent !== null);
          if (!d.length) return null;
          const btns = [...d[0].querySelectorAll('button')]
            .map(b => (b.innerText || '').trim()).filter(Boolean);
          return {btns: btns, text: (d[0].innerText || '').slice(0, 80)};
        }""")
        if not d:
            return "无弹窗"
        pref = ["确认", "确定", "保存", "完成", "应用", "使用", "Crop", "OK", "Done"]
        for b in d["btns"]:
            if any(p in b for p in pref):
                try:
                    page.locator(".MuiDialog-root button:visible").filter(
                        has_text=b).last.click(timeout=4000)
                    time.sleep(1.2)
                    return f"弹窗已确认「{b}」"
                except Exception:
                    pass
        try:
            page.keyboard.press("Escape")
            time.sleep(0.8)
            return f"弹窗按 Esc（按钮={d['btns']}）"
        except Exception:
            return "弹窗未能关闭"
    return "等弹窗超时"


def choose_license(page, want):
    sel = page.locator('input[name="license"]')
    if not sel.count():
        return "许可证: ❌ 找不到 select"
    target = LICENSE_ALIAS.get(want.strip().lower(), want).lower().replace(" ", "")
    page.locator("div.MuiSelect-select").last.click()
    time.sleep(1.0)
    opts = page.locator('[role="option"]:visible')
    names = []
    for i in range(opts.count()):
        raw = (opts.nth(i).inner_text() or "").replace("\n", " ").strip()
        names.append(raw)
        if raw.replace("本作品的许可依据是", "").strip().lower().replace(" ", "") == target:
            opts.nth(i).click()
            time.sleep(0.8)
            return f"许可证: ✅ {raw}"
    page.keyboard.press("Escape")
    return f"许可证: ❌ 没匹配到「{want}」（目标={target}），可选项={names}"


def image_count(page, label):
    body = page.inner_text("body")
    m = re.search(label + r"\s*\(\s*(\d+)\s*/\s*16", body)
    return int(m.group(1)) if m else None


def fill_section(page, spec, report, skip_images=False):
    # 1) 许可证先设（会触发局部重渲染）
    if spec.get("license"):
        report.append(choose_license(page, spec["license"]))

    # 1.5) 模型来源 —— 必填项，不选的话点「保存草稿」会静默什么都不做
    src = spec.get("source", "原创")
    ok = page.evaluate("""(want) => {
        const labs = [...document.querySelectorAll('label')]
            .filter(l => (l.innerText || '').trim() === want && l.offsetParent !== null);
        if (!labs.length) return false;
        labs[0].click();
        return true;
    }""", src)
    time.sleep(1.0)
    report.append(f"模型来源: {'✅ ' + src if ok else '❌ 找不到「' + src + '」单选（必填，不选存不了草稿）'}")

    # 2) 模型名
    if spec.get("title"):
        ti = page.locator('input[name="title"]')
        ti.click()
        page.keyboard.press("Meta+A")
        ti.fill(spec["title"][:50])
        report.append(f"模型名: ✅ {ti.input_value()}（{len(ti.input_value())}/50）")

    # 3) 分类
    cat = spec.get("category")
    if cat:
        try:
            ci = text_inputs(page).nth(2)
            ci.scroll_into_view_if_needed()
            ci.click()
            time.sleep(0.6)
            ci.press_sequentially(cat, delay=150)
            time.sleep(2.0)
            opts = page.locator('[role="option"]:visible')
            hit = exact = None
            for i in range(opts.count()):
                t = (opts.nth(i).inner_text() or "").strip()
                if t == cat:
                    exact = opts.nth(i)
                    break
                if hit is None and cat in t:
                    hit = opts.nth(i)
            (exact or hit).click()
            report.append(f"分类: ✅ {cat}")
        except Exception as e:
            report.append(f"分类: ❌ {type(e).__name__} {e}")

    # 4) 标签
    tags = spec.get("tags") or []
    if tags:
        try:
            ti = text_inputs(page).nth(3)
            ti.scroll_into_view_if_needed()
            ti.click()
            for t in tags:
                ti.press_sequentially(t, delay=40)
                time.sleep(0.5)
                page.keyboard.press("Enter")
                time.sleep(0.4)
            report.append(f"标签: ✅ {len(tags)} 个")
        except Exception as e:
            report.append(f"标签: ❌ {type(e).__name__} {e}")

    # 5) 可见性
    if spec.get("visibility", "公开") == "公开":
        try:
            page.locator('input[type="radio"][value="true"]').last.click(force=True)
            report.append("可见性: ✅ 公开")
        except Exception as e:
            report.append(f"可见性: ❌ {type(e).__name__}")

    # 6) 描述（放在图片之前：图片会弹 cropper 挡住编辑器）
    desc = spec.get("description")
    if desc:
        html = md_to_html(desc)
        marker = re.sub(r"\s+", "", desc)[:12]
        try:
            ed = page.locator(".ck-editor__editable").first
            ed.scroll_into_view_if_needed()
            ed.click()
            time.sleep(0.5)
            page.keyboard.press("Meta+A")      # 重复跑时先清空，避免描述叠加
            page.keyboard.press("Backspace")
            time.sleep(0.5)
            page.evaluate("""(html) => {
                const el = document.querySelector('.ck-editor__editable');
                el.focus();
                const dt = new DataTransfer();
                dt.setData('text/html', html);
                dt.setData('text/plain', '');
                el.dispatchEvent(new ClipboardEvent('paste',
                    {clipboardData: dt, bubbles: true, cancelable: true}));
            }""", html)
            time.sleep(2.5)
            got = page.evaluate(
                "() => { const e=document.querySelector('.ck-editor__editable');"
                " return e ? e.innerText.replace(/\\s+/g,'') : ''; }")
            ok = marker and marker in got
            report.append(f"描述: {'✅' if ok else '⚠️'} 注入 {len(html)} 字符 → 编辑器内 {len(got)} 字符")
        except Exception as e:
            report.append(f"描述: ❌ {type(e).__name__} {e}")

    if skip_images:
        report.append("图片: 跳过（--skip-images）")
        return

    # 7) 图片
    # 实测：隐藏 input 直接 set_input_files 组件不认（既不报错也不上传），
    # 必须点真实可点区域走 file chooser：
    #   封面 = .dropzone（第 1 个 4:3、第 2 个 3:4）
    #   实物图 / 模型图片 = 「添加照片」（第 1 个、第 2 个）
    def upload_via(locators, files, tag):
        """按候选顺序试，第一个能弹出文件选择器的就赢。

        4:3 封面的 dropzone 直接 click 有时不出 chooser（上面压着透明 label），
        所以额外准备了「按文案点」和「点内部 input」两种兜底。
        """
        if not isinstance(locators, (list, tuple)):
            locators = [locators]
        last = ""
        for i, loc in enumerate(locators):
            try:
                with page.expect_file_chooser(timeout=8000) as fc:
                    loc.click(timeout=5000)
                fc.value.set_files(files)
                time.sleep(6)
                report.append(f"{tag}: ✅ {len(files)} 张" + (f"（候选{i+1}）" if i else ""))
                return True
            except Exception as e:
                last = f"{type(e).__name__} {str(e)[:60]}"
        report.append(f"{tag}: ❌ {last}")
        return False

    if spec.get("cover_4x3"):
        upload_via([page.locator(".dropzone:not(.appUpload):visible").first,
                    page.get_by_text("4:3 封面", exact=True).first],
                   [spec["cover_4x3"]], "封面4:3")
    if spec.get("cover_3x4"):
        upload_via([page.get_by_text("App封面3:4", exact=True).first,
                    page.locator(".dropzone.appUpload:visible").first,
                    page.get_by_text("App优先应用3:4的封面", exact=True).first,
                    page.get_by_text("3 : 4", exact=True).first],
                   [spec["cover_3x4"]], "封面3:4")

    shots = spec.get("photos") or []
    have = image_count(page, "实物图")
    if shots:
        if have and have >= len(shots):
            report.append(f"实物图: 已有 {have} 张，跳过")
        elif upload_via([page.get_by_text("添加照片", exact=True).nth(0)], shots, "实物图"):
            report.append(f"实物图计数: {image_count(page, '实物图')}")

    more = spec.get("model_photos") or []
    if more:
        upload_via([page.get_by_text("添加照片", exact=True).nth(1)], more, "模型图片")


def main():
    ap = argparse.ArgumentParser(description="填 MakerWorld 模型草稿")
    ap.add_argument("--draft", required=True, help="草稿 id（drafts/<id>/edit 里的数字）")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--skip-images", action="store_true")
    ap.add_argument("--hold", type=int, default=0)
    a = ap.parse_args()

    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)

    url = f"{BASE}/zh/my/models/drafts/{a.draft}/edit"
    s = Session(headless=False)
    report = []
    try:
        if not s.logged_in():
            print("❌ 未登录，先跑 mw.py login")
            return 2
        print(f"打开 {url}")
        s.goto(url)
        time.sleep(6)
        fill_section(s.page, spec, report, a.skip_images)
        print("\n".join(report))

        if not a.no_save:
            # 用网络信号判断是否真的保存：点「保存草稿」被遮挡时既不报错也不发请求
            puts = []
            s.page.on("request", lambda r: puts.append(r.url)
                      if r.method == "PUT" and "/draft/" in r.url else None)

            def js_save():
                return s.page.evaluate("""() => {
                    const b = [...document.querySelectorAll('button')]
                        .filter(x => (x.innerText || '').trim() === '保存草稿').pop();
                    if (!b) return false;
                    b.scrollIntoView({block: 'center'});
                    b.click();
                    return true;
                }""")

            saved = False
            for attempt in range(3):
                report.append("保存前清弹窗: " + dismiss_dialog(s.page, 8))
                puts.clear()
                clicked = js_save()
                for _ in range(8):
                    time.sleep(1.5)
                    if puts:
                        break
                if puts:
                    saved = True
                    report.append(f"保存草稿: ✅（第{attempt+1}次，PUT {puts[0].split('/api')[-1]}）")
                    break
                report.append(f"保存第{attempt+1}次无效（没发 PUT 请求）")
            if not saved:
                report.append("保存草稿: ❌ 三次都没发出保存请求")

            s.goto(url)
            time.sleep(6)
            title_now = s.page.locator('input[name="title"]').input_value()
            photos_now = image_count(s.page, "实物图")
            desc_now = s.page.evaluate(
                "() => { const e=document.querySelector('.ck-editor__editable');"
                " return e ? e.innerText.replace(/\\s+/g,'').length : 0; }")
            report.append(f"回读: 标题={title_now!r} 实物图={photos_now} 描述={desc_now}字")

        out = os.path.join(os.path.dirname(a.spec), "draft_fill_report.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"draft": a.draft, "url": url, "steps": report}, f,
                      ensure_ascii=False, indent=2)
        print(f"报告: {out}")
        if a.hold:
            time.sleep(a.hold)
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
