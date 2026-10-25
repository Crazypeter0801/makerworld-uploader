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
            item = re.sub(r'^\s*[-*]\s+', '', line)
            out.append(f"<li>{inline(item)}</li>")
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
    """Only confirm a visible image cropper; never Escape (it discards the image)."""
    dialogs = page.locator('[role="dialog"]:visible, .MuiDialog-root:visible')
    if not dialogs.count():
        return "无弹窗"
    dialog = dialogs.last
    if not dialog.locator('canvas, img, [class*="crop"], [class*="Crop"]').count():
        raise RuntimeError("未知弹窗；保留现场，请检查后继续")
    button = dialog.get_by_role("button", name=re.compile(
        r"^(确认|确定|保存|完成|应用|使用|裁剪|Crop|OK|Done|Save|Confirm)$", re.I))
    if button.count() != 1:
        raise RuntimeError("裁剪确认按钮不明确；保留现场")
    button.click(timeout=timeout * 1000)
    dialog.wait_for(state="hidden", timeout=timeout * 1000)
    return "裁剪已确认"


def cover_state(page, portrait=False):
    selector = ".dropzone.appUpload" if portrait else ".dropzone:not(.appUpload)"
    zone = page.locator(selector).first
    if not zone.count():
        return []
    return zone.evaluate("""el => [...el.querySelectorAll('img'), el,
      ...el.querySelectorAll('*')].flatMap(e => {
        if (e.tagName === 'IMG') return e.complete && e.naturalWidth > 0
          ? [e.currentSrc || e.src] : [];
        const bg = getComputedStyle(e).backgroundImage;
        return bg && bg !== 'none' ? [bg] : [];
      }).filter(s => s && !s.includes('data:image/svg'))""")


def upload_image(page, candidates, files, tag, state, timeout=45):
    """Selection is not success: confirm crop, then require changed preview/count.

    Retry a different click target only BEFORE files have been submitted.
    Unknown post-submission state must not cause duplicate gallery uploads.
    """
    before = state()
    last = None
    for loc in candidates:
        try:
            with page.expect_file_chooser(timeout=2500) as chooser:
                loc.click(timeout=2000)
        except Exception as exc:
            last = exc
            continue
        chooser.value.set_files(files)
        deadline = time.monotonic() + timeout
        stable_since = None
        previous = None
        while time.monotonic() < deadline:
            if page.locator('[role="dialog"]:visible, .MuiDialog-root:visible').count():
                dismiss_dialog(page, 10)
                stable_since = None
            alerts = page.locator('[role="alert"]:visible').all_text_contents()
            if any(re.search(r"失败|错误|过大|不支持|failed|error|too large", x, re.I) for x in alerts):
                raise RuntimeError(f"{tag}: {'; '.join(alerts)}")
            now = state()
            changed = (isinstance(now, int) and now >= (before or 0) + len(files)) or (
                isinstance(now, list) and now and now != before)
            busy = page.locator('[role="progressbar"]:visible, [aria-busy="true"]:visible').count()
            if changed and not busy:
                if now != previous:
                    stable_since = time.monotonic()
                elif stable_since is not None and time.monotonic() - stable_since >= 1:
                    return f"{tag}: 预览已更新（{len(files)} 张，保存后仍需回读）"
                previous = now
            else:
                stable_since = None
                previous = None
            page.wait_for_timeout(200)
        raise RuntimeError(f"{tag}: 已选文件但未确认上传完成；停止重传以免重复，请检查裁剪/网络/预览")
    raise RuntimeError(f"{tag}: 未打开文件选择器：{last}")


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
    src = spec.get("source")
    if not src:
        raise ValueError("缺少 source：请根据用户说明填写原创或二次创作")
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

    # Select by visible label: numeric radio values have differed across versions.
    if spec.get("visibility"):
        page.get_by_label(spec["visibility"], exact=True).check()
        report.append(f"可见性: {spec['visibility']}")

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

    # Each upload resolves its own cropper before the next control is clicked.
    if spec.get("cover_4x3"):
        report.append(upload_image(page, [page.get_by_text("4:3 封面", exact=True).first,
            page.locator(".dropzone:not(.appUpload):visible").first],
            [spec["cover_4x3"]], "封面4:3", lambda: cover_state(page)))
    if spec.get("cover_3x4"):
        report.append(upload_image(page, [page.get_by_text("App封面3:4", exact=True).first,
            page.locator(".dropzone.appUpload:visible").first],
            [spec["cover_3x4"]], "封面3:4", lambda: cover_state(page, True)))
    for key, label, index in (("photos", "实物图", 0), ("model_photos", "模型图片", 1)):
        files = spec.get(key) or []
        if not files:
            continue
        have = image_count(page, label)
        if have is None:
            raise RuntimeError(f"{label}: 找不到计数，先 discover，不盲目上传")
        if have:
            raise RuntimeError(f"{label}: 已有 {have} 张；先核对内容，用 --skip-images 保留，或在页面处理后重跑")
        # Sequential selection avoids stacked multi-image croppers.
        for file in files:
            report.append(upload_image(page,
                [page.get_by_text("添加照片", exact=True).nth(index)],
                [file], label, lambda: image_count(page, label)))


def save_and_verify(page, spec, url, report):
    failed = [step for step in report if '❌' in step]
    if failed:
        raise RuntimeError('表单尚未完成：' + '; '.join(failed))
    dismiss_dialog(page)
    expected_counts = {label: image_count(page, label) for label in ("实物图", "模型图片")}
    with page.expect_response(lambda r: r.request.method in ("PUT", "POST")
                              and "/draft/" in r.url, timeout=20000) as pending:
        page.get_by_role("button", name="保存草稿", exact=True).click()
    response = pending.value
    if not response.ok:
        raise RuntimeError(f"草稿保存失败 HTTP {response.status}")
    # HTTP success alone is insufficient. Check the persisted form.
    page.goto(url, wait_until="domcontentloaded")
    page.locator('input[name="title"]').wait_for()
    page.wait_for_timeout(1500)
    if spec.get("title") and page.locator('input[name="title"]').input_value() != spec["title"][:50]:
        raise RuntimeError("保存后标题回读不一致")
    for key, portrait in (("cover_4x3", False), ("cover_3x4", True)):
        if spec.get(key):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not cover_state(page, portrait):
                page.wait_for_timeout(200)
            if not cover_state(page, portrait):
                raise RuntimeError(f"保存后 {key} 无图片预览")
    for label, expected in expected_counts.items():
        if expected is not None and image_count(page, label) != expected:
            raise RuntimeError(f"保存后{label}数量不一致")
    if spec.get("description"):
        got = page.locator('.ck-editor__editable').first.inner_text()
        plain = re.sub('<[^>]+>', '', md_to_html(spec['description']))
        if re.sub(r"\s+", "", htmlmod.unescape(plain)) != re.sub(r"\s+", "", got):
            raise RuntimeError("保存后描述回读不一致")
    report.append("草稿保存成功；已回读标题、封面预览、图片数量和描述（不代表已发布）")


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
            save_and_verify(s.page, spec, url, report)

        out = os.path.join(os.path.dirname(a.spec), "draft_fill_report.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"draft": a.draft, "url": url, "steps": report}, f,
                      ensure_ascii=False, indent=2)
        print(f"报告: {out}")
        if a.hold:
            time.sleep(a.hold)
        return 0
    except Exception as exc:
        from mw import OUT_DIR, ensure_dirs, save
        ensure_dirs()
        s.page.screenshot(path=os.path.join(OUT_DIR, "draft_failure.png"), full_page=True)
        save("draft_failure.json", {"draft": a.draft, "url": s.page.url,
                                    "steps": report, "error": str(exc)})
        print(f"未完成：{exc}", file=sys.stderr)
        return 3
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
