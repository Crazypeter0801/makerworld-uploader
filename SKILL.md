---
name: makerworld-publish
description: 帮用户将 3MF 模型和实拍照片上传到 MakerWorld 中国站并保存草稿，自动起草精简标题、描述、标签，检查图片并处理封面裁剪失败。用于上传模型、准备发布素材或续填已有草稿。
---

# MakerWorld 快速上传

目标：用户给模型和照片，agent 完成体检、起草、上传、填表和保存回读。默认只到草稿；脚本不点击发布。当前选择器针对中国站中文页面，全球站需先检查页面，不能声称已经支持。

## 尽量不让用户填表

1. 从用户提供的文件/目录和当前对话取信息。用户不需要填写 JSON、handle、草稿 ID 或打印参数。
2. 看照片和文件名，自动起草短标题（暂按 50 字以内，并检查当前 maxlength）、一句用途说明、3–5 个相关标签；分类选页面中真实存在的最贴近选项。描述默认 2–4 句，只写已知用途和必要的打印/装配注意事项，不填长模板、不编造打印效果或测试结论。用户要求更详细时再展开。
3. 从 3MF 体检结果取参数；未包含的时长、耗材重量等不猜。原创/二创、许可证优先沿用用户已明确的选择；缺失时把真正必要的问题合并一次问，不能从文件名推断原创、不能擅选许可。二创还需来源和实际改动。不要重复问已有答案。
4. 图片不足时先完成本地体检和文案；提醒缺哪张。没有实拍就保留待补项，渲染图不能冒充实拍。不要为了建议中的照片数量阻塞草稿准备。
5. agent 自己生成 spec，使用绝对路径。可见性沿用用户选择；未指定则保留页面状态。登录时才请用户扫码，不先问账号名。

## 图片怎么给（开始时简短提醒）

“给我 3MF 和现有实拍即可。封面最好是光线均匀、背景干净的完整成品照，四周留白，方便裁成横版 4:3 和竖版 3:4；有使用场景或细节照也可以一起给。”

建议 1 张主图 + 1 张使用场景 + 1 张关键细节，不把 3 张说成固定平台门槛。横竖封面可复用同一原图，先检查两种裁切都不切掉主体；不合适时只补要一张对应方向照片。默认不合成或生成“实拍”。JPEG/PNG、短边约 1200px 以上通常便于处理，是准备建议而非官方最低要求。当前页面的格式、尺寸、数量和大小限制优先。

读 [图片排错](references/image-upload.md) 仅在选图或上传失败时需要。

## 运行

依赖 Google Chrome、Python 3.9+、uv。持久登录数据在 `~/.makerworld-publish/profile`，报告在 `~/.makerworld-publish/out`；可通过 `MAKERWORLD_HOME` 指定工作目录。不要并发打开相同 profile。

```bash
python3 scripts/preflight.py /absolute/model.3mf
uv run --with pillow python scripts/check_images.py /absolute/photo.jpg
# 没有有效登录态才运行一次：
uv run --with playwright python scripts/mw.py login
# agent 生成 spec；单次浏览器会话上传、填表、存草稿：
uv run --with playwright python scripts/mw.py upload --spec /absolute/spec.json
```

先看体检结果；有 blocker 时检查具体原因，不把静态体检当作云切片结果。照片检查只读，不改原件。不固定等待 120 秒，不反复 whoami，不重复创建草稿。

spec 最小结构见 [模板](references/spec-template.json)。`cover_4x3`、`cover_3x4` 为封面；`photos` 为真实打印照片；`model_photos` 为可选补充模型图。未提供的可选字段直接省略。

入口 `/zh/my/models/publish`。`mw.py upload` 只在唯一可识别的 3MF input 上投文件；若打印配置/原始文件通道不明确，先检查页面。等待真实草稿路由后才填表，静态“打印配置”标题不能算上传成功。向导若需额外“下一步”或机型选择，查看当前页面再操作，脚本超时不等于素材错误。

## 封面可靠性与恢复

- 点真实上传区域，用 file chooser 选图。图片依次传，每张先处理裁剪弹窗；不按 Escape 丢弃裁剪。
- 选中文件不算上传成功：等待预览或计数变化且无忙碌状态。页面预览也不是保存证明。
- 保存请求必须成功，重新打开草稿核对标题、封面预览、照片计数、描述。还需视觉核对封面是否为本次选图及分类、标签、许可、来源、可见性；自动检查不能证明这些字段全部一致。
- 上传后状态不明确就停止，保留草稿 URL 和诊断截图，不盲重传。文件选择器尚未打开时才尝试下一个点击目标。
- 从 `out/upload_report.json` 取 `draft_id` 续填，不再次上传 3MF：

```bash
uv run --with playwright python scripts/draft_fill.py --draft ID --spec /absolute/spec.json --skip-images
```

`--skip-images` 仅在核对现有图片正确后使用。只补缺图时生成仅含缺失图片字段的 spec；已有图库非空时脚本停止，先人工/浏览器核对，不能用数量相等当成同一批照片。页面改版或选择器不明确：

```bash
uv run --with playwright python scripts/mw.py discover --url '草稿完整地址'
```

根据实际 DOM 修复；不猜 radio 的 true/false、不盲点未知弹窗。403/人机验证请用户在 Chrome 完成，不试图绕过。

## 交付

给出草稿链接、完成情况和仍缺的素材。只在回读检查通过时说“已存草稿”；不说“已发布”。脚本非零退出视为未完成。截图、页面转储可能含未公开内容，保留本地，不随 skill 打包。

表单/云切片历史说明见 [检查清单](references/publish-checklist.md)，其中平台规则需以当前页面和[官方文档](https://wiki.bambulab.com/zh/makerworld/tutorials/how-to-upload-models)为准，不能把旧经验当作实时事实。
