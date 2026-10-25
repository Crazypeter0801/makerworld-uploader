# MakerWorld Uploader

把 3D 模型发布到 [MakerWorld](https://makerworld.com.cn)（拓竹创客世界）的自动化技能包 —— 面向 Claude Code / WorkBuddy 等支持 **Agent Skills** 的客户端。

**核心判断：MakerWorld 没有公开的上传 API，只能走浏览器。** 本项目做的事是：把「体检 → 上传 → 填表 → 存草稿」这条链路上所有踩过的坑固化成脚本和文档，让 agent 一次跑通。

---

## 它解决什么

手工传一个模型到 MakerWorld，通常要经历：

1. 检查 3mf 是不是切片后保存的、料槽有没有混料、尺寸会不会超床
2. 网页端上传 3mf，等云端切片校验
3. 填标题（限 50 字）、分类、标签、许可证、可见性
4. 传 4:3 和 3:4 封面及实物图（数量以当前页面要求为准）
5. 写描述（CKEditor，粘 Markdown 不认）
6. 再走一遍打印配置页

这套东西一次两次还行，批量发就很折磨。本项目把 1 和 6 的一部分交给脚本，3/4/5 全部结构化，交给 JSON spec 驱动。

## 精简用法

给 agent 一个 3MF 和现有实拍照片即可。标题、短描述、标签和 spec 由 agent 起草；仅补问未知的模型来源、许可证等必要信息。封面建议完整主体、干净背景、均匀光线，留白适配横竖裁切。

图片上传逐张处理裁剪、等待预览变化、保存后回读；未确认成功会返回非零状态并保存诊断。已有草稿可续填，避免重复传 3MF。本轮仅用本地模拟页面验证，真实 MakerWorld 登录上传尚需实测。

## 安装

需要 **Google Chrome**、**uv**（自动装 Playwright，不用下浏览器二进制）。

```bash
git clone https://github.com/Crazypeter0801/makerworld-uploader.git \
  ~/.workbuddy/skills/makerworld-publish
```

> 目录名保持 `makerworld-publish`，和 `SKILL.md` 里的 `name` 字段一致；技能加载器按目录名识别。

运行态数据（Chrome profile、截图、dump）写在 `~/.makerworld-publish/`，**不在仓库里**，所以 clone 下来不会带任何人的登录态。

## 用法

### 1. 登录（只需一次）

```bash
cd scripts
uv run --with playwright python mw.py login
```

会弹出真实 Chrome（headless 会被 Cloudflare 拦，这是实测结论），人工登录后自动收起，profile 长期有效。

### 2. 体检素材

```bash
python3 scripts/preflight.py model.3mf --cover 实拍.jpg
```

纯标准库，不需要 Playwright。检查项：

- 高低温耗材混打（官方 FAQ 明确会判云切片失败的硬错误）
- 非 Bambu 官方预设
- 文件名层高 vs 工程实际层高
- 打印床尺寸 / 贴边距离（触发「超出规避区」的常见原因）
- 是否已切片（**不是必须项** —— 平台用云切片验证，这条降级为提示）

### 3. 上传 3mf，拿草稿 id

```bash
uv run --with playwright python mw.py upload --spec spec.json
```

### 4. 续填已有草稿（首次 upload 已自动填表并保存）

```bash
uv run --with playwright python draft_fill.py --draft <id> --spec spec.json
```

脚本不会点「发布」。默认交付草稿，发布留给用户操作。

## spec 长什么样

见 `references/spec-template.json`：

```json
{
  "files": ["/abs/path/model.3mf"],
  "title": "模型名（≤50 字）",
  "description": "## 支持 Markdown，脚本会转成 HTML 粘进编辑器",
  "tags": ["标签1", "标签2"],
  "category": "其他家用模型",
  "license": "CC BY-SA",
  "visibility": "公开",
  "cover_4x3": "/abs/path/cover.jpg",
  "cover_3x4": "/abs/path/cover_portrait.jpg",
  "photos": ["/abs/path/a.jpg", "/abs/path/b.jpg", "/abs/path/c.jpg"]
}
```

## 实测到的硬坑（都已写进 SKILL.md）

| 坑 | 说明 |
|---|---|
| Cloudflare | `curl` 403；Playwright headless 也被拦；只有真实 Chrome + 有头 + 持久 profile 能过 |
| 上传入口 | 是 `/zh/my/models/publish`，不是 `/{lang}/@{handle}/upload`（那是列表页） |
| 可见性 | radio 的 value 是反的：按可见标签选择，不依赖 true/false |
| 模型来源 | 「原创/二次创作」必填，不选时点「保存草稿」**静默失败**（无报错、无请求） |
| 分类 | MUI Autocomplete，必须 `press_sequentially`；`type()` 不触发 |
| 图片 | 隐藏 input 直接 `set_input_files` 组件不认，必须走 `expect_file_chooser` |
| 描述 | CKEditor 5，只能 paste HTML；重复粘贴要先清空 |
| 保存验证 | 点击成功 ≠ 落盘，只有重新打开页面回读才能确认 |

## 目录

```
SKILL.md                        技能定义与完整踩坑记录
scripts/mw.py                   会话管理（login/whoami/discover/upload/shot）
scripts/draft_fill.py           spec → 草稿表单（含 Markdown 转 HTML）
scripts/preflight.py            3mf 发布前体检（纯标准库）
references/publish-checklist.md 官方字段要求与常见报错
references/spec-template.json   发布 spec 模板
```

## 隐私

- 不收集、不外传任何数据，所有请求只发往 `makerworld.com.cn` / `makerworld.com`
- 登录态只存在本机 `~/.makerworld-publish/profile/`
- `login.json` 只记录「是否已登录 + handle + cookie 名」，不写 cookie 值

## 免责

非官方项目，与 Bambu Lab / MakerWorld 无关联。自动化操作可能触发平台风控，请自行控制频率；发布内容需自行确认版权与许可证合规。

## License

MIT
