#!/usr/bin/env python3
"""MakerWorld（makerworld.com.cn）发布助手 —— 浏览器会话驱动。

为什么必须走浏览器：站点在 HTML 路由上挂了 Cloudflare 人机校验，
纯 HTTP（requests/curl）直接 403；Playwright 的 headless chromium 也会被拦。
实测唯一稳定通路是 **真实 Chrome + 有头模式 + 持久化 profile**。

命令：
    mw.py login     首次登录（弹出 Chrome，人工扫码/输密码，检测到登录态后自动收起）
    mw.py whoami    确认当前登录账号与 handle
    mw.py discover  打开上传页，把表单结构 dump 成 JSON（首次接入时用它拿真实选择器）
    mw.py upload    按 spec.json 走一遍上传，默认只存草稿
    mw.py shot      截当前页面

依赖：playwright（`uv run --with playwright python mw.py ...` 即可，无需装浏览器）
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# 运行态数据（浏览器 profile / 截图 / dump）不放 skill 目录，
# 这样 skill 本身可以直接打包分发，不会带上别人的登录态。
HOME = os.environ.get("MAKERWORLD_HOME") or os.path.expanduser("~/.makerworld-publish")
PROFILE_DIR = os.path.join(HOME, "profile")
OUT_DIR = os.path.join(HOME, "out")
BASE = "https://makerworld.com.cn"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
# 实测：登录后 cookie 里会出现 mw_token / mw_session 之一
AUTH_COOKIES = {"mw_token", "mw_session", "token", "sessionid"}


def log(*a):
    print(*a, flush=True)


def ensure_dirs():
    os.makedirs(OUT_DIR, exist_ok=True)


# ---------------------------------------------------------------- selectors

def by_text(page, pattern, tag="*"):
    """按可见文案模糊定位。中文表单最稳的兜底手段。

    div/span 上 has_text 会同时命中所有祖先节点，DOM 顺序里越靠后越靠近叶子，
    所以容器类标签取 .last；button/a 这类本身就是叶子，取 .first。
    """
    import re
    rx = re.compile(pattern, re.I)
    loc = page.locator(f"{tag}:visible").filter(has_text=rx)
    return loc.first if tag in ("button", "a", "label") else loc.last


def find_input(page, hints):
    """按 placeholder / name / aria-label / 邻近 label 文本找输入框。"""
    for h in hints:
        for sel in (f'input[placeholder*="{h}"]', f'textarea[placeholder*="{h}"]',
                    f'input[name*="{h}"]', f'[aria-label*="{h}"]'):
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                return loc
    return None


def find_file_input(page):
    loc = page.locator('input[type="file"]')
    matches = [loc.nth(i) for i in range(loc.count())
               if '.3mf' in (loc.nth(i).get_attribute('accept') or '').lower()]
    if len(matches) == 1:
        return matches[0]
    return None


def dump_form(page):
    """把页面上的可交互元素结构化，供 agent 决定怎么填。"""
    return page.evaluate("""() => {
      const vis = el => { const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
      const txt = el => (el.innerText || el.value || '').trim().slice(0, 120);
      const fields = [...document.querySelectorAll('input,textarea,select,[contenteditable="true"]')]
        .filter(vis).map(el => ({
          tag: el.tagName.toLowerCase(), type: el.type || null,
          name: el.name || null, id: el.id || null,
          placeholder: el.placeholder || null,
          aria: el.getAttribute('aria-label'),
          value: (el.value || '').slice(0, 80),
          maxlength: el.maxLength > 0 ? el.maxLength : null,
          contenteditable: el.getAttribute('contenteditable'),
          label: (el.labels && el.labels[0] ? el.labels[0].innerText : null)
                 || (el.closest('div') ? txt(el.closest('div')).slice(0, 60) : null)
        }));
      const buttons = [...document.querySelectorAll('button,[role="button"],a.button')]
        .filter(vis).map(el => ({text: txt(el), disabled: el.disabled === true}))
        .filter(b => b.text).slice(0, 60);
      const files = [...document.querySelectorAll('input[type=file]')].length;
      const heads = [...document.querySelectorAll('h1,h2,h3,label,.title')]
        .filter(vis).map(txt).filter(t => t).slice(0, 60);
      return {url: location.href, files, fields, buttons, heads,
              body: (document.body.innerText || '').slice(0, 1500)};
    }""")


# ---------------------------------------------------------------- browser

class Session:
    def __init__(self, headless=False, har=None):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.har = har
        ctx_args = dict(
            user_data_dir=PROFILE_DIR,
            headless=headless,
            channel="chrome",          # 真实 Chrome：headless chromium 会被 Cloudflare 拦
            locale="zh-CN",
            viewport={"width": 1440, "height": 940},
            user_agent=UA,
            args=["--disable-blink-features=AutomationControlled"],
        )
        if har:
            ctx_args["record_har_path"] = har
        self.ctx = self.pw.chromium.launch_persistent_context(**ctx_args)
        self.ctx.set_default_timeout(20000)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def cookie_names(self):
        return {c["name"] for c in self.ctx.cookies() if "makerworld" in c.get("domain", "")}

    def logged_in(self):
        return bool(self.cookie_names() & AUTH_COOKIES)

    def goto(self, url):
        r = self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(2.5)  # 让前端水合
        return r

    def handle(self):
        """当前账号的 handle。

        实测：`/zh/upload` 登录后也是 404，不存在「自动跳转拿 handle」这回事。
        可靠来源是个人页链接 —— `/{lang}/@<handle>/browsing-history` 这类只有自己看得到的入口。
        部分账号 handle 是纯数字（如 /@291651172），所以不能用正则限死字母。
        """
        for c in self.ctx.cookies():
            if c["name"] in ("mw_handle", "handle", "user_handle") and c.get("value"):
                return c["value"]
        if not self.logged_in():
            return None
        if "/@" not in self.page.url:
            try:
                self.goto(f"{BASE}/zh")
            except Exception:
                return None
        try:
            hrefs = self.page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.getAttribute('href'))")
            for h in hrefs:
                m = re.search(r"/@([^/]+)/(?:browsing-history|collections|upload)", h or "")
                if m and m.group(1) not in ("", "user"):
                    return m.group(1)
        except Exception:
            pass
        return None

    def uid(self):
        try:
            return self.page.evaluate("() => localStorage.getItem('uid')")
        except Exception:
            return None

    def close(self):
        try:
            self.ctx.close()
        finally:
            self.pw.stop()


def save(name, data):
    ensure_dirs()
    p = os.path.join(OUT_DIR, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return p


# ---------------------------------------------------------------- commands

def cmd_login(a):
    s = Session(headless=False)
    try:
        log("正在打开 MakerWorld…（首次需要人工登录，登录成功后自动退出）")
        s.goto(f"{BASE}/zh")
        deadline = time.time() + a.timeout
        while time.time() < deadline:
            if s.logged_in():
                s.goto(f"{BASE}/zh")
                h = s.handle()
                log(f"✅ 已登录。cookies={sorted(s.cookie_names())}")
                log(f"   handle={h}")
                save("login.json", {"logged_in": True, "handle": h,
                                    "cookies": sorted(s.cookie_names())})
                return 0
            time.sleep(3)
        log("⌛ 超时未检测到登录态。若页面显示已登录，请把 cookie 名反馈给 skill 维护者。")
        log(f"   当前 cookies={sorted(s.cookie_names())}")
        return 1
    finally:
        s.close()


def cmd_whoami(a):
    s = Session(headless=False)
    try:
        s.goto(f"{BASE}/zh")
        info = {"logged_in": s.logged_in(), "cookies": sorted(s.cookie_names()),
                "handle": s.handle(), "url": s.page.url, "title": s.page.title()}
        log(json.dumps(info, ensure_ascii=False, indent=2))
        return 0 if info["logged_in"] else 1
    finally:
        s.close()


def cmd_discover(a):
    s = Session(headless=False, har=a.har)
    try:
        url = a.url
        if url is None:
            url = f"{BASE}/zh/my/models/publish"
        log(f"打开 {url}")
        r = s.goto(url)
        log(f"HTTP {r.status if r else '?'} | {s.page.title()} | {s.page.url}")
        time.sleep(a.wait)
        data = dump_form(s.page)
        data["logged_in"] = s.logged_in()
        data["cookies"] = sorted(s.cookie_names())
        data["http_status"] = r.status if r else None
        p = save("discover.json", data)
        ensure_dirs()
        png = os.path.join(OUT_DIR, "discover.png")
        s.page.screenshot(path=png, full_page=True)
        log(f"\n字段 {len(data['fields'])} 个 / 按钮 {len(data['buttons'])} 个 / file input {data['files']} 个")
        log(f"结构 → {p}\n截图 → {png}")
        if a.hold:
            log(f"保持打开 {a.hold}s 供人工操作…")
            time.sleep(a.hold)
        return 0
    finally:
        s.close()


def _fill(page, hints, value, label):
    if not value:
        return f"{label}: 跳过（spec 未提供）"
    loc = find_input(page, hints)
    if loc is None:
        return f"{label}: ❌ 找不到输入框（hints={hints}）"
    try:
        loc.click()
        page.keyboard.press("Meta+A")
        loc.type(value, delay=8)
        return f"{label}: ✅"
    except Exception as e:
        return f"{label}: ❌ {type(e).__name__} {e}"


def cmd_upload(a):
    with open(a.spec, encoding="utf-8") as f:
        spec = json.load(f)
    s = Session(headless=False, har=a.har)
    report = {"steps": [], "unresolved": [], "draft_url": None}
    try:
        if not s.logged_in():
            s.goto(f"{BASE}/zh")
        if not s.logged_in():
            log("❌ 未登录。先跑：mw.py login")
            return 2
        url = spec.get("upload_url") or f"{BASE}/zh/my/models/publish"
        log(f"上传页：{url}")
        s.goto(url)
        page = s.page

        # 1) 投文件（第一个 file input 就是模型/打印配置投放区）
        files = [os.path.abspath(p) for p in spec.get("files", [])]
        if not files:
            raise ValueError("spec.files 不能为空")
        missing = [p for p in files if not os.path.exists(p)]
        if missing:
            log(f"❌ 文件不存在：{missing}")
            return 2
        fi = find_file_input(page)
        if fi is None:
            raise RuntimeError("没找到文件输入框，请 discover 检查打印配置入口")
        else:
            fi.set_input_files(files)
            report["steps"].append(f"投递文件 {len(files)} 个")
            log(f"已投递：{[os.path.basename(p) for p in files]}")

        # Wait for the draft route; static wizard headings are not completion signals.
        page.wait_for_url(re.compile(r"/my/models/drafts/[^/]+/edit"),
                          timeout=a.upload_timeout * 1000)
        report["draft_url"] = page.url
        report["draft_id"] = page.url.split("/drafts/")[1].split("/")[0]
        save("upload_report.json", report)  # checkpoint before form filling
        from draft_fill import fill_section, save_and_verify
        page.locator('input[name="title"]').wait_for()
        fill_section(page, spec, report["steps"])
        save_and_verify(page, spec, report["draft_url"], report["steps"])

        # 7) 收尾：默认只存草稿
        ensure_dirs()
        png = os.path.join(OUT_DIR, "upload_final.png")
        page.screenshot(path=png, full_page=True)
        report["screenshot"] = png

        log("\n".join(report["steps"]))
        if report["unresolved"]:
            log("未解决：")
            for u in report["unresolved"]:
                log(f"  - {u}")
        log(f"截图：{png}")
        if a.hold:
            log(f"保持打开 {a.hold}s，人工检查后再关闭…")
            time.sleep(a.hold)
        save("upload_report.json", report)
        return 0 if not report["unresolved"] else 3
    except Exception as exc:
        report["unresolved"].append(str(exc))
        report["current_url"] = s.page.url
        save("upload_report.json", report)
        s.page.screenshot(path=os.path.join(OUT_DIR, "upload_failure.png"), full_page=True)
        log(f"未完成：{exc}；已有草稿请用 draft_fill.py --draft 续传，勿重复创建")
        return 3
    finally:
        s.close()


def cmd_shot(a):
    s = Session(headless=False)
    try:
        if a.url:
            s.goto(a.url)
        ensure_dirs()
        p = a.out or os.path.join(OUT_DIR, "shot.png")
        s.page.screenshot(path=p, full_page=a.full)
        log(p)
        return 0
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description="MakerWorld 发布助手")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login"); p.add_argument("--timeout", type=int, default=300)
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("whoami"); p.set_defaults(fn=cmd_whoami)

    p = sub.add_parser("discover")
    p.add_argument("--url"); p.add_argument("--wait", type=int, default=6)
    p.add_argument("--hold", type=int, default=0); p.add_argument("--har")
    p.set_defaults(fn=cmd_discover)

    p = sub.add_parser("upload")
    p.add_argument("--spec", required=True)
    p.add_argument("--upload-timeout", type=int, default=180)
    p.add_argument("--hold", type=int, default=0)
    p.add_argument("--har")
    p.set_defaults(fn=cmd_upload)

    p = sub.add_parser("shot")
    p.add_argument("--url"); p.add_argument("--out"); p.add_argument("--full", action="store_true")
    p.set_defaults(fn=cmd_shot)

    a = ap.parse_args()
    ensure_dirs()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
