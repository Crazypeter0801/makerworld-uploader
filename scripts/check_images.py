#!/usr/bin/env python3
"""Local image preflight; never changes the user's originals. Requires Pillow."""
import argparse
import json
from pathlib import Path
from PIL import Image, ImageOps


def inspect(path):
    path = Path(path).expanduser().resolve()
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        fmt = image.format          # exif_transpose 返回的新图 format 为 None，必须先取
        image = ImageOps.exif_transpose(image)
        width, height = image.size
        warnings = []
        if min(width, height) < 1200:
            warnings.append('短边不足 1200px，横竖两种封面裁切后可能发糊（建议值，非平台限制）')
        if path.stat().st_size > 8 * 1024 * 1024:
            warnings.append('超过 8MB，建议压缩副本；实际大小限制以当前页面为准')
        if fmt not in ('JPEG', 'PNG'):
            warnings.append(f'实际格式是 {fmt}，建议导出为 JPEG/PNG 副本再上传；不要只改扩展名')
        return {'path': str(path), 'width': width, 'height': height,
                'bytes': path.stat().st_size, 'warnings': warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', nargs='+')
    args = parser.parse_args()
    results = []
    for path in args.images:
        try:
            results.append(inspect(path))
        except Exception as exc:
            results.append({'path': path, 'error': str(exc)})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return int(any('error' in item for item in results))


if __name__ == '__main__':
    raise SystemExit(main())
