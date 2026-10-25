---
name: makerworld-publish
description: 把 3D 模型发布到 MakerWorld（makerworld.com.cn 中国站 / makerworld.com 全球站）。当用户要求「上传/发布模型到 MakerWorld」「把 3mf 发到拓竹」「帮我写发布标题描述标签」「批量上架自己的模型」时使用。含 3mf 打印配置的发布前体检（是否已切片、文件名与参数是否一致、打印床尺寸、耗材/喷嘴）、Cloudflare 环境下的登录态维护、上传向导驱动（可自适应 UI 改版）、草稿优先的发布流程，以及官方字段（原创/二次创作、分类、许可证、可见性）填写要求。
---

# MakerWorld 发布

## 一句话结论

**MakerWorld 没有公开的上传 API。** 官方 wiki 只描述网页端手工上传；第三方提供的「MakerWorld API」都是只读目录接口。
所以本 skill 走 **真实 Chrome + 持久化 profile 驱动网页** —— 实测唯一稳定通路。

## 平台事实（2026-10 实测）

| 事实 | 证据 |
|---|---|
| HTML 路由有 Cloudflare 人机校验 | `curl` 直接访问 → **403** + 「请稍候…」挑战页 |
| headless 浏览器**会被拦** | Playwright headless chromium，UA 齐全 → 仍 403 |
| **真实 Chrome + 有头 + 持久 profile 能过** | `channel="chrome", headless=False` → 200，拿到 `cf_clearance` |
| 内部 API 同域 `/api/v1/...` | 首页请求可见 `search-service`、`put-service`、`/api/csrf` |
| 上传向导的入口是 **`/zh/my/models/publish`** | `/{lang}/@{handle}/upload` 只是「我的已发布」列表页；`/zh/upload` 是 **404** |
| `/_next/static/**` 不校验 | curl 可直取，可用来逆向页面 chunk |

**推论：不要用 requests/curl 直连 `/api/v1/` 做上传。** 就算逆出接口，`cf_clearance` + 登录 cookie + CSRF 也只能由浏览器产生，外挂 HTTP 客户端成本远高于收益。

## 安装 / 依赖

- 需要 **Google Chrome**（`channel="chrome"`，不用装 Playwright 自带浏览器）
- 需要 **uv**（`uv run --with playwright python ...` 会自动装 playwright）
- 纯 Python 3.9+，无其它依赖

放到 skills 目录（用户级 `~/.workbuddy/skills/` 或项目级 `<project>/.workbuddy/skills/`）即可被发现。

运行态数据写在 `~/.makerworld-publish/`（可用环境变量 `MAKERWORLD_HOME` 改），**不写进 skill 目录**，所以整个 skill 目录可直接打包分发。

```
~/.makerworld-publish/
├── profile/        Chrome profile（登录态、cf_clearance）
└── out/            截图、discover dump、上传报告
```

## 隐私

- 不收集、不外传任何数据；所有请求只发往 makerworld.com.cn / makerworld.com
- 登录态只存在本机 `~/.makerworld-publish/profile/`，**不在 skill 目录里**，分发时不会带上
- `login.json` 只记录「是否已登录 + handle + cookie 名」，**不写入 cookie 值**
- 如果要把 skill 分享给别人：直接打包整个目录即可，不含任何账号信息

## 能力边界

**能做**：登录态维护 / 3mf 体检 / 上传文件 / 填标题·描述·标签·分类·许可证 / 存草稿 / 全页截图留证 / 抓页面结构自适应改版

**不做**：不自动点「发布」（除显式 `--publish --yes` 且 spec 里 `publish: true`）；不伪造封面；不刷量、不绕风控

## 首次接入（只做一次）

```bash
cd <skill>/scripts
uv run --quiet --with playwright python mw.py login    # 弹 Chrome 人工登录，检测到登录态自动退出
uv run --quiet --with playwright python mw.py whoami   # 应输出 logged_in: true
```

`login` 成功后写 `~/.makerworld-publish/out/login.json`。profile 别删 —— 删了要重新登录，Cloudflare 也要重新过一次。

## 发布流程

### 1. 体检素材（必须先跑）

```bash
python3 scripts/preflight.py <模型.3mf> [--cover 实拍.jpg] [--out /tmp/plate_thumb.png]
```

末尾一行是 JSON，`blockers` 非空就别往下走。重点看：

| 字段 | 含义 |
|---|---|
| `blockers` 非空 | 一定过不了云切片，别浪费时间上传 |
| 高低温耗材混打 | **最常见的硬错误**。官方 FAQ：高低温耗材无法混合打印。料槽里同时有 PLA 和 PETG-CF/ABS/PC/PA 就会失败 |
| 非 Bambu 官方预设 | 自定义耗材/打印机预设会被判不合规，要求改回官方预设 |
| 文件名 vs `layer_height` 不一致 | 批量导出时最常发错版本 |
| `plate_layer_height` ≠ `layer_height` | 预览/缩略图是旧参数渲染的 |
| `footprint_mm` / `edge_margin_mm` | 超床只能大机型打；贴边（<10mm）可能报「超出规避区」 |

**关于 `sliced: false`**：这**不是**必须项。MakerWorld 上传后用**云切片**做兼容性验证（官方原文：「打印配置需通过 MakerWorld 云切片验证才可成功上线」），平台自己会切。
本地先切一遍的价值是：提前发现规避区/时长问题、发布后用户端直接显示打印时间与耗材。所以 `sliced` 只作为 `notes` 提示，不阻塞。

### 2. 写 spec

复制 `references/spec-template.json`，按 `references/publish-checklist.md` 填。标题 ≤ 100 字符（表单 maxlength 实测）。

### 3. 上传 3mf 拿草稿 id

向导三步：**1 上传 → 2 模型信息 → 3 打印配置**。

```bash
uv run --quiet --with playwright python mw.py upload --spec spec.json --hold 120
```

走到的地址会变成 `/zh/my/models/drafts/<id>/edit`，把 `<id>` 记下来。

### 4. 填草稿

```bash
uv run --quiet --with playwright python draft_fill.py --draft <id> --spec spec.json
```

实测要点（都踩过）：

| 环节 | 真实情况 |
|---|---|
| 模型来源 | 「原创/二次创作」是**必填**，不选时点「保存草稿」**静默失败**（无报错、无请求） |
| 模型名 | `input[name="title"]`，上限 **50 字**（不是 100） |
| 分类 | 第 3 个文本框，MUI Autocomplete，**必须 `press_sequentially`**；`type()` 不触发；真实分类是「其他家用模型 / 收纳工具 / 装饰品…」，**没有「家居」** |
| 标签 | 第 4 个文本框，Enter 分隔，上限 100 字 |
| 许可证 | `input[name="license"]` 的 MUI Select，选项是英文全名（`Creative Commons Attribution-Share Alike` = CC BY-SA） |
| 可见性 | `radio[value="true"]` = 公开 |
| 封面 | `.dropzone`：4:3 的点击要按文案「4:3 封面」点，直接点 div 不一定弹 chooser |
| 实物图 / 模型图片 | 隐藏 input 直接 `set_input_files` **不生效**（组件不认），必须点「添加照片」走 `expect_file_chooser` |
| 描述 | CKEditor 5，只能 `dispatchEvent(new ClipboardEvent('paste'))` 塞 HTML；Markdown 必须先转；重复跑要**先 Cmd+A 清空**，否则叠加 |

脚本默认只**存草稿**，末尾会重新打开页面回读一遍（标题/实物图数/描述字数）——因为「保存草稿」被遮挡时点了也不报错，只有回读能证明真落盘。

### 5. UI 改版了

```bash
uv run --quiet --with playwright python mw.py discover --wait 6
```

产出 `~/.makerworld-publish/out/discover.json`（所有 input/textarea/button + 邻近 label 文案 + 截图）。
拿真实结构改 `mw.py` 里的 hints。**先 dump 再改，不要凭猜。**

## 硬坑清单

1. **别用 headless。** Cloudflare 会拦，改成无头只是白花一次调试时间。
2. **必须 `channel="chrome"`。** Playwright 自带 chromium 的指纹会被挑战页卡住。
3. **上传向导是 `/zh/my/models/publish`。** `/zh/@{handle}/upload` 只是列表页，`/zh/upload` 是 404。handle 要从 `/{lang}/@<handle>/browsing-history` 这类「只有自己看得到」的链接里取，首页那些 `/@某人/upload` 是别人的模型卡片，别拿来当自己。
4. **`has_text` 在 `div` 上会命中所有祖先节点**，容器类选择器取 `.last`，按钮才取 `.first`。
4b. **保存草稿会「安静地失败」**：必填项没填时点下去既不报错也不发请求。唯一的验证方式是保存后重开页面回读。
5. **打印配置 ≠ 原始模型文件。** 3mf 走「打印配置」通道；STL/STEP/OBJ 等走「原始模型文件」通道，选错会被下架。允许的扩展名见 `preflight.py` 的 `RAW_EXTS`（**没有 .rar/.7z/.gcode**）。
6. **封面必须是真实打印成品照。** 渲染图能过初审，但打印配置容易因「无实拍图」被下架。
7. **上传后有平台切片校验**，几秒到几分钟。`--upload-timeout` 默认 180s，大模型要加。
8. **草稿优先。** 发布是公开且不可撤回的动作，agent 永远停在草稿。
9. **同账号并发上传会被限流。** 批量发布要串行 + 间隔。

## 官方云切片会卡什么（摘自官方 FAQ，2026-10 复核）

上传后平台按你勾选的机型做云切片验证，下面这些会直接判失败：

| 判失败的原因 | 处理 |
|---|---|
| 高低温耗材混打（PLA + ABS/PC/PA/PETG-CF…） | 料槽清成同一温度区间 |
| 自定义耗材/打印机预设 | 换回 Bambu 官方预设 |
| 超出规避区（贴边、支撑或擦料塔越界） | 模型往中间挪；擦料塔与耗材清理量恢复默认；或改用 H2 系列 |
| 云切片超时（每盘 5 分钟 / 3mf 15 分钟） | 拆盘，或改用「文件」形式上传 |
| 3mf 过大解析失败（显示 0KB） | 用 Bambu Studio 打开后 Ctrl+S 重存再传 |

**注意**：上传后系统会自动屏蔽不兼容机型，页面上的机型列表有误导性 —— 要发布的机型请自己手动切换逐个测。

## 合规

- 二次创作必须在描述里写清改了什么，且源模型要允许改编
- 不自动化刷印量/点赞/收藏，不绕过「原创」审核
- 上传前由用户确认其对模型拥有版权（AI 生成或参考他人设计的情况尤其要确认）
- 许可证是法律选择，**必须用户自己选**，不要给默认值

## 参考

- 官方上传说明：https://wiki.bambulab.com/zh/makerworld/tutorials/how-to-upload-models
